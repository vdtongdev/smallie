import base64
import io
from pathlib import Path

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage
from PIL import Image

from app.features.documents import images as images_mod
from app.features.documents.images import (
    IMAGE_SUFFIXES,
    MAX_IMAGE_SIDE,
    MAX_IMAGES_PER_FILE,
    MIN_IMAGE_SIDE,
    VISION_CONCURRENCY,
    images_in,
    read_images,
)
from app.features.documents.prompts import IMAGE_PROMPT


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


# --- read_images ---------------------------------------------------------------


def _err(cls, status):
    request = httpx.Request("POST", "https://x/v1/chat/completions")
    return cls("boom", response=httpx.Response(status, request=request), body=None)


class FakeVision:
    """Records every call; raises errors[image bytes] for that image, else replies text."""

    def __init__(self, name, errors=None, text="a reading"):
        self.name, self.errors, self.text = name, errors or {}, text
        self.calls, self.configs, self.messages = [], [], []

    def _reply(self, messages):
        self.messages.append(messages)
        url = messages[0].content[1]["image_url"]["url"]
        raw = base64.b64decode(url.split(",", 1)[1])
        self.calls.append(raw)
        if raw in self.errors:
            return self.errors[raw]
        return AIMessage(content=self.text)

    async def ainvoke(self, messages, config=None, **kw):
        self.configs.append(config)
        out = self._reply(messages)
        if isinstance(out, Exception):
            raise out
        return out

    async def abatch(self, inputs, config=None, return_exceptions=False, **kw):
        self.configs.append(config)
        return [self._reply(m) for m in inputs]


def _use(monkeypatch, *models):
    monkeypatch.setattr(images_mod, "image_models", lambda user_id: list(models))


IMGS = [b"one", b"two", b"three"]


async def test_chat_model_cannot_see_images_switches_for_the_file(monkeypatch):
    chat = FakeVision("chat", {b"one": _err(openai.BadRequestError, 400)})
    vis = FakeVision("vis", text="seen")
    _use(monkeypatch, chat, vis)
    assert await read_images("u", IMGS, []) == ["seen"] * 3
    assert chat.calls == [b"one"]
    assert vis.calls == IMGS


async def test_model_not_found_also_switches(monkeypatch):
    chat = FakeVision("chat", {b"one": _err(openai.NotFoundError, 404)})
    vis = FakeVision("vis", text="seen")
    _use(monkeypatch, chat, vis)
    assert await read_images("u", IMGS, []) == ["seen"] * 3
    assert chat.calls == [b"one"]


async def test_chat_model_reads_everything(monkeypatch):
    chat, vis = FakeVision("chat", text="chat read"), FakeVision("vis")
    _use(monkeypatch, chat, vis)
    assert await read_images("u", IMGS, []) == ["chat read"] * 3
    assert vis.calls == []


async def test_no_fallback_leaves_all_unread(monkeypatch):
    chat = FakeVision("chat", {b"one": _err(openai.BadRequestError, 400)})
    _use(monkeypatch, chat)
    assert await read_images("u", IMGS, []) == [None, None, None]
    assert len(chat.calls) == 1


async def test_image_model_first_never_calls_chat(monkeypatch):
    vis = FakeVision("vis", text="seen")
    _use(monkeypatch, vis)
    assert await read_images("u", IMGS, []) == ["seen"] * 3


async def test_one_failure_in_batch_is_skipped(monkeypatch):
    class Batchy(FakeVision):
        async def abatch(self, inputs, config=None, return_exceptions=False, **kw):
            self.configs.append(config)
            assert return_exceptions is True
            return [self._reply(m) for m in inputs]

    chat = Batchy("chat", {b"two": _err(openai.InternalServerError, 500)})
    _use(monkeypatch, chat)
    assert await read_images("u", IMGS, []) == ["a reading", None, "a reading"]


async def test_rate_limit_on_first_image_does_not_switch(monkeypatch):
    chat = FakeVision("chat", {b"one": _err(openai.RateLimitError, 429)})
    vis = FakeVision("vis")
    _use(monkeypatch, chat, vis)
    assert await read_images("u", IMGS, []) == [None, "a reading", "a reading"]
    assert chat.calls == [b"one", b"two", b"three"]
    assert vis.calls == []


async def test_expected_switch_logs_one_line(monkeypatch, caplog):
    chat = FakeVision("chat", {b"one": _err(openai.NotFoundError, 404)})
    vis = FakeVision("vis", text="seen")
    _use(monkeypatch, chat, vis)
    with caplog.at_level("WARNING", logger=images_mod.log.name):
        assert await read_images("u", IMGS, []) == ["seen"] * 3
    records = [r for r in caplog.records if r.name == images_mod.log.name and r.levelname == "WARNING"]
    assert len(records) == 1
    assert "switching" in records[0].getMessage()
    assert not records[0].exc_info


async def test_unexpected_failure_keeps_traceback(monkeypatch, caplog):
    chat = FakeVision("chat", {b"one": _err(openai.RateLimitError, 429)})
    _use(monkeypatch, chat, FakeVision("vis"))
    with caplog.at_level("WARNING", logger=images_mod.log.name):
        await read_images("u", IMGS, [])
    records = [r for r in caplog.records if r.name == images_mod.log.name and r.levelname == "WARNING"]
    assert len(records) == 1
    assert records[0].exc_info


async def test_blank_reading_counts_as_unread(monkeypatch):
    _use(monkeypatch, FakeVision("chat", text=" \n "))
    assert await read_images("u", IMGS, []) == [None, None, None]


async def test_message_carries_prompt_and_jpeg_data_url(monkeypatch):
    chat = FakeVision("chat")
    _use(monkeypatch, chat)
    await read_images("u", [b"one"], [])
    (message,) = chat.messages[0]
    text, image = message.content
    assert text == {"type": "text", "text": IMAGE_PROMPT}
    assert image["type"] == "image_url"
    assert image["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_calls_reach_callbacks(monkeypatch):
    chat = FakeVision("chat")
    _use(monkeypatch, chat)
    marker = object()
    await read_images("u", IMGS, [marker])
    assert all(c["callbacks"] == [marker] for c in chat.configs)
    assert chat.configs[-1]["max_concurrency"] == VISION_CONCURRENCY


# ---------- process_document: image readings in the index and the card ----------

import uuid  # noqa: E402

from langchain_core.documents import Document  # noqa: E402
from langgraph.store.memory import InMemoryStore  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.db import documents, get_engine  # noqa: E402
from app.core.usage import get_usage  # noqa: E402
from app.features.documents import card as card_module  # noqa: E402
from app.features.documents import ingest as ingest_module  # noqa: E402
from tests.conftest import requires_db  # noqa: E402
from tests.test_graph import make_user  # noqa: E402,F401  (fixture)


class FakeStore:
    def __init__(self):
        self.added = []

    def add_documents(self, docs, ids=None):
        self.added.extend(docs)

    def delete(self, ids):
        pass


def _new_row(user_id: str, filename: str, path: Path) -> str:
    document_id = str(uuid.uuid4())
    with get_engine().begin() as conn:
        conn.execute(documents.insert().values(id=document_id, user_id=user_id, filename=filename,
                                               path=str(path), status="processing"))
    return document_id


def _row(document_id: str):
    with get_engine().connect() as conn:
        return conn.execute(select(documents).where(documents.c.id == document_id)).mappings().one()


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(ingest_module, "get_vector_store", lambda: fake)
    return fake


@pytest.fixture
def cards(monkeypatch):
    written = []

    async def write_card(store, document_id, user_id, filename, chunks, usage=None):
        written.append(chunks)
        return True

    monkeypatch.setattr(card_module, "write_card", write_card)
    return written


@requires_db
async def test_image_only_file_with_no_reading_fails(make_user, store, cards, monkeypatch, tmp_path):
    user, path = make_user(), tmp_path / "a.png"
    path.write_bytes(b"x")
    monkeypatch.setattr(card_module, "images_in", lambda p: [(None, b"jpeg")])

    async def read(user_id, images, callbacks):
        return [None]

    monkeypatch.setattr(card_module, "read_images", read)
    document_id = _new_row(user, "a.png", path)
    await card_module.process_document(InMemoryStore(), document_id, user, path, "a.png")
    row = _row(document_id)
    assert (row["status"], row["error"]) == ("failed", "No text or readable images found.")
    assert store.added == [] and cards == []


@requires_db
async def test_text_and_unread_images_are_ready(make_user, store, cards, monkeypatch, tmp_path):
    user, path = make_user(), tmp_path / "a.pdf"
    monkeypatch.setattr(card_module, "ingest_document", lambda *a: [Document("Some text.")])
    monkeypatch.setattr(card_module, "images_in", lambda p: [(1, b"a"), (2, b"b")])

    async def read(user_id, images, callbacks):
        return [None, None]

    monkeypatch.setattr(card_module, "read_images", read)
    document_id = _new_row(user, "a.pdf", path)
    await card_module.process_document(InMemoryStore(), document_id, user, path, "a.pdf")
    row = _row(document_id)
    assert (row["status"], row["images_unread"], row["image_count"], row["chunk_count"]) == ("ready", 2, 0, 1)
    assert len(cards) == 1


@requires_db
async def test_readings_are_indexed_after_text_and_counted(make_user, store, cards, monkeypatch, tmp_path):
    user, path = make_user(), tmp_path / "a.pdf"
    monkeypatch.setattr(card_module, "ingest_document", lambda *a: [Document("Some text.")])
    monkeypatch.setattr(card_module, "images_in", lambda p: [(1, b"a"), (2, b"b")])

    async def read(user_id, images, callbacks):
        return ["A chart.", None]

    monkeypatch.setattr(card_module, "read_images", read)
    document_id = _new_row(user, "a.pdf", path)
    await card_module.process_document(InMemoryStore(), document_id, user, path, "a.pdf")
    row = _row(document_id)
    assert (row["image_count"], row["images_unread"], row["chunk_count"]) == (1, 1, 2)
    assert [c.metadata.get("kind") for c in store.added] == [None, "image"]
    assert all(c.metadata["user_id"] == user and c.metadata["document_id"] == document_id for c in store.added)
    assert len(cards[0]) == 2


@requires_db
async def test_corrupt_image_file_fails_cleanly(make_user, store, cards, tmp_path):
    user, path = make_user(), tmp_path / "broken.png"
    path.write_bytes(b"this is not an image")
    document_id = _new_row(user, "broken.png", path)
    await card_module.process_document(InMemoryStore(), document_id, user, path, "broken.png")
    row = _row(document_id)
    assert (row["status"], row["error"]) == ("failed", "No text or readable images found.")


@requires_db
async def test_usage_of_readings_is_added(make_user, store, monkeypatch, tmp_path):
    user, path = make_user(), tmp_path / "a.png"
    path.write_bytes(b"x")
    monkeypatch.setattr(card_module, "images_in", lambda p: [(None, b"jpeg")])

    async def read(user_id, images, callbacks):
        callbacks[0].input_tokens += 7
        callbacks[0].output_tokens += 3
        return ["A receipt."]

    def no_model(*a, **k):
        raise RuntimeError("card model down")  # the card is best effort; the readings still count

    monkeypatch.setattr(card_module, "read_images", read)
    monkeypatch.setattr(card_module, "get_chat_model", no_model)
    document_id = _new_row(user, "a.png", path)
    memory = InMemoryStore()
    await card_module.process_document(memory, document_id, user, path, "a.png")
    assert _row(document_id)["status"] == "ready"
    total = await get_usage(memory, user)
    assert (total["input_tokens"], total["output_tokens"]) == (7, 3)


def test_card_opening_keeps_readings_after_long_text():
    text = [Document("t" * 2500), Document("u" * 2500), Document("v" * 5000)]
    reading = Document("[Image 1]\nA stamp from Zephyr Corp.", metadata={"kind": "image"})
    opening = card_module.card_opening([*text, reading])
    assert card_module.CARD_CHARS == 6000 and card_module.CARD_TEXT_CHARS == 4500
    assert opening.count("t") + opening.count("u") + opening.count("v") == 4500
    assert "Zephyr Corp" in opening
    assert len(opening) <= 6000


def test_card_opening_image_file_uses_whole_budget():
    readings = [Document("r" * 5000, metadata={"kind": "image"}), Document("s" * 5000, metadata={"kind": "image"})]
    opening = card_module.card_opening(readings)
    assert len(opening) == 6000 and opening.count("r") == 5000 and opening.count("s") == 999


def test_card_opening_without_readings_is_the_text_opening():
    opening = card_module.card_opening([Document("a" * 5000), Document("b" * 5000)])
    assert len(opening) == 6000


async def test_image_model_also_cannot_see_images_leaves_all_unread(monkeypatch):
    chat = FakeVision("chat", {b"one": _err(openai.BadRequestError, 400)})
    vis = FakeVision("vis", {b"one": _err(openai.BadRequestError, 400)})
    batches = []
    for model in (chat, vis):
        async def abatch(inputs, config=None, return_exceptions=False, _m=model, **kw):
            batches.append(_m.name)
            return []
        model.abatch = abatch
    _use(monkeypatch, chat, vis)
    assert await read_images("u", IMGS, []) == [None, None, None]
    assert chat.calls == [b"one"]
    assert vis.calls == [b"one"]
    assert batches == []
