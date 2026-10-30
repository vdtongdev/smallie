# Plan: chat usage in a popup behind a small button under the chat box

Branch `0.6.1/cache-by-model`, worktree `C:\Users\Asus ROG\projects\smallie\.claude\worktrees\cache-by-model`. Follows `docs/plans/2026-10-29-cache-by-model.md` (per-model counting and storage are done and verified; the `title` tooltip on the footer lines did not work for the user on localhost:8011 after sending a message in a new chat).

## Behaviour wanted (from the user)

- The token footer lines under the input box go away. In their place: one small button under the chat box (label "Usage" / VI "Dung lượng" or similar, via `i18n.js`; hidden while the chat has no usage yet, as the footer is now).
- Clicking it opens a popup with everything the footer and tooltip showed:
  - Last reply: input, output, cached, hit rate.
  - This chat: the same.
  - Per model, for each of the two scopes: model name, calls, input, cached, hit rate; cached "n/a" when the model never reported it. Sorted by input tokens.
  - The note that some hosts don't cache (the footer's no-cache line, commit `ccc53b3`), when it applies.
- The popup updates when a new `usage` SSE event arrives while it is open, re-renders on language switch, closes with Esc, a close button and a click outside. Reuse the look and the `<dialog>` pattern of the existing activity popup (`app/static/app.js`, "status line and activity popup") so it matches the app. Works at phone width.
- Fix the plural: "1 call", not "1 calls" (EN); check VI.

## Task 1: find why the tooltip showed nothing (Agent: implementer, diagnose first)

Before building the popup, find out whether the per-model data reaches the browser for a real reply. Read the user's newest conversation (most recently updated row in `conversations`) read-only: `usage_by_model`, `last_usage`. Check what `/api/...` usage endpoint and the SSE `usage` event return for it (the code, or a read-only request). Report which of these held: data missing in the DB (then why: `model_name` missing in `response_metadata`, agent servers not sending `by_model`, etc.), data present but not sent, or sent but not rendered. If data is missing, that is a bug the popup would inherit: fix it with a failing test first.

## Task 2: the popup (Agent: implementer)

1. `app/static/index.html`, `app/static/app.js` (`renderUsage` and the SSE `usage` handler), `app/static/style.css`, `app/static/i18n.js` (EN and VI). Remove the footer lines and the `title` tooltips; keep `state.usage` as the single source.
2. Restart the worktree app on port 8011 (it runs without `--reload`), rebuild the agent servers only if server code changed.
3. Real-app check with an isolated browser profile (never the user's real Edge profile): open a chat with usage, click the button, screenshot the popup in EN and VI, and at phone width. Report the screenshot paths.
4. `node --check` on the JS files; `uv run pytest -q` after `scripts\dev_db.ps1` (no skips).

## Task 3: docs (Agent: drafter)

Replace the tooltip wording added in `ca308be` (`ARCHITECTURE.md` Token Usage section, `.claude/rules/orchestration.md` line 20) with the popup. Load `writing-llm-document` for `.claude/`.
