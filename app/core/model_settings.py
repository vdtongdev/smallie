"""Each user's own model server: endpoint, model and API key. The key is stored Fernet-encrypted
with MODEL_KEY_SECRET."""

import logging
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.core.db import get_engine, model_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelSettings:
    base_url: str
    model: str
    api_key: str


def _fernet() -> Fernet:
    return Fernet(get_settings().model_key_secret.encode())


def require_secret() -> None:
    if not get_settings().model_key_secret:
        raise RuntimeError("MODEL_KEY_SECRET must be set (see .env.example).")


def _stored(user_id: str):
    with get_engine().connect() as conn:
        return conn.execute(select(model_settings).where(model_settings.c.user_id == user_id)).first()


def load_settings(user_id: str) -> ModelSettings | None:
    row = _stored(user_id)
    if row is None:
        return None
    try:
        key = _fernet().decrypt(row.api_key.encode()).decode()
    except InvalidToken:
        log.warning("Model API key of user %s does not decrypt (MODEL_KEY_SECRET changed?)", user_id)
        return None
    return ModelSettings(row.base_url, row.model, key)


def save_settings(user_id: str, base_url: str, model: str, api_key: str | None) -> None:
    """Upsert. `api_key=None` keeps the stored key."""
    values = {"base_url": base_url, "model": model, "updated_at": func.now()}
    if api_key is None:  # keep the stored key: update the row, it must exist
        if _stored(user_id) is None:
            raise ValueError("API key required")
        stmt = update(model_settings).where(model_settings.c.user_id == user_id).values(**values)
    else:
        values["api_key"] = _fernet().encrypt(api_key.encode()).decode()
        stmt = insert(model_settings).values(user_id=user_id, **values)
        stmt = stmt.on_conflict_do_update(index_elements=["user_id"], set_=values)
    with get_engine().begin() as conn:
        conn.execute(stmt)


def delete_settings(user_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(model_settings).where(model_settings.c.user_id == user_id))


def key_hint(api_key: str) -> str:
    return ("sk-…" if api_key.startswith("sk-") else "…") + api_key[-4:]
