"""scripts/rechunk_images.py against the real Postgres, with fake embeddings."""

import re

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_postgres import PGVector
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.db import documents, get_engine
from app.features.documents import ingest
from tests.conftest import requires_db
from tests.test_graph import add_document, make_user  # noqa: F401 - fixture

READING = "\n".join(f"key {n}: value {n}" for n in range(100))
LABEL = re.compile(r"^\[Image 2, p\. 3\]\n")


@pytest.fixture
def store(monkeypatch):
    from scripts import rechunk_images

    store = PGVector(embeddings=DeterministicFakeEmbedding(size=get_settings().embed_dim),
                     connection=get_settings().sqlalchemy_url, collection_name="test_rechunk",
                     pre_delete_collection=True, use_jsonb=True)
    monkeypatch.setattr(rechunk_images, "get_vector_store", lambda: store)
    yield store
    store.delete_collection()


def old_style(user_id, document_id, filename, text_chunks=()):
    """The chunks as stored before the fix: the reading split with overlap, only the first labelled."""
    pieces = ingest._splitter.split_text(f"[Image 2, p. 3]\n{READING}")
    assert len(pieces) > 2
    chunks = [Document(t, metadata={"filename": filename, "page": 1}) for t in text_chunks]
    chunks += [Document(p, metadata={"filename": filename, "kind": "image", "image": 2, "page": 3}) for p in pieces]
    for chunk in chunks:
        chunk.metadata.update(user_id=user_id, document_id=document_id)
    return chunks


def store_old(store, user_id, filename, text_chunks=()):
    document_id = add_document(user_id, filename)
    chunks = old_style(user_id, document_id, filename, text_chunks)
    store.add_documents(chunks, ids=ingest.chunk_ids(document_id, len(chunks)))
    with get_engine().begin() as conn:
        conn.execute(update(documents).where(documents.c.id == document_id).values(chunk_count=len(chunks)))
    return document_id, len(chunks)


def stored(store, document_id):
    count = chunk_count(document_id)
    return store.get_by_ids(ingest.chunk_ids(document_id, count))


def chunk_count(document_id):
    with get_engine().connect() as conn:
        return conn.execute(select(documents.c.chunk_count).where(documents.c.id == document_id)).scalar_one()


def by_id(chunks):
    return sorted(chunks, key=lambda c: int(c.id.rsplit(":", 1)[1]))


@requires_db
def test_rechunk_rebuilds_old_overlapping_chunks(make_user, store):
    from scripts.rechunk_images import rechunk

    user = make_user()
    document_id, old_count = store_old(store, user, "scan.png")
    assert rechunk([user]) == 1
    chunks = by_id(stored(store, document_id))
    assert len(chunks) == chunk_count(document_id)
    assert all(LABEL.match(c.page_content) and c.metadata["kind"] == "image" for c in chunks)
    assert [c.metadata["part"] for c in chunks] == list(range(len(chunks)))
    assert all(len(c.page_content) <= 800 for c in chunks)
    assert "\n".join(LABEL.sub("", c.page_content) for c in chunks) == READING
    stale = ingest.chunk_ids(document_id, old_count)[len(chunks):]
    assert store.get_by_ids(stale) == []  # old ids that are no longer used are gone


@requires_db
def test_rechunk_leaves_other_users_alone(make_user, store):
    from scripts.rechunk_images import rechunk

    alice, bob = make_user(), make_user()
    store_old(store, alice, "a.png")
    bobs, bobs_count = store_old(store, bob, "b.png")
    before = [(c.id, c.page_content, c.metadata) for c in by_id(stored(store, bobs))]
    assert rechunk([alice]) == 1
    assert chunk_count(bobs) == bobs_count
    assert [(c.id, c.page_content, c.metadata) for c in by_id(stored(store, bobs))] == before


@requires_db
def test_rechunk_keeps_text_chunks(make_user, store):
    from scripts.rechunk_images import rechunk

    user = make_user()
    texts = ["Termination needs 30 days notice.", "Payment is due within 14 days."]
    document_id, _ = store_old(store, user, "contract.pdf", texts)
    rechunk([user])
    chunks = by_id(stored(store, document_id))
    assert [c.page_content for c in chunks[:2]] == texts
    assert [c.metadata.get("kind") for c in chunks[:2]] == [None, None]
    assert all(c.metadata["kind"] == "image" for c in chunks[2:])
