# Team mode: docs searches the user's files; one chart, from the drawer — Fix Plan

**Goal:** In a team round every speaker looks in its source before answering, and docs knows the user's file names. When chart draws, the write-up draws nothing, so the user gets one chart of the agreed data.

**Spec:** the user's report of 2026-10-26 on conversation `10b7f5c2-0045-4e7e-a50b-cb875f22e0b5`:
- "the docs agent didn't read the file top_50_stocks_2024 when I asked about the top growing stocks";
- "the chart agent drew 2 charts from 2 different answers although the verdict was all agents agreeing".

The Goals of `docs/plans/2026-10-26-team-open-table.md` still hold.

## Diagnosis

1. **docs never called `search_documents` (tools []) and withdrew under Task 6.**
   - It didn't know the file existed: `RouteInfo.prompt_note` (the file list, `app/agents/docs/agent.py:49`) reaches only the orchestrator (`compose_section`, `orchestrator.py:197`).
   - In team mode a speaker gets the thread plus `ROUND_MESSAGE` only (`team.py:225,233`).
   - The file `top_50_stocks_2024.pdf` is `ready` with 6 chunks.
2. **The write-up orchestrator drew a `chart_data` chart.**
   - It always has `chart_table`/`chart_data` (`orchestrator.py:252`), and `synthesize` merges its charts (`team.py:355`).
   - `draw` then added the drawer's `chart_table` chart (`team.py:364`), so the user got two charts.
   - The text-only `DRAWING_NOTE` was ignored (trap `weak-models-optional-steps`).

## Global Constraints

- Branch `0.5.2/team-fixes`. Normal mode is unchanged.
- No extra model call. Forcing a tool on the first call replaces that call; it doesn't add one (trap `extra-model-calls`).
- Enforce in code, not prompts (trap `weak-models-optional-steps`).
- `.claude/rules/testing.md`. The tests written by the diagnosis and by the test author are not edited by the implementer.

## Review Focus

- The forced first tool call must work for remote agents. The middleware runs on the agent server, and `context.agent`/`sources` arrive in the body.
- The drawer (chart, `sources=NO_SOURCES`, no source-tagged tool in scope) must NOT be forced, because "no data → draw nothing" must stay possible.
- research's inner researcher is built with `build_agent`. Forcing applies to its first call too (any in-scope source tool), which is fine.
- Providers that reject `tool_choice`: check what `langchain_openai` sends to OpenRouter and say in the report whether it can fail. Don't add a retry (trap `extra-model-calls`).
- The write-up without drawers keeps `chart_table`/`chart_data` as today.

---

### Task 1: Tests

Agent: implementer (test author; not the implementer of Task 2)

- [ ] Keep the two uncommitted diagnosis tests in `tests/test_team.py`:
  - `test_docs_speaker_is_told_the_users_file_names`;
  - `test_orchestrator_cannot_chart_in_the_write_up_when_a_drawer_draws`.
- [ ] Add `test_team_speaker_first_call_must_use_a_source_tool` (`tests/test_scope_tools.py` or `tests/test_team.py`):
  - In a team request whose scope has a source-tagged tool, the first model call of an agent built with `build_agent` is made with a forced tool choice limited to the tools in scope.
  - Later calls in the same turn are not forced.
  - Not forced in normal mode (`context.agent != TEAM_MODE`), or when no source-tagged tool is in scope (the drawer).
- [ ] Show all three failing, then commit only the tests: `test: team speakers search their source; one chart`.

### Task 2: Fix

Agent: implementer

- [ ] Each speaker's round note carries its own `RouteInfo.prompt_note` when non-empty (docs: the file list). Use the `RouteInfo` that `available_agents` already returns in `pick`/`pause`.
- [ ] A `wrap_model_call` middleware in `app/agents/factory.py`, next to `scope_tools`:
  - In a team request, if no tool call has happened yet this turn and a source-tagged tool is in scope, set the model request's tool choice to require a tool.
  - Use the LangChain `ModelRequest` override for tool choice.
- [ ] When `team["drawers"]` is non-empty, the write-up orchestrator is not offered `chart_table`/`chart_data`, and a call is refused, like `refuse_hidden_tools`. Pass the flag through the hidden team note or the context, whichever is cleanest.
- [ ] Run `uv run pytest -q`. Expect green.
- [ ] Update CLAUDE.md "Team mode" in one or two lines (`writing-llm-document`).
- [ ] Commit: `fix: team speakers search their source first; drawer is the only chart`.

### Task 3: Verify

Agent: verifier

- [ ] The test diff since the Task 1 commit is empty.
- [ ] The tests fail on the base and pass after the fix.
- [ ] Run `uv run pytest -q -rs` with Postgres up.
- [ ] Review against Review Focus.

### Task 4: Live run

Agent: implementer

- [ ] Send the user's message: "đánh giá top 5 cổ phiếu tăng trưởng mạnh nhất. cuối cùng hãy vẽ biểu đồ của các cổ phiếu đó".
- [ ] Check that docs calls `search_documents` and finds `top_50_stocks_2024.pdf`.
- [ ] Check that exactly one chart arrives.
- [ ] Check that the final answer isn't the fallback "Sorry, I couldn't finish…".
