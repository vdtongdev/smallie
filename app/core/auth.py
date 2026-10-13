"""Who is the current user?

This is the ONLY place that knows how identity works. Everyone has an account
(name, username, password; rows in app/core/accounts.py) and logs in with it; the signed
session cookie then holds their user_id. Agents, memory and storage are partitioned
by that user_id.

The "admin" account (user id "team") logs in with APP_PASSWORD and may also manage the
other accounts (app/api/routes_admin.py).
"""

import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.core import accounts
from app.core.config import get_settings

SESSION_KEY = "user_id"
FAILED_LOGIN_DELAY_S = 1.0

_known_users: set[str] = set()


def get_current_user(request: Request) -> str:
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        raise HTTPException(401, "Login required")
    if user_id not in _known_users:
        if not accounts.user_exists(user_id):  # e.g. deleted by the admin
            request.session.clear()
            raise HTTPException(401, "Login required")
        _known_users.add(user_id)
    return user_id


def get_admin_user(user_id: str = Depends(get_current_user)) -> str:
    if user_id != accounts.ADMIN_USER_ID:
        raise HTTPException(403, "Admin only")
    return user_id


def forget_user(user_id: str) -> None:
    _known_users.discard(user_id)


router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=128)


class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    username: str = Field(pattern=r"^[a-z0-9_-]{3,32}$")
    password: str = Field(min_length=8, max_length=128)

    @field_validator("name", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("username", mode="before")
    @classmethod
    def _lower(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("username")
    @classmethod
    def _not_reserved(cls, value: str) -> str:
        if value == accounts.ADMIN_USERNAME:
            raise ValueError("this username is reserved")
        return value


def _start_session(request: Request, user_id: str) -> None:
    request.session.clear()
    request.session[SESSION_KEY] = user_id
    _known_users.add(user_id)


@router.post("/signup")
def signup(body: SignupRequest, request: Request):
    try:
        user_id = accounts.create_account(body.name, body.username, body.password)
    except accounts.UsernameTaken:
        raise HTTPException(409, "Username already taken") from None
    except accounts.AccountLimitReached:
        raise HTTPException(403, "Account limit reached") from None
    _start_session(request, user_id)
    return _me(user_id)


@router.post("/login")
def login(body: LoginRequest, request: Request):
    username = body.username.strip().lower()
    user_id = None
    if username == accounts.ADMIN_USERNAME:
        expected = get_settings().app_password
        if expected and secrets.compare_digest(body.password.encode(), expected.encode()):
            accounts.ensure_admin(expected)
            user_id = accounts.ADMIN_USER_ID
    else:
        account = accounts.find_by_username(username)
        # Hash even for unknown usernames so timing doesn't reveal which ones exist.
        if accounts.verify_password(body.password, account and account["password_hash"]) and account:
            user_id = account["id"]
    if not user_id:
        time.sleep(FAILED_LOGIN_DELAY_S)  # slows down password guessing
        raise HTTPException(401, "Wrong username or password")
    _start_session(request, user_id)
    return _me(user_id)


@router.post("/logout", status_code=204)
def logout(request: Request):
    request.session.clear()


@router.get("/me")
def me(user_id: str = Depends(get_current_user)):
    return _me(user_id)


def _me(user_id: str) -> dict:
    profile = accounts.get_profile(user_id) or {}
    return {
        "user_id": user_id,
        "name": profile.get("name") or profile.get("username") or "",
        "username": profile.get("username") or "",
        "is_admin": user_id == accounts.ADMIN_USER_ID,
        "max_accounts": get_settings().max_accounts,
    }
