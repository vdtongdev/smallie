# Plan: team mode never asks the user back their own message

Branch `0.6.1/ask-back-repeat` (worktree `C:\Users\Asus ROG\projects\smallie\.claude\worktrees\ask-back-repeat`). Failing test already committed: `tests/test_team.py::test_review_question_that_repeats_the_users_message_does_not_pause` (commit `4d3f948`).

## Behaviour wanted

The team turn pauses to ask the user (`needs_human`, `pause` node) only with a real question about information only the user has. A review `question` that restates a user message in the thread (the same text, or nearly the same after case, punctuation, whitespace and small wording changes) is not a question: the turn does not pause, adds no orchestrator message, and goes on as if `question` were empty (follow-ups, challenges or the write-up).

Observed case: conversation `4666acba-...`. The review model filled `Board.question` with the user's message, `review` paused on it (`app/orchestration/team.py:545`, `:620-622`), and the user saw their own message asked back. The user did not stop the chat; the pause came from this.

## Task 1: guard plus prompt and schema (Agent: implementer)

1. Tests first, in `tests/test_team.py`, written from the behaviour above (reuse the harness of `test_needs_human_pauses_and_resume_continues` and the committed failing test):
   - a near-copy (the user message with changed case, an added question mark, extra whitespace, or a short lead-in word) does not pause;
   - a restatement of an earlier user message in the thread, not only the latest, does not pause;
   - when the echo is dropped, the turn reaches the write-up: no `needs_human` event, no interrupt, no orchestrator `AIMessage` equal to the dropped question;
   - a genuine question that shares a few words with the user message still pauses (the guard is not over-broad). Check existing tests already cover the plain genuine case.
2. Code in `app/orchestration/team.py`: a small pure helper (e.g. `echoes(question, messages) -> bool`) that normalises (casefold, strip punctuation, collapse whitespace; keep Vietnamese letters) and returns True when the question equals, contains or is contained in a user message's normalised text, or their word sets overlap at least about 80%. Apply it in `review` before `asks` is computed: an echoed question becomes "". Log the drop at info level. No model call, no retry.
3. Lower the chance at the source: in `Board`, give `question` a `Field(description=...)` saying it is a question to the user about missing information only they have, never the user's message. In `BOARD_PROMPT` (`app/orchestration/prompts.py`), add one line next to the `question` rule: never repeat or rephrase the user's message as `question`. Load the `writing-llm-document` skill before editing either. `BOARD_PROMPT` is `.format()`ed: no new braces. No realistic example values.
4. Update `.claude/rules/team-mode.md` (pause and resume) and `ARCHITECTURE.md` if it describes when the team pauses: one sentence on the echo guard.
5. Run `uv run pytest tests/test_team.py tests/test_chat_events.py`, then `uv run pytest`. Run `scripts\dev_db.ps1` first; skipped DB tests are not a pass.
6. Commit on the branch.

Acceptance: new tests and the committed failing test pass; the full suite passes; no change to the `pause` node or the resume path.

## Out of scope

The already-paused conversation `4666acba-...`: its next message is taken as the answer (`_chat_input`). A new chat avoids that.
