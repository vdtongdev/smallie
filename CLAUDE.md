# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FastAPI app hosting two LangChain agents (`create_agent`) behind a router, with a plain HTML/JS UI (`app/static/`, chat view plus a Readme dialog opened from the header, EN/VI toggle in `i18n.js`):
- **docs**: RAG over uploaded PDF/DOCX files (pgvector), answers cite `[filename, p. N]`.
- **sql**: natural-language queries via a read-only role against the `market` schema (200 stocks from yfinance).

All LLM and embedding calls go through OpenRouter (OpenAI-compatible API, see `app/llm.py`). Both agents must reply in the language of the user's latest message (English or Vietnamese); see `LANGUAGE_RULE` in `app/prompts.py`.

## Commands

Development is on Windows with `uv`. Postgres (pgvector) runs on **port 5433**, either in WSL (`db/setup_wsl.sh`; start it with `wsl -d Ubuntu -u root service postgresql start`) or via `docker compose up -d`.

```powershell
uv sync                                            # install deps into .venv
uv run uvicorn app.api.main:app --reload --loop asyncio:SelectorEventLoop
uv run pytest                                      # all tests
uv run pytest tests/test_sql.py::test_name         # single test
uv run python -m scripts.reindex                   # after changing OPENROUTER_EMBED_MODEL or EMBED_DIM (app stopped)
uv run python -m scripts.load_stocks               # first load / manual refresh (--full, --prices-only); the app updates daily itself
gcloud run deploy smallie --source . --region asia-southeast1 --allow-unauthenticated --env-vars-file cloudrun.env.yaml --memory 1Gi --cpu 1 --no-cpu-throttling --min-instances 0 --max-instances 1 --timeout 900   # deploy
```

- `--loop asyncio:SelectorEventLoop` is required on Windows: psycopg's async driver (checkpointer/store) fails on the default Proactor loop. Any standalone async script must do the same (see `scripts/reindex.py`).
- Tests that need Postgres use the `requires_db` marker from `tests/conftest.py` and are skipped when the DB is unreachable. `pytest-asyncio` runs in `auto` mode.
- No linter/formatter is configured.

## Architecture

**Startup (`app/api/main.py` lifespan):** creates app tables (`app/db.py`, SQLAlchemy Core, `metadata.create_all`, no migrations), opens the LangGraph `AsyncPostgresSaver` + `AsyncPostgresStore` on one shared `AsyncConnectionPool` (`app/memory.py`), and builds the assistant graph once into `app.state.assistant`. The SQL agent introspects the schemas in `SCHEMAS` (`market`) (columns, FKs, comments, sample rows) at build time and bakes it into its system prompt, so schema changes need a restart.

**Routing (`app/assistant.py`):** a `StateGraph` `START → router → docs | sql → END`; both agents are compiled *without* checkpointer/store and added as subgraph nodes, so they share the parent's thread, checkpointer, store and runtime context. The router returns `{"route": ...}`: forced by `AgentContext.agent` (`/api/chat/docs|sql`), `sql` when the user has no documents (no model call), `obvious_route` when the message clearly points to one side (keywords, a known ticker, a file name; no model call), otherwise one non-streaming call with `ROUTER_SYSTEM` (file names + short transcript), parsed by `parse_route`. The previous route comes from `AIMessage.name` (`create_agent(name=...)` sets it; `AGENT_ROUTES` maps it back).

**Agent construction (`app/agent_factory.py`):** every agent gets its own tools plus `save_memory`/`search_memory`/`search_chat_history`, a `@dynamic_prompt` middleware that appends recalled memories (`MEMORIES_SECTION`), and `ModelCallLimitMiddleware` (`MAX_MODEL_CALLS` per message). `OPENROUTER_FALLBACK_MODEL` is passed to OpenRouter as `models` in `get_chat_model` (server-side fallback, no extra request); `max_retries=1` because retries also count against the free quota. Per-request data travels in `AgentContext(user_id, memories, recent_chats)`; tools read it via `ToolRuntime[AgentContext]`.

**Chat flow (`app/api/routes_chat.py`):** `POST /api/chat/{auto|docs|sql}` → creates/looks up the conversation row → `recall()` top memories for the message → `assistant.astream(stream_mode=["messages","updates"], subgraphs=True)` with `thread_id = conversation id` → converted by `_to_events(mode, chunk, namespace)` into SSE events (`conversation`, `agent`, `token`, `tool_call`, `tool_result`, `error`, `done`) consumed by `app/static/app.js`. Top-level (`namespace == ()`) updates only yield the router's `agent` event; the agent nodes' top-level updates repeat what their subgraph already streamed. Message history lives only in the LangGraph checkpointer; the `conversations` table is just the sidebar index (`kind` = mode it was started with; the sidebar lists all kinds). The UI always uses `auto`, shows an agent badge per turn, keeps the open conversation in `localStorage` (`conversation_auto`) and reopens it on reload; "New chat" is disabled while a reply streams, and a stream that ends without `done` shows a "connection lost" error.

**Memory:**
- Short-term: checkpointer, per conversation.
- Long-term: store namespace `("users", user_id, "memories")` with a semantic index (`fields=["text"]`). pgvector HNSW only supports ≤2000 dims, so larger `EMBED_DIM` switches to a flat index.
- Cross-chat (`CROSS_CHAT_HISTORY`, default on): `routes_chat` loads the last turns of the user's 3 most recent other chats from their checkpoints (`load_recent_chats`, passed as `AgentContext.recent_chats`, appended as `RECENT_CHATS_SECTION`), and after a finished turn a `BackgroundTask` indexes it under `("users", user_id, "history")` (`save_turn`) for the `search_chat_history` tool. Deleting a conversation also deletes its history items (`forget_conversation`).

**Documents:** upload (`routes_documents.py`) saves to `uploads/<user_id>/<doc_id>.<ext>` and runs `ingest_document` as a FastAPI background task (status `processing` → `ready`/`failed`). Chunks go into the `langchain_postgres` PGVector collection `documents` with deterministic ids `"{document_id}:{i}"` (used for deletion) and `user_id` in metadata.

**Accounts and user isolation:** `app/auth.py::get_current_user` is the *only* place identity is decided: the signed session cookie holds the `user_id` of the logged-in account (401 if missing or the account was deleted). People create accounts (name, username, password) via `POST /api/signup`, capped at `MAX_ACCOUNTS` (default 20, admin not counted, enforced under a Postgres advisory lock). Account rows live on the `users` table and are handled in `app/accounts.py` (scrypt hashes; passwords never leave `accounts.py`/`auth.py`). The `admin` account keeps the old shared user id `"team"` (so it owns the pre-accounts data), logs in with `APP_PASSWORD` (no stored hash; `SESSION_SECRET` is then required) and can list, reset and delete accounts (`app/api/routes_admin.py`; deletion removes checkpoints, store items, chunks and `uploads/<user_id>/`). `scripts.claim_anonymous_data` moves data of old `anon-*` users to the admin. The account's name and username reach the agents as `AgentContext.user` (`USER_SECTION`). Everything else — DB rows, vector search filter `{"user_id": {"$eq": ...}}`, memory namespace, upload dir — is keyed by `user_id`. Preserve this scoping in any new query or tool.

**SQL safety:** the SQL agent connects with `READONLY_DATABASE_URL` as `agent_readonly` (created in `db/init/03_readonly_role.sql`, extended in `04_market_schema.sql`: SELECT-only on `market`, read-only transactions, 10s timeout, no access to app tables). `execute_readonly` additionally rejects anything that isn't a single `SELECT`/`WITH` and caps rows at `SQL_MAX_ROWS`. The prompt shows schema-qualified table names. To target another database, change `READONLY_DATABASE_URL` and `SCHEMAS` in `app/sql/agent.py`.

**Stock data (`app/market/`):** `scripts.load_stocks` → `load_market()` re-applies the idempotent `db/init/04_market_schema.sql` (so existing DBs get the schema and grants), inserts placeholder `stocks` rows, refreshes profiles from `yf.Ticker(t).info` (one request per ticker, retried), then `yf.download` in batches of 50 and upserts `daily_prices` via COPY into a temp table (setting `updated_at`; `/api/market/status` reports the newest price/profile `updated_at` as "last updated" in the Readme). Incremental by default: starts `OVERLAP_DAYS` before `max(trade_date)`. It writes as the owner (`DATABASE_URL`) and can run while the app is up. yfinance's `dividendYield` is already a percentage.

**Daily update (`app/market/scheduler.py`):** the lifespan starts `run_daily()` as an asyncio task when `MARKET_UPDATE_TIME` is set (cancelled on shutdown via the exit stack). It catches up once at startup if `max(trade_date)` is older than `latest_session()`, then sleeps until `next_run()` (weekdays, `America/New_York`; needs `tzdata` on Windows). `run_update()` runs `load_market` in a thread under `update_lock()` (a Postgres advisory lock also taken by `scripts.load_stocks`, so multiple workers or a manual run never overlap). Profiles are refreshed only when older than 7 days. Failures are logged and retried 3 times, 15 min apart; the task never dies. There's no holiday calendar: on a holiday the run just upserts nothing.

## Notes

- Config is `pydantic-settings` reading `.env` (`app/config.py`, cached via `get_settings()`); `.env.example` documents every variable. SQLAlchemy URLs get the `postgresql+psycopg://` prefix via `sqlalchemy_url` properties, while psycopg/LangGraph use the raw `DATABASE_URL`.
- Sync DB/vector work is wrapped in `asyncio.to_thread` from async routes and tools.
- Every DB connection must survive a Postgres restart (WSL stops Postgres when the distro shuts down): SQLAlchemy engines (including PGVector's, via `engine_args`) use `pool_pre_ping=True`, and the LangGraph pool uses `check=AsyncConnectionPool.check_connection` plus TCP keepalives. Don't go back to `from_conn_string`, which holds one connection that never reconnects.
- Deployment: Google Cloud Run (project `smallie-review`, region `asia-southeast1`, https://smallie-976519788978.asia-southeast1.run.app) + Neon Postgres 18 (Singapore). Steps are in README "Deploying".
  - `Dockerfile`: python:3.12-slim + uv, non-root `user` (the app dir is created and chowned before `USER`, otherwise `uv sync` can't create `.venv`), listens on `$PORT`, no `--loop` flag (Linux).
  - Built by Cloud Build via `--source .`; `.gcloudignore`/`.dockerignore` keep `.env`, `.venv`, `uploads`, tests and `cloudrun.env.yaml` out. The default compute service account needs `roles/run.builder`.
  - Config comes from `cloudrun.env.yaml` (gitignored, holds secrets: `.env` values + Neon URLs + `SESSION_HTTPS_ONLY=true`), not `.env`.
  - `--no-cpu-throttling` is required: document ingestion, `save_turn` and the market scheduler run after the response is sent. `--max-instances 1` caps cost. The container disk is ephemeral, which is fine because uploads are only read during ingestion.
  - Neon: use the direct (non `-pooler`) URL, because of the advisory locks. `agent_readonly` was created by hand with a strong password (Neon rejects `'readonly'`); the DB is `smalliedb`. The first stock load was run from the PC (`DATABASE_URL=<neon> load_stocks --full`).
- `*.sh` files must keep LF line endings (they run in WSL).
- Default models are OpenRouter free tier (~20 req/min, low daily cap); one question takes 2–5 model calls plus at most 1 router call.
