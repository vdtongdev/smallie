"""App tables. Every row is owned by a user_id so the app is multi-user ready."""

from functools import lru_cache

from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
    text,
)

from app.config import get_settings

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("display_name", Text),
    Column("username", Text, unique=True),  # login name; NULL for legacy users
    Column("password_hash", Text),  # NULL for the admin, whose password is APP_PASSWORD
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

conversations = Table(
    "conversations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False),
    Column("kind", String(16), nullable=False),  # mode it was started with: "auto" or an agent route
    Column("title", Text, nullable=False),
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
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().sqlalchemy_url, pool_pre_ping=True)


@lru_cache
def get_readonly_engine() -> Engine:
    # The agent_readonly role is already read-only with a 10s timeout; these
    # connection options repeat that in case the URL points at another role.
    return create_engine(
        get_settings().readonly_sqlalchemy_url,
        pool_pre_ping=True,
        connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=10000"},
    )


def init_db() -> None:
    with get_engine().begin() as conn:
        metadata.create_all(conn)
        # create_all doesn't alter existing tables; these columns came with accounts.
        conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS username text UNIQUE, "
            "ADD COLUMN IF NOT EXISTS password_hash text"
        ))
