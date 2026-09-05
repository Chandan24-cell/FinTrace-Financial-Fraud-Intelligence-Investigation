"""Auth dependencies for protected routes."""

from typing import Iterable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from backend.app.auth.jwt import TokenError, decode_access_token
from backend.app.auth.repository import get_user_by_id
from backend.app.deps import get_driver

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
    except TokenError as exc:
        raise credentials_exc from exc

    user_id = payload.get("sub")
    if not user_id:
        raise credentials_exc

    driver = get_driver()
    user = await get_user_by_id(driver, user_id)
    if user is None or not user.get("is_active", False):
        raise credentials_exc
    return user


def require_roles(*allowed: str):
    """Dependency factory that gates a route to specific UserRole values.

    Closes LOCAL_TEST_REPORT §4.6: previously every protected route accepted
    any authenticated user regardless of role. Use as:

        @router.get("/", dependencies=[Depends(require_roles("admin"))])

    or attach via Depends in a route argument when the role string is needed
    in the handler. Tests can override the same dependency on app.dependency_
    overrides[get_current_user] to inject a fake user with the desired role.
    """
    allowed_set = set(allowed)

    async def _gate(user: dict = Depends(get_current_user)) -> dict:
        role = user.get("role")
        if role not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role {role!r} is not authorised for this endpoint. "
                    f"Required one of: {sorted(allowed_set)}"
                ),
            )
        return user

    return _gate
