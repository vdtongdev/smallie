"""The agent servers connect as agent_reader (db/init/06_agent_reader.sql): they can run every
search their tools need, but can't change or read anything else."""

from types import SimpleNamespace

import psycopg
import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from langgraph.store.postgres import PostgresStore
from psycopg.rows import dict_row
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.agents.docs import tools as docs_tools
from app.core import db, graph, memory
from app.core.config import get_settings
from app.core.db import pool_args
from app.core.memory import AgentContext
from app.features.documents import ingest
from tests.conftest import requires_db

pytestmark = requires_db


def reader_url():
    return make_url(get_settings().sqlalchemy_url).set(username="agent_reader", password="reader")


@pytest.fixture
def reader(monkeypatch):
    """Every search pointed at agent_reader, with fake embeddings (no model calls)."""
    fake = DeterministicFakeEmbedding(size=get_settings().embed_dim)
    engine = create_engine(reader_url(), **pool_args())
    monkeypatch.setattr(graph, "get_engine", lambda: engine)
    monkeypatch.setattr(graph, "get_embeddings", lambda: fake)
    monkeypatch.setattr(memory, "get_embeddings", lambda: fake)
    monkeypatch.setattr(get_settings(), "database_url", reader_url().set(drivername="postgresql")
                        .render_as_string(hide_password=False))
    monkeypatch.setattr(ingest, "get_embeddings", lambda: fake)
    store = ingest.get_vector_store.__wrapped__()  # uncached: its init must work read-only
    monkeypatch.setattr(docs_tools, "get_vector_store", lambda: store)
    yield engine
    engine.dispose()


async def test_reader_can_search_documents_facts_and_history(reader, monkeypatch):
    docs_tools.search_with_graph("team", "revenue", entity="Acme")
    graph.search_facts("team", "where do I work")
    # The sync store runs the same queries as open_history_store's async one, which can't run on
    # pytest's Windows event loop.
    with psycopg.connect(get_settings().database_url, autocommit=True, row_factory=dict_row) as conn:
        monkeypatch.setattr(memory, "_history_store", PostgresStore(conn, index=memory.store_index_config()))
        runtime = SimpleNamespace(context=AgentContext(user_id="team"), store=None)
        await memory.search_chat_history.coroutine("stocks", runtime)


def test_app_start_grants_the_reader_its_tables():
    """On a fresh database the tables appear at the app's first start, after the init scripts:
    the app grants them (grant_reader in the lifespan). Not the `reader` fixture: get_engine is
    cached and must stay the owner's."""
    grant = db.grant_reader
    with db.get_engine().begin() as conn:
        conn.execute(text("REVOKE SELECT ON graph_nodes FROM agent_reader"))
    grant()
    engine = create_engine(reader_url(), **pool_args())
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM graph_nodes LIMIT 1"))
    finally:
        engine.dispose()


@pytest.mark.parametrize("table", db.READER_TABLES)
def test_reader_cannot_write_even_in_a_read_write_transaction(reader, table):
    with reader.connect() as conn:
        conn.execute(text("SET TRANSACTION READ WRITE"))  # the role's read-only default is not the guard
        with pytest.raises(DBAPIError, match="permission denied"):
            conn.execute(text(f"DELETE FROM {table} WHERE false"))


def test_agent_server_account_reaches_only_the_agent_databases():
    """The containers' own login (DATABASE_URI, 05_agent_databases.sql) must not open the app's database."""
    url = make_url(get_settings().sqlalchemy_url).set(drivername="postgresql", username="agent_server",
                                                       password="server")
    with pytest.raises(psycopg.OperationalError, match="permission denied"):
        psycopg.connect(url.render_as_string(hide_password=False), connect_timeout=5)
    psycopg.connect(url.set(database="agent_sql").render_as_string(hide_password=False), connect_timeout=5).close()


@pytest.mark.parametrize("table", ["users", "checkpoints", "conversations"])
def test_reader_cannot_read_accounts_or_chats(reader, table):
    with reader.connect() as conn, pytest.raises(DBAPIError, match="permission denied"):
        conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))


def test_reader_can_read_model_settings_but_not_write(reader):
    db.init_db()
    db.grant_reader()
    with reader.connect() as conn:
        conn.execute(text("SELECT 1 FROM model_settings LIMIT 1"))
    with reader.connect() as conn:
        conn.execute(text("SET TRANSACTION READ WRITE"))
        with pytest.raises(DBAPIError, match="permission denied"):
            conn.execute(text("INSERT INTO model_settings (user_id, base_url, model, api_key) "
                              "VALUES ('x', 'u', 'm', 'k')"))
