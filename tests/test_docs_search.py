"""A search hit on an image returns the image's whole reading (app/agents/docs/tools.py)."""

import uuid

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding
from sqlalchemy import delete

from app.agents.docs import tools
from app.core.config import get_settings
from app.core.db import documents, get_engine
from app.features.documents import ingest
from tests.conftest import requires_db
from tests.test_graph import make_user  # noqa: F401 - fixture

pytestmark = requires_db

VAGUE = "detail of the image"
NEAR = "screenshot of a settings window"


class QueryNear(DeterministicFakeEmbedding):
    """The vague query gets the vector of the description text; everything else is hash-distinct."""

    def _fix(self, text):
        return NEAR if text == VAGUE or NEAR in text else text

    def embed_documents(self, texts):
        return super().embed_documents([self._fix(t) for t in texts])

    def embed_query(self, text):
        return super().embed_query(self._fix(text))


@pytest.fixture
def embeddings(monkeypatch):
    fake = QueryNear(size=get_settings().embed_dim)
    monkeypatch.setattr(tools, "get_embeddings", lambda: fake)
    monkeypatch.setattr(ingest, "get_embeddings", lambda: fake)
    store = ingest.get_vector_store.__wrapped__()  # uncached, so it holds the fake embeddings
    monkeypatch.setattr(tools, "get_vector_store", lambda: store)
    monkeypatch.setattr(ingest, "get_vector_store", lambda: store)
    return store


@pytest.fixture
def stored(embeddings):
    ids = []
    yield ids
    if ids:
        embeddings.delete(ids=ids)
    with get_engine().begin() as conn:
        conn.execute(delete(documents).where(documents.c.id.in_({i.split(":")[0] for i in ids})))


def add_document(user_id: str, filename: str) -> str:
    document_id = str(uuid.uuid4())
    with get_engine().begin() as conn:
        conn.execute(documents.insert().values(id=document_id, user_id=user_id, filename=filename,
                                               path=f"/tmp/{document_id}", status="ready", chunk_count=1))
    return document_id


def index(stored, user_id, document_id, texts, parts=True, image=1, kind="image", start=0):
    chunks = []
    for n, text in enumerate(texts):
        meta = {"user_id": user_id, "document_id": document_id, "filename": "shot.png", "kind": kind}
        if kind == "image":
            meta["image"] = image
            if parts:
                meta["part"] = n
        chunks.append(Document(text, metadata=meta))
    ids = [f"{document_id}:{start + n}" for n in range(len(chunks))]
    ingest.get_vector_store().add_documents(chunks, ids=ids)
    stored.extend(ids)


def test_vague_query_returns_the_values_of_the_image(make_user, embeddings, stored):
    user = make_user()
    document_id = add_document(user, "shot.png")
    index(stored, user, document_id, [f"[Image 1]\n{NEAR}", "[Image 1]\nSerial Number: 0000-TEST"])
    other = add_document(user, "notes.txt")  # fillers outrank part 1 (hash-distinct vectors), so it misses the top hits
    index(stored, user, other, [f"filler passage {n}" for n in range(30)], kind="text")

    found = tools.search_with_graph(user, VAGUE)

    assert "Serial Number: 0000-TEST" in found
    assert found.count("[Image 1]") == 1


def test_whole_image_is_capped_at_a_part_boundary(make_user, embeddings, stored):
    user = make_user()
    document_id = add_document(user, "shot.png")
    parts = [f"[Image 1]\n{NEAR if n == 0 else ''}line {n:02d} " + "x" * 760 + f" end{n:02d}" for n in range(12)]
    parts[0] = f"[Image 1]\n{NEAR}"
    parts = [p if n == 0 else p + "" for n, p in enumerate(parts)]
    index(stored, user, document_id, parts)

    found = tools.search_with_graph(user, VAGUE)

    assert len(found) <= tools.MAX_IMAGE_CHARS + 800 + 200  # cap + one part + the source header
    assert found.rstrip().endswith(tuple(f"end{n:02d}" for n in range(1, 12)))
    assert "end11" not in found  # cut, not everything
    assert found.count("[Image 1]") == 1


def test_old_chunks_without_part_keep_chunk_order(make_user, embeddings, stored):
    user = make_user()
    document_id = add_document(user, "shot.png")
    # stored out of order on purpose: ids doc:1 and doc:2, doc:2 nearest the query
    ids = [f"{document_id}:2", f"{document_id}:1"]
    chunks = [Document(f"[Image 1]\n{NEAR}", metadata={"user_id": user, "document_id": document_id,
                                                       "filename": "shot.png", "kind": "image", "image": 1}),
              Document("[Image 1]\nsecond half", metadata={"user_id": user, "document_id": document_id,
                                                           "filename": "shot.png", "kind": "image", "image": 1})]
    ingest.get_vector_store().add_documents(chunks, ids=ids)
    stored.extend(ids)

    found = tools.search_with_graph(user, VAGUE)

    assert NEAR in found and "second half" in found
    assert found.index("second half") < found.index(NEAR)  # id 1 before id 2


def test_other_users_images_never_join(make_user, embeddings, stored):
    user, other = make_user(), make_user()
    document_id = add_document(user, "shot.png")
    index(stored, user, document_id, [f"[Image 1]\n{NEAR}"])
    # the other user's chunk carries the same document_id value
    index(stored, other, document_id, ["[Image 1]\nforeign secret"], start=1)

    found = tools.search_with_graph(user, VAGUE)

    assert NEAR in found
    assert "foreign secret" not in found
    assert tools.search_with_graph(other, VAGUE) != tools.NO_PASSAGES  # the other user's own row is untouched


def _scoped_setup(stored, user):
    """Two files named IMG_0001.jpg of one user, each with its own passage."""
    mine, other = add_document(user, "IMG_0001.jpg"), add_document(user, "IMG_0001.jpg")
    index(stored, user, mine, ["[Image 1]\nSerial Number: AAAA-1"], kind="text")
    index(stored, user, other, ["[Image 1]\nSerial Number: BBBB-2"], kind="text")
    return mine, other


def test_attached_ids_scope_the_search(make_user, embeddings, stored):
    user = make_user()
    mine, _ = _scoped_setup(stored, user)

    found = tools.search_with_graph(user, "serial number IMG_0001.jpg", attached=(mine,))

    assert "AAAA-1" in found and "BBBB-2" not in found


def test_short_name_attachment_scopes(make_user, embeddings, stored):
    user = make_user()
    mine, other = add_document(user, "a.png"), add_document(user, "b.png")
    index(stored, user, mine, ["[Image 1]\nSerial Number: AAAA-1"], kind="text")
    index(stored, user, other, ["[Image 1]\nSerial Number: BBBB-2"], kind="text")

    found = tools.search_with_graph(user, "serial number", attached=(mine,))

    assert "AAAA-1" in found and "BBBB-2" not in found


def test_attached_falls_back_when_nothing_found(make_user, embeddings, stored):
    user = make_user()
    mine = add_document(user, "empty.png")  # attached, but nothing indexed for it
    other = add_document(user, "notes.txt")
    index(stored, user, other, ["Leave policy: 25 days"], kind="text")

    found = tools.search_with_graph(user, "leave policy", attached=(mine,))

    assert "Leave policy: 25 days" in found


def test_attached_ids_filtered_by_user(make_user, embeddings, stored):
    user, stranger = make_user(), make_user()
    theirs = add_document(stranger, "secret.png")
    index(stored, stranger, theirs, ["[Image 1]\nSerial Number: SECRET-9"], kind="text")

    found = tools.search_with_graph(user, "serial number", attached=(theirs,))

    assert "SECRET-9" not in found
