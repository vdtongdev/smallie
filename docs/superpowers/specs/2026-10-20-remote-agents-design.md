# Remote agents (spec 1 of 2)

Date: 2026-10-20. Version: `0.4.0` (an architecture change; the image agent follows as `0.5.0`). Branches: `0.4.0/<slug>`. Status: approved; phases 0–2 done.

## Goal

Run each specialist agent (docs, sql, chart, research) as its own private LangGraph Agent Server. The main app calls them through `RemoteGraph`. Reasons, in the user's words:

- learning: this is a learning project for production-grade tools, not a production app
- the team lead requires it
- later scaling and deploying agents on their own

Spec 2 (a chat room where one user and several agents talk to each other) builds on this and is out of scope here.

## Constraints

- Keep it as free as possible: at most 20 accounts, but only the user and the team lead use the app.
- Agents are always remote, also in local development. Local development simulates the deployment with Docker compose (no `langgraph dev` mode).
- Cold starts are accepted: every Cloud Run service runs with `--min-instances 0`.
- Raise `min-instances` only when the architecture needs it, and state the cost.

## Decisions

| Topic | Decision | Rejected |
|---|---|---|
| Protocol | `RemoteGraph` to Agent Servers (`langgraph-api`, free "lite" mode with a `LANGSMITH_API_KEY`). Every Agent Server also serves A2A at `/a2a/{assistant_id}`, so A2A stays possible. | A2A with `a2a-sdk` (more of our own code); custom HTTP (non-standard); managed LangSmith Deployment (paid) |
| State | Specialists are stateless (runs without `thread_id`). The main app owns every conversation and all memory. | Stateful specialists: duplicate threads, deletion leaks, memory tools reading the wrong store |
| Context | Sent explicitly as `context=asdict(runtime.context)` from a wrapper. | A plain RemoteGraph node: the spike showed it sends no context and leaves a thread behind |
| Local stack | Docker Engine inside WSL Ubuntu plus compose. The WSL Postgres moves into the compose Postgres. | Docker Desktop (licence conditions for companies); a `langgraph dev` fast mode (YAGNI) |
| Specialist DB access | sql and chart use `agent_readonly`. docs and research use a new SELECT-only `agent_reader` role. | The owner `DATABASE_URL` (a bug could then write or delete user data) |

## Spike 1 findings (throwaway, 2026-10-20)

The spike used `langgraph-api` 0.16.2 under `langgraph dev`, and the project's installed `langgraph` / `langgraph-sdk` 0.4.6 as the client.

| Check | Result |
|---|---|
| `add_node("x", RemoteGraph(...))` with a parent `context` | The server got an empty default context and no headers. It forwarded the parent's `thread_id`, which must be a UUID ("badly formed hexadecimal UUID string" otherwise), and the thread stayed on the server. |
| Wrapper node: `remote.ainvoke(input, context=asdict(ctx), headers=...)` | The full context arrived and became the server's dataclass again (lists included). The header showed up in `config["configurable"]` through `http.configurable_headers`. It ran on a temporary thread, and the server's thread count did not go up. |
| Custom auth (`auth.py`, `@auth.authenticate`) without the token | Rejected: `AuthenticationError: bad service token`. With the token, the identity was set from `x-smallie-user-id`. This was tested in dev mode only. |
| Windows | `langgraph dev` needs `colorama` (only for the dev tool). |

## Spike 2 findings (throwaway, 2026-10-20)

The setup was the same as spike 1, with a remote graph that streams, returns a `ToolMessage` with an artifact, sets `additional_kwargs` and `usage_metadata`, and raises `openai.RateLimitError`. The wrapper passed the node's config with `thread_id` removed.

| Check | Result | Consequence |
|---|---|---|
| Streaming through the wrapper | Chunks reach the parent stream with the parent's namespace prefixed (`('sql:<task id>',)`), but as **dicts**, not message objects | `RemoteAgent` converts streamed dicts to message objects before `_to_events` sees them (it checks `isinstance(message, AIMessageChunk)`). |
| Output state | Messages come back as dicts. `ToolMessage.artifact`, `additional_kwargs` (`tables`) and `findings` survive. | Tables and charts keep working. |
| `usage_metadata` | The parent's default dict → message conversion (`add_messages`) moved `usage_metadata` and `invalid_tool_calls` into `additional_kwargs`, and `usage_metadata` became `None` | `RemoteAgent` builds the message objects itself (by `type`: `ai`, `human`, `tool`, `system`) instead of relying on `add_messages`. |
| Remote error | `RemoteException({'error': 'RateLimitError', 'message': 'An internal error occurred'})`: the type name survives, the message doesn't | `RemoteAgent` raises a remote `RateLimitError` / `AuthenticationError` again as the matching `openai` exception. |
| Async graph factory (`async def make_graph()` in `langgraph.json`) | Works | Research's `prepare` (MCP loading) can run inside the factory. |
| CPU throttled between requests | Not testable locally | Checked during the Cloud Run phase. |

## Agent communication

In this spec agents never talk to each other directly. This is a deliberate choice, so the main app keeps control of agent behaviour and limits. Every message goes through the main app (router and orchestrator), in a hub-and-spoke shape:

| Pattern | Example |
|---|---|
| Router → one specialist | "AAPL price last week" → sql |
| Orchestrator asks several in parallel, then combines | `ask_sql` + `ask_docs` for "compare AAPL's P/E with my resume" |
| Handoff | the orchestrator returns `Command(goto="sql")` |
| Research → orchestrator | research returns `findings`, the orchestrator writes the answer |
| Fallback | the main app sees `gave_up` and the orchestrator tries something else |

- Specialists have no URLs for each other and no `RemoteGraph` to another specialist.
- Data between agents (a sql table that chart should draw) goes back through the main app's thread and is sent on with the next call.
- The research agent's `search_documents` and `run_sql` are tool functions imported as code, not calls to the docs and sql agents.

Why:
- **Limits:** `MAX_ASKS` and `no_repeats` in the orchestrator cap repeated calls only because the hub sees every call. Direct calls could loop between servers and burn free-tier model calls.
- **Security:** only the main app passes `user_id` on.
- **Tracing:** one LangSmith trace has one root.

Spec 2's chat room keeps the hub: a moderator holds the transcript and passes it to the next speaker, so agents only seem to talk to each other.

## Architecture

```
                       public                 │              private
 Browser ──► main app (FastAPI, Cloud Run)    │
             UI, login, conversations,        │   docs server      (Agent Server)
             memory, uploads, market job,     │   sql server       (Agent Server)
             router + orchestrator  ──────────┼─► chart server     (Agent Server)
             (owns every conversation)        │   research server  (Agent Server)
                 │  RemoteAgent: context,     │        │
                 │  service token, tracing    │        │ reads app tables
                 ▼                            │        ▼
        Postgres: app tables + one database per Agent Server for its run queue
        Redis: streaming pipe between each server's API part and its worker
```

- **Main app** keeps the router and orchestrator, so no Agent Server ever calls itself through RemoteGraph (that can deadlock once every worker waits on a queued run).
- **Each specialist server** is built from this repo and serves one graph: `spec.build()`, the same function the agent uses today.

## Components

### `RemoteAgent` (`app/agents/remote.py`, new)

It wraps `RemoteGraph(spec.route, url=AGENT_URLS[spec.route], distributed_tracing=True)` and behaves like the compiled graph it replaces. It works both as a graph node and through `.ainvoke(input, context=...)`, which `make_ask_tool` uses. On each call it:

1. sends only the input the specialist's state needs: `messages`, plus `findings` for research
2. passes `context=asdict(runtime.context)` and the headers `x-service-token`, `x-smallie-user-id` and, on Cloud Run, `Authorization: Bearer <ID token>`
3. removes `thread_id` and the checkpoint keys from the config, but keeps the parent's stream hook, so the remote chunks reach the parent stream (`remote.py:699`)
4. returns only the messages added after the input, plus `findings`, so the main app's thread looks the same as today
5. adds the `usage_metadata` of the returned AI messages to the run's `UsageCounter` (callbacks don't cross the network)
6. raises a remote rate-limit or auth error again as the matching `openai` exception, so `routes_chat` still shows its "rate limit reached" message

### Changes in existing code

- `build_assistant(checkpointer, store, agents=None)`: wraps each spec in `RemoteAgent` by default. Tests pass local graphs. `build_orchestrator` gets the same mapping.
- `AgentSpec`: `build` stays the local graph builder, used by the agent server and by the tests.
- Router, fallback (`gave_up` reads the returned messages on the main app's thread), handoff, `make_ask_tool`, research → orchestrator: no logic changes.
- `route_info` (file names, tickers) stays in the main app, because it only reads app tables.
- MCP: the research server runs `prepare` itself. The main app still loads Tavily/Wikipedia for the orchestrator's web search.
- New settings in `app/core/config.py` and `.env.example`:
  - `AGENT_URLS`: one JSON object, route → URL (e.g. `{"sql": "http://localhost:2102", ...}`). At startup the main app fails with a clear error naming any registered route without a URL.
  - `SERVICE_TOKEN`
  - `DB_POOL_SIZE` (SQLAlchemy pool size, default 5)

### Agent server (one generic entry point)

- `app/agents/server.py`: `async def make_graph()` reads `AGENT_ROUTE`, looks the spec up in the registry, awaits `prepare` when the spec has one, and returns `spec.build()`. Spike 2 showed async factories work. No per-agent server file.
- `app/agents/server_auth.py`: custom auth that accepts only `x-service-token == SERVICE_TOKEN`.
- `langgraph.json` (one file for every agent): the graph points at `app/agents/server.py:make_graph`, auth at `server_auth.py`, and `http.configurable_headers.includes = ["x-smallie-*"]`.
- One `Dockerfile.agent`, based on `langchain/langgraph-api:3.12`, installing dependencies with `uv sync --frozen`. The route is runtime config (`AGENT_ROUTE`), so every agent uses the **same image**. A hand-written Dockerfile is needed because `pyproject.toml` has no `build-system` (so `langgraph build`'s `dependencies: ["."]` may not install) and because Cloud Build needs a Dockerfile.

### Adding an agent server (the seamless path)

After writing the agent itself (the existing "Adding an agent" steps in `CLAUDE.md`):

1. `docker-compose.yml`: add one service that extends the `x-agent` template:
   ```yaml
   web:
     <<: *agent
     environment: { <<: *agent-env, AGENT_ROUTE: web }
     ports: ["127.0.0.1:2105:8000"]
   ```
2. `AGENT_URLS`: add `"web": "http://localhost:2105"` (`.env`) or the Cloud Run URL (`cloudrun.env.yaml`).
3. Cloud Run: deploy the shared image once more with `AGENT_ROUTE=web` (the same `cloudbuild.yaml`/deploy command as the others, a different service name).

No changes to the router, orchestrator, `RemoteAgent`, Dockerfile, `langgraph.json` or auth. A test checks that every registered route has a compose service and an entry in `.env.example`'s `AGENT_URLS`.

## Data flow

| Path | Remote behaviour |
|---|---|
| Router → specialist node | `RemoteAgent` runs the remote graph on the thread's messages. The new messages land in the main app's thread. |
| Fallback | Unchanged: `gave_up(turn_messages(...))` on the main app's thread |
| Orchestrator `ask_<route>` | Unchanged: `make_ask_tool` → `.ainvoke(question, context=...)`. Tables and charts come back on the returned `ToolMessage`s and go through `collect()`. |
| Handoff | Unchanged: `Command(goto=route)` → the `RemoteAgent` node |
| Research | `findings` comes back in the output state and is copied into the parent state. |
| Streaming to the UI | Remote chunks are forwarded into the parent stream with namespaces, and `_to_events` keeps its namespace rules. If chunks come back as dicts, they are converted to message objects (see spike 2). |

## Errors

- A specialist that is down or times out:
  - as a node → the existing `error` SSE event
  - as `ask_<route>` → the existing "the X agent failed" tool text, so the orchestrator can still answer
- The client timeout is long enough for cold starts (several seconds per server).

## Security

| Layer | Local (compose) | Cloud Run |
|---|---|---|
| Platform | No public ports. Ports are published on `127.0.0.1:2101-2104` only, for the Windows-side `uvicorn`. | `--no-allow-unauthenticated`. The main app's service account has `roles/run.invoker`, and its ID token comes from the metadata server over `httpx`, so no `google-auth` dependency is added. |
| App | Agent Server custom auth: `x-service-token` | the same |

- A specialist trusts `context.user_id` only after the token check, and every query keeps its `user_id` scoping.
- `--ingress internal` is skipped: it needs VPC egress setup, and IAM already rejects strangers.

## Database

| Service | Reads | Role |
|---|---|---|
| sql, chart | `market` | `agent_readonly` (exists) |
| docs, research | `market`, document chunks, graph tables, store (chat history) | `agent_reader`, new in `db/init/05_agent_reader.sql`, SELECT only |
| main app | everything | owner |

- Every Agent Server gets its own database (`agent_docs`, `agent_sql`, `agent_chart`, `agent_research`) for its own tables, so they don't clash with our `checkpoints`/`store` tables.
- Redis: locally one container with database numbers 1–4. In production Upstash free, or a shared database 0 if the free tier only offers one (to verify).
- Connections: one user with several agents answering at once means 5 processes holding pools. Specialists use `DB_POOL_SIZE=2` plus the Agent Server's pool-size setting. The implementation plan holds a table that adds up the maximum connections and compares them with Neon's cap for its smallest compute.

## Local development

1. Docker Engine and the compose plugin inside WSL Ubuntu (`systemd=true` in `/etc/wsl.conf`).
2. Migration: `pg_dump` the WSL database → the compose `db` (`pgvector/pgvector:pg18`, the same major version as Neon) → `pg_restore` → disable the WSL Postgres service. Port 5433 stays, so `.env` doesn't change.
3. `docker compose up` starts `db`, `redis`, `docs`, `sql`, `chart` and `research`. The main app runs on Windows with `uvicorn` as today, with `AGENT_URLS` pointing at `http://localhost:2101-2104`. An optional `app` service (compose profile `full`) runs everything in containers.
4. After changing code in a specialist: `docker compose up --build <route>`.

## Testing

- `RemoteAgent` unit tests against a fake `RemoteGraph`:
  - context and headers are sent
  - `thread_id` is removed
  - only new messages come back
  - usage is added up
  - errors are mapped back
- Existing tests pass local graphs through `build_assistant(agents=...)` and keep running without containers.
- A new `requires_agents` marker (like `requires_db`) for a few end-to-end tests against the compose stack, skipped when it isn't running.
- `scripts.bench_chat` measures the real network latency once the stack is up.

## Deployment (Cloud Run)

- Four new services `smallie-<route>`: `--no-allow-unauthenticated --min-instances 0 --max-instances 1`, all from the one shared image, each with its own `AGENT_ROUTE`.
- Specialists use request-based CPU billing (the default, no `--no-cpu-throttling`): runs happen inside the open request, and always-on CPU would bill every woken service until it shuts down. The main app keeps `--no-cpu-throttling` for its background tasks.
- Main app env: the four URLs and `SERVICE_TOKEN`. Specialist env: `DATABASE_URI` (its agent database), `REDIS_URI`, `LANGSMITH_API_KEY`, `SERVICE_TOKEN`, the OpenRouter keys, and the app/readonly database URLs for its role.
- Neon: create the four agent databases and the `agent_reader` role.
- Expected cost: free-tier usage only. Neon and Upstash sleep when idle, and Cloud Run bills requests only.

## Phases

Each phase ends with green tests and can merge on its own:

0. **Spike 2** (done, see "Spike 2 findings"). The Agent Server with CPU throttled between requests is still to check during the Cloud Run phase.
1. Docker migration (branch `0.4.0/docker-postgres`, done).
2. LangSmith tracing: approved separately, branch `0.4.0/langsmith-tracing`, off by default. It goes first because `distributed_tracing` uses it.
3. Agent server entrypoints, `Dockerfile.agent`, compose stack.
4. `RemoteAgent` and the switch in `build_assistant`.
5. Security: custom auth, ID tokens, `agent_reader`.
6. Cloud Run deployment, README, CLAUDE.md, CHANGELOG.

## Open items to verify

- Upstash free tier: limits and whether more than database 0 is allowed.
- Agent Server pool-size environment variable name.
- Neon's connection cap and free compute hours.
- Custom auth in licensed lite mode, not just `langgraph dev`.
- Whether the lite node-execution cap (1M) is yearly or lifetime.

## Out of scope

- The chat room (spec 2). Stateless specialists fit it: the room owns the transcript and passes it to each agent. An agent asks another one through a mention or a tool. Pausing mid-run (`interrupt()`) is replaced by ending the turn with a question.
- Network-level privacy (`--ingress internal` + VPC egress).
- A `langgraph dev` hot-reload mode.
