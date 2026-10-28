# Stop and resume a running reply

Date: 2026-10-28. Status: approved design, not built. Version: 0.5.4 (small feature; branch `0.5.4/stop-resume`).

## Why

A reply can run for a long time: a team message spends up to 30 model calls and one to three minutes, and a normal chat can ask several specialists. Today the user cannot stop it. They watch a run go the wrong way, or spend quota on a question they would phrase differently. The goal: the user can stop a running reply in normal (`auto`) chats and team chats, and later resume it, either as it was or with a note that steers it. Work that already finished is kept.

## Decisions

- **Cancel at once, resume from the LangGraph checkpoint** (approach A). The checkpointer already saves every finished step, so stopping keeps finished work, and Stop takes effect immediately.
  - Rejected: pause at the next step boundary (a stop flag each node checks, then `interrupt()`). Stop could lag 30 s or more while an agent runs, and every node needs a check.
  - Rejected: restart with a transcript of what was done. It repeats paid calls and loses team task state.
- **Keywords trigger a plain resume; any other message resumes with that message as a note.** No model call classifies the message, because weak models misjudge it and it would cost a call per message after a stop.
- **A stopped run is dropped only by opening a new chat.** Every message in a stopped chat resumes it.

## 1. Stop

### UI (`app/static/app.js`, strings in `i18n.js`)

- While a reply streams, the Send button becomes a Stop button.
- Clicking it calls `POST /api/chat/{conversation_id}/stop`, then aborts the fetch.
- The partial reply stays on screen, greyed, with the line "Stopped. Type continue to resume, or add a note." (English and Vietnamese).
- The partial text is not saved to the thread.

### Server (`app/api/routes_chat.py`)

- A per-process registry `running[conversation_id] = asyncio.Task` wraps the `astream` loop of each chat request.
- `stop` cancels that task. It is scoped by `user_id`: a conversation of another user answers 404.
- A cancelled stream sends a final `stopped` SSE event instead of `done`.
- Agent servers stop by themselves: the cancelled httpx call disconnects, and `RemoteAgent` already runs with `on_disconnect="cancel"` (`app/agents/remote.py`).
- `remember_turn` (fact extraction) is skipped for a stopped turn.

### The stopped state

A chat is stopped when its checkpoint snapshot has a non-empty `next` and no `interrupts`. Nothing new is stored: the checkpointer already holds this. A network drop leaves the same state, so a dropped connection is resumable too.

Limit: the registry is in-process. The plan checks the uvicorn worker count in `DEPLOYING.md`; with more than one worker the registry moves to Redis, which is already in the stack.

## 2. Resume and steering

`_chat_input` already turns a reply to a team question into `Command(resume=...)`. A new branch handles the stopped state, in `auto` and `team` chats.

- **Plain resume.** A message of a few words that contains a resume word (`resume`, `continue`, `go on`, `tiếp tục`, `làm tiếp`, `tiếp đi`) continues the run with `astream(None, config)`. The keyword message is not added to the thread.
- **Resume with a note.** Any other message joins the thread as a normal visible user message, followed by a hidden `STEER_MESSAGE` (`prompts.py`): the user stopped the run and added this message; treat it as a correction or addition to the task in progress, not as a new question. Then `astream(None, config)` continues the run.
  - The node that was cut off reruns from its start and sees the note.
  - The reply language follows the note, because it is the newest user message.

### Team mode

- The note also goes into `workspace["notes"]`.
- `TASK_MESSAGE` shows the notes to every task not yet done.
- `review` and `synthesize` get the notes too, so the board can add a follow-up when a note asks for more ("also check my files").
- Tasks that already finished do not rerun.
- The question pause (`needs_human`, `pause`) is unchanged.

### Call budget

A resumed run keeps counting against the same message's call cap (`TEAM_MAX_MESSAGE_MODEL_CALLS`), so Stop and continue cannot get around the cap. The plan finds where `RemoteAgent` keeps the count and carries it across a resume.

### Spike first

The plan's first task proves two assumptions with a throwaway test:

1. A cancelled `astream` keeps the writes of tasks that finished in the step that was running (the team's parallel `speak` Sends), so only unfinished tasks rerun.
2. `aupdate_state` can add the note messages without changing `next`.

Fallback for 2: a `steer` state key that the nodes read, written without touching `next`.

## 3. Reload

- `GET .../messages` appends a `stopped: true` marker when the chat is stopped; the UI shows the stopped line.
- Text that streamed before the stop is gone after a reload; only finished steps are kept.
- Team chats: the Activity popup is built from the checkpoint's `workspace`, so finished tasks show while the chat is stopped.

## 4. Edge cases

1. **Stop before the first checkpoint** (during the router, which makes no model call): resume reruns from the start at no cost.
2. **Stop after the reply is done:** the endpoint answers `{"stopped": false}`.
3. **A second message while a run is going:** 409. The UI already blocks it; this is a backstop.
4. **Network drop:** the same stopped state; the "connection lost" message adds "type continue to resume".
5. **Limit, parallel asks:** the orchestrator's `ask_*` calls run inside one tools step. If Stop hits during that step, all of them rerun on resume, finished ones included. Team tasks are separate steps and do not have this limit.

## 5. Testing

Fake models, no quota spent; `requires_db` where checkpoint state is needed.

1. The keyword check matches English and Vietnamese resume words; longer messages do not match.
2. A team run stopped mid-wave keeps the finished task and reruns only the unfinished one.
3. A note reaches a pending task's `TASK_MESSAGE` and the review; a plain resume adds no message to the thread.
4. The call count carries across a resume.
5. Stop is scoped by `user_id`: another user's conversation answers 404.

Plus one manual check in the browser: stop a team reply mid-wave, reload, type a note, see it resume.

## 6. Docs and release

Update `ARCHITECTURE.md`, `.claude/rules/orchestration.md` and `.claude/rules/team-mode.md` in the same branch. The feature ships in patch 0.5.4.
