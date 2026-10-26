# Team mode: no "I can't draw a chart" next to the drawn chart — Fix Plan

**Goal:** When a team turn has a drawer (chart), neither the speakers nor the write-up tell the user that a chart can't be drawn.

**Spec:** the user's report from 2026-10-26 on conversation `0a872743-06cb-4773-b2a2-0fc0c7d93030`. The chart was drawn (`c4131146d`), but the final answer said "Rất tiếc, tôi hiện không thể trực tiếp vẽ biểu đồ…" ("Sorry, I can't draw the chart directly…").

**Diagnosis:**
- `TEAM_MESSAGE` (`app/orchestration/prompts.py:121-132`) is formatted in `synthesize` (`app/orchestration/team.py:281-303`) without `team["drawers"]`.
- `ROUND_MESSAGE` (`prompts.py:89`) doesn't say a chart agent draws. In this chat sql and research each wrote that they can't chart, and the write-up repeated it.
- The single-speaker shortcut (`team.py:288-290`) copies the speaker's text verbatim, so only a `ROUND_MESSAGE` fix reaches that path.

## Global Constraints

- Branch `0.5.2/team-fixes`.
- No new model call (trap `extra-model-calls`).
- Prompt edits go through the `writing-llm-document` skill. Traps: `prompt-format-braces` (a new `{drawing}` slot must be passed at EVERY `.format` call site of both constants, including `pause`), `copyable-prompt-examples`, `reply-language`.
- The line is added only when the team has drawers. Without drawers both prompts read as now, with an empty slot.
- `.claude/rules/testing.md`.

## Review Focus

- Every `ROUND_MESSAGE`/`TEAM_MESSAGE` `.format` call passes `drawing`, or a KeyError appears at runtime on that path (`pick`, `pause`, `synthesize`, tests' `test_prompts_format`).
- No drawer: the formatted text has no leftover blank-line artefact that changes meaning.
- `CONFLICT_MESSAGE` is unchanged; chart never takes part in conflict rounds.

---

### Task 1: Tell speakers and the write-up that the drawer draws

Agent: implementer

**Files:**
- `app/orchestration/prompts.py` (`ROUND_MESSAGE`, `TEAM_MESSAGE`, a new `DRAWING_NOTE` constant)
- `app/orchestration/team.py` (`pick`, `pause`, `synthesize`)
- `tests/test_team.py`

- [ ] Keep the uncommitted diagnosis test `test_write_up_note_says_the_drawer_draws_after_the_answer`. Add `test_round_note_says_the_drawer_draws`: in a team run with a drawer, the `ROUND_MESSAGE` the speakers receive names the drawer and says a chart follows. Without a drawer, neither note mentions a chart. Run them; they fail.
- [ ] Add a `{drawing}` slot to both prompts, filled with `DRAWING_NOTE.format(agent=...)` when `team["drawers"]` is non-empty, else `""`.
  - The note: the `<agent>` agent draws the chart the user asked for, from the team's final answer, right after it is written.
  - So never say a chart can't be drawn, don't apologise for it or tell the user to draw it themselves, and don't describe a chart. At most say the chart follows.
  - Write it with `writing-llm-document`. No sample values.
- [ ] Pass `drawing` at every call site. `pause`'s new agents get the same note.
- [ ] Run `uv run pytest tests/test_team.py tests/test_chat_events.py tests/test_orchestrator.py -q`. Expect green.
- [ ] Update CLAUDE.md "Team mode" in one sentence (`writing-llm-document`).
- [ ] Commit: `fix: team answers no longer say a chart can't be drawn`.

### Task 2: Verify

Agent: verifier

- [ ] Run `uv run pytest -q`: green.
- [ ] Review against the Global Constraints and Review Focus.
