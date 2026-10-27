# Finding the right file in document search

Date: 2026-10-27. Branch: `0.5.3/file-search`. Status: approved design.

## Problem

The user asked "tell me about the portfolio summary doc" in a 1v1 chat. The docs agent ran `search_documents`, and the search returned only passages from a 2151-chunk book. The uploaded "Portfolio summary.docx" is one 557-character chunk (a brokerage screenshot: "Brokerage account €333,21 ▲ 6,88% Cash balance ..."). Neither "portfolio" nor "summary" appears in it.

Causes (diagnosis 2026-10-27, repro `tests/test_docs_file_name_repro.py`):

1. `documents_for` (`app/core/graph.py`) scopes the search to a file only when `entity` equals the whole normalized file name including the extension (`'portfolio summary.docx'`), or when a graph entity links to the file. "portfolio summary" matches neither, so the search runs over all files.
2. The unscoped top-6 search is dominated by a large file. The small file's chunk ranks between 7th and 50th.
3. `_user_files` maps name -> one id, so of two uploads with the same name only one is kept.

## Goal

- Naming a file (without its extension, in the question or in `entity`) limits the search to that file, every copy of it.
- When no file is named or linked, a small file whose content matches the question by meaning is not crowded out of the results by a large file.
- No extra chat-model or embedding calls per question; no extra calls per upload.

Out of scope: keyword (full-text) search, a score threshold for card matches, prompt changes, team mode.

## Design

### A. File names scope the search (`app/core/graph.py`)

- `_user_files` returns `dict[str, list[str]]`: normalized file name -> every document id with that name. Callers (`save_facts`, `documents_for`) are updated; `save_facts` keeps resolving a name to one file node (the first id).
- New helper: the normalized stem of a file name (extension stripped, `normalize`d).
- `documents_for(user_id, query, entity)` order:
  1. `entity` equals a file's normalized name or stem: return that file's ids.
  2. A file stem of at least `MIN_TEXT_MATCH` characters appears in `query` as a whole phrase (the same `(?<!\w)...(?!\w)` match used for entity names): return the ids of every file whose stem appears.
  3. Existing graph steps (entity in `entity`, then known entity names in `query`).
  4. `[]`.

### B. File cards ranked first (`documents.card_embedding`)

Storage:

- New column `documents.card_embedding vector(EMBED_DIM)`, nullable. Declared on the `documents` table in `app/core/db.py`, added to existing databases in `init_db` with `ALTER TABLE documents ADD COLUMN IF NOT EXISTS card_embedding vector(<dim>)`.
- Card text: `"<filename>: <summary>"`, or `"<filename>"` when there is no summary.
- `save_card` embeds the card text in the same `embed_documents` request as the card's entities (one request, as today) and writes it with the summary.
- `agent_reader` already has `SELECT` on `documents` (`READER_TABLES`); a table grant covers new columns.

Search (`search_with_graph` in `app/agents/docs/tools.py`), when step A and the graph give no scope:

1. Embed the query once (`embed_query`) and reuse the vector for every step below (`similarity_search_by_vector`), so the call count per search is unchanged.
2. Card ranking: the user's `CARD_FILES = 3` files with a non-null `card_embedding`, ordered by cosine distance to the query vector, `status != 'failed'`.
3. Passages from those files (filter `document_id $in`), k = `TOP_K`.
4. Passages from every file, k = `TOP_K`.
5. Result: the best `CARD_SLOTS = 3` passages of step 3, then step 4's passages in order, skipping duplicates, up to `TOP_K`. Reserved slots, not a merge by distance: merged by distance the large file wins again.
6. When no file has a card vector, the result is step 4 alone (today's behaviour).

When A or the graph gives a scope, the search stays as today: scoped first, unscoped when the scope has no passages.

Backfill and reindex:

- `scripts/build_graph.py`: a step "Card vectors" embeds the card text of every `ready` document whose `card_embedding` is null, in batches of 64. Embedding calls only.
- `scripts/reindex.py`: when the embedding model or `EMBED_DIM` changes, resize `documents.card_embedding` (`USING NULL`) and re-embed the card text of every document, as `reembed_graph` does for entities.

## Testing

All with real Postgres (`requires_db`), fake embeddings and a fake vector store that records filters, reusing the fixtures in `tests/test_graph.py`.

1. Keep the repro: `entity` "Portfolio summary" scopes the search to that file's id.
2. The name only in the question ("tell me about the portfolio summary doc", `entity` empty) scopes the search to that file.
3. Two uploads with the same name: both ids are returned.
4. A stem shorter than `MIN_TEXT_MATCH` does not scope the search.
5. B: a one-passage file whose card is nearest to the query appears in the results while a large file fills the unscoped top `TOP_K`.
6. B: no card vectors -> the result equals the unscoped search.
7. `save_card` writes `card_embedding` and makes one embedding request for card text plus entities.

## Docs

- `ARCHITECTURE.md`: the document search flow (name scope, card ranking) and the new column.
- `.claude/rules/data.md`: the search order, if it describes document search.
- `CHANGELOG.md` at release (0.5.3): "Fix: a named uploaded file not found by the docs agent", with the backfill step for existing files.
