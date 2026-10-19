# Image Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Images (uploaded, attached in chat, or inside PDF/DOCX) are read once by a vision model into the docs RAG and the graph, and a new `image` agent answers from those readings.

**Architecture:** Ingestion (`app/features/documents/`) gets an `images.py` module that extracts images, reads each one with one vision call, and turns the readings into `kind="image"` chunks after the text chunks. A new agent package `app/agents/image/` searches only those chunks through the existing `search_with_graph`, and its `AgentSpec` plugs into the router and orchestrator with no changes to either. Chat attachments are ordinary document uploads whose names are appended to the message.

**Tech Stack:** FastAPI, LangChain `create_agent`, OpenRouter (OpenAI-compatible, image input as base64 data URL), pypdf, docx2txt, Pillow, pgvector, plain JS UI.

**Spec:** `docs/superpowers/specs/2026-10-19-image-agent-design.md`

## How to work through this plan

Each task is one TDD loop: write the test, watch it fail, write the code, watch it pass, commit. Run tests with `uv run pytest <file>::<test> -v`. Start Postgres first (`wsl -d Ubuntu -u root service postgresql start`), otherwise `requires_db` tests are skipped silently. Before editing a file, run `uv run python .claude/skills/antipatterning/antipattern.py select --path <file> --topic <topic>` and follow what it returns (CLAUDE.local.md). Use the `writing-llm-document` skill for every prompt, tool docstring and `AgentSpec` description.

## Global Constraints

- Every query, vector filter and tool stays scoped by `user_id`; the image search adds to the `user_id` filter, never replaces it.
- Sync DB, vector and file work runs in `asyncio.to_thread` from async code.
- `OPENROUTER_VISION_MODEL` empty = images off: no vision call is ever made, image uploads get 400, images in PDF/DOCX are skipped.
- Limits: `MAX_IMAGES_PER_FILE = 20`, `MIN_IMAGE_SIDE = 100`, `MAX_IMAGE_SIDE = 1600`, `VISION_CONCURRENCY = 4`.
- Image chunks: metadata `kind="image"`, `filename`, `page` (when known), `image` (1-based); content starts `[Image N, p. P]` or `[Image N]`. Text chunks get no `kind` key.
- Agent prompts end with `LANGUAGE_RULE` and `MEMORY_RULE` (`app/core/prompts.py`).
- Route `image`, labels `{"en": "Image agent", "vi": "Trợ lý hình ảnh"}`, `message_names=("image_agent",)`, `overrides=("docs",)`.
- Chat attachments: at most 5 per message; foreign or missing id → 404, still processing → 409, failed → 409.
- New agent = release 0.4.0; the changelog and version are updated at release, not in this plan.

## Review Focus

1. "Summarize report.pdf" where report.pdf contains images must still go to **docs**: the image agent's `matches` only uses stems of image *files* (`.png/.jpg/.jpeg/.webp`). Test in Task 6.
2. PDF images in odd formats (CMYK, palette, undecodable streams) must be converted or skipped without failing the file. Tests in Task 2.
3. A vision call that errors (rate limit, provider down) for some images must keep the others; the file is `ready` if anything was read, and the usage is still recorded. Tests in Tasks 3 and 4.
4. An attachment id of another user must give 404 and never reveal the file name. Test in Task 7.
5. With images off, a PDF that has images ingests its text with zero vision calls. Test in Task 4.

---

### Task 1: Vision model setting and per-call fallbacks

**Files:**
- Modify: `app/core/config.py` (after `openrouter_embed_model`), `app/core/llm.py:21-46`, `.env.example`, `pyproject.toml` (via `uv add pillow`)
- Test: `tests/test_llm.py`

**Interfaces:**
- Produces: `Settings.openrouter_vision_model: str = ""`, comma-separated, the first one is the model and the rest are its fallbacks. `get_chat_model(model: str | None = None, *, streaming: bool = True, fallbacks: str | None = None)`. When `fallbacks` is not `None`, it replaces `openrouter_fallback_model` (the chat fallbacks may not accept images).

- [ ] **Step 1: Write the failing test**

```python
def test_explicit_fallbacks_replace_the_chat_fallbacks(monkeypatch):
    # set openrouter_fallback_model="chat-b" the same way test_fallback_models_are_tried_in_order does
    model = get_chat_model("vision-a", fallbacks="vision-b, vision-a")
    assert model.extra_body == {"models": ["vision-a", "vision-b"]}
    assert get_chat_model("vision-a", fallbacks="").extra_body is None
```

- [ ] **Step 2: Run** `uv run pytest tests/test_llm.py -v`. Expected: FAIL, `unexpected keyword argument 'fallbacks'`.
- [ ] **Step 3: Implement.** Add the `fallbacks` parameter (`s.openrouter_fallback_model if fallbacks is None else fallbacks`), the setting, and an `.env.example` entry explaining the comma list and that empty turns images off. Run `uv add pillow`, which pins the version that is already installed.
- [ ] **Step 4: Run** `uv run pytest tests/test_llm.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Add OPENROUTER_VISION_MODEL and per-call fallbacks"`

### Task 2: Extract images from files

**Files:**
- Create: `app/features/documents/images.py`
- Test: `tests/test_images.py`

**Interfaces:**
- Produces:
  - `IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}` and the four limit constants.
  - `images_in(path: Path) -> list[tuple[int | None, bytes]]`: `(page, jpeg_bytes)` in document order. The page is 1-based for PDFs and `None` for DOCX and image files.

- [ ] **Step 1: Write the failing tests** (build the fixtures with Pillow, reportlab `drawImage` and python-docx `add_picture`, which the tests already use):

```python
def test_image_file_is_one_rgb_jpeg(tmp_path):        # 800x600 CMYK .jpg
    [(page, data)] = images_in(path)
    assert page is None and Image.open(BytesIO(data)).mode == "RGB"

def test_small_images_are_skipped(tmp_path):           # 50x50 png
    assert images_in(path) == []

def test_large_images_are_shrunk(tmp_path):            # 4000x3000 png
    assert max(Image.open(BytesIO(images_in(path)[0][1])).size) == 1600

def test_pdf_images_keep_their_page(tmp_path):         # text on p.1, 400x300 image on p.2
    assert [page for page, _ in images_in(path)] == [2]

def test_docx_images_have_no_page(tmp_path):
    assert [page for page, _ in images_in(path)] == [None]

def test_cap(tmp_path, monkeypatch):                   # 3 images, MAX_IMAGES_PER_FILE patched to 2
    assert len(images_in(path)) == 2

def test_unreadable_image_is_skipped(tmp_path, monkeypatch):
    # patch the per-image converter to raise for the first of two images
    assert len(images_in(path)) == 1
```

- [ ] **Step 2: Run** `uv run pytest tests/test_images.py -v`. Expected: FAIL, import error.
- [ ] **Step 3: Implement** `images_in` with a private `_to_jpeg(data: bytes) -> bytes | None` that returns `None` when the image is too small or Pillow can't open it (logged).
  - Steps inside `_to_jpeg`: `Image.open`, then `convert("RGB")`, then `thumbnail((1600, 1600))`, then save as JPEG at quality 85.
  - PDF: `page.images` gives the bytes in `image.data`.
  - DOCX: read the `word/media/*` entries straight from the zip (`zipfile`, stdlib), sorted by their number. Not `docx2txt.process(path, dir)`: it also extracts the package thumbnail (`docProps/thumbnail.jpeg`), which isn't an image in the document.
- [ ] **Step 4: Run** `uv run pytest tests/test_images.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Extract images from PDF, DOCX and image files"`

### Task 3: Read images with the vision model (+ pick the model)

**Files:**
- Modify: `app/features/documents/images.py`, `app/features/documents/prompts.py`
- Test: `tests/test_images.py`

**Interfaces:**
- Consumes: `get_chat_model(..., streaming=False, fallbacks=...)` (Task 1), `images_in` (Task 2).
- Produces:
  - `class ImageReading(BaseModel)` with fields:
    - `description: str` (max 2000)
    - `text: str = ""`
    - `doc_type: str` (max 40)
    - `summary: str` (max 200)
    - `entities: list[str]` (max 10)
    - `as_text() -> str`: `"Description: …\nText: …"`, leaving out the Text line when `text` is empty.
  - `read_images(images: list[bytes], filename: str, callbacks: list) -> list[ImageReading | None]`.
  - `read_file_images(path: Path, filename: str, callbacks: list) -> list[tuple[int | None, ImageReading]]`: only the successful readings. It returns `[]` without calling `images_in` when `openrouter_vision_model` is empty.
  - `image_chunks(readings: list[tuple[int | None, ImageReading]], filename: str) -> list[Document]`: unsplit.
  - `IMAGE_PROMPT` in `prompts.py`.

- [ ] **Step 1: Write the failing tests**

```python
def test_failed_reads_become_none(monkeypatch):
    # fake get_chat_model: .with_structured_output(...).batch(inputs, config=..., return_exceptions=True)
    # returns [READING, RuntimeError("429")]
    assert read_images([b"a", b"b"], "x.png", []) == [READING, None]

def test_images_off_means_no_calls(monkeypatch):
    # openrouter_vision_model = ""; images_in patched to raise if called
    assert read_file_images(Path("r.pdf"), "r.pdf", []) == []

def test_image_chunks_header_and_metadata():
    [doc] = image_chunks([(3, READING)], "r.pdf")
    assert doc.page_content.startswith("[Image 1, p. 3]\nDescription: ")
    assert doc.metadata == {"filename": "r.pdf", "page": 3, "image": 1, "kind": "image"}
    [doc] = image_chunks([(None, READING)], "a.png")
    assert doc.page_content.startswith("[Image 1]\n") and "page" not in doc.metadata
```

- [ ] **Step 2: Run** `uv run pytest tests/test_images.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Model: `get_chat_model(first, streaming=False, fallbacks=rest)`, with the setting split on commas.
  - Structured output: `with_structured_output(ImageReading, method="function_calling")`.
  - Calls: `batch(..., config={"callbacks": callbacks, "max_concurrency": VISION_CONCURRENCY}, return_exceptions=True)`.
  - Each input is `[SystemMessage(IMAGE_PROMPT), HumanMessage(content=[{"type": "text", "text": f"File name: {filename}"}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,…"}}])]`.
  - Exceptions become `None` and are logged.
  - `IMAGE_PROMPT` (writing-llm-document skill) asks for:
    - a description that is factual and specific (what, who, numbers, layout of charts and tables);
    - `text` transcribed exactly in its original language, keeping line breaks for receipts and tables;
    - `doc_type`, `summary` and `entities` with the same meaning as in `CARD_PROMPT`.
- [ ] **Step 4: Run** `uv run pytest tests/test_images.py -v`. Expected: PASS.
- [ ] **Step 5: Pick the model (manual).**
  - Put 4 real images in your scratch folder: a receipt, a Vietnamese scan, a chart screenshot and a photo.
  - For each candidate, `google/gemini-…-flash` and `google/gemma-4-31b-it:free`, set `OPENROUTER_VISION_MODEL` and run `uv run python -c "from pathlib import Path; from app.features.documents.images import read_file_images; [print(p, r.as_text(), r.summary, r.entities) for p, r in read_file_images(Path('<img>'), '<img>', [])]"`.
  - Keep the model that transcribes the Vietnamese and the receipt numbers exactly and returns valid structured output every time. Set it (plus the other as fallback) in `.env` and `cloudrun.env.yaml`.
- [ ] **Step 6: Commit** `git commit -m "Read images with one vision call each"`

### Task 4: Ingest image readings as chunks

**Files:**
- Modify: `app/core/db.py` (`documents` table + `init_db`), `app/features/documents/ingest.py`, `app/features/documents/card.py`, `scripts/reindex.py:27`
- Test: `tests/test_ingest.py`, `tests/test_graph.py` (next to `test_document_card_is_best_effort`)

**Interfaces:**
- Consumes: `IMAGE_SUFFIXES`, `read_file_images`, `image_chunks`, `ImageReading` (Tasks 2–3).
- Produces:
  - Column `documents.image_count integer NOT NULL DEFAULT 0`.
  - `SUPPORTED_SUFFIXES` = PDF, DOCX and `IMAGE_SUFFIXES`.
  - `load_and_split(path, filename, readings: Sequence[tuple[int | None, ImageReading]] = ()) -> list[Document]`: text chunks first, then the split image chunks. An image file gives no text chunks.
  - `ingest_document(document_id, user_id, path, filename, callbacks: list | None = None) -> tuple[list[Document], list[ImageReading]]`.
  - `process_document` keeps its signature.

- [ ] **Step 1: Write the failing tests**

```python
def test_image_chunks_come_after_text_chunks(english_pdf):
    chunks = load_and_split(english_pdf, "handbook.pdf", [(2, READING)])
    assert [c.metadata.get("kind") for c in chunks] == [None, None, "image"]

def test_long_transcription_is_split(tmp_path):        # .png path, reading.text = VI_TEXT * 40
    chunks = load_and_split(path, "a.png", [(None, long_reading)])
    assert len(chunks) > 1 and all(c.metadata["kind"] == "image" for c in chunks)

@requires_db
def test_image_file_with_no_reading_fails(...):        # read_file_images patched to []
    # ingest_document returns ([], []); the row is "failed" with error "No text or readable images found."

@requires_db
def test_image_count_is_saved(...):                    # read_file_images patched to 2 readings
    # the row is "ready", image_count == 2, chunk_count == len(chunks)

async def test_image_upload_card_needs_no_model_call(monkeypatch):   # in test_graph.py
    # ingest_document patched to return (chunks, [READING]); save_card and get_chat_model patched
    # save_card called with f"{READING.doc_type}: {READING.summary}" and READING.entities; get_chat_model not called

async def test_pdf_card_still_uses_write_card(monkeypatch):
    # .pdf path: write_card is awaited

async def test_vision_usage_is_recorded_even_when_reads_fail(monkeypatch):
    # add_usage is awaited once with the counter passed to ingest_document
```

  To cover Review Focus 5, the images-off case is covered by Task 3's `test_images_off_means_no_calls`, because `ingest_document` always goes through `read_file_images`.

- [ ] **Step 2: Run** `uv run pytest tests/test_ingest.py tests/test_graph.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
  - `init_db`: `ALTER TABLE documents ADD COLUMN IF NOT EXISTS image_count integer NOT NULL DEFAULT 0`, plus the `Column` in the table definition.
  - `ingest_document`: call `read_file_images(path, filename, callbacks or [])` first, then `load_and_split(path, filename, readings)`; set `image_count=len(readings)` with the status; update the empty-file error text.
  - `process_document`: create a `UsageCounter`, pass `[usage]` to `ingest_document`, then:
    - image file with readings: `save_card(user_id, document_id, f"{r.doc_type}: {r.summary}", entities ≤ 80 chars)` through `asyncio.to_thread`, best effort as in `write_card`;
    - otherwise: `write_card` as now.
  - Then `add_usage(store, user_id, usage)` inside a try/except, as `write_card` does.
  - `reindex.py`: no change needed, because the return value isn't used there. Check that it still runs with `--help`, or by reading it.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: everything passes, old tests included (`test_docx_keeps_vietnamese_diacritics` still gets metadata without `kind`).
- [ ] **Step 5: Commit** `git commit -m "Ingest image readings as image chunks"`

### Task 5: Accept image uploads

**Files:**
- Modify: `app/features/documents/routes.py:26-30`, `app/static/index.html:67` (+ the Readme dialog text), `app/static/i18n.js`
- Test: `tests/test_documents_upload.py` (new)

**Interfaces:**
- Consumes: `SUPPORTED_SUFFIXES`, `IMAGE_SUFFIXES`, `openrouter_vision_model`.

- [ ] **Step 1: Write the failing tests.** Call `upload` directly. Validation runs before anything is written.

```python
def test_images_are_rejected_when_image_reading_is_off(monkeypatch):   # vision model ""
    with pytest.raises(HTTPException) as e:
        upload([UploadFile(BytesIO(b"x"), filename="a.png")], BackgroundTasks(), request=None, user_id="u1")
    assert e.value.status_code == 400 and "a.png" in e.value.detail

def test_other_types_name_the_supported_ones():
    # notes.txt -> 400, detail contains "PDF, DOCX or image"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_documents_upload.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the two checks.
  - Error texts: `f"{name}: only PDF, DOCX or image (PNG, JPG, WEBP) files are supported"` and `f"{name}: image reading is turned off"`.
  - `accept=".pdf,.docx,.png,.jpg,.jpeg,.webp"`.
  - Mention images in the Readme dialog in both languages.
- [ ] **Step 4: Run** `uv run pytest tests/test_documents_upload.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Accept image uploads"`

### Task 6: Image agent

**Files:**
- Modify: `app/agents/docs/tools.py:13-36`, `app/agents/registry.py:9`, `tests/conftest.py`
- Create: `app/agents/image/__init__.py`, `tools.py`, `prompts.py`, `agent.py`
- Test: `tests/test_router.py`, `tests/test_orchestrator.py:163`, `tests/test_image_search.py` (new, `requires_db`)

**Interfaces:**
- Consumes: `search_with_graph`, `make_mentions` (docs agent), `IMAGE_SUFFIXES`, the `documents.image_count` column.
- Produces:
  - `search_user_chunks(user_id, query, k=TOP_K, document_ids=None, kind: str | None = None)` and `search_with_graph(user_id, query, entity="", kind: str | None = None)`. `kind` adds `{"kind": {"$eq": kind}}` under `$and` with the user filter, and is kept on the every-file retry too.
  - `image/tools.py`: `NO_IMAGES = "No matching images in the user's files."` and the `@tool async def search_images(query: str, runtime: ToolRuntime[AgentContext], entity: str = "") -> str`. It returns `NO_IMAGES` when the search returns `NO_PASSAGES`.
  - `image/agent.py`:
    - `image_files(user_id) -> list[tuple[str, str | None, int]]`: `(filename, summary, image_count)` of non-failed files with `image_count > 0`, newest first.
    - `route_info`, `build_image_agent`, `HINTS`, `SPEC`.
  - `tests/conftest.py`: an autouse fixture that patches `app.agents.image.agent.image_files` to `lambda user_id: []`, so router tests never hit the DB. Tests that need image files patch it again.

- [ ] **Step 1: Write the failing tests**

```python
async def test_image_words_and_image_files_route_to_image(monkeypatch):
    # docs: [("receipt.png", None), ("report.pdf", None)]
    # image_files: [("receipt.png", "receipt: Highlands", 1), ("report.pdf", None, 3)]
    assert obvious_route("What's on the receipt?", available) is None       # no image word, no file name
    assert obvious_route("Summarize receipt.png", available) == "image"     # image file beats docs
    assert obvious_route("What does the photo show?", available) == "image"
    assert obvious_route("Ảnh chụp này có gì?", available) == "image"
    assert obvious_route("What does the diagram in report.pdf show?", available) == "image"  # overrides docs
    assert obvious_route("Summarize report.pdf", available) == "docs"       # Review Focus 1
    assert obvious_route("how much did I pay?\n\nAttached: receipt.png", available) == "image"
    assert obvious_route("Chart AAPL vs MSFT", available) == "chart"

async def test_no_image_files_means_no_image_agent(monkeypatch):
    # image_files -> []: "What does the photo show?" is not "image"; image not in available routes

def test_image_agent_is_offered_as_a_tool():           # test_orchestrator.py, next to line 163
    assert "- image:" in lines and "`handoff` or `ask_image`" in lines

@requires_db
def test_image_search_returns_only_the_users_image_chunks():
    # add text + image chunks for u1 and an image chunk for u2 (the same words)
    # search_with_graph("u1", q, kind="image") contains only u1's "[Image 1" passage
```

- [ ] **Step 2: Run** `uv run pytest tests/test_router.py tests/test_orchestrator.py tests/test_image_search.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - `HINTS`: `\b(images?|photos?|pictures?|screenshots?|diagrams?|ảnh|hình|hình ảnh|ảnh chụp)\b`, ignoring case. Check that `test_chart_words_win_over_a_ticker_but_not_over_a_mention_of_files` still passes: "vẽ một con mèo" contains none of these words.
  - `route_info`: returns `None` when there are no files. Otherwise:
    - `prompt_note`: `"The user's images and files with images: receipt.png (receipt: Highlands), report.pdf (3 images)"`, at most 30 files;
    - `matches`: stems of the image-suffix files only;
    - `mentions`: `make_mentions` over every listed stem.
  - `IMAGE_SYSTEM` (writing-llm-document skill): answer only from `search_images` results, which are readings written when the image was uploaded, not the image itself; cite `[filename]` or `[filename, p. N]`; when the reading doesn't cover the asked detail, say so. End with `LANGUAGE_RULE` and `MEMORY_RULE`. Also write `ROUTE_DESCRIPTION` and `TOOL_DESCRIPTION`.
  - `SPEC`: as in Global Constraints, plus `no_result=lambda text: text == NO_IMAGES`.
  - Registry: `AGENTS = (DOCS, SQL, RESEARCH, CHART, IMAGE)`.
  - Add `"image reading": "image"` (a phrase from `IMAGE_SYSTEM`) to `AGENT_PHRASES` in `scripts/bench_chat.py`.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: everything passes.
- [ ] **Step 5: Commit** `git commit -m "Add the image agent"`

### Task 7: Chat attachments (API)

**Files:**
- Modify: `app/api/routes_chat.py:31-54`
- Test: `tests/test_attachments.py` (new, `requires_db`)

**Interfaces:**
- Produces:
  - `ChatRequest.attachments: list[str] = Field(default_factory=list, max_length=5)`.
  - `attachment_names(user_id: str, ids: list[str]) -> list[str]`: the file names in the order given. It raises `HTTPException(404, "Attachment not found")` for an id that is missing or belongs to someone else, and `HTTPException(409, "<file name> is still being read")` / `(409, "<file name> could not be read")` for `processing` / `failed`.
  - The graph receives `f"{body.message}\n\nAttached: {', '.join(names)}"` when there are attachments. `create_conversation` still receives `body.message`.

- [ ] **Step 1: Write the failing tests**

```python
def test_attachments_of_another_user_are_not_found():   # doc of u2, asked by u1
    with pytest.raises(HTTPException) as e: attachment_names("u1", [doc_id])
    assert e.value.status_code == 404 and "receipt" not in e.value.detail

def test_processing_attachment_is_a_conflict(): ...   # 409
def test_ready_attachments_keep_their_order(): ...    # == ["b.png", "a.png"]
```

- [ ] **Step 2: Run** `uv run pytest tests/test_attachments.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `attachment_names` (one `select` on `documents` with `id IN ids AND user_id = user_id`). In `chat`, call it through `asyncio.to_thread` before the conversation is created, so a bad attachment creates no empty conversation, and build the message text from it.
- [ ] **Step 4: Run** `uv run pytest -v`. Expected: everything passes.
- [ ] **Step 5: Commit** `git commit -m "Accept image attachments in chat"`

### Task 8: Chat attachments (UI)

**Files:**
- Modify: `app/static/index.html` (📎 button + hidden `attachInput` with the same `accept` as `fileInput`), `app/static/app.js` (`addUserMessage`, `sendMessage`, `chatForm.onsubmit`, `loadDocuments`, new attachment code next to `fileInput.onchange`), `app/static/i18n.js`, `app/static/style.css`

**Interfaces:**
- Consumes: `POST /documents` (it returns `[{id, filename, status}]`), `GET /documents` polling, `ChatRequest.attachments`.
- Produces:
  - `state.attachments: [{id, filename, status, error}]`.
  - `sendMessage(text, attachments = [])`, where `attachments` are `{id, filename}`.
  - `addUserMessage(text)` renders a trailing `\n\nAttached: a, b` as chips under the text, both live and on reload.

- [ ] **Step 1: Implement.**
  - **Attaching:** 📎, paste (`clipboardData.files`) or drop on `chatForm` uploads with `api("/documents", {method: "POST", body: form})`, pushes the returned rows into `state.attachments` and calls `loadDocuments()`.
  - **Polling:** `loadDocuments` updates the status and error of each attachment from its list and re-renders the chips. Its existing 2 s poll keeps running while anything is `processing`.
  - **Chips:** each chip shows the file name with "reading…", ✓ or the error, and has a × to remove it.
  - **Sending:** disabled while any attachment is `processing`. On submit, the ready attachments are sent and the chips cleared; failed ones are dropped.
  - **i18n keys:** `attach`, `readingImage`, `attachFailed` (EN and VI).
- [ ] **Step 2: Verify in the real app.**
  - Run `uv run uvicorn app.api.main:app --reload --loop asyncio:SelectorEventLoop`.
  - Attach a receipt photo and ask "how much did I pay?". The chip goes from reading… to ✓, the badge says Image agent, and the answer cites `[receipt.jpg]`.
  - Reload: the chip is shown under the message again.
  - Ask in a **new** chat "what was on my receipt?". This uses the graph or the file list, not the attachment.
  - Ask "is that receipt over my budget in budget.pdf?": the orchestrator calls `ask_image` and `ask_docs`.
  - Upload a scanned PDF through the Documents button: it becomes `ready` and answers cite `p. N`.
- [ ] **Step 3: Commit** `git commit -m "Attach images from the chat input"`

### Task 9: Documentation

**Files:**
- Modify: `CLAUDE.md` (writing-llm-document skill), `README.md`

- [ ] **Step 1: Update `CLAUDE.md`.**
  - Add the image agent to "What this is".
  - In the Documents section: images, `kind="image"` chunks, `image_count`, the card coming from the reading for image files, and `OPENROUTER_VISION_MODEL`.
  - In the chat flow section: `attachments` and the `Attached:` line.
  - Call counts in Notes: an image upload is 1 call; a PDF is 1 plus 1 per image read (≤ 20).
- [ ] **Step 2: Update `README.md`** with images and attachments in the feature list, and `OPENROUTER_VISION_MODEL` in setup.
- [ ] **Step 3: Run** the `select` from CLAUDE.local.md with every changed path, and `uv run pytest`. Expected: everything passes.
- [ ] **Step 4: Commit** `git commit -m "Document the image agent"`, then push the branch and open a PR to `main` once you're happy with it.
