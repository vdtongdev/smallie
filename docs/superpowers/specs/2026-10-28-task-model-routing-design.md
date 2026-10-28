# Task model routing: each team task runs on a model that fits its difficulty

## Goal

Spend less on easy work and use a stronger model on hard work, without extra model calls and without making non-technical users understand models. The team planner already makes one call per message; it now also rates each task `easy`, `medium` or `hard`. The user picks one **profile** (Economy, Balanced, Advanced) and up to three **models** (small, standard, strong) on their one endpoint and key. A fixed table turns difficulty plus profile into a model.

Success: existing users notice no change until they set a profile or a second model; on Balanced with three models set, team messages cost less than on one fixed model at comparable answer quality (measured with `bench_chat`, see Evaluation).

## Decisions

| Question | Decision |
|---|---|
| Providers | One endpoint and key per user, as today (`model_settings`). Three model names on it. No new credential store. |
| What the user chooses | One profile (Economy / Balanced / Advanced) and the models in three slots. The difficulty-to-model table is fixed in code and not shown. |
| Which calls are routed | Team-mode tasks (`speak`) by their difficulty, and every 1v1 message as a `medium` task (see 1v1 mode). The team's plan call, review, write-up and chart task (`draw`), fact extraction and document cards use the standard model, whatever the profile. |
| Where difficulty comes from | Team mode: the existing `ask_plan` call. No separate classification call. 1v1 mode has no plan call, so it is always `medium`. |
| Unknown or missing difficulty | `medium`, recorded as defaulted. |
| Fallback | Always on, no toggle. One retry on the standard model for model-level failures (below). |
| Quality-based escalation, spending caps, per-agent profiles, a profile per endpoint | Out of scope. |

## Profiles and models

Settings shows two groups (EN/VI via `i18n.js`):

- **Profile**: three cards. Economy, "spend less"; Balanced, "recommended" (default); Advanced, "best answers".
- **Models**: three dropdowns, filled from the endpoint's `GET /models` list (the save check already fetches it); free text when the endpoint gives no list.
  - **Standard model**: today's `model` field, required.
  - **Small model**, **Strong model**: optional; empty shows "Same as standard".

### Routing table

`pick_tier(difficulty, profile) -> tier`:

| Task difficulty | Economy | Balanced | Advanced |
|---|---|---|---|
| easy | small | small | standard |
| medium | small | standard | strong |
| hard | standard | strong | strong |

Economy moves every task one tier down and Advanced one tier up, clamped at the ends; a hard task never drops to small.

`pick_model(saved, tier) -> (model, tier_used, reason)`: the slot's model, or the standard model when the slot is empty (`tier_used = "standard"`, reason names the empty slot). Both functions are pure: identical inputs give the identical result.

Unknown `profile` values read as `balanced`; unknown tiers as `standard`.

## Storage

`model_settings` gets three columns: `small_model` (nullable), `strong_model` (nullable), `profile` (text, default `'balanced'`). `model` stays the standard model. The app has no migrations (`metadata.create_all`), so `init_db` adds the columns with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. `agent_reader` already reads the table. An existing row (only `model` set, profile `balanced`) routes every task to `model`: no behaviour change.

`ModelSettings` (`app/core/model_settings.py`) gains `small_model`, `strong_model`, `profile`. `save_settings` takes them; `load_settings` returns them.

## API

- `GET /api/model-settings` adds `small_model`, `strong_model`, `profile`. Never the key.
- `PUT /api/model-settings` accepts them (optional; omitted small/strong mean empty, omitted profile keeps the saved one). `profile` must be one of the three (400 otherwise). The existing `GET /models` check runs once; each set model name not in the list gets the existing "not listed" warning, naming the slot.
- `GET /api/model-settings/models` (new): the endpoint's model ids for the dropdowns, using the saved key; empty list on any failure.

## Classification

- `Task` (`app/orchestration/team_plan.py`) gets `difficulty: str = "medium"` and `difficulty_reason: str = ""`. Both have defaults: a missing required field rejects the whole plan (trap `structured-output-required-fields`).
- `PLAN_PROMPT` gets the rules, written about the task, never the agent:
  - **easy**: a clear, bounded task; a single lookup, extraction or straightforward tool use. A database query for items the user named or holds (several tickers or holdings, lookups, simple sums or averages) is easy.
  - **medium**: several steps, ordinary research, combining a little evidence, or a database query that needs judgement about what to compare or how to define a figure.
  - **hard**: real ambiguity, conflicting evidence, comparing several sources, nuanced synthesis.
- The three definitions are one shared constant, `DIFFICULTY_LEVELS` (`app/orchestration/prompts.py`), used by `PLAN_PROMPT` and `BOARD_PROMPT`, so the plan call and the review rate by the same rules.
- `validate_plan` lowercases the value; anything other than the three becomes `medium`. Each workspace task records `difficulty_source`: `plan`, `review` or `default`.
- A follow-up added by the review carries the review's `difficulty` and `difficulty_reason` (source `review`); a missing or invalid rating falls back to `medium`, source `default`.
- Tasks that skip the plan call take `medium`, source `default`: fallback plan, one named agent, retry tasks (`retry_tasks` keeps the old task's difficulty when it has one), reply tasks.

## 1v1 mode

1v1 has no plan call and never had more than one model, so there is no difficulty to read. Every 1v1 message counts as `medium`: the chat route (`app/api/routes_chat.py`) sets `tier = pick_tier("medium", saved.profile)` in the `AgentContext` it builds. So Economy runs 1v1 on the small model, Balanced on standard, Advanced on strong, and the profile means the same thing in both modes. The orchestrator, a handed-off specialist and the specialists it asks all run on that tier. Fact extraction after the reply stays on standard. The middleware fallback below applies.

The Activity popup of a 1v1 reply shows "medium (1v1) · Economy → small model" and, after a fallback, which model failed.

## Running a task on its model

- `AgentContext` (`app/core/memory.py`) gets `tier: str = "standard"`. A tier, never a model name or key.
- `speak` computes `tier = pick_tier(task.difficulty, saved.profile)` and passes it in the context. `pick_model` is run once there for the trace (model name, reason). The team's write-up and chart task run with `tier = "standard"`.
- The `user_model` middleware (`app/agents/factory.py`) calls `get_chat_model(user_id, tier=context.tier)`, which loads the row and applies `pick_model`. Agent servers get the context in the request body and run the same middleware: no new channel, the key stays out of requests and traces.
- `get_chat_model(user_id)` without a tier keeps returning the standard model, so every direct call (plan, review, fact extraction, cards) is unchanged.
- Parallel tasks in a wave each carry their own context; there is no shared selection state.

## Fallback

The fallback lives in the `user_model` middleware, so team tasks and 1v1 replies share it and no task or reply is rerun from the start. When a model call on `small` or `strong` fails with one of these, the middleware repeats that one call on the standard model, and every later call of the same agent run uses standard too (a `tier_failed` key in the agent's state, set by the middleware). The repeated call's `AIMessage` carries `response_metadata["fallback"] = {from_model, category}` for the trace. Categories:

| Category | Error |
|---|---|
| `model_not_found` | `openai.NotFoundError` (404) |
| `unsupported` | `openai.BadRequestError` (400), other than context length |
| `context_length` | 400 whose code or message is `context_length_exceeded` |
| `server_error` | `InternalServerError` (5xx), `APITimeoutError`, `APIConnectionError` |

No retry for:
- `RateLimitError` and `AuthenticationError`. They propagate as today; all slots share one key, so a second call cannot help.
- A task already on standard (including an empty slot that resolved to standard).
- A failure of the repeated call: it propagates as today (team: the task becomes `failed`; 1v1: the SSE error).

Limits, stated honestly: the repeated call counts toward `MAX_MESSAGE_MODEL_CALLS` / `TEAM_MAX_MESSAGE_MODEL_CALLS`. A 5xx in the middle of a streamed reply may already have streamed tokens from the failed model; the plan must check how the UI handles a reply that restarts, and if needed retry only failures raised before the first token. Each agent run (a team task, a 1v1 specialist, the orchestrator) learns about a broken slot on its own, so one message can see the same failure once per agent run. `CallBudgetExceeded` is never retried.

The middleware runs inside agent servers too, so the fallback happens there and its `response_metadata` travels back with the messages. The plan must confirm `RemoteAgent` keeps `response_metadata` on returned messages.

## Trace and what the user sees

Each workspace task gains:

- `difficulty`, `difficulty_source`, `difficulty_reason`
- `profile`, `tier`, `model`, `reason` (e.g. "medium task, Economy -> small model")
- `fallback`: `{from_model, category}` or `null`
- `usage`: `model_calls`, `input_tokens`, `output_tokens`, `cost` (`null` when the provider reported none, never `0`)
- `duration_s`

Per-task usage: `speak` passes its own `UsageCounter` in the task's config callbacks (in-process); an agent server already returns its run's totals. The request-level counter and the call budget keep working as today. `UsageCounter` tracks whether any reply reported a cost so the task's `cost` can be `null`.

Events: the `plan` event's tasks carry `difficulty`; `spoke` carries `tier`, `model`, `reason`, `fallback`, `usage`. Both reach `additional_kwargs["team"]` through `_transcript`, so a reload shows them.

Activity popup (`app/static/app.js`), per task: "Research · medium task · Economy → small model · 3 calls · 1,240 tokens"; with a fallback: "small model failed (model not found), used standard". Cost shows only when known.

Never in events, the workspace or logs: the key, the provider's raw error text. Only the category and HTTP status.

## Tests

Unit (no provider calls):
1. `pick_tier`: all 9 cells; unknown profile reads as balanced.
2. `pick_model`: each slot; empty small/strong -> standard with the reason.
3. Plan: missing difficulty, invalid value, mixed case -> as specified; a plan without the field parses; skipped-plan tasks get `medium`/`default`.
4. Middleware: `context.tier` selects the slot's model; no tier -> standard.
5. Two tasks in one wave with different tiers each run on their own model.
6. Fallback (middleware): each retry category repeats the call once on standard, records `fallback`, and later calls of the run stay on standard; rate limit, auth, a standard-tier failure and a failure of the repeated call do not retry.
6a. 1v1: the chat route sets the tier from the profile's medium row (Economy -> small, Balanced -> standard, Advanced -> strong); fact extraction stays on standard.
7. Trace: unknown cost is `null`; no key or raw error text in the workspace or events.
8. Compatibility: an existing row (only `model`, profile `balanced`) runs every task, 1v1 and direct call on `model`; `init_db` on an old table adds the columns.
9. API: `GET` never returns the key; invalid profile -> 400; unlisted slot model -> warning naming the slot; one user cannot read or change another's settings.

DB tests use `requires_db`.

## Evaluation

After the tests pass, and only with the user's go-ahead (spends quota): `scripts.bench_chat` team scenarios, `--runs 2`, once with only the standard model and once on Balanced with all three slots set on the same endpoint. Compare per scenario: calls, cost (OpenRouter `GET /api/v1/key` usage delta), latency, fallbacks, and answer quality (planted error flagged, choice precision/recall). A cheaper setup that answers worse or falls back often is not a win.

## Docs

`ARCHITECTURE.md` (model selection, settings table), `.claude/rules/team-mode.md` (Task fields, `speak`, fallback, trace), `.claude/rules/config.md` (three models and profile), Readme dialog (EN/VI: what the profiles do), `.env.example` unchanged.
