"""Images of an uploaded file, ready for the vision model: (page, JPEG bytes)."""

import logging
import re
import zipfile
from collections.abc import Callable, Iterator
from io import BytesIO
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_IMAGES_PER_FILE = 20  # vision calls per upload
MIN_IMAGE_SIDE = 100  # px; smaller ones are logos, icons and bullets
MAX_IMAGE_SIDE = 1600  # px; larger ones are shrunk before sending (fewer tokens)


def images_in(path: Path) -> list[tuple[int | None, bytes]]:
    """(1-based page or None, JPEG bytes) of each usable image, in document order, at most MAX_IMAGES_PER_FILE."""
    found = []
    for page, open_image in _candidates(path):
        if (data := _to_jpeg(open_image)) is not None:
            found.append((page, data))
            if len(found) == MAX_IMAGES_PER_FILE:
                break  # the generator stops too, so the rest of a long PDF is never decoded
    return found


def _candidates(path: Path) -> Iterator[tuple[int | None, Callable[[], Image.Image]]]:
    """(page, function that opens the image) for every image in the file, before any filtering."""
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        yield None, lambda: Image.open(path)
    elif suffix == ".pdf":
        for number, page in enumerate(PdfReader(path).pages, start=1):
            for picture in page.images:
                yield number, lambda picture=picture: picture.image  # pypdf decodes here, so it may fail
    elif suffix == ".docx":
        with zipfile.ZipFile(path) as docx:
            # Only the document's own pictures, not docProps/thumbnail.jpeg (Word's preview of the file).
            # shortcut: media numbers follow insertion order, usually but not always the reading order.
            names = sorted((n for n in docx.namelist() if n.startswith("word/media/")), key=_number)
            for name in names:
                data = docx.read(name)
                yield None, lambda data=data: Image.open(BytesIO(data))


def _number(name: str) -> int:
    """image10.png -> 10, so image2 sorts before image10."""
    match = re.search(r"\d+", name)
    return int(match.group()) if match else 0


def _to_jpeg(open_image: Callable[[], Image.Image]) -> bytes | None:
    """RGB JPEG at most MAX_IMAGE_SIDE; None when the image is too small or can't be decoded."""
    try:
        image = open_image()
        if min(image.size) < MIN_IMAGE_SIDE:
            return None
        image = image.convert("RGB")  # PDFs often hold CMYK or palette images, which JPEG can't store
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))  # keeps the aspect ratio, never enlarges
        buffer = BytesIO()
        image.save(buffer, "JPEG", quality=85)
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 - one unreadable image must not fail the whole upload
        log.warning("Skipping an image that can't be decoded", exc_info=True)
        return None
