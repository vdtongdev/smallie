# Images in chat and the Documents pop-up (part 2) — design

Date: 2026-11-16. Release: patch 0.8.4 (small features of the docs agent's files). Branch: `0.8.4/chat-images`.

Part 1 (`docs/superpowers/specs/2026-11-15-image-reading-design.md`, released in 0.8.1–0.8.3) made images readable: an uploaded image is read once at upload by the user's chat model (image model as fallback) and stored as text chunks the docs agent searches. Part 2 lets the user attach an image straight from the chat and see it in the conversation, and replaces the upload strip above the chat with a Documents pop-up.

## Intent

The user wants to drop a photo into the chat, ask about it in the same message, and see the image in the conversation like any chat app, including after a reload. The upload strip above the chat grows with every file and takes space from the chat; the file list moves into a pop-up.

**Success**
- Paste, drop or pick an image in the chat box, wait for "reading…" to finish, send a question, and the docs agent answers from that image. The message shows the thumbnail; clicking it opens the image. Reloading the conversation shows the same.
- A folder button opens the Documents pop-up: every file with its status, chunks and images on the left, drag-and-drop or an Upload button on the right.

## Decisions taken with the user

1. **An attached image is a normal Documents file.** It uploads through `POST /api/documents`, is read once like any upload, appears in the pop-up's list, and any later chat can ask about it.
2. **Sending waits for the reading.** The attachment shows as a chip with a thumbnail and "reading…"; Send is disabled until every attachment is ready. If reading fails, the chip shows the error and the image is not attached.
3. **The message is tagged with the file**, so the docs agent searches that file first, and the thumbnail shows in the user's message, also after a reload. Clicking it opens the full image.
4. **Deleting the file in the pop-up removes its image everywhere.** Old messages show "image deleted" in place of the thumbnail.
5. **Storage: approach A** (below): keep the shrunk JPEG part 1 already makes, plus a thumbnail, in Postgres.
6. **Documents pop-up**: a folder button replaces the upload strip above the chat and opens a pop-up split in two: left, all uploaded files with their information; right, drag-and-drop or an Upload button in the middle.
7. **No file size** in the pop-up: it is not stored today and is not added.

## Non-goals

- Showing images that sit inside PDF/DOCX files, or scanned PDF pages, in the chat. Only uploaded image files get a stored copy.
- Full-resolution originals. The stored copy is the ≤1600 px JPEG.
- A preview for images uploaded before this release: they were never kept. They show "no preview" (their readings still work).
- Attaching PDF/DOCX in the chat box. Documents go through the pop-up.
- Image editing or generation; showing images the assistant produced.
- File sizes in the list.

## Anti-goals (failures even if it "works")

- Another user can load someone's image (every image route is scoped by `user_id`).
- The agent is asked about an attachment whose reading is not finished, and answers "I can't see images".
- The stored images quietly fill the free Neon database (keep the copy small; no originals).
- More model calls per chat message.

## Constraints

- Cloud Run's disk is ephemeral: anything shown later must live in Postgres (Neon, 0.5 GB free tier shared with everything else).
- Invariants from `CLAUDE.md`: every row and route keyed by `user_id`; sync DB work from async code via `asyncio.to_thread`; no new engines; config from `get_settings()`.
- Agent servers run as `agent_reader` and must not get access to the image table (they don't need it). `grant_reader` (`app/core/db.py`) must not list it.
- `MAX_UPLOAD_MB` (25) and the image suffixes of part 1 (`.png .jpg .jpeg .webp`) apply to attachments too.
- Docs (CHANGELOG, version 0.8.4 with `uv.lock`, ARCHITECTURE, DEPLOYING, README, `.claude/rules/data.md`) are updated in the branch before the PR (`.claude/rules/version-control.md`).

## Approaches considered

- **A. Shrunk copy in Postgres (chosen).** Keep the ≤1600 px JPEG that part 1 already produces for the model, plus a ~256 px thumbnail, in a new table deleted with the file. About 150–300 KB per image (500 photos ≈ 100 MB). No new infrastructure; backs up with the database. Only image files are kept.
- **B. Google Cloud Storage bucket.** Full originals, near-free, but a bucket, service permissions, a local stand-in and new deploy steps. Only worth it for full resolution or thousands of images.
- **C. Original file in Postgres.** Simplest code, but 3–8 MB per phone photo fills the free tier in about 100 photos. Ruled out.

## Design

### 1. Stored images (backend)

- New table `document_images` in `app/core/db.py`: `document_id` (primary key, FK `documents.id` `ON DELETE CASCADE`), `user_id` (FK `users.id` `ON DELETE CASCADE`, indexed), `image bytea NOT NULL` (the ≤1600 px JPEG), `thumb bytea NOT NULL` (longest side 256 px, JPEG), `created_at`. Created by `metadata.create_all` in `init_db` like the other tables.
- At upload (`index_file` / `process_document`, `app/features/documents/card.py`): for an **image file** (suffix in `IMAGE_SUFFIXES`) whose `images_in` returned an image, store that JPEG and a thumbnail made from it (Pillow), in `asyncio.to_thread`. Store it when the file ends `ready`; a failed file stores nothing. PDF/DOCX store nothing.
- Deleting a document (`DELETE /api/documents/{id}`) and deleting an account remove the row through the cascade.
- New route `GET /api/documents/{id}/image?size=thumb|full` (default `full`): returns `image/jpeg` for the caller's own document; 404 when the document is not theirs, does not exist, or has no stored image. Header `Cache-Control: private, max-age=86400`.
- `GET /api/documents` adds `has_image: bool` per file (a stored image exists).

### 2. Chat attachments (backend)

- `ChatRequest` gains `attachments: list[str] = []` (document ids, at most 5). `message` may be empty when `attachments` is not empty; otherwise it stays required (1–8000 chars).
- `chat` checks each id: a document of this user (404 otherwise) with status `ready` (409 otherwise). Duplicates are dropped.
- The text that goes to the graph is the user's text plus a last line `Attached: <filename>, <filename>`, so the models know what was attached. When the text is empty, the message is just that line.
- **The docs agent searches exactly the attached files (user's decision).** File names are not enough: two uploads can share a name (`IMG_0001.jpg`), and names under 4 characters never match (`documents_for`, `MIN_TEXT_MATCH`). So the attached document ids travel with the request:
  - `AgentContext` (`app/core/memory.py`) gains `attached_documents: list[str] = []`, set by the chat route for that turn.
  - It reaches the docs agent however it runs: directly, through the orchestrator's `ask_docs`/handoff, in team mode, and on the remote agent servers (`RemoteAgent` passes the context; check it serialises the new field).
  - `search_with_graph` (`app/agents/docs/tools.py`): when `attached_documents` is not empty, search those ids first (`search_user_chunks(..., document_ids=attached)`); the existing name/entity/card logic only runs when that finds nothing. The prefetch added in 0.8.3 follows the same rule. Ids are always filtered by `user_id`.
  - Later turns of the same chat don't carry the ids; follow-ups rely on the file name in the earlier `Attached:` line, as today.
- **Routing (my call):** a message with attachments counts as naming the docs agent's files (`route_info.matches` is true), so it goes straight to docs. If another agent's hints also match, the existing rule sends it to the orchestrator, which gets the same text and context. No extra model call.
- **Answering a paused team question (my call):** the paperclip is hidden and `attachments` refused (400) while the chat awaits an answer to a team question (`Command(resume=...)` path).
- **Memory (my call):** `save_turn` and fact extraction get the user's text plus the `Attached:` line, so cross-chat history knows which file was meant.
- **An image file with no stored copy** (its only image was under 100 px, or Pillow could not open it): treated like "no preview" in messages and the pop-up (my call).
- **Persistence check for the builder:** confirm the checkpointer keeps `additional_kwargs` on the user's `HumanMessage` through the orchestrator and team graphs. If it doesn't, store the attachments in a small table keyed by conversation and turn instead; the API shape stays the same.
- The attachments (`[{id, filename}]`) are saved with the user's message so a reload can show them: in the `HumanMessage`'s `additional_kwargs["attachments"]` (the checkpointer keeps it). The conversation title is made from the user's text, without the `Attached:` line; with no text, from the file names.
- `GET /api/conversations/{id}/messages`: a user message returns its `content` **without** the `Attached:` line and a new field `attachments: [{id, filename, deleted: bool}]`. `deleted` is true when the document no longer exists for this user (one query per conversation, not per message).
- Team mode and the orchestrator get the same message text; nothing else changes in routing.

### 3. Chat box (frontend, `frontend/src/features/chat/Composer.tsx`)

- A paperclip button left of the text box opens a file picker for image files (`accept=".png,.jpg,.jpeg,.webp"`, multiple). Pasting an image into the text box and dropping an image onto the chat box attach it too. Non-image files dropped or pasted are refused with a short message pointing to the Documents pop-up.
- Each attachment uploads at once through `POST /api/documents` and shows as a chip above the text box: a thumbnail made locally from the file (object URL), the file name, and "reading…". The chip follows the file's status by polling `GET /api/documents` (same 2 s interval as the Documents list) until `ready` or `failed`.
  - `ready`: the chip shows the thumbnail only.
  - `failed`: the chip shows the error and is not sent; the user can remove it.
  - A ready file with `images_unread > 0` (no model could read it): the chip shows "not read: set an image model in Settings" and is not sent.
- The ✕ on a chip removes it from the message only; the file stays in Documents.
- Send is disabled while any chip is reading. At most 5 chips; more are refused with a message.
- Sending posts `attachments` with the ready ids and clears the chips.

### 4. Messages (frontend, `UserMessage.tsx` and the message list)

- A user message with attachments shows the thumbnails (from `/api/documents/{id}/image?size=thumb`) above its text, right-aligned like the bubble.
- Clicking a thumbnail opens a dialog with the full image (`size=full`) and the file name.
- `deleted: true`: a small placeholder "image deleted" with the file name. A file without a stored image (uploaded before 0.8.4): "no preview" with the file name.
- The message just sent shows its thumbnails immediately (from the chips), before any reload.

### 5. Documents pop-up (frontend, replaces `DocumentsPanel` above the chat)

- The upload strip above the chat (`Shell.tsx` renders `DocumentsPanel`) is replaced by a folder button with the file count (e.g. "📁 5"), in the same place.
- The button opens a dialog split into two areas:
  - **Left:** every uploaded file, newest first, scrolling: file name, status ("processing…", "ready", "failed" with its error), chunk count, image count, the "N images not read: set an image model in Settings" note, and the ✕ delete with its confirm step (today's behaviour). Image files with a stored image show a small thumbnail. No size.
  - **Right:** a drop zone with an **Upload files** button in the middle and the accepted types (PDF, DOCX, PNG, JPG, WEBP) and the per-file limit.
- The list refreshes every 2 s while a file is processing (today's behaviour), and after upload and delete.
- On a narrow screen the right (upload) area stacks above the list.
- The existing EN/VI strings move with it; new strings get both languages.

## Cost

No new model calls. Each image upload costs what part 1 costs (1 reading + 1 card). Storage: about 150–300 KB per image file in Postgres.

## Tests (from this design)

- Backend: image row stored for an image file and not for a PDF/DOCX or a failed file; thumbnail longest side 256 px; image route 200 for the owner, 404 for another user, a missing file, or no stored image; delete cascades; `has_image` in the list; `ChatRequest` with attachments (404 other user's, 409 not ready, empty text allowed only with attachments, more than 5 refused); the graph receives the `Attached:` line; the docs search uses exactly the attached ids (two files with the same name, and a 3-character name), also through the orchestrator, team mode and a remote agent's context; an attached message routes to docs; attachments refused while a team question is pending; messages API strips the line, returns `attachments`, marks `deleted`; title without the line; `agent_reader` has no grant on `document_images` (`requires_db`, `make_user` users only).
- Frontend: paperclip, paste and drop attach; chip states (reading, ready, failed, not read); Send disabled while reading; 5-chip cap; ✕ detaches only; message thumbnails, dialog, "image deleted" and "no preview"; folder button with count opens the pop-up; left list fields; drop zone and Upload button; narrow layout stacks.
- Real check before merge: attach a photo in the chat on the local app, ask about it, reload, delete it in the pop-up, reload again.

## Left to the builder

Component structure and names, how polling is shared between the chips and the pop-up, JPEG quality of the thumbnail, Vietnamese wording, icons, exact dialog sizes, and whether the thumbnail in the pop-up is shown for every image file or only on hover.
