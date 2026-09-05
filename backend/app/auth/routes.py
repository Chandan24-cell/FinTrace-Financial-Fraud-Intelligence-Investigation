"""Auth endpoints — register, login, me. PRD §10 Day 2."""

from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.auth.deps import get_current_user
from backend.app.auth.hashing import verify_password
from backend.app.auth.jwt import create_access_token
from backend.app.auth.models import (
    TokenOut,
    UserLoginIn,
    UserOut,
    UserRegisterIn,
)
from backend.app.auth.repository import (
    UserAlreadyExistsError,
    create_user,
    get_user_by_email,
)
from backend.app.config import get_settings
from backend.app.deps import get_driver

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(payload: UserRegisterIn) -> UserOut:
    driver = get_driver()
    try:
        record = await create_user(
            driver, payload.email, payload.password, payload.role,
        )
    except UserAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email already registered: {exc}",
        ) from exc
    return UserOut(**record)


@router.post("/login", response_model=TokenOut)
async def login(payload: UserLoginIn) -> TokenOut:
    driver = get_driver()
    record = await get_user_by_email(driver, payload.email)
    if record is None or not verify_password(payload.password, record["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    settings = get_settings()
    token = create_access_token(
        subject=record["user_id"],
        extra_claims={"email": record["email"], "role": record["role"]},
    )
    return TokenOut(
        access_token=token,
        token_type="bearer",
        expires_in_seconds=settings.jwt_expiry_hours * 3600,
    )


@router.get("/me", response_model=UserOut)
async def me(current_user: dict = Depends(get_current_user)) -> UserOut:
    return UserOut(**current_user)
