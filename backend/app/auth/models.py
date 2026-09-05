"""Pydantic schemas for auth endpoints."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

UserRole = Literal["credit_officer", "investigator", "auditor", "admin"]

DEFAULT_SELF_REGISTER_ROLE: UserRole = "credit_officer"

# Roles that can be chosen during open self-registration.
# admin is excluded — must be granted by a seeding script or an admin API call.
SELF_REGISTRABLE_ROLES: frozenset[str] = frozenset(
    {"credit_officer", "investigator", "auditor"}
)


class UserRegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = DEFAULT_SELF_REGISTER_ROLE

    @field_validator("role")
    @classmethod
    def role_must_be_self_registrable(cls, v: str) -> str:
        if v not in SELF_REGISTRABLE_ROLES:
            raise ValueError(
                f"Role '{v}' cannot be self-registered; contact an admin."
            )
        return v


class UserLoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    user_id: str
    email: EmailStr
    role: UserRole
    created_at: datetime
    is_active: bool


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in_seconds: int
