"""Per-JWT (or per-IP) sliding-window rate limiter — PRD §10 Day 24.

PRD §10 Day-24 verbatim: 'Rate limiting (10 req/min per JWT).'

Implementation notes:
  - Bucket key = `sub` claim from the Bearer JWT, falling back to the
    client IP when the request is anonymous (login, /health). This means
    a logged-out attacker can't hammer the API by rotating tokens.
  - Window is a true sliding 60s — we keep the per-key timestamp deque
    and pop entries older than `window_seconds`.
  - On limit breach, returns 429 with Retry-After in seconds (PRD §13
    Definition of Done: 'returns 422 for malformed CIN, 429 for rate
    exceeded'). The header is consumed by the React client.
  - Storage is in-process (single Gunicorn worker is current target).
    Multi-worker deploys would need a shared store; out of scope here per
    PRD §2.1's 'no Redis' constraint.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware

from backend.app.config import Settings, get_settings

# Routes that should never count against the rate limit — health probes,
# the OpenAPI doc, and the auth bootstrap flow (login itself is rate-limited
# but lookup/me endpoints would lock users out under the same key).
_EXEMPT_PATH_PREFIXES = ("/health", "/docs", "/openapi.json", "/redoc")


def _extract_sub(authorization: str | None, settings: Settings) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm],
            options={"verify_exp": False},  # expiry handled separately by get_current_user
        )
    except JWTError:
        return None
    sub = payload.get("sub")
    return str(sub) if sub else None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding 60s window. Default 10 req/min."""

    def __init__(self, app, limit_per_min: int | None = None, window_seconds: float = 60.0):
        super().__init__(app)
        self._limit = limit_per_min
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    @property
    def limit(self) -> int:
        return self._limit if self._limit is not None else get_settings().rate_limit_per_min

    def _bucket_key(self, request: Request) -> str:
        sub = _extract_sub(request.headers.get("authorization"), get_settings())
        if sub:
            return f"jwt:{sub}"
        client = request.client.host if request.client else "unknown"
        return f"ip:{client}"

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if any(request.url.path.startswith(p) for p in _EXEMPT_PATH_PREFIXES):
            return await call_next(request)

        key = self._bucket_key(request)
        now = time.monotonic()
        bucket = self._hits[key]
        cutoff = now - self._window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= self.limit:
            retry_after = max(1, int(self._window - (now - bucket[0])))
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded ({self.limit}/min)"},
                headers={"Retry-After": str(retry_after)},
            )

        bucket.append(now)
        response = await call_next(request)
        # Surface remaining budget so the React client can degrade gracefully.
        response.headers["X-RateLimit-Limit"] = str(self.limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, self.limit - len(bucket)))
        return response

    def reset(self) -> None:
        """Test hook — wipes all per-key counters."""
        self._hits.clear()
