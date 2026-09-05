"""User repository — Cypher reads/writes against the Neo4j User node.

PRD constraint: Neo4j is the single data store. No external user table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from neo4j import AsyncDriver

from backend.app.auth.hashing import hash_password
from backend.app.auth.models import UserRole
from backend.app.config import get_settings


class UserAlreadyExistsError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


def _record_to_dict(record: Any) -> dict[str, Any]:
    node = record["u"]
    data = dict(node)
    created_raw = data.get("created_at")
    if created_raw is not None and hasattr(created_raw, "to_native"):
        data["created_at"] = created_raw.to_native()
    return data


async def create_user(
    driver: AsyncDriver, email: str, password: str, role: UserRole
) -> dict[str, Any]:
    settings = get_settings()
    user_id = str(uuid4())
    now = datetime.now(timezone.utc)
    hashed = hash_password(password)

    cypher = """
    CREATE (u:User {
      user_id: $user_id,
      email: $email,
      hashed_password: $hashed_password,
      role: $role,
      created_at: datetime($created_at),
      is_active: true
    })
    RETURN u
    """
    async with driver.session(database=settings.neo4j_database) as session:
        try:
            result = await session.run(
                cypher,
                user_id=user_id,
                email=email,
                hashed_password=hashed,
                role=role,
                created_at=now.isoformat(),
            )
            record = await result.single()
        except Exception as exc:
            msg = str(exc)
            if "ConstraintValidationFailed" in msg or "already exists" in msg.lower():
                raise UserAlreadyExistsError(email) from exc
            raise

    if record is None:
        raise RuntimeError("CREATE (u:User) returned no record")
    return _record_to_dict(record)


async def get_user_by_email(driver: AsyncDriver, email: str) -> dict[str, Any] | None:
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run("MATCH (u:User {email: $email}) RETURN u", email=email)
        record = await result.single()
    if record is None:
        return None
    return _record_to_dict(record)


async def get_user_by_id(driver: AsyncDriver, user_id: str) -> dict[str, Any] | None:
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run("MATCH (u:User {user_id: $user_id}) RETURN u", user_id=user_id)
        record = await result.single()
    if record is None:
        return None
    return _record_to_dict(record)
