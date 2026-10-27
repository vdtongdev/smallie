# Team mode: open table with withdrawal — Goal-driven Plan

**Goal:** In a team chat, a question that names no source brings in every available agent. An agent whose sources hold nothing withdraws, and the others go on. Chart only draws, and only when asked. Everything else stays as it is.

**Loop:** tests are written from the Goals below by an agent that doesn't implement them (Task 1). The verifier checks them against the Goals (Task 2). A different agent makes them pass without editing them (Task 3). The verifier checks the result (Task 4), then a live run follows (Task 5). A failed check goes back to the same agent once, then escalates one tier.

## Goals (the user's words, 2026-10-26, with their decisions)

1. "When asking an ambiguous question, without specifying a source, all available agents with permission should join the conversation and try to get the answer from their available sources."
   - **Specifying a source** means naming an agent or its source explicitly ("SQL agent", "database", "docs agent", "my files", a file's name, "research agent", "web"). Topic words such as "stocks", or a ticker, don't count.
   - **Available with permission** means `available_agents` (for example docs only when the user has files).
   - **Small talk:** a short message with no question and no data words ("hi", "thanks", "ok cảm ơn") goes to the orchestrator alone, as today. Rule-based, no model call.
2. "If an agent cannot give an answer from its source, it will withdraw from the conversation, while the others continue."
   - **If every agent withdraws:** "the orchestrator answers like in normal mode but also mentions that the other agents cannot answer."
3. "Chart agent's only job is to draw a chart if asked." Chart is a drawer (already built); it takes part only when the message asks for a chart.
4. "The rest stays the same as is." Rounds, the board, conflicts, pause and resume, the draw step and normal mode are unchanged, apart from the above.

## Global Constraints

- Branch `0.5.2/team-fixes`. Normal mode (`router.py`, `obvious_route`, the orchestrator outside team mode) is unchanged.
- No new model call. Withdrawal saves calls: it skips the board when at most one agent is left with an answer, and it keeps withdrawn agents out of conflict rounds. Trap `extra-model-calls`.
- Prompt edits go through `writing-llm-document`. Traps: `prompt-format-braces`, `copyable-prompt-examples`, `reply-language`, `weak-models-optional-steps` (enforce in code where possible).
- `.claude/rules/testing.md`. In Task 3, tests from Task 1 are never edited. If one contradicts the Goals, stop and report.

## Interfaces (fixed here so tests and code agree)

- `AgentSpec.names: re.Pattern | None`: an agent's explicit names and source words, English and Vietnamese. The implementer chooses the exact regex. Tests use only unambiguous phrases:
  - sql: "SQL agent", "the database";
  - docs: "docs agent", "my documents", or a file name the user has uploaded (via `RouteInfo.matches`/`mentions` for file names and resume/CV words);
  - research: "research agent", "search the web";
  - chart: "chart agent", "draw a chart", "biểu đồ".
- `participants(available, message) -> list[AgentSpec]` (`app/orchestration/team.py`), in registry order:
  - If some speaker (non-drawer) is named: the named speakers, plus `fed_by` of named drawers, plus `with_others` agents when another agent takes part (as today).
  - If no speaker is named: every available speaker.
  - Drawers are included only when their `names` match.
  - Small talk returns `[]`.
- `is_small_talk(message: str) -> bool` (`team.py`): True only for short messages (at most 5 words) with no "?" and where no agent's `names`/`hints`/route matches fire.
- Speech entries gain `withdrew: bool`, which is `spec.gave_up(turn)`, or for an agent with `answers_directly=False` (research) "no findings". The `spoke` event carries `withdrew`. The transcript (`additional_kwargs["team"]`) gains `withdrawn: list[str]`.
- Withdrawn agents are left out of:
  - the board's answers;
  - conflict rounds and `fixable_by`;
  - the write-up's ANSWERS;
  - the single-speaker shortcut count.
- When at most one non-withdrawn answer remains, the board call is skipped and no `board` model call is made.
- If every speaker withdraws, the orchestrator answers as in normal mode. Its hidden note names the withdrawn agents and tells it to say plainly that they found nothing. A handoff is run as in `pick`'s nobody-mentioned path. Then `draw` runs if there are drawers.

## Review Focus

- A withdrawn agent's earlier answer from round 1 must not leak into a conflict round or the write-up.
- A paused team (`needs_human`) resumed with a reply: withdrawal still applies to the new round.
- research `answers_directly=False`: "withdrew" uses its findings, not `gave_up` on messages (it keeps findings outside `messages`).
- Docs with no files is not available, so it never joins and never "withdraws".
- `gave_up`'s `NOT_FOUND` also matches short negative answers ("the report doesn't mention X"). That's accepted; the agent withdraws.

---

### Task 1: Acceptance tests from the Goals

Agent: implementer

- Write the tests in `tests/test_team.py`, using the existing fakes and helpers there. Read only the test file, `app/agents/base.py` (`AgentSpec`, `gave_up`), CLAUDE.md "Team mode" and this plan. Do NOT read or change the `team.py` implementation beyond its public names.
- Cover each Goal and each Interfaces line with at least one test. Also cover:
  - small-talk cases (EN and VI);
  - the user's own messages:
    - "find in different sources that you can use" → every available speaker, docs included when the user has files;
    - "SQL Agent, please pull the top 3 stocks... Chart Agent, ..." → sql (named) + chart drawer;
  - "evaluate top 3 stocks" → all speakers (topic words don't restrict);
  - withdrawal of one agent, all-but-one (board skipped), and all (orchestrator note names them).
- Existing tests that the Goals deliberately change (for example `_routes("chart AAPL")` expectations): list each in the report, with the Goal line that changes it, and update it.
- Commit `test: team open table and withdrawal acceptance tests`. Tests are expected to fail.

### Task 2: Review the tests against the Goals

Agent: verifier

- For each Goal and Interfaces line, name the test(s) covering it. Flag:
  - tests that assert something the Goals don't ask for;
  - Goals with no test;
  - weakened or deleted old tests not justified by a Goal line.
- Run them: the new ones fail for the right reason, and the rest pass.

### Task 3: Make them pass

Agent: implementer

- Implement in `app/agents/base.py` (`names`), each agent's `agent.py` (`names` regexes), `app/orchestration/team.py`, and `app/orchestration/prompts.py` if a note is needed.
- Don't edit any test from Task 1.
- Update CLAUDE.md "Team mode" and ARCHITECTURE.md section 11.
- Commit `feat: team open table; agents with nothing withdraw`.

### Task 4: Verify

Agent: verifier

- Run `scripts\dev_db.ps1` and `uv run pytest -q -rs`: green, no DB skips.
- Check that `git diff` of the tests since the Task 1 commit is empty.
- Review against the Global Constraints and Review Focus.

### Task 6: An agent that never looked in its sources withdraws (from the Task 5 live run)

The live run (conversation `71c29ce9-8a8d-48c9-a5b7-0c5509675a8d`) found a gap. docs made no tool call and answered "I do not have access to real-time stock market data … I cannot browse the live web", with `withdrew: false`. `gave_up` only catches empty tool results or short `NOT_FOUND` phrases ("couldn't find" and similar). An answer that never searched its source and only says what it lacks slips through.

Rule (Goal 2, "cannot give an answer from its source"): in team mode, a speaker that made no call to a source-tagged tool (`TOOL_SOURCE` in `tool.metadata`; memory and skill tools don't count) gave no answer from its source. It withdraws. Use `ToolRecorder`/the server's `tools` list, which already records calls.

- Test author (implementer, Goal-driven): `test_speaker_that_never_used_its_source_withdraws`. A docs fake that answers with text and no tool call gets `withdrew: True`. A sql fake that ran `run_sql` and answered does not. Commit only the test.
- Implementer: make it pass without editing tests, and update CLAUDE.md in one line.
- Verifier: test diff empty, suite green.

### Task 5: Live run

Agent: implementer

- One team message, "find in different sources that you can use: <a stock question>", on port 8001 (the user's dev server may hold 8000).
- Report the participants, who withdrew, whether the board ran, and the final answer.
