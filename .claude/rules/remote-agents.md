---
paths:
  - "app/agents/remote.py"
  - "app/agents/server*.py"
  - "langgraph.json"
  - "Dockerfile.agent"
  - "docker-compose.yml"
  - "db/init/**"
  - "app/orchestration/graph.py"
  - "app/core/db.py"
  - "app/core/usage.py"
  - "tests/conftest.py"
---

How the main app calls the specialists as separate agent servers.

**Remote agents (`app/agents/remote.py`, `app/agents/server.py`; design in `docs/superpowers/specs/2026-10-20-remote-agents-design.md`):** each specialist runs as its own LangGraph Agent Server. The main app never imports a specialist's graph; it calls it through `RemoteAgent`.
- **The call.** `build_assistant` builds one `RemoteAgent` per spec from `AGENT_URLS` (a missing route fails at startup; tests pass `agents=local_agents()` from `tests/conftest.py`). `RemoteAgent` wraps `RemoteGraph("agent", ...)` with its own client (`make_client`: no LangSmith key sent, only connection-level retries, `AGENT_TIMEOUT_S` plus an `asyncio.timeout` cap).
- **What it sends and keeps.** It sends `asdict(AgentContext)` in the body, `x-service-token` and `x-smallie-user-id`, and drops only `thread_id`, so the run is stateless, with `on_disconnect="cancel"` (a call cut off by the timeout or a closed chat stops on the server instead of spending uncounted calls). On Cloud Run (`K_SERVICE` set) it also sends `Authorization: Bearer` from `id_token(url)` (metadata server, cached until the token's `exp` minus 5 min). Keep `checkpoint_ns`: RemoteGraph uses it to prefix streamed chunks with the parent namespace, and `_to_events` relies on that. Remote JSON becomes messages via `as_message`; `_to_events` skips unknown types. Only `messages`, `findings` and `tools` (the server's tool-call list, see **Team mode**) reach the parent state, so a remote key can't steer routing.
- **Errors and the budget.** Remote `RateLimitError`/`AuthenticationError` are raised again as `openai` errors. Before each call, `RemoteAgent` raises `CallBudgetExceeded` once the request's `UsageCounter.model_calls` reaches `MAX_MESSAGE_MODEL_CALLS`. That is a soft backstop on top of the per-agent caps: parallel asks in one step can overshoot it. A team request (`context.agent == TEAM_MODE`) uses `TEAM_MAX_MESSAGE_MODEL_CALLS` instead.
- **The server side.** `server.py`'s `make_graph` (graph `agent` in `langgraph.json`; langgraph-api calls it on every run, so it builds once under a lock) serves the spec named by `AGENT_ROUTE` behind a `run` node (`RUN_NODE`). It runs `prepare`, opens the app's store for `search_chat_history` (`set_history_store`, no `setup()`), and returns only the new messages plus `usage` (model calls, tokens, cost, including nested calls such as research's researcher). `RemoteAgent` adds that `usage` to the counter, since this process can't see remote calls. `_to_events` skips the `run` node's update, because the inner steps already streamed it.
- **Auth.** `server_auth.py` accepts only `x-service-token == SERVICE_TOKEN`, comparing bytes, and fails closed when unset.
- **User model keys.** A server has no operator model. It reads the caller's `model_settings` row itself (`agent_reader`, by `x-smallie-user-id`) and decrypts the key with `MODEL_KEY_SECRET`, which it requires at startup like the main app. The key never goes in the request body, headers or `AgentContext`. No row means `NoModelSettings` (the main app returns `no_model` before the call, so this is a backstop).
- **Agents never call each other** (`test_specialists_cannot_import_remote_graph`). Agent servers don't install fastapi: `Dockerfile.agent` drops the pins that clash with the base image's constraints (`protobuf`, `fastapi`, `opentelemetry-api`), and a test checks that the server never imports it.
- **Database logins.** A server never uses the app database's owner. `DATABASE_URL` is `agent_reader` (`06_agent_reader.sql`): SELECT on `READER_TABLES` only (now including `model_settings`, the encrypted keys; still no write), which `grant_reader` (`app/core/db.py`) grants at the end of every app start, because on a fresh database the tables appear only then. So server-side code paths must not write: no DDL, no TTL refresh (`refresh_ttl=False`), PGVector with `create_extension=False` (the documents feature's `start` creates its tables). `DATABASE_URI` is `agent_server` (`05_agent_databases.sql`), owner of the `agent_<route>` databases; the app database refuses it (`06` revokes CONNECT from PUBLIC). Stock queries go through `agent_readonly`.
- **Compose settings.** Each server has its own database `agent_<route>` (created by `dev_db.ps1`) and Redis database; on Cloud Run they share one Redis, separated by `REDIS_KEY_PREFIX`. It runs with `BG_JOB_MAX_RETRIES=1`: that value counts attempts, so `0` never runs; `1` means a run cut off by a restart is not repeated. `BG_JOB_TIMEOUT_SECS=360` stops a run whose cancel never arrived. It also runs with `LANGGRAPH_POSTGRES_POOL_MAX_SIZE=5` (the Go core reads it too) and `DB_POOL_SIZE=2`. `Dockerfile.agent`'s base image is pinned by digest; upgrade it on purpose.
