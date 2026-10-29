# Prompt cache layout and token footer

Date: 2026-10-29. Branch: `0.6.1/prompt-cache-layout`. Part A of the layered-prompt work; part B (SOUL persona and BOOTSTRAP onboarding) gets its own spec after this ships.

## Goal

Make each user's model provider reuse its prompt cache across the model calls of one message and across the turns of one chat, and show the user what the cache saves.

Providers with prefix caching (OpenAI, DeepSeek, Gemini, vLLM and others behind OpenRouter) reuse only the part of a request that is byte-identical to an earlier one from its first token on. Today three things break that prefix early in the prompt:

1. The orchestrator's system prompt changes between the calls of one message: `compose_section` appends per-request notes and the reply language on every call, plus `TABLES_SECTION`, the formats and the citation rules once it is composing. The history after the system prompt is then billed in full on every call.
2. `MEMORIES_SECTION` and `RECENT_CHATS_SECTION` are rebuilt on every turn. `recall_facts` lists the newest facts first, so each fact `remember_turn` saves changes the system prompt and the next turn pays for the whole history again. Recent chats change whenever the user writes in another chat.
3. `RESEARCHER_SYSTEM` starts with `Today is {today}`, so the researcher's whole prompt changes every day.

Nothing measures this yet: `UsageCounter` reads no cached-token figure.

## Out of scope

- The SOUL persona and BOOTSTRAP onboarding (part B).
- Splitting the user's profile from the other facts. Both are frozen per chat (below), so the split gains nothing here; part B needs it for BOOTSTRAP's condition.
- Explicit cache markers (`cache_control`). Only automatic prefix caching is targeted.
- Moving prompts out of Python constants into files.

## Success criteria

- The share of cached input tokens rises in `bench_chat` scenarios with several model calls or several turns, on a provider that reports cached tokens. Where the provider reports none, the result says so instead of claiming a gain.
- `bench_chat` routing choices and reply language are no worse than the baseline run.
- The footer under the chat box shows input, output and cached tokens and the hit rate for the last reply and for the whole chat, and still shows them after a reload.

## 1. Prompt order and the tail note

Each model request is laid out from the most stable part to the least stable:

| Order | Part | Changes |
|---|---|---|
| 1 | tools (unchanged; fixed order per agent, filtered per source set in team mode) | per agent / source set |
| 2 | the agent's static `system_prompt` | on deploy |
| 3 | the chat's snapshot: `USER_SECTION`, `MEMORIES_SECTION`, `RECENT_CHATS_SECTION` (section 2) | per snapshot |
| 4 | the mode-stable extra: `research_prompt_extra` (source list, skills, schema; depends only on `context.sources`) | per mode |
| 5 | the thread's messages | grows; earlier messages never change |
| 6 | the tail note | every call |

The snapshot comes before the mode-stable extra, as `build_agent` appends them; both stay fixed for a chat, so every call within a chat reuses them either way.

Parts 2 to 4 form the system prompt (`build_agent`'s dynamic prompt). Nothing in them may depend on the current message or call.

The tail note is a new `build_agent(tail=...)` argument: a callable `(ModelRequest) -> str`. A `wrap_model_call` middleware adds its text as one hidden `HumanMessage` after the last message of the model request, through `request.override(messages=...)`. It is not saved to the thread, not streamed and not seen by fact extraction; its name joins `HIDDEN`. An empty text adds no message. `make_last_call` puts `LAST_CALL_NOTE` into the same message instead of adding a second one.

Moved into the tail:

- From the orchestrator's `compose_section`: `AgentContext.notes`, `REPLY_LANGUAGE`, and, only while composing (`is_composing`, as today), `TABLES_SECTION` and `COMPOSE_SECTION` with the formats and citations.
- From the chart agent's `prompt_extra`: `table_note`.
- From `RESEARCHER_SYSTEM`: `Today is {today}`.

The tail is built from `request.messages` without the tail itself, so `is_composing` and `reply_language` see what they see today.

Risk: these instructions move from the system role to a final user-role message. Weak models may follow them better (language) or worse (routing). The `bench_chat` comparison in section 4 decides; if routing gets worse, `notes` move back into the system prompt in this branch.

## 2. The frozen memory snapshot

The memory part of the system prompt is read once and reused for the rest of a chat. Facts learned during the chat are already in its history, so the snapshot does not need them.

Storage: two columns on `conversations`, added in `init_db` with `ALTER TABLE conversations ADD COLUMN IF NOT EXISTS`:

- `memory_snapshot jsonb`: `{"user": ..., "memories": ..., "recent_chats": ...}`, the three strings `AgentContext` carries today.
- `snapshot_at timestamptz`.

Each turn `routes_chat`:

1. Reuses the snapshot when it exists and the conversation's `updated_at` (its last turn) is less than `MEMORY_SNAPSHOT_MINUTES` ago. The new setting defaults to 60, matching the longest common provider cache lifetime, so a refresh happens only after the cache has expired anyway. It is read through `get_settings()` and documented in `.env.example`.
2. Otherwise builds it as today (`recall_facts`, `_recent_chats`, `_user`), saves it with `snapshot_at = now()` and uses it.
3. Fills `AgentContext.user`, `memories` and `recent_chats` from it. The `AgentContext` fields do not change, so agent servers, team mode and the `ask_<route>` tools need no change.

All DB work runs through `asyncio.to_thread` and is keyed by `user_id` and conversation id.

Clearing: deleted data must never reach a prompt again. These routes set `memory_snapshot = NULL` on every conversation of that user (and of no other user):

- deleting a fact (`/api/memories`, `delete_fact`);
- deleting a document (the snapshot can name the file);
- deleting a conversation (the other chats' `recent_chats` can quote its turns). Its facts stay in the graph, as today, so this is only about the quoted turns.

The next turn of each chat then builds a new snapshot. Deleting a conversation or an account removes its row and with it the snapshot.

`scripts.bench_chat` builds its context the same way as `routes_chat`, so the benchmark measures the snapshot's real behaviour.

## 3. Token counting and the footer

Counting (`app/core/usage.py`, `UsageCounter`):

- `cached_tokens` sums `usage_metadata["input_token_details"]["cache_read"]` (filled by `langchain_openai` from `prompt_tokens_details.cached_tokens`).
- `cache_known` is set when any reply carried `input_token_details.cache_read`, like `cost_known` for cost. When no reply did, cached tokens are unknown, not zero. Unknown happens when the provider sends no `prompt_tokens_details` (local servers such as Ollama or llama.cpp, some hosts behind OpenRouter), when a call fails before its usage arrives, and when an agent server still runs an image without this change. A reported 0 is known: the provider caches but reused nothing.
- `totals()` includes `cached_tokens` only when `cache_known`; an absent key means unknown. `add()` sums `cached_tokens` when the key is present and then sets `cache_known`. So agent servers report it back through `RemoteAgent` like the other totals, and an old agent server's totals still add up.
- The hit rate is `cached_tokens / input_tokens` (LangChain's `input_tokens` includes the cached ones). It is computed for display and never stored.

Saving:

- Per user: unchanged. `add_usage` keeps input, output and cost; cached tokens are kept per conversation only, since nothing shows a per-user cached total. The sidebar usage box does not change.
- Per conversation: new `conversations` columns `input_tokens`, `output_tokens`, `cached_tokens` (bigint, not null, default 0), `cache_known` (boolean, not null, default false; true once any reply of the chat reported cached tokens) and `last_usage jsonb` (`input_tokens`, `output_tokens`, `cached_tokens`, `cache_known` of the latest reply), added with `ADD COLUMN IF NOT EXISTS`. `routes_chat`'s `finally` block updates them in one statement, `SET input_tokens = input_tokens + :input, ...` keyed by conversation id and `user_id`, so failed and cut-off replies count, as they do in the user's totals today.
- "Reply" means the whole message: every model call it caused, specialists and agent servers included. Existing conversations start at 0; nothing is backfilled.

Reaching the UI:

- A new SSE event `usage`, `{"reply": {...}, "chat": {...}}`, sent after the numbers are saved and before `done`.
- A new `GET /api/conversations/{id}/usage` returns the same object (404 for another user's or a missing chat), so a reload shows the footer. `GET .../messages` stays a list.

Footer: two small muted lines under the chat box (`app.js`, `style.css`, English and Vietnamese strings in `i18n.js`), hidden while the open chat has no usage:

```
Last reply: in 12,400 · out 380 · cached 9,800 (79%)
This chat:  in 61,200 · out 2,100 · cached 44,500 (73%)
```

With `cache_known` false the cached part reads "cached n/a" and shows no percentage.

## 4. Tests, measurement and order of work

Order:

1. Counting, saving and the footer (section 3), so the cache can be measured before prompts move.
2. Baseline: `bench_chat` on that build. It prints input, output and cached tokens and the hit rate per scenario, next to its routing and language results.
3. Prompt order and tail note (section 1) and the snapshot (section 2).
4. `bench_chat` again on the same scenarios and models; the two runs go into the PR description.

Each `bench_chat` run spends free quota.

Tests, written from this spec:

- Two model calls of one orchestrator message get a byte-identical system prompt, and the tail note is the last message of each request.
- The tail note never appears in the saved thread, in the SSE stream or in the input of fact extraction.
- `RESEARCHER_SYSTEM` holds no date; the researcher's tail does.
- `make_last_call` adds no second hidden message.
- Snapshot (`requires_db`): reused within `MEMORY_SNAPSHOT_MINUTES`; rebuilt after the gap and when missing; cleared for that user's chats only when a fact, a document or a conversation is deleted; another user's snapshots stay.
- `UsageCounter`: counts `cache_read`; `totals()` has no `cached_tokens` when no reply carried cache details; a reported 0 is kept as 0; totals survive `add()`, also from a server that sends no cached key.
- The per-conversation usage update adds to the totals, replaces `last_usage`, and touches only that user's conversation; `GET /api/conversations/{id}/usage` returns it.
- `tests/test_orchestrator.py` checks that read the notes, `REPLY LANGUAGE`, `CITATIONS` or the tables note from the system prompt read them from the tail message instead (same content); each changed test is named in its commit message.
- Footer: checked in the running app in English and Vietnamese, before and after a reload.

## Docs updated in this branch

- `ARCHITECTURE.md`: prompt layout, memory snapshot, token footer.
- `.claude/rules/memory.md` (snapshot), `orchestration.md` (tail note, `usage` event), `agents.md` (`build_agent(tail=...)`), `config.md` (`MEMORY_SNAPSHOT_MINUTES`).
- `.env.example`: `MEMORY_SNAPSHOT_MINUTES`.
