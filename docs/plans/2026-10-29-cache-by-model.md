# Plan: cache detail per model in the token footer's tooltip

Branch `0.6.1/cache-by-model`, created from `0.6.1/prompt-cache-layout` (the unmerged token-footer work), in its own worktree `C:\Users\Asus ROG\projects\smallie\.claude\worktrees\cache-by-model`.

## Behaviour wanted (decided with the user)

The footer under the input box (`renderUsage` in `app/static/app.js`) shows two lines, the last reply and the whole chat, each with input, output, cached tokens and hit rate. Hovering a line shows a browser tooltip (`title`, as the sidebar's `usageTokens` does) with one row per model that answered in that scope: model name, model calls, input tokens, cached tokens and hit rate. Cached is "n/a" for a model whose replies never reported cached tokens (not 0). Both scopes: the last reply and the whole chat. EN and VI strings via `i18n.js`.

## Task 1: count and store per model (Agent: implementer)

1. Tests first (`tests/test_usage.py`, `tests/test_conversation_usage.py`, `tests/test_remote.py` or where the agent-server totals are tested), from the behaviour:
   - `UsageCounter` keeps per-model calls, input, output and cached tokens. The model is `response_metadata["model_name"]` (fallback `"unknown"` when missing). Cached stays None for a model until one of its replies reports `cache_read`, and 0 is a count.
   - `totals()` includes `by_model`. `add(totals)` merges another counter's `by_model` (an agent server's run, `app/agents/remote.py`), and totals without `by_model` (an older agent server) still add.
   - `add_conversation_usage` stores the reply's `by_model` in `last_usage` and adds it into a new per-chat JSONB column. `get_conversation_usage` returns both, scoped by `user_id`. A chat saved before this change reads as an empty breakdown.
   - The overall totals keep their current values and meaning (no change to existing assertions).
2. Code: `app/core/usage.py`, `app/core/conversations.py`, `app/core/db.py` (new column `usage_by_model jsonb NOT NULL DEFAULT '{}'` in the table and in the `ADD COLUMN IF NOT EXISTS` startup migration next to `cache_known`/`last_usage`). Merge per model in Python inside the same transaction (`SELECT ... FOR UPDATE`, then update), keyed by `user_id` and conversation id. Agent servers must send `by_model` in the totals they already report; check `app/agents/server.py` and `remote.py`.
3. Run `scripts\dev_db.ps1`, then `uv run pytest`. Skipped DB tests are not a pass.

## Task 2: the tooltip (Agent: implementer, after Task 1)

1. `renderUsage` in `app/static/app.js` sets each line's `title` to the per-model rows of that scope, one row per model, sorted by input tokens. New strings in `app/static/i18n.js` in EN and VI. Re-render on language switch (it already re-renders through `state.usage`, check). No tooltip when the breakdown is empty.
2. Confirm in the real app (the `run` skill, an isolated browser profile, not the user's real Edge profile: trap browser-check-real-profile) that hovering both lines shows the rows, then report a screenshot path.

## Task 3: docs (Agent: drafter)

`ARCHITECTURE.md` (token footer section and the conversations table columns) and the matching `.claude/rules/` file (`config.md` or `data.md`, wherever the token footer is described). Load `writing-llm-document` for `.claude/` text.

Acceptance: all tests pass with no skips beyond the known env-credential pair in a fresh worktree; the tooltip shows correct per-model numbers for both lines in the real app; agent-server images rebuilt (`docker compose up -d --build`) before the real-app check, since `remote.py`/`server.py` totals change.
