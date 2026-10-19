"""Images of an uploaded file: found and shrunk (`images_in`), read once by the vision model
(`read_images`), and turned into searchable chunks (`image_chunks`)."""

import base64
import logging
import re
import zipfile
from collections.abc import Callable, Iterator
from io import BytesIO
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from PIL import Image
from pydantic import BaseModel, Field, field_validator
from pypdf import PdfReader

from app.core.config import get_settings
from app.core.llm import get_chat_model
from app.features.documents.prompts import IMAGE_PROMPT

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_IMAGES_PER_FILE = 20  # vision calls per upload
MIN_IMAGE_SIDE = 100  # px; smaller ones are logos, icons and bullets
MAX_IMAGE_SIDE = 1600  # px; larger ones are shrunk before sending (fewer tokens)
VISION_CONCURRENCY = 4  # vision calls in flight at once for one file


class ImageReading(BaseModel):
    """What the vision model saw: the searchable text plus the document card fields."""

    description: str
    text: str = ""
    doc_type: str = Field(max_length=40)
    summary: str = Field(max_length=200)
    entities: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("doc_type", "summary", mode="before")
    @classmethod
    def trim(cls, value, info):
        # A few characters too many shouldn't throw away the whole reading, text included.
        limit = cls.model_fields[info.field_name].metadata[0].max_length
        return value[:limit] if isinstance(value, str) else value

    @field_validator("entities", mode="before")
    @classmethod
    def first_ones(cls, value):
        return value[:10] if isinstance(value, list) else value

    def as_text(self) -> str:
        return f"Description: {self.description}" + (f"\nText: {self.text}" if self.text.strip() else "")


def _vision_model():
    """The first model of OPENROUTER_VISION_MODEL, with the rest as its only fallbacks."""
    first, *rest = [m.strip() for m in get_settings().openrouter_vision_model.split(",") if m.strip()]
    # Streaming stays on: OpenRouterChat only keeps the call's cost from streamed chunks. No
    # quantization filter: vision providers report none, so OpenRouter would find no endpoint (404),
    # and the filter only guards the language of chat answers.
    return get_chat_model(first, fallbacks=",".join(rest), quantizations="")


def read_images(images: list[bytes], filename: str, callbacks: list) -> list[ImageReading | None]:
    """One vision call per JPEG, VISION_CONCURRENCY at a time; None where the call failed."""
    model = _vision_model().with_structured_output(ImageReading, method="function_calling")
    inputs = [[SystemMessage(IMAGE_PROMPT), HumanMessage(content=[
        {"type": "text", "text": f"File name: {filename}"},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(data).decode()}"}},
    ])] for data in images]
    results = model.batch(inputs, config={"callbacks": callbacks, "max_concurrency": VISION_CONCURRENCY},
                          return_exceptions=True)
    readings = []
    for result in results:
        if isinstance(result, ImageReading):
            readings.append(result)
        else:  # an exception (rate limit, provider error, unparsable reply) or no structured reply
            log.warning("Reading an image of %s failed: %r", filename, result)
            readings.append(None)
    return readings


def read_file_images(path: Path, filename: str, callbacks: list) -> list[tuple[int | None, ImageReading]]:
    """(page, reading) of each image in the file that was read; [] when image reading is off."""
    if not get_settings().openrouter_vision_model.strip():
        return []
    found = images_in(path)
    if not found:
        return []
    readings = read_images([data for _, data in found], filename, callbacks)
    return [(page, reading) for (page, _), reading in zip(found, readings) if reading is not None]


def image_chunks(readings: list[tuple[int | None, ImageReading]], filename: str) -> list[Document]:
    """One unsplit Document per reading, tagged kind="image" so the image agent can search only these."""
    chunks = []
    for number, (page, reading) in enumerate(readings, start=1):
        header = f"[Image {number}, p. {page}]" if page else f"[Image {number}]"
        metadata = {"filename": filename, "image": number, "kind": "image"}
        if page:
            metadata["page"] = page
        chunks.append(Document(f"{header}\n{reading.as_text()}", metadata=metadata))
    return chunks


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
