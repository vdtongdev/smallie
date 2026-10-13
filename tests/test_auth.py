import uuid

import pytest
from fastapi.testclient import TestClient

from app import accounts, auth
from app.api.main import app
from app.config import get_settings
from tests.conftest import requires_db

ADMIN_PASSWORD = "s3cret-admin"


class FakeAccounts:
    """In-memory stand-in for the account rows in app/accounts.py."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def create_account(self, name, username, password):
        if any(r["username"] == username for r in self.rows.values()):
            raise accounts.UsernameTaken
        if sum(1 for r in self.rows.values() if r["password_hash"]) >= get_settings().max_accounts:
            raise accounts.AccountLimitReached
        user_id = str(uuid.uuid4())
        self.rows[user_id] = {"name": name, "username": username, "password_hash": accounts.hash_password(password)}
        return user_id

    def ensure_admin(self, app_password):
        self.rows.setdefault(accounts.ADMIN_USER_ID, {"name": "Admin", "username": "admin", "password_hash": None})

    def find_by_username(self, username):
        return next(({"id": i, "password_hash": r["password_hash"]}
                     for i, r in self.rows.items() if r["username"] == username), None)

    def user_exists(self, user_id):
        return user_id in self.rows

    def get_profile(self, user_id):
        r = self.rows.get(user_id)
        return {"name": r["name"], "username": r["username"]} if r else None


@pytest.fixture
def client(monkeypatch):
    fake = FakeAccounts()
    for name in ("create_account", "ensure_admin", "find_by_username", "user_exists", "get_profile"):
        monkeypatch.setattr(accounts, name, getattr(fake, name))
    monkeypatch.setattr(auth, "_known_users", set())
    monkeypatch.setattr(auth, "FAILED_LOGIN_DELAY_S", 0)
    monkeypatch.setattr(get_settings(), "app_password", ADMIN_PASSWORD)
    # Not used as a context manager, so the lifespan (DB + agents) never starts.
    c = TestClient(app)
    c.fake = fake
    return c


def signup(client, username="lan", password="password1", name="Lan Nguyen"):
    return client.post("/api/signup", json={"name": name, "username": username, "password": password})


def test_password_hashing():
    stored = accounts.hash_password("correct horse")
    assert stored.startswith("scrypt$")
    assert accounts.verify_password("correct horse", stored)
    assert not accounts.verify_password("wrong horse", stored)
    assert not accounts.verify_password("anything", None)


def test_requires_login(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/conversations").status_code == 401
    assert client.post("/api/chat/auto", json={"message": "hi"}).status_code == 401


def test_signup_logs_in(client):
    res = signup(client, username=" Lan ")
    assert res.status_code == 200
    me = client.get("/api/me").json()
    assert me["name"] == "Lan Nguyen"
    assert me["username"] == "lan"
    assert me["is_admin"] is False
    assert "password" not in str(me)


def test_agents_endpoint_and_unknown_agent(client):
    signup(client)
    agents = client.get("/api/agents").json()
    assert [a["route"] for a in agents] == ["docs", "sql"]
    assert agents[0]["labels"]["vi"] == "Trợ lý tài liệu"
    assert client.post("/api/chat/nope", json={"message": "hi"}).status_code == 404


@pytest.mark.parametrize("body", [
    {"name": "A", "username": "ab", "password": "password1"},  # username too short
    {"name": "A", "username": "lan nguyen", "password": "password1"},  # space
    {"name": "A", "username": "admin", "password": "password1"},  # reserved
    {"name": "A", "username": "lan", "password": "short"},
    {"name": "  ", "username": "lan", "password": "password1"},
])
def test_signup_validation(client, body):
    assert client.post("/api/signup", json=body).status_code == 422


def test_duplicate_username(client):
    assert signup(client).status_code == 200
    assert signup(client).status_code == 409


def test_account_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_accounts", 2)
    assert signup(client, "one").status_code == 200
    assert signup(client, "two").status_code == 200
    assert signup(client, "three").status_code == 403


def test_login_logout(client):
    signup(client)
    client.post("/api/logout")
    assert client.get("/api/me").status_code == 401

    assert client.post("/api/login", json={"username": "lan", "password": "nope-nope"}).status_code == 401
    assert client.post("/api/login", json={"username": "ghost", "password": "password1"}).status_code == 401
    assert client.get("/api/me").status_code == 401

    assert client.post("/api/login", json={"username": "LAN", "password": "password1"}).status_code == 200
    assert client.get("/api/me").json()["username"] == "lan"


def test_each_account_has_its_own_id(client):
    signup(client, "one")
    first = client.get("/api/me").json()["user_id"]
    client.post("/api/logout")
    signup(client, "two")
    assert client.get("/api/me").json()["user_id"] != first


def test_admin_login(client):
    assert client.post("/api/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"username": "admin", "password": ADMIN_PASSWORD}).status_code == 200
    me = client.get("/api/me").json()
    assert me["user_id"] == accounts.ADMIN_USER_ID  # inherits the old shared team data
    assert me["is_admin"] is True


def test_no_admin_without_app_password(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "app_password", "")
    assert client.post("/api/login", json={"username": "admin", "password": ""}).status_code == 401


def test_admin_routes_need_admin(client):
    assert client.get("/api/admin/accounts").status_code == 401
    signup(client)
    assert client.get("/api/admin/accounts").status_code == 403


def test_deleted_account_is_logged_out(client):
    signup(client)
    user_id = client.get("/api/me").json()["user_id"]
    del client.fake.rows[user_id]
    auth.forget_user(user_id)
    assert client.get("/api/me").status_code == 401


@requires_db
def test_account_rows_roundtrip():
    from app.db import init_db

    init_db()
    username = f"test-{uuid.uuid4().hex[:12]}"
    user_id = accounts.create_account("Test User", username, "password1")
    try:
        found = accounts.find_by_username(username)
        assert found["id"] == user_id
        assert accounts.verify_password("password1", found["password_hash"])
        assert accounts.get_profile(user_id) == {"name": "Test User", "username": username}
        with pytest.raises(accounts.UsernameTaken):
            accounts.create_account("Other", username, "password2")
        assert accounts.set_password(user_id, "password2")
        assert accounts.verify_password("password2", accounts.find_by_username(username)["password_hash"])
    finally:
        accounts.delete_user_row(user_id)
    assert not accounts.user_exists(user_id)
