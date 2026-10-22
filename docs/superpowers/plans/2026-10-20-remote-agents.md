# Remote Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the docs, sql, chart and research agents as private, stateless LangGraph Agent Servers that the main app calls through `RemoteGraph`, locally in Docker compose and later on Cloud Run.

**Architecture:** The main app keeps the router, orchestrator, conversations and memory. Every `AgentSpec` is wrapped in a `RemoteAgent` (`app/agents/remote.py`), which calls `RemoteGraph` with the context in the body, a service token header, no `thread_id`, and converts the JSON that comes back into message objects. One shared image (`Dockerfile.agent`) serves any agent, chosen by `AGENT_ROUTE`.

**Tech Stack:** Python 3.12, LangGraph 1.2 / `langgraph-sdk` 0.4 (`RemoteGraph`), `langchain/langgraph-api:3.12` base image (lite mode, `LANGSMITH_API_KEY`), Postgres 18 + pgvector, Redis, Docker Engine in WSL, Cloud Run.

**Spec:** `docs/superpowers/specs/2026-10-20-remote-agents-design.md` (read its "Spike 1 findings", "Spike 2 findings" and "Agent communication" sections first).

## Global Constraints

- Branch: `0.4.0/remote-agents`. Version `0.4.0`; CHANGELOG and `pyproject.toml` change only in the release task (Task 11).
- Agents never call each other: no `RemoteGraph` inside a specialist, and none pointing at its own server.
- Production loop guard (user's decision, 2026-10-22): every agent server reports its run's `usage` (`model_calls`, `input_tokens`, `output_tokens`, `cost`). The main app adds it to the per-request `UsageCounter`, and `RemoteAgent` refuses a call once that message used `MAX_MESSAGE_MODEL_CALLS` (default 30) model calls. The reported counts also feed the usage box (research included) and the Task 9 audit. LangSmith stays optional.
- No loops or runaway calls. Every model call stays under a cap that exists today: the orchestrator's `no_repeats` / `MAX_ASKS` and `ORCHESTRATOR_MAX_MODEL_CALLS`, and each agent's `MAX_MODEL_CALLS` / `RESEARCH_MAX_MODEL_CALLS`. Going remote must add no retry of a run (AP-010) and no path back into routing. One chat message makes the same number of model calls before and after this change (Task 9 measures it).
- Specialists are stateless: never send `thread_id`. Every `user_id`-scoped query stays scoped.
- Free tiers only: Cloud Run `--min-instances 0`; cold starts are accepted.
- Before editing any file run `uv run python .claude/skills/antipatterning/antipattern.py select --path <file> ...` and follow the rules it returns (AP-002: every new engine/pool gets `pool_pre_ping=True` / `check=`). Run it again with every changed path at the end of each task.
- Prompts, CLAUDE.md and `.claude/` text: use the `writing-llm-document` skill.
- Run `scripts\dev_db.ps1` before DB tests; skipped DB tests are not a pass.
- No real OpenRouter calls in automated tests. Manual end-to-end checks that spend free quota are marked, and are run only once per task.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Don't push; the user approves PRs.
- After each task, update `ARCHITECTURE.md` section 10's status table.

## Review Focus

1. **An agent server is down or cold-starting mid-chat.** A node call surfaces the existing `error` SSE event within the timeout; an `ask_<route>` call returns "The <route> agent failed …" so the orchestrator still answers. Nothing hangs. → Task 3 tests.
2. **A rate limit inside a specialist.** The user still sees "OpenRouter rate limit reached …", not "internal error". → Task 3 test.
3. **A registered agent with no URL in `AGENT_URLS`.** Startup fails with an error naming the route, not a crash at the first chat. → Task 4 test.
4. **"Plot that" after a remote sql answer.** The table artifact survives the round trip, so the chart agent finds it. → Task 2 test.
5. **A request without the service token, or with a wrong one.** Rejected with 401, even when `SERVICE_TOKEN` is unset on the server (fail closed). → Task 4 test.
6. **Agents talking in a loop, or for too long.** A message keeps the call counts it has today, with no echo back into routing and no repeated runs:
   - a remote output key such as `route`, `direct`, `failed` or `attempt` must not reach the parent state
   - an orchestrator that keeps asking the same specialist is still stopped by `MAX_ASKS`
   - a cold start or a dropped connection must not make the client or the server run the same specialist call twice
   - no specialist package can import `RemoteGraph`

   → Task 2, Task 3 and Task 4 tests, plus the Task 9 loop audit.

---

### Task 1: Settings and message conversion

**Files:**
- Modify: `app/core/config.py` (Settings), `app/core/usage.py`, `.env.example`
- Create: `app/agents/remote.py` (conversion helpers only in this task)
- Test: `tests/test_remote.py`

**Interfaces:**
- Produces: `Settings.agent_urls: str = ""` (JSON object route → URL), `Settings.agent_url_map -> dict[str, str]` (property; invalid JSON raises `ValueError` naming `AGENT_URLS`), `Settings.service_token: str = ""`, `Settings.agent_timeout_s: float = 300` (read timeout of a specialist call; generous for cold starts)
- Produces: `Settings.db_pool_size: int = 5`, passed as `pool_size=` with `max_overflow=0` to `get_engine`, `readonly_engine` and PGVector's `engine_args` (the agent services set 2, see Task 5; this keeps 5 processes under Neon's connection cap)
- Produces: `as_message(value: BaseMessage | dict) -> BaseMessage` in `app/agents/remote.py`
- Produces: `UsageCounter.count(message: BaseMessage) -> None` (the per-message part of `on_llm_end`, which now calls it)

- [ ] **Step 1: Write the failing tests** in `tests/test_remote.py`:
  - `test_agent_url_map_parses_json`: `Settings(_env_file=None, agent_urls='{"sql": "http://localhost:2102"}').agent_url_map == {"sql": "http://localhost:2102"}`; empty → `{}`; `"not json"` → `pytest.raises(ValueError, match="AGENT_URLS")`.
  - `test_as_message_rebuilds_each_type`: dicts with `type` `"ai"`, `"AIMessageChunk"`, `"human"`, `"tool"`, `"system"` become `AIMessage`, `AIMessageChunk`, `HumanMessage`, `ToolMessage`, `SystemMessage`; message objects pass through unchanged.
  - `test_as_message_keeps_usage_artifact_and_kwargs`: an `"ai"` dict with `usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}`, `response_metadata={"cost": 0.0001}`, `additional_kwargs={"tables": [{"id": "t1"}]}`, `tool_calls=[...]` keeps all four on the object (`usage_metadata` is NOT moved into `additional_kwargs`, the bug spike 2 found in `add_messages`). A `"tool"` dict keeps `artifact={"tables": [{"id": "t1", "rows": [[1]]}]}`, `name` and `tool_call_id`.
  - `test_usage_counter_counts_one_message`: `count()` on that AI message adds 11 / 7 / 0.0001.
  - `test_engines_use_the_pool_size`: with `db_pool_size=2`, a fresh `readonly_engine(url).pool.size() == 2`.
- [ ] **Step 2:** `uv run pytest tests/test_remote.py -v` → FAIL (import errors).
- [ ] **Step 3: Implement.** `as_message`: map `type` to the class and build it with the dict's keys (e.g. `AIMessage.model_validate`), dropping unknown keys; don't use `convert_to_messages`. `UsageCounter.count`: move the loop body of `on_llm_end`. Document `AGENT_URLS`, `SERVICE_TOKEN`, `AGENT_TIMEOUT_S` in `.env.example` (empty by default; one comment line each).
- [ ] **Step 4:** `uv run pytest tests/test_remote.py tests/test_usage.py -v` → PASS.
- [ ] **Step 5: Commit** `Add remote-agent settings and message conversion`.

### Task 2: `RemoteAgent`

**Files:**
- Modify: `app/agents/remote.py`
- Test: `tests/test_remote.py`

**Interfaces:**
- Consumes: Task 1 (`as_message`, `UsageCounter.count`, settings)
- Produces: `class RemoteAgent` with
  - `__init__(self, spec: AgentSpec, url: str, *, service_token: str, timeout_s: float, remote: Any = None)`. `remote` defaults to `RemoteGraph("agent", url=url, headers={"x-service-token": service_token}, distributed_tracing=True)` with a `langgraph_sdk` client whose timeout is `timeout_s`; tests inject a fake with an async `ainvoke(input, config, *, context, headers)`.
  - `async node(self, state: dict, config: RunnableConfig, runtime: Runtime[AgentContext]) -> dict`: what `build_assistant` adds as the graph node.
  - `async ainvoke(self, input: dict, config: RunnableConfig | None = None, *, context: AgentContext) -> dict`: what `make_ask_tool` calls.
  - Both return `{"messages": <only the messages the specialist added>}`, plus `findings` when the output has it. Nothing else from the remote state is copied, so it can't set `route`, `direct`, `failed` or `attempt` and loop the parent graph.
  - The remote call is never retried: one `ainvoke` is one run.

- [ ] **Step 1: Write the failing tests** (a fake `remote` records its calls):
  - `test_sends_context_and_user_header_without_thread_id`: the call to `remote.ainvoke` gets `context == asdict(ctx)`, `headers == {"x-smallie-user-id": "u-7"}`, and a config whose `configurable` has no `thread_id`, `checkpoint_ns` or `checkpoint_id`. The parent's other `configurable` entries stay (the stream hook).
  - `test_returns_only_new_messages_as_objects`: input 2 messages, fake output = those 2 dicts + 2 new dicts → `node()` returns the 2 new ones as message objects, plus `findings` when the output has it.
  - `test_keeps_table_artifact_for_plot_that` (Review Focus 4): a returned `ToolMessage` dict with `artifact={"tables": [...]}` comes back with `collect(result["messages"])["tables"]` equal to it.
  - `test_counts_usage_of_returned_ai_messages`: config `callbacks=[counter]` (a `UsageCounter`, or a callback manager holding one) → the counter gets the returned AI messages' tokens and cost.
  - `test_remote_rate_limit_becomes_openai_error` (Review Focus 2): the fake raises `RemoteException({"error": "RateLimitError", "message": "..."})` → `pytest.raises(openai.RateLimitError)`; `"AuthenticationError"` → `openai.AuthenticationError`; any other → `RemoteException` unchanged.
  - `test_unreachable_agent_raises_quickly` (Review Focus 1, 6): the fake raises `httpx.ConnectError` → `node()` raises it (not wrapped), and the fake saw exactly 1 call.
  - `test_remote_state_cannot_steer_the_parent` (Review Focus 6): fake output `{"messages": [...], "findings": "f", "route": "sql", "direct": True, "failed": "docs", "attempt": "x"}` → the result's keys are exactly `{"messages", "findings"}`.
  - `test_sdk_client_does_not_retry_runs` (Review Focus 6): build the real `RemoteGraph` client against an `httpx.MockTransport` that answers `POST .../runs/stream` with 503, and assert one request was made. If `langgraph_sdk` retries, disable its retries in the client RemoteAgent builds and note the setting in the code.
- [ ] **Step 2:** `uv run pytest tests/test_remote.py -v` → the new tests FAIL.
- [ ] **Step 3: Implement** `RemoteAgent`. Strip the keys with `{k: v for k, v in conf.items() if k not in {"thread_id", "checkpoint_ns", "checkpoint_id", "checkpoint_map"}}`. Find counters in `config["callbacks"]` (a list or a manager's `.handlers`). Build the `openai` errors with `httpx.Response(429 | 401, request=httpx.Request("POST", url))`.
- [ ] **Step 4:** `uv run pytest tests/test_remote.py -v` → PASS.
- [ ] **Step 5: Commit** `Add RemoteAgent: stateless RemoteGraph calls with context and usage`.

### Task 3: Main app uses remote agents

**Files:**
- Modify: `app/orchestration/graph.py` (`build_assistant`), `app/orchestration/orchestrator.py` (`build_orchestrator` keeps taking `agents: Mapping[str, Runnable]`), `app/agents/delegation.py` (`make_ask_tool`), `app/api/routes_chat.py` (`_to_events`), `app/api/main.py`, `scripts/bench_chat.py`, `tests/conftest.py`, tests that call `build_assistant`
- Test: `tests/test_remote.py`, `tests/test_chat_events.py`

**Interfaces:**
- Consumes: Task 2 `RemoteAgent.node`, `RemoteAgent.ainvoke`
- Produces: `build_assistant(checkpointer, store, agents: Mapping[str, Any] | None = None)`. With `None`, it builds `RemoteAgent(spec, url, ...)` per spec from `settings.agent_url_map` and adds each one's `.node` as the graph node. Tests pass local compiled graphs.
- Produces: `remote_agents(specs) -> dict[str, RemoteAgent]` in `app/agents/remote.py`; raises `RuntimeError("AGENT_URLS has no URL for: docs, sql")`, naming every missing route.
- Produces: `local_agents()` fixture in `tests/conftest.py` → `{spec.route: spec.build() for spec in AGENTS}`.

- [ ] **Step 1: Write the failing tests:**
  - `test_missing_agent_url_names_the_routes` (Review Focus 3): `agent_urls='{"sql": "http://x"}'` → `remote_agents(AGENTS)` raises `RuntimeError` whose message contains `docs`, `chart` and `research`.
  - `test_ask_tool_failure_still_answers` (Review Focus 1): `make_ask_tool(spec, agent)` where `agent.ainvoke` raises `httpx.ConnectError` → the tool returns `"The sql agent failed (ConnectError); it has no answer."`.
  - `test_max_asks_still_caps_remote_calls` (Review Focus 6): the orchestrator, built with a scripted model (`tests/scripted.py`) that asks `ask_sql` with a different question at every step, and a fake `RemoteAgent` that counts calls → the fake sees at most `MAX_ASKS` calls, and the turn ends with an answer.
  - `test_specialists_cannot_import_remote_graph` (Review Focus 6): no file under `app/agents/<route>/` (for every route in `ROUTES`) contains `RemoteGraph`, `langgraph.pregel.remote` or `app.agents.remote`.
  - In `tests/test_chat_events.py`, `test_remote_dict_chunks_become_events`: `_to_events("messages", ({"type": "AIMessageChunk", "content": "Hi"}, {"langgraph_node": "model"}), ("sql:abc",))` → `[{"type": "token", "text": "Hi"}]`. An `updates` chunk whose messages are dicts (an AI tool call + a tool result) yields the same `tool_call` / `tool_result` events as message objects do.
- [ ] **Step 2:** run those tests → FAIL.
- [ ] **Step 3: Implement:**
  - `_to_events` applies `as_message` to every message it reads.
  - `make_ask_tool` passes `config=runtime.config` to `agent.ainvoke`, so a remote specialist's tool calls stream (its tokens stay dropped by the `tools` namespace rule).
  - `main.py`: the lifespan still awaits every `spec.prepare` (the orchestrator's web search tools come from research's MCP load), then calls `build_assistant(checkpointer, store)`.
  - Point every existing `build_assistant(...)` call in `tests/` at `agents=local_agents`.
  - `bench_chat` uses the remote default, which needs the compose stack.
- [ ] **Step 4:** full `uv run pytest -rs` → all pass, no "Postgres not running" skips.
- [ ] **Step 5: Commit** `Main app calls specialists through RemoteAgent`. From here until Task 5 the local app refuses to start without `AGENT_URLS`, so only the tests run; tell the user that before committing.

### Task 4: Agent server entry point, auth and memory store

**Files:**
- Create: `app/agents/server.py`, `app/agents/server_auth.py`, `langgraph.json`
- Modify: `app/core/memory.py` (`search_chat_history`)
- Test: `tests/test_agent_server.py`

**Interfaces:**
- Produces (call reporting, the loop guard): `make_graph()` returns a one-node wrapper graph (node name `run`: not a route or `tools`, AP-004). The node runs the spec's graph with a fresh `UsageCounter` added to its callbacks (`merge_configs`, so the stream handlers stay), and returns `{"messages": ..., "findings": ... (when present), "usage": counter.totals()}`.
  - `UsageCounter` gains `model_calls`, counted in `on_llm_end`, plus `totals() -> dict` and `add(totals: dict)`.
  - `RemoteAgent` replaces its message-based counting with `counter.add(output["usage"])`.
  - Before each call, `RemoteAgent` raises `CallBudgetExceeded` (a `RuntimeError` naming the budget) when any counter in the config has `model_calls >= settings.max_message_model_calls`. That surfaces as an error on the node path, and as "The <route> agent failed (CallBudgetExceeded) …" on the ask path, so the orchestrator still answers.
  - New setting `max_message_model_calls: int = 30` (`MAX_MESSAGE_MODEL_CALLS`).
  - Tests:
    - `test_server_reports_usage_of_every_call` (research workflow with a scripted model: the researcher's calls are counted although `gather` keeps no messages)
    - `test_reported_usage_reaches_the_counter`
    - `test_budget_stops_further_specialist_calls`
    - `test_wrapper_keeps_tokens_and_tool_events_streaming` (namespace with `run:` still yields token and tool events)
- Produces: `async def make_graph()` in `app/agents/server.py`. It reads `AGENT_ROUTE`, looks the spec up with `registry.get` (unknown or empty → `ValueError` naming the value), and awaits `spec.prepare(_STACK)` when set. `_STACK` is a module-level `AsyncExitStack`, kept open for the process; mark it with `# shortcut: never closed; the process lifetime is the server's`. It opens the app store with `set_history_store(...)` and returns `spec.build()`. The result is cached, so the graph is built once per process.
- Produces: `set_history_store(store: BaseStore) -> None` in `app/core/memory.py`. When set, `search_chat_history` searches that store instead of `runtime.store`, because an Agent Server's own store is empty. The server's store is an `AsyncPostgresStore` on `DATABASE_URL` with a pool like `open_persistence`'s (`max_size=2`, `check=`, keepalives) and `index=store_index_config()`. It never calls `setup()`, because the role may be read-only.
- Produces: `auth = Auth()` in `app/agents/server_auth.py`, with `@auth.authenticate async def authenticate(headers: dict) -> dict`. It accepts only when `SERVICE_TOKEN` is non-empty and `hmac.compare_digest` matches the `x-service-token` header (bytes or str keys). Identity = `x-smallie-user-id`. Everything else raises `Auth.exceptions.HTTPException(401)`.
- Produces: `langgraph.json` = `{"dependencies": ["."], "graphs": {"agent": "./app/agents/server.py:make_graph"}, "auth": {"path": "./app/agents/server_auth.py:auth"}, "http": {"configurable_headers": {"includes": ["x-smallie-*"]}}, "python_version": "3.12"}`.

- [ ] **Step 1: Write the failing tests:**
  - `test_unknown_route_is_refused`: `AGENT_ROUTE=nope` → `pytest.raises(ValueError, match="nope")`.
  - `test_make_graph_builds_the_route` (`requires_db`): `AGENT_ROUTE=chart` → a compiled graph whose `.name` is the chart agent's.
  - `test_auth_rejects_missing_wrong_and_unset_token` (Review Focus 5): `SERVICE_TOKEN=s3cret`: no header → 401, wrong → 401, right → identity from `x-smallie-user-id`. `SERVICE_TOKEN=""` with any header → 401.
  - `test_chat_history_uses_the_set_store`: after `set_history_store(fake)`, the tool searches `fake` (an `InMemoryStore` holding one history item) even though `runtime.store` is another store.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Smoke-run the server locally without Docker:** `uvx --python 3.12 --from "langgraph-cli[inmem]" --with colorama langgraph dev --port 2102 --no-browser --no-reload`, with `AGENT_ROUTE=sql` and `SERVICE_TOKEN=dev` in the environment, from the repo root. `GET http://127.0.0.1:2102/ok` → `{"ok":true}`. `POST /assistants/search` without the header → 401.
- [ ] **Step 5:** `uv run pytest -rs` → all pass. **Commit** `Agent server entry point, service-token auth, app history store`.

### Task 5: Docker image and compose stack

**Files:**
- Create: `Dockerfile.agent`, `db/init/05_agent_databases.sql`
- Modify: `docker-compose.yml`, `scripts/dev_db.ps1`, `.dockerignore` (if needed), `.env.example`, README, CLAUDE.md
- Test: `tests/test_compose.py`

**Interfaces:**
- Consumes: Task 4 `langgraph.json`, `make_graph`, `auth`
- Produces: compose services `redis` and `docs`, `sql`, `chart`, `research`, each `<<: *agent` from an `x-agent` template, published on `127.0.0.1:2101`–`2104` (container port 8000). Databases `agent_docs`, `agent_sql`, `agent_chart`, `agent_research` in `smallie-db`.

- [ ] **Step 1: Write the failing test** `test_every_route_has_a_service_and_a_url` in `tests/test_compose.py`. Parse `docker-compose.yml` with `yaml.safe_load` (PyYAML is installed with LangChain). For every route in `ROUTES`, assert:
  - a service named after it exists, and its environment has `AGENT_ROUTE: <route>`
  - the `AGENT_URLS` example line in `.env.example` holds the route
  - `05_agent_databases.sql` mentions `agent_<route>`
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: Get the base image's exact env names.** Run `uvx --from langgraph-cli langgraph dockerfile -c langgraph.json <scratchpad>/reference.Dockerfile` and copy its `LANGSERVE_GRAPHS`, `LANGGRAPH_AUTH` and `LANGGRAPH_HTTP` lines, with paths under `/deps/smallie`. Don't commit the reference file.
- [ ] **Step 4: Write `Dockerfile.agent`:**
  - `FROM langchain/langgraph-api:3.12`, `COPY` the repo to `/deps/smallie`, `ENV PYTHONPATH=/deps/smallie` plus the three lines from Step 3.
  - Install the dependencies with `uv export --frozen --no-dev --no-hashes` → `uv pip install --system -r requirements.txt -c /api/constraints.txt`, using the base image's constraints so its `langgraph-api` keeps working.
  - If the resolver reports a conflict, stop and report the conflicting packages to the user. Don't loosen our lock on your own.
- [ ] **Step 5: Write the compose services, init SQL and script:**
  - `x-agent` template: `build: {context: ., dockerfile: Dockerfile.agent}`, `env_file: .env`, `depends_on: {db: {condition: service_healthy}, redis: {condition: service_started}}`, `restart: unless-stopped`.
  - `x-agent-env` overrides:
    - `DATABASE_URL: postgresql://smallie:smallie@db:5432/smallie`
    - `READONLY_DATABASE_URL: postgresql://agent_readonly:readonly@db:5432/smallie`
    - `DATABASE_URI: postgres://smallie:smallie@db:5432/agent_<route>`
    - `REDIS_URI: redis://redis:6379/<1-4>`
    - `SERVICE_TOKEN: ${SERVICE_TOKEN}`
    - `DB_POOL_SIZE: "2"`
  - `redis`: `redis:7-alpine`, not published.
  - `05_agent_databases.sql`: one `SELECT 'CREATE DATABASE agent_<route>' WHERE NOT EXISTS (...)\gexec` per route.
  - `dev_db.ps1`: also runs that file with `docker exec smallie-db psql -U smallie -d postgres -f /docker-entrypoint-initdb.d/05_agent_databases.sql`, because init scripts only run on a fresh volume.
- [ ] **Step 6:** `uv run pytest tests/test_compose.py -v` → PASS.
- [ ] **Step 7: Bring the stack up and check it by hand:**
  - `wsl -d Ubuntu -u root --cd "<repo>" -- docker compose up -d --build`, then `docker compose ps` → the 4 agent services are running.
  - `curl http://127.0.0.1:2101/ok` to `:2104/ok` → `{"ok":true}`.
  - A request without `x-service-token` → 401.
  - List the tables the server created in `agent_sql` (`\dt`) and record them in the spec's "Database" section; that closes the spec's open question.
  - Look at the `run` table's columns and the server's startup log for a retry or re-queue setting (Review Focus 6). If a run interrupted by a container restart is re-queued and run again, find the setting that turns that off (start with the env vars in `langgraph-api`'s docs), set it in the `x-agent` env, and record it in the spec. If there is none, record it as an open risk in the spec's "Open items".
- [ ] **Step 8: End-to-end check** (spends about 3 free-quota calls). Set `AGENT_URLS` to the four localhost ports and `SERVICE_TOKEN` in `.env`, start `uvicorn`, and ask "AAPL dividend yield?". The answer and the `run_sql` tool events stream, the reply is saved in the thread, and the usage box goes up. Then ask "plot that" and check that the chart draws.
- [ ] **Step 9:** update README (running the stack, rebuilding after changes), CLAUDE.md (Commands + a **Remote agents** paragraph) and `ARCHITECTURE.md` section 10. **Commit** `Agent server image and compose stack`.

### Task 6: Read-only database role for the agent servers

All four servers, not only docs and research: every server opens the app's store for `search_chat_history` through `DATABASE_URL` (Task 5 review).

**Files:**
- Create: `db/init/06_agent_reader.sql`
- Modify: `docker-compose.yml` (the shared `x-agent-env` `DATABASE_URL` → `agent_reader`), `scripts/dev_db.ps1` (also runs `06`), `app/features/documents/ingest.py` only if the test below shows `PGVector` writing on init
- Test: `tests/test_agent_reader.py` (`requires_db`)

**Interfaces:**
- Produces: role `agent_reader` (password `reader` locally). It gets `CONNECT`, `USAGE` on `public` and `market`, and `SELECT` on `documents`, `graph_nodes`, `graph_edges`, `langchain_pg_collection`, `langchain_pg_embedding`, `store`, `store_vectors`, `store_migrations` and `market.*`, with `default_transaction_read_only = on` and `statement_timeout = '10s'`. It is idempotent (`DO $$ ... IF NOT EXISTS ... $$`).

- [ ] **Step 1: Write the failing tests** (connect as `agent_reader`):
  - `test_reader_can_search_documents_and_facts`: `search_user_chunks`, `documents_for` and `search_facts`, pointed at the reader URL, run without error for an existing user.
  - `test_reader_cannot_write`: `INSERT INTO documents ...` and `DELETE FROM graph_edges` fail with a permission or read-only error.
- [ ] **Step 2:** run → FAIL (role missing).
- [ ] **Step 3: Implement the SQL.** If `PGVector(...)` fails on init as `agent_reader` (extension or table creation), pass `create_extension=False` in `get_vector_store` when the URL's user is not the owner, and rerun. Change nothing else.
- [ ] **Step 4:** `uv run pytest -rs` → all pass. Rebuild the stack and rerun Task 5 Step 8's first question against `docs` (a question about an uploaded file).
- [ ] **Step 5: Commit** `Read-only database role for the docs and research servers`.

### Task 7: Cloud Run ID tokens

**Files:**
- Modify: `app/agents/remote.py`
- Test: `tests/test_remote.py`

**Interfaces:**
- Produces: `id_token(audience: str) -> str | None`, which fetches `http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=<audience>` with the header `Metadata-Flavor: Google` over `httpx`. It caches per audience for 50 minutes, and returns `None` when `K_SERVICE` is not set (not on Cloud Run).
- `RemoteAgent` adds `Authorization: Bearer <token>` to each call's headers when `id_token(url)` returns one.

- [ ] **Step 1: Write the failing tests:**
  - `test_no_id_token_off_cloud_run`: `K_SERVICE` unset → `None`, and no request is made.
  - `test_id_token_is_fetched_once_and_cached`: with `K_SERVICE` set and an `httpx.MockTransport`, two calls make one request.
  - `test_remote_call_carries_bearer_on_cloud_run`: the fake remote sees `Authorization: Bearer <token>`.
- [ ] **Step 2–4:** fail → implement → pass (`uv run pytest tests/test_remote.py -v`).
- [ ] **Step 5: Commit** `Send Cloud Run ID tokens to the agent services`.

### Task 8: Cloud Run deployment config and docs

**Files:**
- Create: `cloudbuild.agent.yaml` (builds `Dockerfile.agent` once into Artifact Registry as `smallie-agent`)
- Modify: README "Deploying", CLAUDE.md deployment bullets, `ARCHITECTURE.md` section 12

**Interfaces:**
- Produces documented commands (the user runs them; no deploy in this task):
  1. Build the image once: `gcloud builds submit --config cloudbuild.agent.yaml`.
  2. Deploy per route: `gcloud run deploy smallie-<route> --image <registry>/smallie-agent --region asia-southeast1 --no-allow-unauthenticated --min-instances 0 --max-instances 1 --memory 1Gi --cpu 1 --env-vars-file cloudrun.agent.<route>.env.yaml`. One env file per route, because gcloud rejects `--set-env-vars` next to `--env-vars-file` (verify), and each route needs its own `AGENT_ROUTE`, `DATABASE_URI` (`agent_<route>`), `REDIS_URI` and `REDIS_KEY_PREFIX` (`<route>:`; one shared Upstash database must not mix the four servers' run queues and sweep keys). The files list only what a server needs (model/embedding settings, `LANGSMITH_API_KEY`, `SERVICE_TOKEN`, research's `TAVILY_API_KEY`/`MCP_SERVERS`/`EXTERNAL_DATABASE_URL`, the reader `DATABASE_URL`, `READONLY_DATABASE_URL`, `BG_JOB_MAX_RETRIES`, the pool sizes), never `APP_PASSWORD` or `SESSION_SECRET`. Request-based CPU billing, no `--no-cpu-throttling`.
  3. Grant `roles/run.invoker` on each agent service to the main app's service account.
  4. Main app: add `AGENT_URLS` (the four `*.run.app` URLs) and `SERVICE_TOKEN` to `cloudrun.env.yaml`.
  5. Neon: run `05` (role `agent_server` and the four `agent_<route>` databases it owns) and `06` (role `agent_reader`; the app database stops accepting every login), each with a strong password, then restart the main app so `grant_reader` grants the tables. Check the live SQL agent still connects (`06` grants `agent_readonly` CONNECT explicitly) and that Neon allows `CREATE DATABASE ... OWNER agent_server` after `05`'s `GRANT agent_server TO <owner>`. Each route's `DATABASE_URI` uses `agent_server`, never the owner. Redis: an Upstash free database; `REDIS_URI` in the agents' env file.
- [ ] **Step 1:** write `cloudbuild.agent.yaml` and the docs. Add `cloudrun.agent.*.env.yaml` to `.gitignore`, `.gcloudignore` and `.dockerignore`.
- [ ] **Step 2: Verify:** `gcloud builds submit --config cloudbuild.agent.yaml --dry-run` is not available, so check by reading. Each command uses the real project, region and image names from CLAUDE.md, and every secret goes in a gitignored file.
- [ ] **Step 3: Commit** `Cloud Run deployment config for the agent services`.

### Task 9: Whole-branch review and PR

- [ ] **Step 1:** run `uv run pytest -rs` (all pass, no DB skips), then the antipattern `select` with every changed path.
- [ ] **Step 2: Loop audit** (Review Focus 6; spends free quota, so ask the user first).
  1. Run `uv run python -m scripts.bench_chat` on `main` (in-process agents), then on this branch with the compose stack up. On this branch, `bench_chat` takes call counts from the `UsageCounter`'s `model_calls`, which holds the counts the agent servers report; its local `Timing` callback can't see remote calls.
  2. Model calls per scenario must be equal. Record both tables and the latency difference in the PR description.
  3. Check that no scenario comes near `MAX_MESSAGE_MODEL_CALLS`. If one does, raise the default and record the measured worst case.
  4. A higher count on this branch is a bug to fix before the PR, not a note.
- [ ] **Step 3:** run a fresh reviewer on the whole branch diff against `main`, with the spec and this plan as context; tell it to check Review Focus 6 explicitly. Fix the confirmed findings and commit.
- [ ] **Step 4:** ask the user to approve; then push and `gh pr create` to `main`. Merge only when the user asks.

### Task 10: Deploy (with the user)

- [ ] **Step 1:** after the merge: `git switch main && git pull`, check the tree is clean, then run Task 8's commands with the user (they hold the gcloud and Neon credentials).
- [ ] **Step 2:** check that `https://smallie-976519788978.asia-southeast1.run.app` answers a sql question and a docs question. Calling an agent service URL directly without auth → 403.
- [ ] **Step 3:** measure a cold start (first message after 15 minutes idle) and a warm message, and note both in CHANGELOG.

### Task 11: Release 0.4.0

**Files:** `CHANGELOG.md`, `pyproject.toml`, `uv.lock` (following `.claude/rules/changelog.md`)

- [ ] **Step 1:** on branch `0.4.0/release` from `main`, add `## 0.4.0 - Remote agents`:
  - **Remote agents**: what changed for operators, the new settings `AGENT_URLS`, `SERVICE_TOKEN`, `AGENT_TIMEOUT_S`, and the measured cold-start and warm latency
  - **LangSmith tracing**: `LANGSMITH_*`, off by default
  - **Local Postgres in Docker**: listed as tooling, with `scripts\dev_db.ps1`
- [ ] **Step 2:** set the version to `0.4.0` and run `uv lock`. Commit `Release 0.4.0: remote agents`. Then PR and merge as the user approves.
