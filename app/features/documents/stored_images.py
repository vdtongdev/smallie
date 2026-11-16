"""The kept copy of an image file: its JPEG of at most 1600 px and a 256 px thumbnail, per user."""

import io

from PIL import Image
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.db import document_images, get_engine

THUMB_SIDE = 256


def make_thumb(jpeg: bytes) -> bytes:
    """JPEG whose longest side is at most THUMB_SIDE."""
    image = Image.open(io.BytesIO(jpeg))
    image.thumbnail((THUMB_SIDE, THUMB_SIDE))
    out = io.BytesIO()
    image.convert("RGB").save(out, "JPEG", quality=80)
    return out.getvalue()


def save_image(document_id: str, user_id: str, jpeg: bytes) -> None:
    values = {"document_id": document_id, "user_id": user_id, "image": jpeg, "thumb": make_thumb(jpeg)}
    with get_engine().begin() as conn:
        conn.execute(insert(document_images).values(**values).on_conflict_do_update(
            index_elements=["document_id"], set_={"image": values["image"], "thumb": values["thumb"]}))


def load_image(user_id: str, document_id: str, size: str) -> bytes | None:
    column = document_images.c.thumb if size == "thumb" else document_images.c.image
    with get_engine().connect() as conn:
        return conn.execute(select(column).where(
            document_images.c.document_id == document_id, document_images.c.user_id == user_id)).scalar()
