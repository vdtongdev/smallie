# Image agent — design

Date: 2026-10-19. Release: 0.4.0 (new agent). Branch: `0.4.0/image-agent`.

## Goal

Users can give smallie images, through the Documents upload button or as a chat attachment, and ask about them in any chat. Images inside uploaded PDF/DOCX files are understood too, and scanned PDFs (today rejected with "needs OCR") become searchable.

Each image is looked at **once, at ingestion**: a vision model writes a description and transcribes any text on it. That reading is chunked and embedded like any other passage in the docs RAG (pgvector), and the knowledge graph links the file to the entities in it. A dedicated **image agent** answers image questions on its own route, using that stored reading and the graph. It never sees the pixels at question time. Compound questions go through the orchestrator, which gets an `ask_image` tool.

The agent is its own package so it can grow into a full image processing agent later (editing, generation). That growth is out of scope here.

**Success:** a user uploads or attaches a receipt photo, a chart screenshot or a scanned contract, asks about it, and gets an answer that cites `[filename]` or `[filename, p. N]`, in the language of the question.

## Out of scope

- Keeping the original images or re-reading them at question time. Cloud Run's disk is ephemeral, so this would need a bucket or DB storage.
- Image editing, generation or multimodal (image) embeddings.
- Back-filling images in files uploaded before this release. Running `scripts.reindex` re-reads the files it can still find, which costs vision calls.

## Settings and limits

- New setting `OPENROUTER_VISION_MODEL`, documented in `.env.example` and set in `cloudrun.env.yaml`. When it is empty, images are off: image uploads are rejected (400), images inside PDF/DOCX are skipped, and the image agent sees no files (`route_info` returns `None`).
- Candidates: a Gemini Flash model (preferred: cheap, strong OCR including Vietnamese, reliable structured output) and Gemma 4 31B (free, takes images). A short check with 3–4 real images picks the model before the code is written. The app moves to the paid low-cost tier, so one call per image is affordable.
- Constants in code, not settings:
  - `MAX_IMAGES_PER_FILE = 20`
  - `MIN_IMAGE_SIDE = 100` px: smaller images (logos, icons, bullets) are skipped
  - `MAX_IMAGE_SIDE = 1600` px: larger images are shrunk before sending
  - `VISION_CONCURRENCY = 4`

## Ingestion (`app/features/documents/`)

### New module `images.py`

- `images_in(path) -> list[(page | None, bytes)]` yields JPEG bytes ready to send:
  - PDF: `pypdf` `page.images`, with 1-based page numbers.
  - DOCX: `docx2txt.process(path, tmpdir)` extracts the images; there are no page numbers.
  - Image files (`.png .jpg .jpeg .webp`): the file itself, with page `None`.
  - Every image goes through Pillow: it is opened, skipped if its shorter side is below `MIN_IMAGE_SIDE`, converted to RGB (PDF images can be CMYK, palette, etc.), shrunk to `MAX_IMAGE_SIDE` and saved as JPEG. Images Pillow can't open are skipped and logged. The list stops at `MAX_IMAGES_PER_FILE`.
- `read_images(images, filename, callbacks) -> list[ImageReading | None]` makes one vision call per image (base64 data URL in a `HumanMessage`), at most `VISION_CONCURRENCY` at once (`model.batch(..., return_exceptions=True)`). A failed call becomes `None` and is logged.
- `ImageReading` (Pydantic, `with_structured_output(method="function_calling")`):
  - `description`: what the image shows.
  - `text`: every piece of text on the image, transcribed exactly, in its original language. Empty when there is none.
  - `doc_type`, `summary` (≤ 200 characters) and `entities` (≤ 10): the same fields as `DocumentCard`, so an image upload needs no separate card call.
- The vision prompt lives in `app/features/documents/prompts.py` as `IMAGE_PROMPT`.

### Changes to `ingest.py` and `card.py`

- `SUPPORTED_SUFFIXES` gains `.png .jpg .jpeg .webp`, and `IMAGE_SUFFIXES` is a new set.
- `load_and_split` returns the text chunks first, then one chunk per successful reading. These chunks:
  - have metadata `kind="image"`, `filename`, `page` (when known) and `image` (1-based index in the file);
  - have content starting with `[Image N, p. P]` (or `[Image N]`), followed by the description and the transcribed text;
  - go through the same splitter, so a long transcription becomes several chunks.
- Text chunks keep their current metadata, with no `kind` key, so existing data needs no migration.
- Because the image chunks come last, the ids stay `"{document_id}:{i}"` and `chunk_ids` / `delete_document_chunks` are unchanged.
- The file fails only when it has no text **and** no image could be read. The old "Scanned/image-only PDFs need OCR" error becomes "No text or readable images found."
- New column `documents.image_count integer NOT NULL DEFAULT 0`, set with the status. It is added in `init_db` with `ADD COLUMN IF NOT EXISTS`, the same way `summary` was.
- Card:
  - **Image upload:** the card comes from the image's `ImageReading`, so `save_card` gets `f"{doc_type}: {summary}"` and the entities with no extra call.
  - **PDF/DOCX:** `write_card` runs as today, over the text and image chunks.
- Usage: the vision calls report to a `UsageCounter`, which `process_document` adds to the user's usage (`add_usage`), as `write_card` does.
- `scripts/reindex.py` keeps calling `ingest_document`, updated if its signature changes.

### Cost per upload

- Image file: 1 call.
- PDF/DOCX: 1 card call plus 1 call per image read (at most 20).

## Upload and chat attachment

### Documents upload

`upload` (`routes.py`):
- accepts the new suffixes;
- returns 400 for an image when `OPENROUTER_VISION_MODEL` is empty;
- updates the error text ("PDF, DOCX or image files").

`index.html` updates `accept=`, and the Readme dialog and i18n strings mention images in both languages.

### Chat attachment

- **UI:**
  - A 📎 button next to the chat input; pasting or dropping an image into the input works too.
  - It uploads through the existing `POST /documents`.
  - A chip shows the file name and "reading…" until the documents poll reports `ready`; on `failed` the chip shows the error and the file is not attached.
  - Send is disabled while an attachment is still processing.
  - The message is sent with the ready attachments' ids.
- **API:** `ChatRequest` gets `attachments: list[str] = []` (at most 5).
  - `chat` checks that each id is a document of the user with status `ready`, and returns 404 / 409 otherwise.
  - It then appends a line `Attached: <file name>, <file name>` to the message text before it goes to the graph. The conversation title uses the text without that line.
- **Why this needs no routing code:**
  - The file name is now in the message, so the image agent's `route_info.matches` makes the route obvious.
  - `documents_for` limits the search to that file.
  - The orchestrator's notes list the file.
- On reload, the UI renders the `Attached:` line of a user message as chips.

## Image agent (`app/agents/image/`)

- **`tools.py`:** `search_images(query, entity="")` calls `search_with_graph(user_id, query, entity, kind="image")`.
  - `search_with_graph` and `search_user_chunks` (`app/agents/docs/tools.py`) gain an optional `kind` that adds `{"kind": {"$eq": kind}}` to the filter, next to the existing `user_id` filter.
  - The docs agent passes nothing, so it still searches every chunk, image readings included.
  - When nothing matches, it returns `NO_IMAGES`.
- **`prompts.py`:**
  - `IMAGE_SYSTEM`: answer only from the stored image readings, cite `[filename]` or `[filename, p. N]` like the docs agent, and say plainly when the reading doesn't cover the asked detail. Ends with `LANGUAGE_RULE` and `MEMORY_RULE`.
  - `ROUTE_DESCRIPTION`, and `TOOL_DESCRIPTION` for `ask_image`.
- **`agent.py`:**
  - `build_image_agent()` is `build_agent(name="image_agent", ...)`.
  - `HINTS`: image(s), photo(s), picture(s), screenshot(s), diagram(s), ảnh, hình, hình ảnh, ảnh chụp. "Chart" is not included, because it stays the chart agent's word.
  - `route_info(user_id)`:
    - lists the user's non-failed files with `image_count > 0` (newest first, with their card summary);
    - returns `None` when there are none;
    - `matches` checks file-name stems and `mentions` checks file-name words, reusing `make_mentions` from the docs agent.
  - `SPEC`:
    - `route="image"`, labels "Image agent" / "Trợ lý hình ảnh";
    - `message_names=("image_agent",)`;
    - `overrides=("docs",)`, so "the photo in report.pdf" and "the image I uploaded" go to image, not docs;
    - `tool_description=TOOL_DESCRIPTION`;
    - `no_result=lambda text: text == NO_IMAGES`.
  - The SPEC is appended to `AGENTS` in `app/agents/registry.py`.
- **Not changed:** the router, orchestrator, SSE events and `main.py`. `GET /api/agents` serves the new labels.
- **Fallback:** when the image agent was picked directly and finds nothing, the orchestrator takes over, as with every directly-routed agent.

## Tests

- **`tests/test_router.py`:**
  - Image hints route to image when the user has image files.
  - Image beats docs on "the photo in report.pdf".
  - "Chart AAPL" still goes to chart.
  - With no image files the image agent is unavailable.
  - An `Attached: x.png` message routes to image.
- **`tests/test_orchestrator.py`:** `agent_lines` includes image.
- **`tests/test_ingest.py`**, with the vision call faked:
  - A PNG produces image chunks with `kind="image"`.
  - A PDF with an embedded image produces a chunk with its page number.
  - Images below `MIN_IMAGE_SIDE` are skipped.
  - The `MAX_IMAGES_PER_FILE` cap holds.
  - An image-only file where every read fails ends up `failed`.
  - `image_count` is set.
- **DB test (`requires_db`):** `search_images` returns only image chunks, and only the user's own.
- **Chat attachment:** an attachment of another user gets 404, a processing one gets 409, and a ready one adds the `Attached:` line.

## Release

Version 0.4.0 "Image agent". The changelog and `pyproject.toml` are updated at release (`.claude/rules/changelog.md`). Pillow becomes a direct dependency in `pyproject.toml`; it is already installed as a transitive one.
