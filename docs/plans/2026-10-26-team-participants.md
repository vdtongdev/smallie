# Team mode: who takes part, and speaking for others — Fix Plan

**Goal:** In team mode, an agent the user names takes part (chart's `overrides` no longer drops sql), a reply to the board's question can bring in a newly named agent, and an agent in a round never claims to speak for another agent.

**Architecture:** Three small changes in `app/orchestration/team.py` and `app/orchestration/prompts.py`. No new model calls, no new nodes.

**Tech Stack:** Python, LangGraph, pytest (fake models, `tests/test_team.py` patterns).

**Spec:** the diagnosis of conversation `91535bdd-0a95-468e-8e64-f303071eb530` (2026-10-26): `participants()` returned `['docs','research','chart']` for a message naming all four agents; chart wrote a "Data Extraction (SQL Agent)" section although sql never ran; the user's later reply "for sql agent, use the internal stock database..." (an answer to the board's question, so a resume) did not add sql.

## Global Constraints

- Branch `0.5.2/team-fixes` (user's choice; it holds all three fixes, one commit each). It carries `0.5.0/agent-team` + `0.5.1/docs-refresh`, because `main` has no team mode yet.
- No new model call, retry or graph step (trap `extra-model-calls`).
- Normal mode routing (`obvious_route`) keeps `overrides` unchanged.
- Prompt and CLAUDE.md edits use the `writing-llm-document` skill. Traps for prompts: `prompt-format-braces` (`ROUND_MESSAGE` goes through `.format(language=...)`: no literal braces), `copyable-prompt-examples` (no realistic sample values), `reply-language`.
- `.claude/rules/testing.md`: never adjust a test to pass.

## Review Focus

- "chart AAPL" in team mode must still give chart only (no sql): existing `_routes("chart AAPL") == ["research","chart"]` assertion stays green.
- A resume reply that names no new agent must behave exactly as before (goes to `judge`, no new round).
- A resume reply naming an agent already taking part must not run it twice.
- An agent whose `route_info` is `None` (docs with no files) is never added on resume.
- `ROUND_MESSAGE.format(language=...)` must still work (no KeyError).

---

### Task 1: Keep the regression test, set up the branch

Agent: drafter

- [ ] Rename the current branch: `git branch -m 0.5.1/team-sql-chart 0.5.2/team-fixes`.
- [ ] Keep `tests/test_team.py::test_message_naming_sql_and_chart_agents_runs_both` (uncommitted, written by the diagnosis). Run `uv run pytest tests/test_team.py -q -k naming_sql`; expected: FAIL `assert 'sql' in ['research', 'chart']`.
- [ ] Commit only that test: `test: team message naming sql and chart runs both`.

### Task 2: A named agent is not dropped by `overrides`

Agent: implementer

**Files:** Modify `app/orchestration/team.py:71-77` (`participants`). Test: `tests/test_team.py`.

- [ ] Change `participants()`: an agent is dropped for another's `overrides` only when its own `hints` don't match the message (it was mentioned by a ticker/file match alone). Signature unchanged: `participants(available, message) -> list[AgentSpec]`.
- [ ] Run `uv run pytest tests/test_team.py -q`: the Task 1 test and the "chart AAPL" assertion both pass.
- [ ] Update the `pick` line in CLAUDE.md "Team mode" ("of an `overrides` pair only the winner") and ARCHITECTURE.md section 11 if it says the same.
- [ ] Commit: `fix: team mode keeps an agent the message names next to one that overrides it`.

### Task 3: A reply to the board's question can bring in a named agent

Agent: implementer

**Files:** Modify `app/orchestration/team.py` (`pause`, about line 258). Test: `tests/test_team.py`.

- [ ] First confirm with a test that the resume path keeps the original participants (the diagnosis did not trace it). Write `test_reply_to_question_naming_a_new_agent_brings_it_in`: a team run with participants `[chart]` pauses on a board question; the resume reply names sql ("for sql agent, use the internal stock database"); assert sql speaks in the next round (a `speak` entry for `sql`, kind `answer`) and is in `team["participants"]`. Also `test_reply_without_new_agent_goes_straight_to_judge` (no new round) and a case where the reply names an agent already taking part (not run again). Run: they fail for the right reason (the first one).
- [ ] In `pause`, after `interrupt` returns: `available_agents(user_id, specs)` + `participants(available, reply)`; the routes not yet in `team["participants"]` are added and sent one answer round (`sends(new, rnd + 1, "answer", ROUND_MESSAGE notes, messages + reply)`), with the reply in the messages update; else `goto="judge"` as now. Nothing may run before `interrupt` (the node reruns from its start on resume). Settings and the round cap stay as they are.
- [ ] Run `uv run pytest tests/test_team.py -q`: green.
- [ ] Update CLAUDE.md "Pause and resume" and ARCHITECTURE.md section 11 in one sentence each.
- [ ] Commit: `fix: a reply to the team's question brings in an agent it names`.

### Task 4: An agent in a round answers only for itself

Agent: implementer

**Files:** Modify `app/orchestration/prompts.py:89` (`ROUND_MESSAGE`). Test: `tests/test_team.py` or `tests/test_orchestrator.py` (whichever already checks prompt constants).

- [ ] Load the `writing-llm-document` skill.
- [ ] Add to `ROUND_MESSAGE`: answer only the part your own sources cover, even when the user's message gives tasks to other agents; you can't see the other agents' answers, so never write as another agent, attribute data to one, or claim another agent checked your figures. No literal braces, no sample values.
- [ ] Test `test_round_message_formats_with_language`: `ROUND_MESSAGE.format(language="English")` works and contains no unformatted placeholder. (The model behaviour itself can't be tested deterministically; say so in the report.)
- [ ] Run `uv run pytest tests/test_team.py tests/test_orchestrator.py -q`: green.
- [ ] Commit: `fix: team agents answer only their own part`.

### Task 5: Whole suite

Agent: verifier

- [ ] `powershell -ExecutionPolicy Bypass -File scripts\dev_db.ps1`, then `uv run pytest -q`: no failures, DB tests not skipped.
- [ ] Confirm the Task 1 test fails on the Task 1 commit and passes on the branch head.
