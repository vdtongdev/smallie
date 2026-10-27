"""Knowledge graph (app/core/graph.py) against the real Postgres, with fake embeddings and models."""

import uuid
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update

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
    monkeypatch.setattr("app.agents.docs.tools.get_embeddings", lambda: fake)
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
    too_many = FactUpdate.model_validate({"facts": [{"subject": "User", "relation": "holds", "object": "x"}] * 9})
    assert len(too_many.facts) == graph.MAX_FACTS  # the first ones are kept
    assert FactUpdate.model_validate({}).facts == []


def test_every_standard_relation_is_named_in_the_prompt():
    from app.core.prompts import EXTRACT_PROMPT

    assert graph.FUNCTIONAL <= graph.STANDARD
    assert all(relation in EXTRACT_PROMPT for relation in graph.STANDARD)
    assert all(target in graph.STANDARD for target in graph.ALIASES.values())


def test_known_wordings_become_the_standard_relation():
    assert Fact(subject="User", relation="is named", object="x").relation == "has_name"
    assert Fact(subject="User", relation="works_for", object="x").relation == "works_at"


@requires_db
def test_one_fact_is_kept_once_whatever_it_is_called(make_user, embeddings):
    user = make_user()
    resume = add_document(user, "resume.pdf")
    save = lambda *facts: graph.save_facts(user, [Fact(subject=s, relation=r, object=o) for s, r, o in facts], "chat")  # noqa: E731
    save(("User", "is", "An"), ("User", "is_named", "An"))  # invented, then standard: the standard one stays
    save(("User", "has_name", "An"), ("User", "called", "An"))  # invented next to a standard one: dropped
    save(("User", "has_income_type", "unstable income"))
    save(("User", "has_financial_characteristic", "unstable income"))  # newest invented wording wins
    save(("resume.pdf", "is_cv_of", "User"), ("User", "describes_career_of", "resume.pdf"))  # one link, User -> file
    save(("User", "holds", "Acme"), ("User", "works_at", "Acme"))  # two standard relations both stay
    texts = sorted(f["text"] for f in graph.list_facts(user))
    assert texts == ["User describes career of resume.pdf", "User has financial characteristic unstable income",
                     "User has name An", "User holds Acme", "User works at Acme"]
    assert resume  # the file node was linked, not a new entity named after it
    assert node_names(user, "file") == ["resume.pdf"]


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
    assert kept == 1  # all three name one node, and one pair keeps one edge (the standard `holds`)
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

    store = PGVector(embeddings=DeterministicFakeEmbedding(size=get_settings().embed_dim),
                     connection=get_settings().sqlalchemy_url, collection_name="test_graph", pre_delete_collection=True, use_jsonb=True)
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


class CountingEmbedding(DeterministicFakeEmbedding):
    requests: list = []

    def embed_documents(self, texts):
        self.requests.append(list(texts))
        return super().embed_documents(texts)


def card_embedding(document_id: str):
    with get_engine().connect() as conn:
        return conn.execute(select(documents.c.card_embedding).where(documents.c.id == document_id)).scalar()


@requires_db
def test_save_card_stores_a_vector_of_the_card_in_one_embedding_request(make_user, monkeypatch):
    fake = CountingEmbedding(size=get_settings().embed_dim)
    fake.requests = []
    monkeypatch.setattr(graph, "get_embeddings", lambda: fake)
    user = make_user()
    with_summary, without = add_document(user, "contract.pdf"), add_document(user, "notes.docx")

    graph.save_card(user, with_summary, "Contract: supply deal", ["Acme", "Globex"])
    assert len(fake.requests) == 1 and set(fake.requests[0]) == {"Acme", "Globex", "contract.pdf: Contract: supply deal"}
    assert list(card_embedding(with_summary)) == pytest.approx(fake.embed_query("contract.pdf: Contract: supply deal"))

    graph.save_card(user, without, "", [])
    assert list(card_embedding(without)) == pytest.approx(fake.embed_query("notes.docx"))


@requires_db
def test_embed_cards_fills_missing_vectors_and_can_redo_all(make_user, monkeypatch):
    fake = CountingEmbedding(size=get_settings().embed_dim)
    fake.requests = []
    monkeypatch.setattr(graph, "get_embeddings", lambda: fake)
    user, other = make_user(), make_user()
    done, missing, others = add_document(user, "a.pdf"), add_document(user, "b.pdf"), add_document(other, "c.pdf")
    graph.save_card(user, done, "Report", [])
    with get_engine().begin() as conn:
        conn.execute(update(documents).where(documents.c.id == missing).values(summary="Notes"))
        fake.requests.clear()
        graph.embed_cards(conn, only_missing=True, user_ids=[user])
        assert [r for r in fake.requests if "b.pdf: Notes" in r] and not [r for r in fake.requests if "a.pdf: Report" in r]
    assert card_embedding(missing) is not None
    assert card_embedding(others) is None  # another user's document is untouched
    with get_engine().begin() as conn:
        fake.requests.clear()
        assert graph.embed_cards(conn, only_missing=False, user_ids=[user]) == 2
    assert any("a.pdf: Report" in r for r in fake.requests)
    assert card_embedding(others) is None


class RecordingStore:
    def __init__(self):
        self.filters = []

    def similarity_search(self, query, k, filter):
        self.filters.append(filter)
        return []


@pytest.fixture
def recorded(monkeypatch):
    from app.agents.docs import tools as docs_tools

    store = RecordingStore()
    monkeypatch.setattr(docs_tools, "get_vector_store", lambda: store)
    return store


def scoped_to(user: str, *ids: str) -> dict:
    return {"$and": [{"user_id": {"$eq": user}}, {"document_id": {"$in": list(ids)}}]}


@requires_db
def test_naming_a_file_without_its_extension_scopes_the_search_to_it(make_user, embeddings, recorded):
    from app.agents.docs.tools import search_with_graph

    user = make_user()
    wanted = add_document(user, "Portfolio summary.docx")
    add_document(user, "The Intelligent Investor.pdf")

    search_with_graph(user, "summarize my portfolio summary", entity="Portfolio summary")

    assert recorded.filters[0] == scoped_to(user, wanted)


@requires_db
def test_a_file_named_only_in_the_question_scopes_the_search_to_it(make_user, embeddings, recorded):
    from app.agents.docs.tools import search_with_graph

    user = make_user()
    wanted = add_document(user, "Portfolio summary.docx")
    add_document(user, "The Intelligent Investor.pdf")

    search_with_graph(user, "tell me about the portfolio summary doc")

    assert recorded.filters[0] == scoped_to(user, wanted)


@requires_db
def test_two_uploads_with_the_same_name_are_both_searched(make_user, embeddings):
    user = make_user()
    first, second = add_document(user, "Portfolio summary.docx"), add_document(user, "Portfolio summary.docx")

    assert sorted(graph.documents_for(user, "portfolio summary", "Portfolio summary")) == sorted([first, second])
    assert sorted(graph.documents_for(user, "what is in the portfolio summary?")) == sorted([first, second])


@requires_db
def test_a_file_name_shorter_than_the_minimum_does_not_scope_the_search(make_user, embeddings):
    user = make_user()
    add_document(user, "cv.pdf")

    assert graph.documents_for(user, "what is on my cv?") == []


class RankedStore:
    """Fake vector store: `passages` are (document_id, text) in order of closeness; it records the
    filters of by-vector searches and returns what matches them."""

    def __init__(self, passages):
        self.passages, self.filters = passages, []

    def similarity_search_by_vector(self, embedding, k, filter):
        self.filters.append(filter)
        wanted = None
        for part in filter.get("$and", []):
            if "document_id" in part:
                wanted = part["document_id"].get("$in") or [part["document_id"]["$eq"]]
        return [Document(text, metadata={"document_id": d, "filename": d})
                for d, text in self.passages if wanted is None or d in wanted][:k]


class CountingQueries(CountingEmbedding):
    queries: list = []

    def embed_query(self, text):
        self.queries.append(text)
        return super().embed_query(text)


@pytest.fixture
def card_search(monkeypatch):
    from app.agents.docs import tools as docs_tools

    fake = CountingQueries(size=get_settings().embed_dim)
    fake.requests, fake.queries = [], []
    monkeypatch.setattr(graph, "get_embeddings", lambda: fake)
    monkeypatch.setattr(docs_tools, "get_embeddings", lambda: fake)
    return fake


@requires_db
def test_a_small_file_whose_card_matches_is_not_crowded_out_by_large_ones(make_user, card_search, monkeypatch):
    from app.agents.docs import tools as docs_tools

    user = make_user()
    big_a, big_b, small = (add_document(user, "alpha.pdf"), add_document(user, "bravo.pdf"),
                           add_document(user, "q3.xlsx"))
    for document_id, summary in ((big_a, "Book: investing"), (big_b, "Book: economics"), (small, "Brokerage statement")):
        graph.save_card(user, document_id, summary, [])
    store = RankedStore([(big_a, f"alpha passage {i}") for i in range(10)]
                        + [(big_b, f"bravo passage {i}") for i in range(10)] + [(small, "cash balance")])
    monkeypatch.setattr(docs_tools, "get_vector_store", lambda: store)

    # The query is the small file's card text, so its card is the nearest (its stem is too short to scope).
    result = docs_tools.search_with_graph(user, "q3.xlsx: Brokerage statement")

    assert result.startswith("[" + small + "]\ncash balance")  # first: the nearest card's best passage
    assert "alpha passage 0" in result and "bravo passage 0" in result  # each top card file's best passage
    assert len(result.split("\n\n---\n\n")) == docs_tools.TOP_K
    assert card_search.queries == ["q3.xlsx: Brokerage statement"]  # embedded once


@requires_db
def test_without_card_vectors_the_search_is_the_plain_unscoped_one(make_user, card_search, monkeypatch):
    from app.agents.docs import tools as docs_tools

    user = make_user()
    add_document(user, "book.pdf")  # no card written
    store = RecordingStore()
    monkeypatch.setattr(docs_tools, "get_vector_store", lambda: store)

    docs_tools.search_with_graph(user, "something")

    assert store.filters == [{"user_id": {"$eq": user}}]
    assert card_search.queries == []


async def chat_history(checkpointer, conversation_id: str, questions: list[str]) -> None:
    from langchain_core.messages import AIMessage
    from langgraph.graph import START, MessagesState, StateGraph

    def reply(state: MessagesState) -> dict:
        return {"messages": [AIMessage(f"answer to {state['messages'][-1].text}", name="sql_agent")]}

    graph_ = StateGraph(MessagesState)
    graph_.add_node("reply", reply)
    graph_.add_edge(START, "reply")
    app = graph_.compile(checkpointer=checkpointer)
    for question in questions:
        await app.ainvoke({"messages": [("user", question)]}, {"configurable": {"thread_id": conversation_id}})


@requires_db
async def test_build_graph_mines_every_past_turn_once_per_user(make_user, monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    from app.core.db import conversations
    from app.core.memory import memory_namespace
    from scripts import build_graph

    calls = []

    async def extract(user_id, text, callbacks, source, conversation_id=None):
        calls.append((user_id, text, conversation_id))
        return 1

    monkeypatch.setattr(build_graph, "extract_from_turns", extract)
    store, checkpointer = InMemoryStore(), InMemorySaver()
    user, resumed = make_user(), make_user()
    chat, empty, other = (str(uuid.uuid4()) for _ in range(3))
    with get_engine().begin() as conn:
        conn.execute(conversations.insert().values(
            [{"id": chat, "user_id": user, "kind": "auto", "title": "a", "mined_turns": 0},
             {"id": empty, "user_id": user, "kind": "auto", "title": "no checkpoint", "mined_turns": 0},
             {"id": other, "user_id": resumed, "kind": "auto", "title": "a", "mined_turns": 4}]))
    questions = [f"q{i}" for i in range(1, 6)]
    await chat_history(checkpointer, chat, questions)
    await chat_history(checkpointer, other, questions)
    await store.aput(memory_namespace(user), "m1", {"text": "User likes tea"})

    assert {user, resumed} <= set(build_graph.users_to_build())
    assert await build_graph.build_user(store, checkpointer, user) == 3
    memory_call, first_batch, second_batch = calls
    assert "User likes tea" in memory_call[1] and memory_call[2] is None
    assert first_batch[2] == second_batch[2] == chat  # every turn, sql answers too
    assert all(f"User: q{i}" in first_batch[1] for i in range(1, 5)) and "answer to q4" in first_batch[1]
    assert "User: q5" in second_batch[1] and "User: q1" not in second_batch[1]
    assert await store.asearch(memory_namespace(user)) == []

    calls.clear()
    assert await build_graph.build_user(store, checkpointer, resumed) == 1  # resumes after turn 4
    assert len(calls) == 1 and "User: q5" in calls[0][1] and "User: q4" not in calls[0][1]

    remaining = set(build_graph.users_to_build())
    assert user not in remaining and resumed not in remaining  # never built twice
    with get_engine().connect() as conn:
        assert conn.execute(select(conversations.c.mined_turns).where(conversations.c.id == chat)).scalar() == 5


@requires_db
def test_new_accounts_never_need_building(make_user):
    from app.core import accounts
    from scripts.build_graph import users_to_build

    user_id = accounts.create_account("Test", f"test-graph-{uuid.uuid4().hex[:8]}", "a long enough password")
    try:
        assert user_id not in users_to_build()
    finally:
        accounts.delete_user_row(user_id)


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
