# CLAUDE.md

Guidance for Claude Code in this repository.

## What this is

FastAPI app hosting LangChain agents (`create_agent`) behind an orchestrator, with a plain HTML/JS UI (`app/static/`, EN/VI toggle in `i18n.js`). The **orchestrator** answers general questions, hands a message to one specialist, or asks several as tools and writes the answer. Specialists: **docs**, **sql**, **chart**, **research**, each its own agent server. All LLM and embedding calls go through OpenRouter (`app/core/llm.py`). Every agent replies in the language of the user's latest message (English or Vietnamese): `LANGUAGE_RULE` in `app/core/prompts.py`.

Layout of `app/`: `core/` (config, llm, db, memory, auth, accounts, conversations, shared prompts; knows nothing about specific agents), `agents/` (one package per agent: `agent.py` with build + `SPEC`, `tools.py`, `prompts.py`; listed in `agents/registry.py`), `features/` (data an agent uses plus its API, ingestion and background jobs; listed in `features/registry.py`), `orchestration/` (router + assistant graph), `api/` (shared routes and `main.py`).

## Where the detail is

- `ARCHITECTURE.md`: overview for people. When a change alters a part, flow, table, tool or deployment step it describes, update it in the same branch.
- `DEPLOYING.md`: deploy steps.
- `.claude/rules/`: path-scoped files that load when you read a matching path; open one deliberately for its area.
  - `agents.md`: `AgentSpec`, registry, `build_agent`, adding an agent or workflow.
  - `orchestration.md`: startup, routing, fallback, orchestrator, chat stream and SSE events.
  - `team-mode.md`: round table, board, pause and resume, independent sources.
  - `remote-agents.md`: `RemoteAgent`, agent servers, auth, database logins, compose settings.
  - `memory.md`: knowledge graph, fact extraction, cross-chat history.
  - `research.md`: research workflow, citations, MCP, skills.
  - `data.md`: documents, SQL safety, charts and tables, stock data, daily update.
  - `accounts.md`: accounts, admin, user isolation.
  - `config.md`: settings, LangSmith tracing, model-call budget.
  - `deployment.md`: Dockerfile and ignore-file facts `DEPLOYING.md` lacks.
  - `changelog.md`, `version-control.md`, `testing.md`: always loaded; follow them.

## Commands

Development is on Windows with `uv`. Postgres 18 (pgvector) runs on **port 5433** in the compose `db` container (`smallie-db`), on Docker Engine inside WSL Ubuntu (no Docker Desktop). Run Docker commands through WSL: `wsl -d Ubuntu -u root --cd "/mnt/c/Users/Asus ROG/projects/smallie" -- docker <args>`. The WSL-installed Postgres (`db/setup_wsl.sh`) is legacy and disabled. No local `docker build` is needed before deploying the main app.

**Run `scripts\dev_db.ps1` before DB tests, the app or `bench_chat`.** WSL stops about 25 s after the last `wsl` command ends (measured on WSL 3.0.3), even with Docker running, and takes Postgres with it. Symptoms: DB tests skipped ("Postgres not running"), or a run that freezes in `psycopg.connect` because WSL stopped mid-run (a stack dump shows `wait_conn`). The script starts a hidden `sleep infinity` WSL session that keeps WSL up until the PC shuts down (or `wsl --shutdown`), runs `docker compose up -d db` and waits until `smallie-db` is healthy. It is safe to re-run. A one-off `wsl ... docker ps` only buys 25 s. If a run is already frozen: Ctrl+C, run the script, rerun.

**The agent servers run in the same compose project.** `docker compose up -d` (through WSL, as above) starts `redis` and `docs`/`sql`/`research`/`chart` on `127.0.0.1:2101`–`2104`, all from the one image `smallie-agent` (`Dockerfile.agent`). The main app needs them: `.env` holds `AGENT_URLS` and `SERVICE_TOKEN`, and startup fails without `AGENT_URLS`. After changing code an agent server runs, rebuild: `docker compose up -d --build`. **Turn off Cloudflare WARP and the SoftEther VPN client before Docker pulls or builds, or before using the stack.** They corrupt WSL's HTTPS traffic. Symptoms: `tls: bad record MAC` from Docker, `decryption failed or bad record mac` from curl, while Windows' own downloads stay fine.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev_db.ps1   # start Postgres, keep WSL up until shutdown
uv sync                                            # install deps into .venv
uv run uvicorn app.api.main:app --reload --loop asyncio:SelectorEventLoop
uv run pytest                                      # all tests
uv run pytest tests/test_sql.py::test_name         # single test
uv run python -m scripts.reindex                   # after changing OPENROUTER_EMBED_MODEL or EMBED_DIM (app stopped; also re-embeds graph entities)
uv run python -m scripts.build_graph               # once after deploying the graph: old memories + past chats -> graph facts, cards for old files (spends free quota; done users are skipped)
uv run python -m scripts.load_stocks               # first load / manual refresh (--full, --prices-only); the app updates daily itself
uv run python -m scripts.bench_chat                # latency and model calls per scenario on the real models (spends free quota)
```

Tests that need Postgres use the `requires_db` marker (`tests/conftest.py`) and are skipped when the DB is unreachable. A skipped DB test is not a pass: run `scripts\dev_db.ps1` and rerun. `pytest-asyncio` runs in `auto` mode. No linter or formatter is configured.

## Invariants

- **User scoping**: every DB row, vector filter, memory namespace, upload path and tool is keyed by `user_id`. Keep it in every new query or tool.
- **Windows event loop**: uvicorn and any standalone async script that touches Postgres use `asyncio.SelectorEventLoop` (psycopg's async driver fails on Proactor; see `scripts/reindex.py`).
- **DB connections survive a Postgres restart**: SQLAlchemy engines (also PGVector's, via `engine_args`) use `pool_pre_ping=True`; the LangGraph pool uses `check=AsyncConnectionPool.check_connection` plus TCP keepalives. Never use `from_conn_string`.
- **Agent servers never write app data**: server-side code runs as `agent_reader` (`.claude/rules/remote-agents.md`).
- **Agents never call each other**: only the orchestrator and the router delegate.
- **Sync DB or vector work** in async routes and tools goes through `asyncio.to_thread`.
- **Config** comes from `get_settings()`; `.env.example` documents every variable.
- **Deploy** only from `main`, after merging the PR, updating `CHANGELOG.md` and the version in `pyproject.toml` (`.claude/rules/changelog.md`), with a clean working tree.
- **`*.sh` files** keep LF line endings (they run in WSL).
