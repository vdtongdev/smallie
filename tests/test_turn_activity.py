import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.core.conversations import create_conversation, delete_conversation, get_turn_activity, save_turn_activity
from app.core.db import get_engine, init_db, turn_activity, users
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def env():
    init_db()
    created: list[str] = []

    def make_user() -> str:
        user_id = f"test-activity-{uuid.uuid4()}"
        with get_engine().begin() as conn:
            conn.execute(users.insert().values(id=user_id, display_name="Test"))
        created.append(user_id)
        return user_id

    yield SimpleNamespace(user=make_user)
    with get_engine().begin() as conn:
        conn.execute(delete(users).where(users.c.id.in_(created)))


def test_saved_events_come_back_under_their_turn(env):
    user = env.user()
    chat = create_conversation(user, "auto", "hi")["id"]
    save_turn_activity(user, chat, 1, [{"type": "agent", "name": "sql"}])
    assert get_turn_activity(user, chat) == {1: [{"type": "agent", "name": "sql"}]}


def test_rows_of_one_turn_are_joined_in_order(env):
    user = env.user()
    chat = create_conversation(user, "auto", "hi")["id"]
    a, b, c = {"type": "a"}, {"type": "b"}, {"type": "c"}
    save_turn_activity(user, chat, 2, [a])
    save_turn_activity(user, chat, 2, [b])
    save_turn_activity(user, chat, 1, [c])
    assert get_turn_activity(user, chat) == {1: [c], 2: [a, b]}


def test_another_users_rows_are_never_returned(env):
    owner, other = env.user(), env.user()
    chat = create_conversation(owner, "auto", "hi")["id"]
    save_turn_activity(owner, chat, 1, [{"type": "a"}])
    assert get_turn_activity(other, chat) == {}
    assert get_turn_activity(owner, chat) == {1: [{"type": "a"}]}


def test_deleting_the_chat_removes_its_rows(env):
    user = env.user()
    chat = create_conversation(user, "auto", "hi")["id"]
    save_turn_activity(user, chat, 1, [{"type": "a"}])
    delete_conversation(user, chat)
    with get_engine().begin() as conn:
        rows = conn.execute(select(turn_activity).where(turn_activity.c.conversation_id == chat)).all()
    assert rows == []


def test_a_chat_without_rows_reads_empty(env):
    user = env.user()
    chat = create_conversation(user, "auto", "hi")["id"]
    assert get_turn_activity(user, chat) == {}
