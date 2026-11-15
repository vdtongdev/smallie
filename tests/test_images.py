import io
from pathlib import Path

import pytest
from PIL import Image

from app.features.documents import images as images_mod
from app.features.documents.images import (
    IMAGE_SUFFIXES,
    MAX_IMAGE_SIDE,
    MAX_IMAGES_PER_FILE,
    MIN_IMAGE_SIDE,
    VISION_CONCURRENCY,
    images_in,
)


def _noise(size, mode="RGB"):
    # Varied pixels so the encoded image is a real picture, not one flat colour.
    img = Image.effect_noise(size, 64).convert("RGB")
    return img if mode == "RGB" else img.convert(mode)


def _open(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def _pdf(path: Path, pics: list[Image.Image]) -> Path:
    pics[0].save(path, "PDF", save_all=True, append_images=pics[1:])
    return path


def _docx(path: Path, pics: list[Image.Image]) -> Path:
    from docx import Document

    doc = Document()
    for i, pic in enumerate(pics):
        png = path.parent / f"pic{i}.png"
        pic.save(png)
        doc.add_picture(str(png))
    doc.save(path)
    return path


def test_constants():
    assert IMAGE_SUFFIXES == {".png", ".jpg", ".jpeg", ".webp"}
    assert (MIN_IMAGE_SIDE, MAX_IMAGE_SIDE, MAX_IMAGES_PER_FILE, VISION_CONCURRENCY) == (
        100,
        1600,
        20,
        4,
    )


def test_png_file_is_one_image(tmp_path):
    p = tmp_path / "a.png"
    _noise((200, 150)).save(p)
    result = images_in(p)
    assert len(result) == 1
    page, data = result[0]
    assert page is None
    assert _open(data).format == "JPEG"


def test_pdf_image_keeps_its_page(tmp_path):
    from pypdf import PdfWriter

    img_pdf = _pdf(tmp_path / "img.pdf", [_noise((200, 200))])
    blank = tmp_path / "blank.pdf"
    w = PdfWriter()
    w.add_blank_page(200, 200)
    with open(blank, "wb") as f:
        w.write(f)
    from pypdf import PdfReader

    out = PdfWriter()
    out.add_page(PdfReader(str(blank)).pages[0])
    out.add_page(PdfReader(str(img_pdf)).pages[0])
    p = tmp_path / "two.pdf"
    with open(p, "wb") as f:
        out.write(f)
    result = images_in(p)
    assert [page for page, _ in result] == [2]


def test_image_only_pdf_gives_one_image_per_page(tmp_path):
    p = _pdf(tmp_path / "scan.pdf", [_noise((200, 200)) for _ in range(3)])
    assert [page for page, _ in images_in(p)] == [1, 2, 3]


def test_docx_images_have_no_page(tmp_path):
    p = _docx(tmp_path / "d.docx", [_noise((200, 200)), _noise((220, 180))])
    result = images_in(p)
    assert len(result) == 2
    assert all(page is None for page, _ in result)
    assert all(_open(data).format == "JPEG" for _, data in result)


def test_small_images_are_skipped(tmp_path):
    small = tmp_path / "small.png"
    ok = tmp_path / "ok.png"
    _noise((80, 300)).save(small)
    _noise((100, 100)).save(ok)
    assert images_in(small) == []
    assert len(images_in(ok)) == 1


def test_cap_is_twenty(tmp_path):
    p = _pdf(tmp_path / "many.pdf", [_noise((120, 120)) for _ in range(25)])
    assert len(images_in(p)) == 20


def test_cmyk_and_rgba_become_rgb_jpeg(tmp_path):
    cmyk = tmp_path / "c.jpg"
    _noise((150, 150)).convert("CMYK").save(cmyk)
    rgba = tmp_path / "r.png"
    Image.new("RGBA", (150, 150), (255, 0, 0, 0)).save(rgba)
    pal = tmp_path / "p.png"
    Image.new("P", (150, 150)).save(pal, transparency=0)
    for path in (cmyk, rgba, pal):
        (_, data), = images_in(path)
        img = _open(data)
        assert img.format == "JPEG" and img.mode == "RGB"
    # fully transparent pixels land on white, not black
    (_, data), = images_in(rgba)
    r, g, b = _open(data).getpixel((75, 75))
    assert min(r, g, b) > 200 or (r > 200 and g > 200 and b > 200)


def test_animated_webp_uses_first_frame(tmp_path):
    p = tmp_path / "anim.webp"
    red = Image.new("RGB", (150, 150), (255, 0, 0))
    blue = Image.new("RGB", (150, 150), (0, 0, 255))
    red.save(p, save_all=True, append_images=[blue], duration=100, loop=0)
    (_, data), = images_in(p)
    r, _, b = _open(data).getpixel((75, 75))
    assert r > b


def test_large_image_is_shrunk(tmp_path):
    p = tmp_path / "big.png"
    Image.new("RGB", (4000, 1000), (10, 120, 200)).save(p)
    (_, data), = images_in(p)
    assert max(_open(data).size) == 1600


def test_unreadable_image_is_skipped(tmp_path):
    p = tmp_path / "bad.png"
    p.write_bytes(b"this is not an image at all")
    assert images_in(p) == []


def test_decompression_bomb_is_skipped(tmp_path, monkeypatch):
    p = tmp_path / "bomb.png"
    _noise((300, 300)).save(p)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    assert images_in(p) == []
    assert images_mod  # module imported
