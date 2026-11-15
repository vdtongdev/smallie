"""Pull images out of uploaded files, normalised to JPEG for a vision model."""

import asyncio
import base64
import io
import logging
import tempfile
from pathlib import Path

from langchain_core.messages import HumanMessage
from PIL import Image

from app.agents.factory import fallback_category
from app.core.llm import image_models
from app.features.documents.prompts import IMAGE_PROMPT

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MIN_IMAGE_SIDE = 100
MAX_IMAGE_SIDE = 1600
MAX_IMAGES_PER_FILE = 20
VISION_CONCURRENCY = 4


def _normalise(data: bytes) -> bytes | None:
    """JPEG bytes for one image, or None when it is too small."""
    img = Image.open(io.BytesIO(data))
    img.seek(0)
    if min(img.size) < MIN_IMAGE_SIDE:
        return None
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        img = flat
    else:
        img = img.convert("RGB")
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


def _raw_images(path: Path):
    """Yield (page or None, raw bytes) for each image in the file."""
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        yield None, path.read_bytes()
    elif suffix == ".pdf":
        from pypdf import PdfReader

        for number, page in enumerate(PdfReader(str(path)).pages, start=1):
            try:
                found = list(page.images)
            except Exception:
                log.warning("Could not read images on page %s of %s", number, path.name, exc_info=True)
                continue
            for image in found:
                yield number, image.data
    elif suffix == ".docx":
        import docx2txt

        with tempfile.TemporaryDirectory() as tmp:
            docx2txt.process(str(path), tmp)
            for extracted in sorted(Path(tmp).iterdir()):
                if extracted.stem == "thumbnail":  # docProps preview, not content
                    continue
                yield None, extracted.read_bytes()


def images_in(path: Path) -> list[tuple[int | None, bytes]]:
    """Return (1-based page or None, JPEG bytes) for up to MAX_IMAGES_PER_FILE images."""
    result: list[tuple[int | None, bytes]] = []
    try:
        for page, raw in _raw_images(path):
            if len(result) >= MAX_IMAGES_PER_FILE:
                break
            try:
                jpeg = _normalise(raw)
            except Exception:
                log.warning("Skipped an unreadable image in %s", path.name, exc_info=True)
                continue
            if jpeg is not None:
                result.append((page, jpeg))
    except Exception:
        log.warning("Could not extract images from %s", path.name, exc_info=True)
    return result


def _message(jpeg: bytes) -> list[HumanMessage]:
    url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    return [HumanMessage(content=[{"type": "text", "text": IMAGE_PROMPT}, {"type": "image_url", "image_url": {"url": url}}])]


def _text(reply) -> str | None:
    content = reply.content
    if isinstance(content, list):
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return content.strip() or None


def _log_failure(exc: BaseException, number: int) -> None:
    log.warning(
        "Image %s was not read: category=%s status=%s",
        number, fallback_category(exc), getattr(exc, "status_code", None), exc_info=exc,
    )


async def read_images(user_id: str, images: list[bytes], callbacks: list) -> list[str | None]:
    """Read each image once; None marks one that could not be read. Image 1 is a probe: if the first
    model cannot take images (400 or 404) and another is set, that one reads the whole file."""
    if not images:
        return []
    models = await asyncio.to_thread(image_models, user_id)
    config = {"callbacks": callbacks, "max_concurrency": VISION_CONCURRENCY}
    model, readings = models[0], []
    try:
        readings.append(_text(await model.ainvoke(_message(images[0]), config=config)))
    except Exception as exc:
        category = fallback_category(exc)
        switching = category in ("unsupported", "model_not_found") and len(models) > 1
        if switching:
            log.warning(
                "Model %s can't read images (%s, HTTP %s); switching to %s for this file",
                getattr(model, "model_name", ""), category, getattr(exc, "status_code", None),
                getattr(models[1], "model_name", ""),
            )
        else:
            _log_failure(exc, 1)
        readings.append(None)
        if category in ("unsupported", "model_not_found"):
            if len(models) == 1:  # it cannot see images and nothing else can: the rest would fail too
                return [None] * len(images)
            model = models[1]
            try:
                readings[0] = _text(await model.ainvoke(_message(images[0]), config=config))
            except Exception as retry_exc:
                _log_failure(retry_exc, 1)
                if fallback_category(retry_exc) in ("unsupported", "model_not_found"):
                    return [None] * len(images)  # the last model cannot see images either
    results = await model.abatch([_message(i) for i in images[1:]], config=config, return_exceptions=True)
    for number, result in enumerate(results, start=2):
        if isinstance(result, BaseException):
            _log_failure(result, number)
            readings.append(None)
        else:
            readings.append(_text(result))
    return readings
