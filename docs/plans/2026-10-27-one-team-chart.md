# One chart per team answer

Bug: in team mode the chart agent draws the same chart twice, once from the planned chart task and once from a review follow-up (`origin: "follow_up"`).

Cause (diagnosed): the follow-up check in `review` (`app/orchestration/team.py:551-559`) drops a follow-up only when its agent and normalized goal match an existing task exactly. The reviewer words the chart goal differently, so a second chart task is added, and `draw` (`team.py:659-684`) runs every pending chart task. The only other guard is a prompt line (`app/orchestration/prompts.py:139`), which weak models ignore.

## Task 1: keep the regression test
Agent: implementer

`test_review_follow_up_does_not_add_a_second_chart` in `tests/test_team.py` (uncommitted) fails on the current code with `assert 2 == 1`. Keep it unchanged.

## Task 2: drop a chart follow-up when a chart task exists
Agent: implementer

- Branch: `git switch -c 0.5.3/one-team-chart` from the current HEAD (`0.5.3/try-all`); the uncommitted test comes along.
- In `review`'s follow-up loop, skip a follow-up whose agent `draws` when `charts` (the existing chart task ids) is non-empty. Code, not prompt.
- Update the follow-up bullet in `.claude/rules/team-mode.md` and the follow-up text in `ARCHITECTURE.md` if it describes follow-ups: a chart follow-up is dropped when the turn already has a chart task. Load the `writing-llm-document` skill before editing `.claude/rules/team-mode.md`.
- Commit test + fix + docs as `fix: team answers draw one chart, not a second one from a follow-up`.

Acceptance: the regression test passes; `uv run pytest tests/test_team.py` is green; `uv run pytest` shows no new failures.
