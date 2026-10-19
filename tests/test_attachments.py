import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes_chat import ChatRequest, attachment_names, with_attachments
from app.core.db import documents, get_engine, init_db, users
from tests.conftest import requires_db


@pytest.fixture
def two_users():
    init_db()
    alice, bob = f"test-attach-{uuid.uuid4()}", f"test-attach-{uuid.uuid4()}"
    with get_engine().begin() as conn:
        conn.execute(users.insert(), [{"id": alice, "display_name": "A"}, {"id": bob, "display_name": "B"}])
    yield alice, bob
    with get_engine().begin() as conn:  # cascades to their documents
        conn.execute(users.delete().where(users.c.id.in_([alice, bob])))


def add_document(user_id: str, filename: str, status: str = "ready") -> str:
    document_id = str(uuid.uuid4())
    with get_engine().begin() as conn:
        conn.execute(documents.insert().values(id=document_id, user_id=user_id, filename=filename,
                                               path=f"/tmp/{document_id}", status=status))
    return document_id


def error_of(*args) -> HTTPException:
    with pytest.raises(HTTPException) as error:
        attachment_names(*args)
    return error.value


@requires_db
def test_ready_attachments_keep_their_order(two_users):
    alice, _ = two_users
    a, b = add_document(alice, "a.png"), add_document(alice, "b.png")
    assert attachment_names(alice, [b, a]) == ["b.png", "a.png"]
    assert attachment_names(alice, []) == []


@requires_db
def test_another_users_attachment_is_not_found_and_not_named(two_users):
    alice, bob = two_users
    error = error_of(alice, [add_document(bob, "bob-receipt.png")])
    assert error.status_code == 404 and "bob-receipt" not in error.detail
    assert error_of(alice, [str(uuid.uuid4())]).status_code == 404  # no such document


@requires_db
def test_attachments_still_being_read_or_failed_are_conflicts(two_users):
    alice, _ = two_users
    assert error_of(alice, [add_document(alice, "slow.png", "processing")]).detail == "slow.png is still being read"
    assert error_of(alice, [add_document(alice, "bad.png", "failed")]).detail == "bad.png could not be read"
    assert error_of(alice, [add_document(alice, "bad.png", "failed")]).status_code == 409


def test_attached_files_are_named_after_the_message():
    assert with_attachments("how much did I pay?", ["receipt.png", "menu.jpg"]) == \
        "how much did I pay?\n\nAttached: receipt.png, menu.jpg"
    assert with_attachments("hi", []) == "hi"


def test_at_most_five_attachments():
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", attachments=[str(i) for i in range(6)])
