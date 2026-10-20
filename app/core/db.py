"""App tables. Every row is owned by a user_id so the app is multi-user ready."""

from functools import lru_cache

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    text,
)

from app.core.config import get_settings

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("display_name", Text),
    Column("username", Text, unique=True),  # login name; NULL for legacy users
    Column("password_hash", Text),  # NULL for the admin, whose password is APP_PASSWORD
    Column("graph_built_at", DateTime(timezone=True)),  # set once scripts.build_graph filled the user's graph
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

conversations = Table(
    "conversations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("kind", String(16), nullable=False),  # mode it was started with: "auto" or an agent route
    Column("title", Text, nullable=False),
    Column("mined_turns", Integer, nullable=False, server_default="0"),  # turns scripts.build_graph read so far
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

documents = Table(
    "documents",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("filename", Text, nullable=False),
    Column("path", Text, nullable=False),
    Column("status", String(16), nullable=False),  # "processing" | "ready" | "failed"
    Column("error", Text),
    Column("chunk_count", Integer, nullable=False, server_default="0"),
    Column("summary", Text),  # "<doc type>: <one line>" from the document card (app/features/documents/card.py)
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

# Knowledge graph, one per user (app/core/graph.py): a vertex table and an edge table, so a
# PostgreSQL 20 `CREATE PROPERTY GRAPH` can later sit on top of them without moving data.
graph_nodes = Table(
    "graph_nodes",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("kind", String(8), nullable=False),  # "user" | "file" | "entity"
    Column("name", Text, nullable=False),
    Column("norm", Text, nullable=False),  # graph.normalize(name), for exact matches
    Column("document_id", String(36), ForeignKey("documents.id", ondelete="CASCADE"), unique=True),  # file nodes
    # shortcut: no vector index, a query only scans one user's nodes; add HNSW past ~10k nodes per user.
    Column("embedding", Vector(get_settings().embed_dim)),  # entity nodes
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Index("graph_nodes_name", "user_id", "kind", "norm", unique=True, postgresql_where=text("document_id IS NULL")),
)

graph_edges = Table(
    "graph_edges",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("source_id", String(36), ForeignKey("graph_nodes.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("target_id", String(36), ForeignKey("graph_nodes.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("relation", String(40), nullable=False),
    Column("source", String(8), nullable=False),  # "chat" | "document" | "import"
    Column("document_id", String(36), ForeignKey("documents.id", ondelete="CASCADE")),  # edges from a document card
    Column("conversation_id", String(36)),  # no FK: deleting a chat keeps what was learned in it
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    UniqueConstraint("user_id", "source_id", "relation", "target_id"),
)


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().sqlalchemy_url, **pool_args())


@lru_cache
def get_readonly_engine() -> Engine:
    # The agent_readonly role is already read-only with a 10s timeout; the connection
    # options repeat that in case the URL points at another role.
    return readonly_engine(get_settings().readonly_sqlalchemy_url)


def readonly_engine(sqlalchemy_url: str) -> Engine:
    """An engine whose transactions are read-only and time out after 10s."""
    return create_engine(
        sqlalchemy_url,
        **pool_args(),
        connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=10000"},
    )


def pool_args() -> dict:
    """Engine pool settings: survive a Postgres restart (AP-002), and stay small so several
    processes (the app plus the agent servers) fit under Neon's connection cap."""
    return {"pool_pre_ping": True, "pool_size": get_settings().db_pool_size, "max_overflow": 0}


def init_db() -> None:
    with get_engine().begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))  # graph_nodes.embedding needs it
        metadata.create_all(conn)
        # create_all doesn't alter existing tables; these columns came with accounts.
        conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS username text UNIQUE, "
            "ADD COLUMN IF NOT EXISTS password_hash text"
        ))
        # These came with the knowledge graph.
        conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary text"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS graph_built_at timestamptz"))
        conn.execute(text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS mined_turns integer NOT NULL DEFAULT 0"))
