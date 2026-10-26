# Research ids of any shape are dropped; the drawer charts tables — Fix Plan

**Goal:**
- (A) No `[source: <id>]` that isn't a known source id reaches the write-up, whatever its shape.
- (B) When the team's final answer carries a table, the chart drawer can't copy numbers with `chart_data`. It draws from the table with `chart_table`.

**Spec:** the user's approval on 2026-10-26 of the two fixes for "I thought this was resolved in the past?".
- (A) In live run `71c29ce9-8a8d-48c9-a5b7-0c5509675a8d`, research cited `[source: cf61ec-00]`. `_SOURCE_ID`/`_BRACKETED_ID` (`app/agents/research/agent.py:204-206`) only know the `S` + 4 hex shape. So `expand_source_ids` (`:279`) left it in, and the write-up linked it to google.com.
- (B) In live run `946c79ec-2cea-41c8-832c-ba1f2079e0a4`, the drawer called `chart_data` with the numbers inline, although the final answer carried a table. That is trap `data-through-model`. `DRAW_MESSAGE` asks for `chart_table` only in text (trap `weak-models-optional-steps`).

## Global Constraints

- Branch `0.5.2/team-fixes`. No new model call (trap `extra-model-calls`). Enforce in code.
- (A) A known `S` id keeps working exactly as now (the existing research tests). A quote next to an unknown id stays, because `link_sources` finds the page by its quote. Only the id goes.
- (B) The drawer is never forced to call a tool: `source_first` must not fire for it, so "no data → draw nothing" stays possible. Without a table on the final answer, `chart_data` stays available, because figures written in the answer are fine. Normal mode and the orchestrator are unchanged.
- `.claude/rules/testing.md`. Tests are written by the test author and not edited by the implementer.

## Review Focus

- (A) Text that merely looks like a bracket must survive: a year, a ticker in brackets, a normal `[1]` citation number in findings. Only `[source: …]`-style brackets lose unknown ids.
- (B) A table on the final answer that came from an earlier turn, not this one: does it count? It should count only when it is on THIS turn's final answer (`additional_kwargs["tables"]`).

---

### Task 1: Tests

Agent: implementer (test author)

- [ ] (A) In `tests/test_research_workflow.py`, following its `expand_source_ids`/`link_sources` patterns:
  - `test_unknown_source_id_of_any_shape_is_dropped`: findings `"X grew 177% [source: cf61ec-00]"` with an index that lacks it. The output has no `cf61ec-00` and no URL invented for it.
  - With a quote, `[source: cf61ec-00 | "<a quote on a known page>"]`: the id goes, and the quote still links to its page.
  - A known `S` id still expands.
- [ ] (B) In `tests/test_team.py`:
  - `test_drawer_cannot_copy_numbers_when_the_answer_has_a_table`: the final answer carries a table. The drawer fake is not offered `chart_data`, and a `chart_data` call is refused (no chart from it).
  - When the final answer carries no table, `chart_data` is offered.
  - The drawer's first call is not forced.
- [ ] Show them failing (the guards may pass), then commit only the tests and this plan: `test: unknown citation ids; drawer charts the table`.

### Task 2: Fix

Agent: implementer

- [ ] (A) In `app/agents/research/agent.py`, unknown ids of any shape inside `[source: …]` brackets are dropped in `expand_source_ids`/`link_sources`, with the quote kept.
- [ ] (B) In `app/orchestration/team.py` `draw` (plus `factory.py`/`orchestrator.py` middleware if needed), hide and refuse `chart_data` for the drawer when this turn's final answer carries a table. Don't tag `chart_data` in a way that makes `source_first` force the drawer.
- [ ] Run `uv run pytest -q`. Expect green.
- [ ] Add a CLAUDE.md line for each, using `writing-llm-document`.
- [ ] Commit: `fix: drop unknown citation ids; drawer charts from the answer's table`.

### Task 3: Verify

Agent: verifier

- [ ] The test diff since the Task 1 commit is empty.
- [ ] The tests fail on the base and pass after the fix.
- [ ] Run `uv run pytest -q -rs` with Postgres up.
- [ ] Review against Review Focus.
