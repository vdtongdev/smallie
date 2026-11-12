# Turn activity saved for reloads: design

Date: 2026-11-12. Status: approved in conversation, awaiting review of this file.
Release: 0.8.0, with the React UI (`2026-11-12-react-ui-migration-design.md`). Built on branch `0.8.0/react-ui-m2` before M2 Task 3 is finished.

## Problem

A reloaded chat shows less than it showed live. The thread in the LangGraph checkpointer keeps only what later prompts need: the orchestrator writes only its final `AIMessage` (`.claude/rules/orchestration.md`), and research keeps its notes in `findings`, never in the messages (`.claude/rules/research.md`). So after a reload:

- a research answer is badged "orchestrator", and its web search is gone from the Activity popup;
- a tool step the orchestrator ran through a specialist (`tool_call` with `agent`) loses that agent, and moves from under the agent to the plain tool list;
- workflow `step` badges, `routing`, model fallbacks and a workflow `fallback` are gone.

Measured on the 7 recorded chats (`frontend/src/lib/__fixtures__/`): `research` and `vi` differ; the answer text and charts match in all 7. The old UI behaves the same. Nothing is wrong with the answers; only the shown process is lost.

## Goal

A reloaded 1-to-1 or team turn shows the same badges, steps, tools and Activity popup as it did live, without putting anything new into prompts.

## Design

**Save (backend, `app/api/routes_chat.py`).** While `events()` streams a reply, it also appends each event it yields to a list, except `token`, `usage`, `done` and `conversation` (text is already in the thread; usage has its own columns). A `tool_result` `content` is cut to 4000 characters (the cut `_serialize` already uses). Before the run it counts the messages `_serialize` shows as `role: "user"` in the thread (`n_before`; `_chat_input` already reads the state for interrupts, reuse that read where possible); in the `finally` block, after usage is saved, it counts again (`n_after`). `turn` = `n_after`. If the run added no user message (`n_after == n_before`): a resume of a paused team turn saves under `n_before` (the user message the paused turn answers); anything else (e.g. `no_model` before the graph ran) saves nothing, because that user message is not in the history to attach to. It saves for failed and cut-off replies too, whenever the rule above gives a `turn`. Saving is best effort: an error is logged and the reply is unaffected (same pattern as `add_conversation_usage`). Through `asyncio.to_thread`.

**Table (`app/core/db.py`).** `turn_activity`: `id` BigInteger identity primary key; `user_id` String(64) FK `users.id` ON DELETE CASCADE, indexed; `conversation_id` String(36) FK `conversations.id` ON DELETE CASCADE; `turn` Integer (1-based index of the user message the reply answers); `events` JSONB; `created_at`. Index on `(conversation_id, turn)`. Created by `metadata.create_all` at startup like every app table. Deleting a chat or an account removes the rows through the cascades.

**Read (`app/core/conversations.py` + `app/api/routes_conversations.py`).** `get_turn_activity(user_id, conversation_id) -> dict[int, list[dict]]`: every row of that chat for that user, grouped by `turn`, events of rows with the same `turn` concatenated in `id` order (a resumed team turn saves a second row for the same user message). `GET /api/conversations/{id}/messages` adds `activity` to each user message: the events for its 1-based position, or `null` when there is none (chats from before this change).

**Show (frontend, `frontend/src/lib/history.ts`).** For a user message with `activity`, the turn's badges, step badges, `activity` (routing, fallbacks, tools, asked) and pending state come from reducing those events with `reduceTurn` (the same reducer as live); the text blocks and charts come from the history messages, as now. Without `activity`, history works as it does today.

## What does not change

- Prompts and model calls: the rows are outside the thread and nothing reads them for a prompt. No extra model call; one extra DB read and one write per reply.
- The old UI: it ignores the new `activity` field.
- Agent servers: they never read or write the table (`agent_reader` needs no grant).
- User scoping: every query filters by `user_id`.

## Tests

- Backend: a 1v1 reply saves one row whose events are the streamed events minus `token`/`usage`/`done`/`conversation`, with `turn` = its user message's position; a long `tool_result` is cut to 4000 characters; a failed reply still saves its events; a save error does not break the reply; the messages API attaches `activity` to the right user message and `null` to older ones; two rows with the same `turn` are concatenated; another user's rows are never returned; deleting the chat removes its rows. DB tests use the `requires_db` marker and clean up only their own rows (trap `tests-shared-dev-db`).
- Frontend: the 7 fixtures are re-recorded after this change; the replay test then requires a reloaded turn to equal the live one on badges (with steps), text, chart ids, tools, asked agents and their tools, routing and fallbacks, with no exceptions.

## Docs

`ARCHITECTURE.md` (the table and the history field) and `.claude/rules/orchestration.md` "Chat flow" (the saved events) are updated in the same branch.
