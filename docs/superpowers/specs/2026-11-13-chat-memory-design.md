# Chat memory: every agent knows the data fetched earlier in the chat

Status: draft for review (2026-11-13). Branch `0.8.0/chat-memory`; ships with 0.8.0.

## Why

The user's report (2026-11-13): "When the app shuts down mid question, and there is already data in the 2 previous questions, when asking to retry the last request (which needs the data), it fetches the data again instead of using existing data."

The user's direction: each chat has a memory that stores what was found in it; every agent can read it and understands the chat's context, the way claude.ai and ChatGPT keep a whole conversation (questions, answers, tool calls and tool results) and send it with every turn.

## What happens today

- **1v1 chats** already keep a specialist's tool calls and results on the thread (`RemoteAgent.node` returns the agent's new messages, `app/agents/remote.py:130-131,165`), so the next turn's agent sees them. Nothing caps their total size.
- **Team chats** keep only the final answer on the thread (`app/orchestration/team.py:745-749`). Each task's results sit in `additional_kwargs["team"]`, which no model reads. `plan` resets the workspace every message (`team.py:452`). A later question re-queries everything.
- **A crashed turn** (app or agent servers shut down) loses its finished tasks: they live only in the team subgraph's checkpoint, and the next message starts a new run. `remember_turn` saves nothing for an unfinished turn (`app/api/routes_chat.py:130`).
- **"Retry"** (`retry_tasks`, `team.py:168`) re-runs the tasks of the newest *finished* team answer. After a crash that is the turn before the crashed one, so it likely retries the wrong question (not yet reproduced).
- **The orchestrator's `ask_<route>`** gives the asked agent only the question (`app/agents/delegation.py:38`), not the chat.
- **Cross-chat memory** (knowledge graph, recent chats, `search_chat_history`) stores facts and 500-character answer excerpts, never fetched data. It stays as it is.

How Claude and ChatGPT do it (Anthropic and OpenAI docs, read 2026-11-13): the client keeps the full conversation, tool results included, and sends it every turn; long chats are kept in budget by clearing old tool results to a placeholder (oldest first) or by summarising older turns; cross-chat memory is a separate store; sub-agents work in their own context and return condensed results, with an external store for what must persist.

## Goals

1. Every agent call in a chat, in every mode, gets the data fetched earlier in that chat: team tasks, the plan, the review, the write-up, the chart task, 1v1 specialists, the orchestrator and the agents it asks.
2. Data is saved the moment each step finishes, so a crash keeps every finished step.
3. "Retry" after a crash re-asks the crashed question, and its agents use the saved data instead of querying again.
4. The chat's data has a size cap: newest in full, older shortened.
5. Deleting a chat or an account removes its chat memory.

## Non-goals

- Cleaning up unused memory (old LangGraph checkpoints, idle chats' data). Separate work, set aside by the user on 2026-11-13.
- Resuming a stopped or crashed run from its checkpoint (stop-and-resume, PR #36, on hold). This design neither needs nor blocks it.
- Changing cross-chat memory, the knowledge graph or `search_chat_history`.
- Showing the chat memory in the UI.
- A summarising (compaction) step: it would add a model call per long chat. Revisit only if the cap proves too tight.

## Anti-goals (a failure even if it works)

- More model calls per message. This design adds none (memory: extra-model-calls, mixed-models-cost-reporting).
- An optional "read chat data" tool that weak models would skip (memory: weak-models-optional-steps). The data is given to every call; nothing depends on the model choosing to fetch it.
- Agent servers writing app data. Only the main app writes (Invariant: agent servers run as `agent_reader`).
- Edits to `app/api/routes_chat.py` or `app/api/routes_conversations.py`: another session (branch `0.8.0/react-ui-m3`) is changing them.
- Data from one user or chat reaching another (Invariant: user scoping).

## Approaches considered

- **A. Data on the thread** (team tasks' tool messages join the parent thread, as in 1v1). Simple, but the thread gets nothing until the team subgraph finishes, so a crash still loses it; the history endpoint would need filtering in the other session's file; some providers reject tool messages for tools the model was not given.
- **B. A per-chat data log in Postgres, written as each step finishes, given to every agent call (chosen).** Survives crashes, touches neither shared route file, works the same in every mode. Costs a new table and a size cap.
- **C. Rebuild the data from earlier answers' `team` transcripts.** No new table, but tool results are cut at 4,000 characters and a crashed turn is still lost.

The user chose B, for all modes, on 2026-11-13.

## Design

### Storage: `chat_data` table

A new SQLAlchemy Core table in `app/core/db.py`, created by `init_db` like the others:

| column | meaning |
|---|---|
| `id` | serial primary key (orders entries) |
| `user_id` | FK `users.id` ON DELETE CASCADE |
| `conversation_id` | FK `conversations.id` ON DELETE CASCADE |
| `turn` | number of visible user messages in the thread when the step ran (1 = first question) |
| `step` | where it came from: a team task id (`t2`), `chart`, `reply` (1v1) or `ask` |
| `agent` | the route that called the tool |
| `tool` | tool name; `findings` for research's notes |
| `args` | JSON of the call's arguments |
| `content` | the result text the model saw, cut to `CHAT_DATA_ENTRY_CHARS` |
| `artifacts` | JSON `{tables, charts}` the call produced (tables keep their ids and rows, as today's artifacts) |
| `created_at` | timestamp |

Index on `(user_id, conversation_id, id)`. `chat_data` joins `READER_TABLES` (`app/core/db.py:153`) so agent servers can SELECT it. Memory tools (`MEMORY_TOOL_NAMES`) and failed tool calls (error results) are not saved.

Deleting a chat deletes its `conversations` row (`delete_conversation`), which cascades to `chat_data`; no route change. Account deletion: the builder confirms that it removes the `users` row (and so the chat rows); if it does not, add the delete next to the existing account cleanup.

### Writing (main app only)

A new module `app/core/chat_memory.py` with `save_entries(user_id, conversation_id, turn, step, agent, messages)` that pairs each tool-calling `AIMessage` with its `ToolMessage`s and writes one row per tool call (sync DB work runs through `asyncio.to_thread`). Call sites, each right after its step returns:

- team `speak` (`team.py`): the task's turn messages; for research, its `findings` as one `findings` entry.
- team `draw`: the chart task's messages.
- 1v1 `RemoteAgent.node`: the new messages it appends to the thread.
- orchestrator `ask_<route>` (`delegation.py`): the asked agent's messages, which today are not returned to the thread.

The conversation id comes from the graph config's `thread_id`; the user id from `AgentContext`. A failed save is logged and never fails the reply.

### Reading: the chat data note

`chat_memory.render(user_id, conversation_id)` builds one text block, oldest first, under a fixed heading (wording through the `writing-llm-document` skill), one entry per saved row: turn, agent, tool, the arguments (a query, a search phrase), table ids with row counts, and the content. Size control, no model call:

- Total budget `CHAT_DATA_CHARS` (setting). The newest entries are shown in full; once the budget is spent, older entries keep only their header line (turn, agent, tool, arguments, table ids and row count) and a marker that the result was shortened, so an agent knows the data existed and may query again if it needs the detail.
- The note is read fresh at each agent call (one indexed SELECT), so a later wave or a retried turn sees everything saved so far.

Who gets it:

- **Agents on agent servers** (team tasks, the chart task, 1v1, asked agents): `RemoteAgent.ainvoke` adds `conversation_id` and the rendered `chat_data` to the `AgentContext` it sends (two new fields in `app/core/memory.py`). `build_agent`'s `prompt_with_memories` (`app/agents/factory.py`) renders it after the existing sections, last in the system prompt so the cached prefix stays stable (prompt-cache layout).
- **In-process agents** (tests pass in-process graphs): the same fields, filled by the caller the same way.
- **The orchestrator, the plan call, the review and the write-up**: the same note, added to their input in `orchestrator.py` and `team.py`.
- **`ask_<route>`**: the asked agent now gets the note through its context, so it is no longer blind to the chat's data (it still gets only the question as messages).

The prompt rule (shared, in `app/core/prompts.py`): data in the chat data note was fetched earlier in this chat; use it when it answers the request and name its turn as the source; query only for what is missing or when the user asks for fresh data. Wording, placement and Vietnamese handling go through `writing-llm-document` (memory: reply-language, prompt-format-braces).

### Earlier tool messages no longer go to agents

The note replaces the thread's earlier tool messages as the way agents see old data, so the same data is not sent twice and the cap holds. `RemoteAgent.node` and the team's `speak` pass the thread without the tool-calling `AIMessage`s and `ToolMessage`s of earlier turns; the current turn is unchanged (`gave_up` and `turn_messages` read only the current turn). The thread itself is not changed, so history and reloads look the same.

Every place that reads earlier tables from the messages today keeps working by also reading the chat memory's tables (for this user and chat, by `context.conversation_id`), current-turn tables first:

- `chart_table` finds a table by id (`app/agents/chart/tools.py:129`).
- `chart_sql` reuses an earlier table of the same SQL (`same_query`, `chart/tools.py:100-101`) instead of running it again. This keeps today's behaviour, which already reuses a same-query table from any earlier turn on the thread (decision: the designer's call; the prompt's "query again when the user asks for fresh data" still applies to the agents' own SQL).
- The orchestrator's table note (`orchestrator.py:206-207`) and the chart agent's `tables_section` (`chart/agent.py:25-26`).

One helper (for example `chat_tables(user_id, conversation_id)` in `chat_memory.py`) serves all three, so they read the same set.

### Retry after a crash

A crashed turn is a visible user message with no answer after it, followed by the new message.

- **Retry message** (the existing `RETRY` words and `SMALL_TALK_WORDS` cap, `team.py:52,171`) after a crashed turn: the crashed question replaces the retry text as the turn's question. The workspace `goal` (today `messages[-1].text`, `team.py:452`) and the plan's question (today the last `HumanMessage`, `team.py:250-251`) both use the crashed question. The team runs a normal plan for it (one plan call, as for any question); the plan, tasks and write-up see the chat data note, which already holds the crashed turn's finished tasks. The write-up answers the crashed question.
- **Retry message after a finished turn:** `retry_tasks` as today.
- **Any other message after a crashed turn** (a new question): an ordinary turn. Its agents see the crashed turn's saved data through the note like any earlier data.
- **1v1 chats:** no special handling. The agent receives the thread (the crashed question, then the retry) and the note, and answers the crashed question.

The builder first reproduces today's behaviour with a failing test: does the crashed question reach the parent thread (this also decides `turn` numbering for rows saved during the crashed turn), and does retry pick the turn before it?

### Settings

- `CHAT_DATA_CHARS` (default chosen by the builder from measured sizes; starting point 24000): total size of the note.
- `CHAT_DATA_ENTRY_CHARS` (starting point 6000, as `RESULT_CHARS`): cap per saved result.

Both in `get_settings()` and `.env.example`.

## Cost and calls per message

- Model calls: no new ones. A question whose data is already in the chat should need fewer tool-calling steps (each skipped query saves a model step).
- Input tokens: each agent call carries the note, up to `CHAT_DATA_CHARS` characters (about 6,000 tokens at 24,000 characters) in a long chat. In 1v1 chats this replaces today's uncapped earlier tool messages, so long 1v1 chats get smaller.
- To report before merging: `scripts.bench_chat` calls and cost per scenario before and after, plus a scenario of three questions where the third reuses the first two's data, and a crash-then-retry scenario.

## Docs to update in the same branch

`ARCHITECTURE.md`, `.claude/rules/memory.md` (chat memory next to cross-chat memory), `team-mode.md` (workspace, retry), `data.md` (where tables live), `remote-agents.md` (`AgentContext` fields, `READER_TABLES`), `config.md` (settings).

## Left to the builder

- Exact column types, JSON shapes, and the note's line format.
- The budget defaults, after measuring typical entry sizes.
- How research's `findings` are split into entries.
- Whether the plan prompt shows the full note or only its header lines (it must keep the one plan call).
- Test layout. Tests that need Postgres use `requires_db` and must not touch other rows in the shared dev DB (memory: tests-shared-dev-db).
