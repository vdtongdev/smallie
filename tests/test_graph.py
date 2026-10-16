"""Knowledge graph (app/core/graph.py) against the real Postgres, with fake embeddings and models."""

import uuid
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding
from pydantic import ValidationError
from sqlalchemy import delete, func, select

from app.core import graph
from app.core.config import get_settings
from app.core.db import documents, get_engine, graph_edges, graph_nodes, init_db, users
from app.core.graph import Fact, FactUpdate
from tests.conftest import requires_db


class AliasEmbedding(DeterministicFakeEmbedding):
    """Fake embeddings where every name in `aliases` gets the vector of its canonical name."""

    aliases: dict[str, str] = {}

    def embed_documents(self, texts):
        return super().embed_documents([self.aliases.get(t, t) for t in texts])

    def embed_query(self, text):
        return super().embed_query(self.aliases.get(text, text))


@pytest.fixture
def embeddings(monkeypatch):
    fake = AliasEmbedding(size=get_settings().embed_dim)
    monkeypatch.setattr(graph, "get_embeddings", lambda: fake)
    return fake


@pytest.fixture
def make_user():
    init_db()
    created = []

    def make() -> str:
        user_id = f"test-graph-{uuid.uuid4()}"
        with get_engine().begin() as conn:
            conn.execute(users.insert().values(id=user_id, display_name="Test"))
        created.append(user_id)
        return user_id

    yield make
    with get_engine().begin() as conn:  # cascades to documents and the graph
        conn.execute(delete(users).where(users.c.id.in_(created)))


def add_document(user_id: str, filename: str) -> str:
    document_id = str(uuid.uuid4())
    with get_engine().begin() as conn:
        conn.execute(documents.insert().values(id=document_id, user_id=user_id, filename=filename,
                                               path=f"/tmp/{document_id}", status="ready", chunk_count=1))
    return document_id


def count(table, user_id: str, *where) -> int:
    with get_engine().connect() as conn:
        return conn.execute(select(func.count()).select_from(table).where(table.c.user_id == user_id, *where)).scalar()


def node_names(user_id: str, kind: str = "entity") -> list[str]:
    with get_engine().connect() as conn:
        return sorted(conn.execute(select(graph_nodes.c.name).where(
            graph_nodes.c.user_id == user_id, graph_nodes.c.kind == kind)).scalars())


def test_fact_schema_normalizes_relations_and_rejects_bad_ones():
    assert Fact(subject="User", relation="Works at", object="x").relation == "works_at"
    with pytest.raises(ValidationError):
        Fact(subject="User", relation="123", object="x")
    with pytest.raises(ValidationError):
        FactUpdate.model_validate({"facts": [{"subject": "User", "relation": "holds", "object": "x"}] * 6})
    assert FactUpdate.model_validate({}).facts == []


def test_every_single_valued_relation_is_named_in_the_prompt():
    from app.core.prompts import EXTRACT_PROMPT

    assert all(relation in EXTRACT_PROMPT for relation in graph.FUNCTIONAL)


@requires_db
def test_facts_are_scoped_per_user(make_user, embeddings):
    alice, bob, carol = make_user(), make_user(), make_user()
    graph.save_facts(alice, [Fact(subject="User", relation="works_at", object="Acme")], "chat")
    graph.save_facts(bob, [Fact(subject="I", relation="lives_in", object="Hanoi")], "chat")

    assert graph.recall_facts(alice) == "- User works at Acme"
    assert graph.recall_facts(bob) == "- User lives in Hanoi"
    assert graph.recall_facts(carol) == ""
    assert "Acme" not in graph.search_facts(bob, "Acme")
    # Another user's fact id deletes nothing.
    alice_edge = graph.list_facts(alice)[0]["id"]
    assert not graph.delete_fact(bob, alice_edge)
    assert graph.delete_fact(alice, alice_edge)
    assert graph.recall_facts(alice) == "" and node_names(alice) == []


@requires_db
def test_names_merge_and_general_knowledge_is_dropped(make_user, embeddings):
    user = make_user()
    embeddings.aliases = {"ACME Corporation": "Acme Corp"}
    kept = graph.save_facts(user, [
        Fact(subject="User", relation="holds", object="Acme Corp"),
        Fact(subject="User", relation="follows", object=" acme corp "),  # same normalized name
        Fact(subject="User", relation="admires", object="ACME Corporation"),  # same embedding
        Fact(subject="Acme Corp", relation="located_in", object="Singapore"),  # touches neither user nor a file
    ], "chat")
    assert kept == 3
    assert node_names(user) == ["Acme Corp"]


@requires_db
def test_single_valued_relations_replace_the_old_value(make_user, embeddings):
    user = make_user()
    graph.save_facts(user, [Fact(subject="User", relation="lives_in", object="Hanoi")], "chat")
    graph.save_facts(user, [Fact(subject="User", relation="lives_in", object="Da Nang")], "chat")
    graph.save_facts(user, [Fact(subject="User", relation="holds", object="AAA"),
                            Fact(subject="User", relation="holds", object="BBB")], "chat")
    assert node_names(user) == ["AAA", "BBB", "Da Nang"]  # Hanoi's node went with its edge
    assert "lives in Da Nang" in graph.recall_facts(user)


@requires_db
async def test_extract_facts_saves_what_the_model_returns(make_user, embeddings, monkeypatch):
    user = make_user()
    add_document(user, "contract.pdf")
    seen = []

    async def ainvoke(messages, config):
        seen.append(messages[1].content)
        return FactUpdate(facts=[Fact(subject="User", relation="risk_tolerance", object="balanced"),
                                 Fact(subject="Acme", relation="party_in", object="contract.pdf")])

    extractor = SimpleNamespace(ainvoke=ainvoke)
    monkeypatch.setattr(graph, "get_chat_model", lambda model=None: SimpleNamespace(
        with_structured_output=lambda schema, method: extractor))
    assert await graph.extract_facts(user, "q1: B", "You are balanced.", [], conversation_id="c1") == 2
    assert seen == ["The user's files: contract.pdf\n\nUser: q1: B\n\nAssistant: You are balanced."]
    facts = {f["text"] for f in graph.list_facts(user)}
    assert facts == {"User risk tolerance balanced", "Acme party in contract.pdf"}


@requires_db
def test_deleting_files_chats_and_accounts(make_user, embeddings):
    user = make_user()
    contract = add_document(user, "contract.pdf")
    graph.save_card(user, contract, "Contract: supply deal", ["Acme", "Globex"])
    graph.save_facts(user, [Fact(subject="User", relation="works_at", object="Acme")], "chat", conversation_id="c1")
    assert node_names(user) == ["Acme", "Globex"]
    assert [f["text"] for f in graph.list_facts(user)] == ["User works at Acme"]  # cards aren't memories

    with get_engine().begin() as conn:
        conn.execute(delete(documents).where(documents.c.id == contract))
    graph.drop_orphans(user)
    assert node_names(user) == ["Acme"]  # still used by the chat fact
    assert node_names(user, "file") == []
    assert graph.recall_facts(user) == "- User works at Acme"  # deleting chat c1 would not touch it

    with get_engine().begin() as conn:
        conn.execute(delete(users).where(users.c.id == user))
    assert count(graph_nodes, user) == count(graph_edges, user) == 0


@pytest.fixture
def chunks(monkeypatch):
    from langchain_postgres import PGVector

    from app.agents.docs import tools as docs_tools

    store = PGVector(embeddings=DeterministicFakeEmbedding(size=32), connection=get_settings().sqlalchemy_url,
                     collection_name="test_graph", pre_delete_collection=True, use_jsonb=True)
    monkeypatch.setattr(docs_tools, "get_vector_store", lambda: store)
    yield store
    store.delete_collection()


@requires_db
def test_search_follows_the_graph_to_the_linked_file(make_user, embeddings, chunks):
    from app.agents.docs.tools import NO_PASSAGES, search_with_graph

    alice, bob = make_user(), make_user()
    contract, resume = add_document(alice, "contract.pdf"), add_document(alice, "resume.pdf")
    chunks.add_documents([
        Document("Termination needs 30 days notice.", metadata={"user_id": alice, "document_id": contract,
                                                               "filename": "contract.pdf", "page": 2}),
        Document("Termination of my last job in 2024.", metadata={"user_id": alice, "document_id": resume,
                                                                 "filename": "resume.pdf", "page": 1}),
    ])
    graph.save_card(alice, contract, "Contract: supply deal", ["Acme"])
    # The cross question: the user's employer, then the files that mention it.
    graph.save_facts(alice, [Fact(subject="User", relation="works_at", object="Acme")], "chat")
    assert "User works at Acme" in graph.recall_facts(alice)

    scoped = search_with_graph(alice, "termination clause", entity="Acme")
    assert "contract.pdf" in scoped and "resume.pdf" not in scoped
    by_name = search_with_graph(alice, "termination terms in the Acme deal")  # entity left empty
    assert "contract.pdf" in by_name and "resume.pdf" not in by_name
    by_file = search_with_graph(alice, "termination", entity="resume.pdf")
    assert "resume.pdf" in by_file and "contract.pdf" not in by_file
    unknown = search_with_graph(alice, "termination", entity="Initech")  # falls back to every file
    assert "contract.pdf" in unknown and "resume.pdf" in unknown
    assert search_with_graph(bob, "termination", entity="Acme") == NO_PASSAGES  # never alice's files


@requires_db
async def test_document_card_is_best_effort(make_user, embeddings, monkeypatch):
    from langgraph.store.memory import InMemoryStore

    from app.features.documents import card as card_module

    user = make_user()
    good, broken, empty = add_document(user, "a.pdf"), add_document(user, "b.pdf"), add_document(user, "c.pdf")
    replies = {"a.pdf": card_module.DocumentCard(doc_type="Contract", summary="A supply deal.", entities=["Acme"]),
               "b.pdf": RuntimeError("model down")}
    calls = []

    async def ainvoke(messages, config):
        filename = messages[1].content.split("\n", 1)[0].removeprefix("File name: ")
        calls.append(filename)
        if isinstance(reply := replies[filename], Exception):
            raise reply
        return reply

    monkeypatch.setattr(card_module, "get_chat_model", lambda model=None: SimpleNamespace(
        with_structured_output=lambda schema, method: SimpleNamespace(ainvoke=ainvoke)))
    monkeypatch.setattr(card_module, "ingest_document", lambda document_id, user_id, path, filename:
                        [] if filename == "c.pdf" else [Document("Opening text.")])
    for document_id, filename in ((good, "a.pdf"), (broken, "b.pdf"), (empty, "c.pdf")):
        await card_module.process_document(InMemoryStore(), document_id, user, None, filename)

    assert calls == ["a.pdf", "b.pdf"]  # failed ingestion makes no card call
    with get_engine().connect() as conn:
        summaries = dict(conn.execute(select(documents.c.filename, documents.c.summary)
                                      .where(documents.c.user_id == user)).all())
    assert summaries == {"a.pdf": "Contract: A supply deal.", "b.pdf": None, "c.pdf": None}
    assert node_names(user) == ["Acme"]
