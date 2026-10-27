# User model keys: each account brings its own OpenAI-compatible server

## Goal

Every account, the admin's included, chats on its own model server: an OpenAI-compatible endpoint, an API key and one model name, entered in a Settings dialog. The operator's OpenRouter key pays only for embeddings and is never used for chat, not even as a fallback.

## Decisions

| Question | Decision |
|---|---|
| Which servers | Any OpenAI-compatible endpoint (DeepSeek, OpenAI, OpenRouter, DashScope, ...). |
| Models | One model name per user, used by every chat role: orchestrator, specialists, team planner and judge, fact extraction, document cards. |
| Key storage | On the server, encrypted with `MODEL_KEY_SECRET` (Fernet, `cryptography` is already in `uv.lock`). The full key never goes back to the browser. |
| No settings | The chat does not start: SSE error `no_model`, "Set your API key in Settings". No model call is made. |
| User's server fails | The error is shown and names the user's server host. No fallback. |
| Embeddings | Stay on the operator's key, renamed `OPENROUTER_API_KEY` -> `EMBED_API_KEY`. Embeddings need one model at a fixed `EMBED_DIM` for all users, and some providers (DeepSeek) have none. |
| Usage box | Hidden in the UI for now. Usage counting stays (the model-call budget uses it). |
| Agent servers | Read the user's row themselves as `agent_reader` and decrypt with `MODEL_KEY_SECRET`. The key is never in request bodies, headers or `AgentContext`, so never in traces or run records. |

## UI

- A **Settings** button in the left sidebar opens a dialog (EN/VI via `i18n.js`) with three fields: **API key** (password field; once saved, shown masked as `sk-…a1b2`), **API endpoint**, **Model**. A **Clear** button deletes the settings.
- A field saves when it loses focus, once all three are filled and the check below passes. "Saved" / "Đã lưu" shows at the top of the dialog; a failed check shows its message there instead.
- The Readme dialog gets a section in both languages with this table, plus "Any OpenAI-compatible service works" and the privacy line "Your key is stored encrypted on the server and sent only to the provider you chose. The app's own OpenRouter key is used only for embeddings (document and memory search)."

| Provider | Key page | API endpoint | Model |
|---|---|---|---|
| DeepSeek (cheap, takes local cards) | platform.deepseek.com | `https://api.deepseek.com/v1` | `deepseek-chat` |
| OpenAI | platform.openai.com | `https://api.openai.com/v1` | `gpt-4o-mini` |
| OpenRouter | openrouter.ai | `https://openrouter.ai/api/v1` | your choice |
| Alibaba DashScope | dashscope.aliyun.com | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |

- The usage box is hidden.

## Saving: the connection check

`PUT /api/model-settings` (body: `base_url`, `model`, optional `api_key`; an omitted key keeps the saved one) runs, in order:

1. **Endpoint safety**: see SSRF below. Refused -> 400 with the reason.
2. **Key-prefix hints**, no network: a `sk-or-` key with a host other than `openrouter.ai` -> "This looks like an OpenRouter key; use https://openrouter.ai/api/v1". A `sk-ant-` key -> "This looks like an Anthropic key; use https://api.anthropic.com/v1/".
3. **`GET {base_url}/models`** with the key, 10 s timeout:
   - 401/403 -> 400 "The key was rejected by this endpoint. Check that key and endpoint come from the same provider."
   - unreachable, timeout, or a body without a `data` list -> 400 with what happened.
   - the model is not in `data` -> saved, with a warning in the response ("model not listed by this server; check the name"). Some providers list models incompletely.

`GET /api/model-settings` returns `base_url`, `model`, `key_hint` (`sk-…` + last 4) or 404. `DELETE` removes the row. All three are scoped by `user_id` from `get_current_user`.

## Storage

Table `model_settings`: `user_id` (PK, FK to accounts, delete cascades with the account), `base_url`, `model`, `api_key` (Fernet token), `updated_at`. Created by `init_db`. Added to `READER_TABLES` so `grant_reader` gives `agent_reader` SELECT on it. `MODEL_KEY_SECRET` is required at startup of the main app and every agent server (startup fails without it, as with `AGENT_URLS`); `.env.example` documents it and how to generate one.

## Model calls

- `get_chat_model(user_id, *, streaming=True)` in `app/core/llm.py` loads the user's row (sync DB work via `asyncio.to_thread` where called from async code), checks endpoint safety, and returns a `ChatOpenAI`-based client for that endpoint, key and model (temperature 0, `stream_usage=True`, `max_retries=1`). Clients are cached per user and rebuilt when `updated_at` changes. No row -> raises `NoModelSettings`. It never reads `EMBED_API_KEY`.
- The OpenRouter `usage.cost` capture stays (cost shows when an endpoint reports it). The OpenRouter-only request extras go: `models` fallbacks, `provider.quantizations`, `provider.sort`. Settings removed: `OPENROUTER_CHAT_MODEL`, `OPENROUTER_FALLBACK_MODEL`, `OPENROUTER_ORCHESTRATOR_MODEL`, `OPENROUTER_JUDGE_MODEL`, `OPENROUTER_QUANTIZATIONS`, `OPENROUTER_PROVIDER_SORT`. `build_agent` loses `model=`; `get_chat_model` loses `model` and `exact_model`.
- **Agents** (`build_agent`): a `wrap_model_call` middleware, first in the list, replaces the request's model with `get_chat_model(context.user_id)`. The model passed to `create_agent` at build time is a placeholder with no usable key, so a call that skipped the middleware fails rather than spends anyone's money.
- **Direct calls** pass the `user_id` they already have: team planner and judge (`app/orchestration/team.py`), fact extraction (`app/core/graph.py`), document cards (`app/features/documents/card.py`).
- **Agent servers** use the same `get_chat_model` (same code, same image); `agent_reader` can read the row and the server has `MODEL_KEY_SECRET`.
- **Chat route**: before streaming, if the user has no settings -> SSE `{"type":"error","code":"no_model"}` then `done`. `openai.AuthenticationError` and `RateLimitError` messages name the user's server host instead of `OPENROUTER_API_KEY` / OpenRouter. The remote error mapping (`_OPENAI_ERRORS`) keeps working unchanged.
- **Scripts**: `bench_chat` and `build_graph` use the saved settings of the account they act for; `build_graph` skips users without settings. `reindex` uses `EMBED_API_KEY`.

## Endpoint safety (SSRF)

The server, not the browser, calls the endpoint, so a user could otherwise make it call internal services (Cloud Run metadata server, Redis, Postgres). `check_endpoint(url)`:

- scheme `https`, a host, no user-info;
- every address the host resolves to is public: not private, loopback, link-local (incl. `169.254.169.254`), multicast, reserved or unspecified, IPv4 and IPv6.

Runs on save and each time a client is built. `ALLOW_PRIVATE_MODEL_URLS=true` (dev only, default false) allows `http` and private addresses, so a local Ollama works when the app runs on the same machine. Known limit: a host that re-resolves to a private address between the check and the request (DNS rebinding) is not caught.

## Superseded

Branch `0.5.1/split-models` (sql and chart on a different `OPENROUTER_CHAT_MODEL` in compose) and Task 2/3 of `docs/plans/2026-10-27-judge-empty-review-and-model-split.md` no longer apply: there is one model per user and that setting is removed.

## Cut-over

After deploy no account can chat until it has saved settings, the admin's included. Deploy steps add `MODEL_KEY_SECRET` (all five services) and rename `OPENROUTER_API_KEY` to `EMBED_API_KEY`.

## Docs

`ARCHITECTURE.md` (model calls, agent servers' DB access, new table, settings), `DEPLOYING.md` (new and renamed variables), `.env.example`, `.claude/rules/config.md` and `.claude/rules/remote-agents.md` (agent_reader now reads `model_settings`).

## Tests

- Removed, because this design removes the behaviour: the fallback, quantization and provider-sort tests in `tests/test_llm.py`.
- New: no settings -> `NoModelSettings`, and the operator's key is never used for chat; `check_endpoint` refuses private, loopback, link-local and non-https URLs and allows them under `ALLOW_PRIVATE_MODEL_URLS`; the middleware gives each user their own model; chat without settings sends `no_model` and makes no model call; the save check maps 401, unreachable and unlisted model as above; the GET never returns the key; one user cannot read or change another's settings; `agent_reader` can read `model_settings` and cannot write it.
