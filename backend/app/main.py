"""Sentinel-G FastAPI entrypoint. PRD §10 Day 1 — Done when /health returns 200 and gds.version() works."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app import __version__
from backend.app.analytics_cache import get_status as _cache_status
from backend.app.api.analyse import router as analyse_router
from backend.app.api.companies import router as companies_router
from backend.app.api.narrative import router as narrative_router
from backend.app.api.report import router as report_router
from backend.app.api.shells import router as shells_router
from backend.app.api.sources import router as sources_router
from backend.app.api.trending import router as trending_router
from backend.app.api.upload import router as upload_router
from backend.app.auth.routes import router as auth_router
from backend.app.config import get_settings
from backend.app.deps import get_driver, lifespan
from backend.app.middleware.rate_limit import RateLimitMiddleware
from backend.app.ml_inference import get_status as _ml_status
from backend.app.narrative import get_narrator


_INSECURE_JWT_DEFAULT = "dev-only-not-for-production"


def create_app() -> FastAPI:
    settings = get_settings()
    # Refuse to boot in non-dev environments while still using the source-tree
    # default JWT secret. Anyone with the public repo can otherwise forge tokens.
    if settings.app_env != "dev" and settings.jwt_secret == _INSECURE_JWT_DEFAULT:
        raise RuntimeError(
            f"JWT_SECRET is using the insecure source-tree default in "
            f"app_env={settings.app_env!r}. Set JWT_SECRET to a strong random "
            f"value (>=32 bytes) before starting in staging/prod."
        )
    app = FastAPI(
        title="Sentinel-G API",
        version=__version__,
        description="SME Financial Fraud Detection — HackHazards '26",
        lifespan=lifespan,
    )

    # PRD §10 Day 24 — settings-driven origin list (no wildcards, no
    # automatic localhost-in-prod). `CORS_ALLOWED_ORIGINS` env var picks
    # up the Vercel preview/prod URLs in deployed environments.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Report-Id"],
    )

    # PRD §10 Day 24 — 10 req/min per JWT (per-IP fallback for anonymous
    # requests). Returns 429 with Retry-After on breach.
    app.add_middleware(RateLimitMiddleware, limit_per_min=settings.rate_limit_per_min)

    app.include_router(auth_router)
    app.include_router(analyse_router)
    app.include_router(companies_router)
    app.include_router(narrative_router)
    app.include_router(upload_router)
    app.include_router(report_router)
    app.include_router(sources_router)
    app.include_router(shells_router)
    app.include_router(trending_router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "env": settings.app_env}

    @app.get("/health/ml", tags=["meta"])
    async def ml_health() -> JSONResponse:
        """ML-tier readiness. Returns 503 when meta-learner artefacts are not
        loaded OR analytics_cache has not built yet — both states cause
        /analyse to silently emit `p_fraud_calibrated: null`."""
        ml = _ml_status()
        cache = _cache_status()
        ok = bool(ml["loaded"] and cache.get("built"))
        body = {"ok": ok, "meta_learner": ml, "analytics_cache": cache}
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.get("/health/mistral", tags=["meta"])
    async def mistral_health() -> JSONResponse:
        """Mistral narrative service readiness. Returns 503 when the API
        key isn't configured OR the SDK failed to initialise — the
        narrative endpoint will still respond via the template fallback,
        but the frontend can surface a 'narrative service offline'
        banner so judges aren't surprised by the generic prose."""
        st = await get_narrator().status()
        ok = bool(st["configured"] and st["live"])
        return JSONResponse(st | {"ok": ok}, status_code=200 if ok else 503)

    @app.get("/health/neo4j", tags=["meta"])
    async def neo4j_health() -> dict[str, object]:
        """Verifies the Neo4j driver is up and GDS plugin is loaded (PRD §10 Day 1 acceptance)."""
        driver = get_driver()
        async with driver.session(database=settings.neo4j_database) as session:
            result = await session.run("RETURN 1 AS ok")
            ok_record = await result.single()

            gds_result = await session.run("CALL gds.version() YIELD gdsVersion RETURN gdsVersion")
            gds_record = await gds_result.single()

        return {
            "neo4j_reachable": bool(ok_record and ok_record["ok"] == 1),
            "gds_version": gds_record["gdsVersion"] if gds_record else None,
        }

    return app


app = create_app()
