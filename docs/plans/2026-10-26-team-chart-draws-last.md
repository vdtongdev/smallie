# Team mode: chart draws from the final answer — Plan

**Goal:** In team mode the chart agent no longer queries or debates. It runs once, after the write-up, and draws the chart from the final answer's data. A team message that names chart also brings in sql to fetch the data. The live chat shows the chart without a reload.

**Architecture:** Two new `AgentSpec` fields mark chart as a team drawer fed by sql. `pick` keeps drawers out of the rounds. A new `draw` node after `synthesize` runs each drawer on the thread plus the final answer, with no data sources (so `scope_tools` hides `chart_sql`). Its charts merge into the final answer, and a new `charts` team event shows them live. Normal mode is unchanged.

**Spec:** the user's decisions, 2026-10-26:
- "chart agent's only job in team chat is to draw chart from final verdict. so it will use the data in the final answer."
- "when a team message names only chart, sql will also be triggered to pull data for chart agent."
- "disable chart_sql in the chat room."

Diagnosis of conversation `1f492eaf-5584-4860-9a12-eff03147626b`:
- chart ran `chart_sql` in parallel with sql's `run_sql`, giving duplicate tables `tdf52e5`/`t6dcb37`.
- The final write-up carried chart `c2c56c66c` in `additional_kwargs["charts"]`, but no SSE event delivers write-up charts. `app.js:739-741` keeps per-agent tool charts out of the answer, and `:711-720` adds charts only for a lone speaker. So the chart showed only after a reload.

## Global Constraints

- Branch `0.5.2/team-fixes` (continue on it; one commit per task).
- Normal mode (`router.py`, `obvious_route`, orchestrator) is unchanged. Chart keeps `chart_sql` there.
- No model call besides the drawer's own run. The drawer replaces chart's round turn, so a team message costs fewer calls than now. Trap `extra-model-calls`.
- Data never passes through model text when a table exists: the drawer uses `chart_table` on the final answer's tables. `chart_data` is only for figures written in the answer with no table (trap `data-through-model`).
- The node name `draw` must not clash with a route, `tools` or a silent step (trap `workflow-node-names`). Grep before naming.
- Prompt text (`DRAW_MESSAGE`, CLAUDE.md) goes through the `writing-llm-document` skill. Traps: `prompt-format-braces`, `copyable-prompt-examples`, `reply-language`.
- Tests: `.claude/rules/testing.md`. Task 2's earlier team assertion `_routes("chart AAPL") == ["research","chart"]` encodes behaviour this spec deliberately changes. Replace it with the new expected behaviour and name that in the commit message.

## Review Focus

- Remote chart agent: the final answer's `additional_kwargs["tables"]` must reach the agent server so `chart_table` finds them. Check `RemoteAgent`/`as_message` serialization and test it.
- The final answer has no table and no figures (for example, docs found nothing): the drawer says it has nothing to draw. No invented numbers, no crash, and the answer stands.
- The drawer fails (exception): the final answer stands without a chart, and the error is logged.
- The single-speaker shortcut (sql alone + chart drawer): sql's answer stands, then chart draws from sql's table.
- A team message without chart: no `draw` node work, no `charts` event unless the answer already has charts.

---

### Task 1: Chart is a drawer fed by sql

Agent: implementer

**Files:**
- `app/agents/base.py` (`AgentSpec`)
- `app/agents/chart/agent.py` (`SPEC`)
- `app/orchestration/team.py` (`participants`, `pick`, `synthesize` transcript)
- `tests/test_team.py`

**Interfaces (produced):**
- `AgentSpec.draws: bool = False`. True: in team mode it takes no part in rounds or boards and runs once after the write-up, without data sources.
- `AgentSpec.fed_by: tuple[str, ...] = ()`. Routes brought into a team turn whenever this agent takes part.
- Chart `SPEC`: `draws=True, fed_by=("sql",)`.
- `participants(available, message) -> list[AgentSpec]`: the mentioned agents, plus their available `fed_by` routes, plus `with_others` agents when another agent takes part, in registry order. The Task 2 `overrides` filter of the earlier plan is removed from team mode. Chart now brings sql in, so nothing is dropped.
- `team["drawers"]: list[str]`: the chosen drawers. `team["participants"]` holds speakers only. The transcript (`additional_kwargs["team"]`) gains `drawers`.

- [ ] Tests:
  - `test_chart_mention_brings_in_sql`: "chart AAPL" -> routes include `sql` and `chart`.
  - `test_drawer_does_not_speak_in_rounds`: a team run with sql + chart. The `round` event's agents exclude chart, the board's answers exclude chart, and `team["drawers"] == ["chart"]`.
  - Replace the old `_routes("chart AAPL") == ["research","chart"]` assertion (deliberate behaviour change).
  - Run them; they fail.
- [ ] Implement it, then run `uv run pytest tests/test_team.py tests/test_router.py tests/test_orchestrator.py -q`. Expect green.
- [ ] Commit: `feat: team chart agent draws instead of speaking; naming chart brings in sql`.

### Task 2: The `draw` step

Agent: implementer

**Files:**
- `app/orchestration/team.py` (new `draw` node, graph edges)
- `app/orchestration/prompts.py` (`DRAW_MESSAGE`)
- `app/api/routes_chat.py` (`TEAM_EVENTS` += `"charts"`)
- `tests/test_team.py`; `tests/test_remote.py` if needed

**Interfaces:**
- Consumes `team["drawers"]` (Task 1).
- Produces:
  - custom event `{"type": "charts", "charts": [ChartSpec dicts]}` (the final answer's charts), emitted once at the end of a team turn whenever the final answer has charts;
  - transcript key `drawn: [{agent, text, tools}]`.

- [ ] Tests:
  - `test_drawer_charts_the_final_answers_table`: sql speaks with a table artifact, then the write-up. The chart fake model calls `chart_table` with that table id. Assert:
    - the final answer's `additional_kwargs["charts"]` holds the chart;
    - a `charts` event was emitted;
    - `chart_sql` was not offered to the drawer (check the tools bound to the fake model, or that a `chart_sql` call is refused by `refuse_hidden_tools`).
  - `test_drawer_failure_keeps_the_answer`.
  - `test_no_drawer_no_draw`.
  - A remote-serialization test that a message's `additional_kwargs["tables"]` survives `RemoteAgent`'s request/`as_message` round trip (or show an existing test covers it).
  - Run them; they fail.
- [ ] `synthesize` -> `draw` when `team["drawers"]`, else END.
  - The `draw` node: for each drawer, `emit speaker` (so its tool events carry the agent).
  - Invoke it with `[*thread, final answer, HumanMessage(DRAW_MESSAGE.format(language=...), name=TEAM)]` and `context.sources=()`.
  - Merge `collect(turn)` charts into the final answer: same message id, so `add_messages` replaces it. Give the answer an id in `synthesize` if it has none.
  - Add `drawn` to the transcript, then emit `charts`.
  - `synthesize` without drawers also emits `charts` when its answer has any.
  - A drawer exception is logged and the answer stands. `RateLimitError`/`AuthenticationError` propagate, as in `speak`.
- [ ] `DRAW_MESSAGE` (`writing-llm-document`):
  - Say that the team's final answer is the message above.
  - Draw the chart the user asked for from that answer's data: with `chart_table` when a table listed in this chat holds it, else with `chart_data` from figures written in the answer.
  - Never add or change figures. If the answer holds no data to chart, say so in one line.
  - Reply with one short line about the chart, not another answer.
  - End with `Write in {language}.`
- [ ] Run `uv run pytest tests/test_team.py tests/test_chat_events.py tests/test_remote.py tests/test_scope_tools.py -q`. Expect green.
- [ ] Update CLAUDE.md "Team mode" (the round table steps, custom events, independent sources) and ARCHITECTURE.md section 11.
- [ ] Commit: `feat: team chart agent draws from the final answer`.

### Task 3: The live chat shows team charts

Agent: implementer

**Files:**
- `app/static/app.js`
- `app/static/i18n.js` (only if a new label is needed)

- [ ] On the `charts` SSE event, call `addCharts(event.charts)` on the current answer bubble. Dedupe by `data-chart-id` already exists.
- [ ] Keep `:711-720`'s lone-speaker path from adding the same chart twice. The dedupe covers it; confirm.
- [ ] Activity popup (`renderActivity`/`teamActivity`): show the drawer's tool calls under a "chart" entry from `drawn` (live: from the drawer's `tool_call`/`tool_result` events; reload: from `message.team.drawn`).
- [ ] There is no JS test runner. Check it by running the app per CLAUDE.md commands, sending a team message that names sql and chart, and confirming the chart appears live (before any reload) and once after a reload. Trap `browser-check-real-profile`: don't write into the user's real browser profile; use a fresh profile or API-level SSE capture (curl the stream and show the `charts` event).
- [ ] Commit: `fix: team chats show charts live`.

### Task 4: Whole suite

Agent: verifier

- [ ] Run `scripts\dev_db.ps1`, then `uv run pytest -q`. Expect no failures and no DB skips.
- [ ] Review Tasks 1–3 against this plan's Global Constraints and Review Focus.
