"""Account rows: name, username and password hash on the `users` table.

The admin account keeps the id "team" (the old shared user, so it inherits that data),
has the username "admin" and logs in with APP_PASSWORD; no hash is stored for it.
Passwords are hashed with scrypt and never leave this module and app/core/auth.py.
"""

import hashlib
import secrets
import uuid

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.db import get_engine, users

ADMIN_USER_ID = "team"
ADMIN_USERNAME = "admin"

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
_SIGNUP_LOCK = 7_310_001  # pg advisory lock id: one signup at a time, so the limit holds


class UsernameTaken(Exception):
    pass


class AccountLimitReached(Exception):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        scheme, salt, digest = (stored or "").split("$")
    except ValueError:
        scheme, salt, digest = "scrypt", "00", ""
    actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    return scheme == "scrypt" and secrets.compare_digest(actual.hex(), digest)


def ensure_admin(app_password: str) -> None:
    if not app_password:
        return
    stmt = insert(users).values(id=ADMIN_USER_ID, username=ADMIN_USERNAME, display_name="Admin")
    stmt = stmt.on_conflict_do_update(
        index_elements=[users.c.id],
        set_={"username": ADMIN_USERNAME, "display_name": func.coalesce(users.c.display_name, "Admin")},
    )
    with get_engine().begin() as conn:
        conn.execute(stmt)


def create_account(name: str, username: str, password: str) -> str:
    """Insert a new account and return its user id."""
    user_id = str(uuid.uuid4())
    password_hash = hash_password(password)
    try:
        with get_engine().begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _SIGNUP_LOCK})
            count = conn.execute(select(func.count()).where(users.c.password_hash.is_not(None))).scalar_one()
            if count >= get_settings().max_accounts:
                raise AccountLimitReached
            conn.execute(users.insert().values(
                id=user_id, display_name=name, username=username, password_hash=password_hash,
            ))
    except IntegrityError as exc:
        raise UsernameTaken from exc
    return user_id


def find_by_username(username: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(users.c.id, users.c.password_hash).where(users.c.username == username)
        ).mappings().first()
    return dict(row) if row else None


def user_exists(user_id: str) -> bool:
    with get_engine().connect() as conn:
        return conn.execute(select(users.c.id).where(users.c.id == user_id)).first() is not None


def get_profile(user_id: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(users.c.display_name.label("name"), users.c.username).where(users.c.id == user_id)
        ).mappings().first()
    return dict(row) if row else None


def list_accounts() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(users.c.id, users.c.display_name.label("name"), users.c.username, users.c.created_at)
            .where(users.c.username.is_not(None))
            .order_by(users.c.created_at)
        ).mappings().all()
    return [{**r, "is_admin": r["id"] == ADMIN_USER_ID} for r in rows]


def set_password(user_id: str, password: str) -> bool:
    with get_engine().begin() as conn:
        result = conn.execute(
            update(users).where(users.c.id == user_id, users.c.password_hash.is_not(None))
            .values(password_hash=hash_password(password))
        )
    return result.rowcount > 0


def delete_user_row(user_id: str) -> None:
    """Delete the user; conversations and documents rows go with it (ON DELETE CASCADE)."""
    with get_engine().begin() as conn:
        conn.execute(delete(users).where(users.c.id == user_id))
