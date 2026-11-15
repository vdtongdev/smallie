"""Pull images out of uploaded files, normalised to JPEG for a vision model."""

import io
import logging
import tempfile
from pathlib import Path

from PIL import Image

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
