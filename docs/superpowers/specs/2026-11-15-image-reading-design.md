# Images in documents (part 1: reading) — design

Date: 2026-11-15. Release: patch 0.8.1 (a feature of the docs agent). Branch: `0.8.1/image-reading`.

Supersedes the agent half of `docs/superpowers/specs/2026-10-19-image-agent-design.md` (branch `0.4.0/image-agent`, never merged): there is **no image agent**. An image is just another file for the docs agent. The ingestion ideas of that spec are kept and rebuilt on current `main`.

## Goal

Users upload images (`.png .jpg .jpeg .webp`) through Documents, and images inside their PDF/DOCX files are understood too. Scanned PDFs, rejected today with "needs OCR", become searchable. Each image is read **once, at upload**: a model describes it and transcribes every piece of text on it. The reading is stored as chunks in the existing pgvector store, so the docs agent finds it with `search_documents`, and the document card links the file to the entities in it.

Images are only read. No editing, no generation, no image embeddings, and the pixels are never looked at again at question time.

**Success:** a user uploads a receipt photo, a screenshot or a scanned contract, asks about it, and the docs agent answers from it, citing `[filename]` or `[filename, p. N]`, in the language of the question.

## Out of scope (part 2, its own spec)

- Attaching images in the chat box (paperclip, paste, drop) and tagging the message with the file.
- Showing images in chat (thumbnail, pop-up). This needs permanent storage of the original (Neon `bytea` or a GCS bucket), because the Cloud Run disk is ephemeral and uploads are only kept while they are indexed.

Also out of scope: re-reading files uploaded before this release. `scripts.reindex` re-reads any file it still has, which costs calls.

## 1. Settings and choosing the model

The model that reads images is the user's own, on their saved endpoint and key.

**Stored**: `model_settings` gets two columns, added in `init_db` with `ADD COLUMN IF NOT EXISTS`:
- `image_model text NULL`: the fallback image model, a 4th slot on the same `base_url` and API key as `model`, `small_model`, `strong_model`. Empty means no fallback.
- `image_model_first boolean NOT NULL DEFAULT false`: when true, images go straight to the image model and the chat model is never tried.

**Backend**
- `ModelSettings` (`app/core/model_settings.py`) gets `image_model: str | None = None` and `image_model_first: bool = False`; `load_settings` and `save_settings` carry them.
- `GET /model-settings` returns both. `PUT /model-settings` accepts both and warns about an `image_model` the server does not list, as it does for `small_model` and `strong_model` (`app/api/routes_model_settings.py`).
- New `image_models(user_id) -> list[ChatOpenAI]` in `app/core/llm.py`, the models to try in order, non-streaming, built with `_build_chat_model`:
  - `image_model_first` and slot set → `[image]`
  - slot set → `[standard, image]`
  - slot empty → `[standard]` (the toggle is ignored)
  - no model settings → raises `NoModelSettings`

**Settings dialog** (`frontend/src/features/settings/`)
- A 4th model select, "Image model (fallback)", under the strong model, with a blank "None" option.
- A checkbox under it, "Always read images with the image model", disabled while the slot is empty.
- One help line: "Images are read once, at upload. If your chat model can't take images, the image model reads them."
- EN and VI strings in `frontend/src/i18n/`.

## 2. Reading images at upload

`process_document` (`app/features/documents/card.py`) does, in order:

1. **Text chunks**, as today (`load_and_split`, in `asyncio.to_thread`).
2. **Images**: `images_in(path)` in a new module `app/features/documents/images.py` (in `asyncio.to_thread`) returns `(page | None, jpeg_bytes)`:
   - image file: the file itself, page `None`;
   - PDF: `pypdf` `page.images`, 1-based page. A scanned PDF is one full-page image per page, so the same code covers it;
   - DOCX: `docx2txt.process(path, tmpdir)` extracts the images, no page numbers.
   - Each image goes through Pillow: skipped if its shorter side is below `MIN_IMAGE_SIDE = 100` px; converted to RGB (PDF images can be CMYK or palette); shrunk to `MAX_IMAGE_SIDE = 1600` px; saved as JPEG. Images Pillow can't open are skipped and logged. The list stops at `MAX_IMAGES_PER_FILE = 20`.
3. **Readings**: `read_images(user_id, images, callbacks) -> list[str | None]` in `images.py`, one call per image, with the models from `image_models()`:
   - The **first image goes alone** to the first model. If that call fails and `fallback_category(exc)` (`app/agents/factory.py`) is `unsupported` or `model_not_found`, and there is a next model, the first image is retried on it and **the switch holds for the rest of the file**. At most one failed call per file.
   - The other images run on the chosen model, `VISION_CONCURRENCY = 4` at a time (`batch(..., return_exceptions=True)`). A failed image becomes `None` and is logged; the others are kept.
   - If the first image fails on the last model in the list, every image of the file is `None` (unread).
   - The message is a `HumanMessage` with the `IMAGE_PROMPT` text and the image as a base64 `data:image/jpeg` URL. `IMAGE_PROMPT` lives in `app/features/documents/prompts.py`: describe what the image shows, then transcribe every piece of text on it exactly, in its original language. The reply is **plain text**, not structured output.
   - Calls are counted in a `UsageCounter`; `process_document` adds it to the user's usage with `add_usage`, as `write_card` does.
4. **Image chunks**: each reading becomes a `Document` whose content starts with `[Image N, p. P]` (or `[Image N]` without a page), followed by the reading, with metadata `filename`, `kind="image"`, `image` (1-based index in the file) and `page` when known. They go through the same splitter and come **after** the text chunks, so ids stay `"{document_id}:{i}"` and `chunk_ids` / `delete_document_chunks` are unchanged. Text chunks keep their current metadata (no `kind`), so existing data needs no migration.
5. **Index** all chunks (`to_thread`), then **write the card** (section 3).

**Outcome on the file row**: `documents` gets `image_count integer NOT NULL DEFAULT 0` (images read) and `images_unread integer NOT NULL DEFAULT 0` (images found but not read), added in `init_db`.
- The file fails only when it has no text **and** no image was read. The message "No text found. Scanned/image-only PDFs need OCR…" becomes "No text or readable images found."
- Images unread because the chat model can't take images and no image model is set leave the file `ready`, with `images_unread` set.
- `GET /documents` returns `image_count` and `images_unread`.
- `ingest_document` is split so the sync parts (text chunks, indexing, status) stay in `to_thread` and the reading runs async in `process_document`. `scripts/reindex.py` is updated to the new entry point, so a reindex re-reads images too.

**Cost per upload** (the user's calls; calls per chat message are unchanged):
- image file: 1 reading + 1 card;
- PDF/DOCX: 1 card + up to 20 readings, + 1 failed call when the chat model can't take images.

**Limit**: the fallback only fires when the server returns an error (OpenRouter returns 404 "no endpoints support image input"). A server that silently drops the image (some Ollama or llama.cpp setups) is not detected, and that model's reading is made up. The user can avoid it with "Always read images with the image model".

## 3. Document card and memory

Images get the same memory integration as other files through the document card (`write_card` → `save_card` in `app/core/graph.py`): a summary on `documents.summary`, a card vector for `_search_by_cards`, a file node in the knowledge graph with `mentions` edges to the entities, and the summary in the router's and orchestrator's notes (`route_info`).

The card reads the opening of the file (`CARD_CHARS = 6000`). Image chunks come after the text, so in a long PDF the readings would be cut off and entities seen only in images would never reach the graph. The card input becomes:
- the text chunks, up to `CARD_TEXT_CHARS = 4500` characters, then
- the image readings, up to the rest of `CARD_CHARS`.

When there is less text, the readings get the space it leaves (an image file or a scanned PDF gets all 6,000). Still one call.

`scripts/build_graph.py` (cards for files from before the graph) is unchanged: files from before this release have no image chunks.

## 4. Docs agent, upload panel, deploy

**Docs agent** (`app/agents/docs/`): no new tool; `search_documents` already searches every chunk, readings included.
- `ROUTE_DESCRIPTION`, `TOOL_DESCRIPTION`, `REVIEW` and the `search_documents` docstring say "(PDF, DOCX or image)" instead of "(PDF/DOCX)".
- `AGENTS.md` gets one rule: a passage starting with `[Image N]` is a reading of an image, not the image; cite it as `[filename]` or `[filename, p. N]`; when the reading does not cover the asked detail, say so plainly.
- `HINTS` gains `images?`, `photos?`, `pictures?`, `screenshots?`, `scans?`, `ảnh`, `hình ảnh`, `ảnh chụp`. "Chart", "graph" and "diagram" stay out (the chart agent's words).
- `route_info` is unchanged: image files are listed by name and card summary like any file.
- Edits to these prompt files load the `writing-llm-document` skill first.

**Upload** (`app/features/documents/routes.py`, `frontend/src/features/documents/DocumentsPanel.tsx`):
- `SUPPORTED_SUFFIXES` gains `.png .jpg .jpeg .webp`; the file input's `accept=` too.
- The 400 message becomes "only PDF, DOCX or image files are supported".
- An image file is refused (400) when the user has no model settings: it could never be read.
- A ready file shows "· N images" when `image_count > 0`, and "· N images not read: set an image model in Settings" when `images_unread > 0`. EN and VI strings.
- The Readme dialog mentions images, in both languages.

**Deploy and docs**
- Pillow becomes a direct dependency in `pyproject.toml` (`uv lock`).
- Ingestion runs in the main app. The agent servers only change through the docs prompts: rebuild with `docker compose up -d --build` locally, redeploy the docs Cloud Run service.
- Update `ARCHITECTURE.md` (upload and ingestion, docs agent, model settings), `.claude/rules/data.md`, and `DEPLOYING.md` if a step changes.

## 5. Tests

Written from this spec. Model calls are faked; no real calls.

- **Settings** (`tests/test_model_settings.py`, `tests/test_model_settings_api.py`): `image_model` and `image_model_first` round-trip; an image model the server does not list gets the warning; clearing the slot stores NULL.
- **`image_models()`** (`tests/test_llm.py`): the four cases of section 1.
- **`images_in`** (new `tests/test_images.py`): a PNG gives one image; an image embedded in a PDF keeps its page; a scanned (image-only) PDF gives one image per page; images below 100 px are skipped; the 20-image cap holds; a CMYK image is converted to RGB JPEG; images above 1600 px are shrunk.
- **`read_images`**:
  - chat model fails `unsupported` on image 1 → exactly one failed call, image 1 and every other image read by the image model;
  - chat model works → the image model is never called;
  - slot empty and chat model fails → every image unread;
  - `image_model_first` → the chat model is never called;
  - one image fails inside the batch → only that one is unread;
  - an error that is not a fallback category (bad key) → no switch.
- **Ingestion**:
  - image-only file with no reading → `failed` with "No text or readable images found.";
  - text plus unread images → `ready`, `images_unread` set, `image_count` 0;
  - image chunks come after text chunks, with `kind="image"`, `image`, `page` and the `[Image N, p. P]` prefix;
  - text chunks have no `kind` key.
- **Card**: a PDF whose text fills the opening still sends its image reading to the card; an image file sends its whole reading.
- **DB (`requires_db`)**: `search_documents` returns a user's image readings to that user only; an entity found only in an image reading gets a `mentions` edge, and `documents_for(user_id, query, entity)` returns that file.
- **Routing** (`tests/test_router.py`): "what's in the photo" goes to docs when the user has files; "chart AAPL" still goes to chart.
- **Upload route**: image suffixes accepted; an image upload without model settings gets 400.
- **Frontend** (`npm test`): the Settings image select and the checkbox disabled while it is empty; the Documents panel shows the image count and the unread note.
- **Real check before merge**: upload 3–4 real images (a receipt, a screenshot, a Vietnamese scan, a PDF with a chart) with a chat model that can't take images and an image model set; record calls and cost per upload in the PR.

## Release

Patch `0.8.1`, changelog entry "**Images in documents**", following `.claude/rules/changelog.md` at deploy time.
