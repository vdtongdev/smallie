# Plan: finding the right file in document search

Spec: `docs/superpowers/specs/2026-10-27-file-search-design.md` (read it first; it is the source of truth).
Branch: `0.5.3/file-search` (already created; commit each task on it, don't push).
Before DB tests: `powershell -ExecutionPolicy Bypass -File scripts\dev_db.ps1`. A skipped DB test is not a pass.

Tasks 1 to 4 touch overlapping files and run in order. Task 5 (docs) is independent of them.

## Task 1: file names scope the search (A)

Agent: implementer

- Files: `app/core/graph.py` (`_user_files`, `save_facts`, `documents_for`, a stem helper), `tests/test_graph.py`.
- Regression test first: move `test_*` from the untracked `tests/test_docs_file_name_repro.py` into `tests/test_graph.py` (reuse that file's fixtures; drop throwaway lines), then delete the repro file. It must fail before the fix.
- Add tests (spec "Testing" 2, 3, 4): name only in the question ("tell me about the portfolio summary doc", entity empty) scopes to that file; two uploads with the same name both returned; a stem shorter than `MIN_TEXT_MATCH` doesn't scope.
- Implement spec section A.
- Acceptance: the new tests pass; `uv run pytest tests/test_graph.py tests/test_agent_reader.py -q` green; full `uv run pytest -q` green.
- Commit: `fix: naming an uploaded file limits the document search to it`.

## Task 2: card vectors stored (B, storage)

Agent: implementer

- Files: `app/core/db.py` (`documents.card_embedding`, `init_db` ALTER), `app/core/graph.py` (`save_card`), `tests/test_graph.py`.
- Test first (spec "Testing" 7): `save_card` writes `card_embedding`, and card text plus entities go in one `embed_documents` request. Card text `"<filename>: <summary>"`, `"<filename>"` with no summary.
- Acceptance: test passes; full suite green; `init_db` runs twice on an existing DB without error.
- Commit: `feat: store a vector of each document card`.

## Task 3: card ranking in the search (B, search)

Agent: implementer

- Files: `app/agents/docs/tools.py` (`search_with_graph`, maybe a helper in `app/core/graph.py` for ranking files by `card_embedding`), tests in `tests/test_graph.py` or a docs-tools test file next to the existing ones.
- Tests first (spec "Testing" 5, 6): a one-passage file whose card is nearest appears while a large file fills the unscoped top `TOP_K`; with no card vectors the result equals today's unscoped search; the query is embedded once per search (count `embed_query` calls).
- Implement spec "Search" steps 1 to 6 (`CARD_FILES = 3`, `CARD_SLOTS = 3`, reserved slots, not a distance merge; filter by `user_id` and `status != 'failed'`). Sync DB work stays inside the existing `asyncio.to_thread`.
- Acceptance: tests pass; full suite green.
- Commit: `feat: rank files by their card before searching passages`.

## Task 4: backfill and reindex

Agent: implementer

- Files: `scripts/build_graph.py` (a "Card vectors" step: `ready` documents with null `card_embedding`, batches of 64, embeddings only), `scripts/reindex.py` (resize `documents.card_embedding` `USING NULL` and re-embed every card, alongside `reembed_graph`).
- Keep the scripts' existing event-loop setup (Windows `SelectorEventLoop`).
- Acceptance: run the backfill against the dev DB (embedding calls only; the dev DB has a few documents) and show `SELECT count(*) FROM documents WHERE status='ready' AND card_embedding IS NULL` = 0 afterwards. Don't run `scripts.reindex` (it re-embeds every chunk); show it imports and that its new function is called from `main`. Full suite green.
- Commit: `feat: backfill and reindex document card vectors`.

## Task 5: docs

Agent: drafter

- Files: `ARCHITECTURE.md` (document search section near "`search_documents(query, entity)` searches the user's chunks"; the documents table), `.claude/rules/data.md` (the **Documents** paragraph). Load the `writing-llm-document` skill before editing `.claude/rules/data.md`.
- Describe: a named file (stem, no extension) scopes the search to every file of that name; otherwise graph scoping; otherwise the top 3 files by card vector get 3 reserved passage slots, the rest from the unscoped search; `documents.card_embedding` holds "filename: summary", written by `save_card`, backfilled by `scripts.build_graph`, resized by `scripts.reindex`.
- Not `CHANGELOG.md` (done at release).
- Commit: `docs: document search by file name and card`.

## Finish

Verifier reviews the branch diff against the spec and runs the full suite plus the regression test on the base commit (must fail there) and on the branch head (must pass).
