from pathlib import Path

import pytest
from docx import Document as DocxDocument
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.features.documents.ingest import chunk_ids, load_and_split

VI_TEXT = "Chính sách đổi trả: khách hàng được đổi sản phẩm trong vòng 30 ngày kể từ ngày mua."


@pytest.fixture
def english_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "handbook.pdf"
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.drawString(72, 800, "Page one: the office opens at 8 am.")
    pdf.showPage()
    pdf.drawString(72, 800, "Page two: the refund window is 30 days.")
    pdf.showPage()
    pdf.save()
    return path


@pytest.fixture
def vietnamese_docx(tmp_path: Path) -> Path:
    path = tmp_path / "chinh-sach.docx"
    doc = DocxDocument()
    doc.add_heading("Quy định cửa hàng", level=1)
    doc.add_paragraph(VI_TEXT)
    doc.save(path)
    return path


def test_pdf_chunks_keep_filename_and_one_based_pages(english_pdf: Path):
    chunks = load_and_split(english_pdf, "handbook.pdf")
    assert [c.metadata["page"] for c in chunks] == [1, 2]
    assert all(c.metadata["filename"] == "handbook.pdf" for c in chunks)
    assert "refund window" in chunks[1].page_content


def test_docx_keeps_vietnamese_diacritics(vietnamese_docx: Path):
    chunks = load_and_split(vietnamese_docx, "chính-sách.docx")
    text = " ".join(c.page_content for c in chunks)
    assert VI_TEXT in text
    assert chunks[0].metadata == {"filename": "chính-sách.docx"}


def test_long_text_is_split_into_bounded_chunks(tmp_path: Path):
    path = tmp_path / "long.docx"
    doc = DocxDocument()
    for _ in range(40):
        doc.add_paragraph(VI_TEXT)
    doc.save(path)
    chunks = load_and_split(path, "long.docx")
    assert len(chunks) > 1
    assert max(len(c.page_content) for c in chunks) <= 800


def test_unsupported_type_rejected(tmp_path: Path):
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    with pytest.raises(ValueError):
        load_and_split(path, "notes.txt")


def test_chunk_ids_are_deterministic():
    assert chunk_ids("doc", 3) == ["doc:0", "doc:1", "doc:2"]


def test_image_chunks_follow_text_chunks():
    from langchain_core.documents import Document

    from app.features.documents.ingest import image_chunks

    text = [Document("Body.", metadata={"filename": "f.pdf", "page": 1})]
    images = image_chunks(["A chart.", "A table."], [2, 3], "f.pdf")
    assert [c.page_content.split("\n")[0] for c in images] == ["[Image 1, p. 2]", "[Image 2, p. 3]"]
    assert all(c.metadata["kind"] == "image" for c in images)
    assert [c.metadata["image"] for c in images] == [1, 2]
    assert [c.metadata["page"] for c in images] == [2, 3]
    assert all("kind" not in c.metadata for c in text)
    assert chunk_ids("d", len(text) + len(images)) == ["d:0", "d:1", "d:2"]


def test_unread_images_keep_their_number():
    from app.features.documents.ingest import image_chunks

    chunks = image_chunks([None, "Second.", "   ", "Fourth."], [None, None, None, None], "f.docx")
    assert [c.metadata["image"] for c in chunks] == [2, 4]
    assert chunks[0].page_content.startswith("[Image 2]\n")
    assert "page" not in chunks[0].metadata


def test_image_file_has_no_text_chunks(tmp_path: Path):
    path = tmp_path / "scan.png"
    path.write_bytes(b"not read here")
    assert load_and_split(path, "scan.png") == []
