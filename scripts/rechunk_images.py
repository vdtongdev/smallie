"""Rebuild stored image chunks: every chunk labelled, no overlap, `part` in metadata.

Readings are rebuilt from the old chunks (no model calls); only embeddings are spent.

Usage:  uv run python -m scripts.rechunk_images
"""

from sqlalchemy import select, update

from app.core.db import documents, get_engine
from app.features.documents.ingest import IMAGE_LABEL, chunk_ids, get_vector_store, image_chunks

MAX_OVERLAP = 120  # the old splitter's chunk_overlap


def join_overlapping(pieces: list[str]) -> str:
    """Join consecutive chunks, dropping the longest suffix of one that starts the next."""
    text = pieces[0]
    for piece in pieces[1:]:
        size = next((k for k in range(min(MAX_OVERLAP, len(text), len(piece)), 0, -1) if text.endswith(piece[:k])), 0)
        text += piece[size:] if size else "\n" + piece
    return text


def rechunk_file(document_id: str, filename: str, count: int, store) -> bool:
    old = sorted(store.get_by_ids(chunk_ids(document_id, count)), key=lambda c: int(c.id.rsplit(":", 1)[1]))
    texts = [c for c in old if c.metadata.get("kind") != "image"]
    images = [c for c in old if c.metadata.get("kind") == "image"]
    if not images:
        return False
    numbers = sorted({c.metadata["image"] for c in images})
    readings, pages = [None] * max(numbers), [None] * max(numbers)
    for number in numbers:
        group = [c for c in images if c.metadata["image"] == number]
        pieces = [c.page_content for c in group]
        label = IMAGE_LABEL(number, group[0].metadata.get("page"))
        if pieces[0].startswith(label):
            pieces[0] = pieces[0][len(label):].lstrip("\n")
        readings[number - 1] = join_overlapping(pieces)
        pages[number - 1] = group[0].metadata.get("page")
    new = texts + image_chunks(readings, pages, filename)
    for chunk in new:
        chunk.metadata.update(user_id=old[0].metadata["user_id"], document_id=document_id)
    store.add_documents(new, ids=chunk_ids(document_id, len(new)))
    if len(new) < count:
        store.delete(ids=chunk_ids(document_id, count)[len(new):])
    with get_engine().begin() as conn:
        conn.execute(update(documents).where(documents.c.id == document_id).values(chunk_count=len(new)))
    return True


def rechunk(user_ids: list[str] | None = None) -> int:
    """Rebuild the image chunks of every ready file (of the given users, or all). Returns files rebuilt."""
    query = select(documents.c.id, documents.c.filename, documents.c.chunk_count).where(
        documents.c.status == "ready", documents.c.chunk_count > 0
    )
    if user_ids is not None:
        query = query.where(documents.c.user_id.in_(user_ids))
    with get_engine().connect() as conn:
        rows = conn.execute(query).all()
    store = get_vector_store()
    return sum(rechunk_file(row.id, row.filename, row.chunk_count, store) for row in rows)


def main() -> None:
    print(f"Rebuilt the image chunks of {rechunk()} files.")


if __name__ == "__main__":
    main()
