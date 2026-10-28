---
paths:
  - "app/core/config.py"
  - ".env.example"
  - "scripts/bench_chat.py"
---

Settings, LangSmith tracing and the model-call budget.

- Config is `pydantic-settings` reading `.env` (`app/core/config.py`, cached via `get_settings()`). SQLAlchemy URLs get the `postgresql+psycopg://` prefix via `sqlalchemy_url` properties, while psycopg/LangGraph use the raw `DATABASE_URL`.
- LangSmith tracing is off by default (`LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`). `export_tracing` (`app/core/config.py`, first step of the lifespan) copies them from `Settings` into `os.environ` and clears the SDK's cached env reads, because `.env` never reaches `os.environ`. Scripts don't call it; set the variables in the shell to trace them. Chat runs carry `metadata.user_id`. Traces hold full prompts (users' messages, file passages, memories).

- Model keys: each account's chat models come from its saved settings only: one endpoint and key, the standard model (`model`), optional `small_model` and `strong_model` (empty means standard), and `profile` (`economy`, `balanced` or `advanced`). `pick_tier(difficulty, profile)` and `pick_model(saved, tier)` (`app/core/llm.py`) choose the model; `get_chat_model(user_id, tier=...)` defaults to standard. There is no operator chat model. The only retry is the `user_model` middleware's fallback (`app/agents/factory.py`): one repeated call on standard after a model-level failure, never for rate limit, auth or budget. A task's `cost` is null unless a reply reported one (`UsageCounter.cost_known`). `MODEL_KEY_SECRET` (Fernet) encrypts the saved keys and is required by the app and every agent server. `EMBED_API_KEY` is the operator's OpenRouter key, used only for embeddings. `ALLOW_PRIVATE_MODEL_URLS` (default false) is dev only. Never log or return a saved key.
- Model-call counts below were measured with the old fixed free models, before per-user settings. Every upload takes 1 (the document card); a general question takes 1 call (+1 for fact extraction after the reply, on every turn the orchestrator answers), an obvious specialist question 2–5, a handed-off one 1 more, a composed one 1 + each specialist's + 1 (research: up to `RESEARCH_MAX_MODEL_CALLS` + 1 for the write-up, +1 when not obvious). Team mode: 1 plan call (none for small talk or one named agent) + each task's calls + 1 review call per reviewed wave + 1 per challenge or conflict round + 1 write-up + the chart task, up to `TEAM_MAX_MESSAGE_MODEL_CALLS` (30); measured on the task graph (2026-10-26): 2 calls with no specialist, 4 (stock question), 7 (planted error), 15 (all agents). `scripts.bench_chat` measures calls and latency per scenario on the real models.
