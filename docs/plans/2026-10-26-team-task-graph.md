# Team task graph Implementation Plan

> **For agentic workers:** executed with the foreman loop (one agent per task, then the verifier). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the team round table (`pick -> speak -> judge -> synthesize -> draw`) with a planned task graph: the orchestrator plans tasks with dependencies, tasks run in waves, a review after each wave extracts challenges, conflicts and follow-up tasks, and one workspace holds the state.

**Architecture:** The `team` subgraph becomes `plan -> dispatch -> speak x N -> review -> (speak | dispatch | pause) ... -> synthesize`. Pure plan logic (models, validation, readiness, follow-up merging) lives in a new `app/orchestration/team_plan.py`; the graph nodes, `Review` and prompts stay in `team.py` / `prompts.py`. The `speak` node keeps its name (the chat stream maps `speak:<task id>` namespaces), extended with a task id.

**Tech Stack:** Python 3.12, LangGraph (`Send`, `Command`, `interrupt`), Pydantic structured output via `get_chat_model(...).with_structured_output(..., method="function_calling")`, pytest-asyncio (auto), plain JS UI.

**Spec:** `docs/superpowers/specs/2026-10-26-team-task-graph-design.md` (read it with this plan; section numbers below refer to it).

## Global Constraints

- Branch `0.5.0/team-task-graph`; commit per task; end every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Settings: `TEAM_MAX_TASKS` new, default `6` (follow-ups included); `TEAM_MAX_ROUNDS` default `4 -> 2`, now counted **per meeting point**; `TEAM_MAX_MESSAGE_MODEL_CALLS` stays `30`; the review call uses `OPENROUTER_JUDGE_MODEL` exactly as `ask_board` does now (`exact_model=True` when set).
- The plan call uses the orchestrator model: `get_chat_model(settings.openrouter_orchestrator_model or None)`.
- No new model calls beyond: 1 plan call per message (skipped for small talk and for exactly one named speaker) and 1 review call per wave (skipped per spec section 2). Follow-ups come from the review call.
- Structured calls: `with_structured_output(<Schema>, method="function_calling")`; texts go into the human message, never into a `.format` template (answers may contain braces).
- Prompt constants (`app/orchestration/prompts.py`): load the `writing-llm-document` skill before editing; every `{placeholder}` is passed at each `.format` call; literal braces doubled; no realistic example values (use `<placeholders>`); the language is named outright via `{language}` = `reply_language(messages)`.
- `RateLimitError` / `AuthenticationError` always propagate; every other agent/review/plan failure is logged and degrades as spec section 7 says.
- Tests: never weaken or rewrite an existing test to pass. Remove only tests of behaviour this spec removes (the `draw` step, `fed_by` drawers, rounds over all participants at once), naming each removed test and the reason in the commit message. When an existing test contradicts the spec, stop and report it (spec line + test).
- Run `powershell -ExecutionPolicy Bypass -File scripts\dev_db.ps1` before the full suite; skipped DB tests are not a pass.

## Review Focus

- The planner returns duplicate or empty task ids (two `t1`, an `""` id) -> ids are made unique (`t1`, `t2`, ... in plan order, dependencies remapped by original id where unambiguous, dropped otherwise); test in Task 1.
- A task depends on itself -> treated as a cycle -> fallback plan; test in Task 1.
- A follow-up repeats an existing task (same agent and same goal after whitespace/case normalisation) -> dropped, so a review cannot loop the same work; test in Task 6.
- A Vietnamese message -> the task notes, review prompt and write-up name Vietnamese via `{language}`; test in Task 3 (task note) and Task 7 (write-up).
- A second message in the same team thread -> the workspace starts empty (no tasks/results of the earlier message); test in Task 3.

---

### Task 1: Plan models and pure plan logic

Agent: implementer

**Files:**
- Create: `app/orchestration/team_plan.py`
- Modify: `app/core/config.py` (`team_max_rounds: int = 2`, add `team_max_tasks: int = 6`), `.env.example` (document `TEAM_MAX_TASKS`, new `TEAM_MAX_ROUNDS` meaning and default)
- Test: `tests/test_team_plan.py`

**Interfaces:**
- Produces:
  - `class Task(BaseModel)`: `id: str`, `agent: str`, `goal: str`, `depends_on: list[str] = []`
  - `class Plan(BaseModel)`: `tasks: list[Task] = []`
  - `validate_plan(plan: Plan, available: Sequence[str], max_tasks: int) -> list[dict] | None` — returns workspace task dicts `{"id", "agent", "goal", "depends_on", "status": "pending", "origin": "plan"}`; `[]` for a legitimately empty plan; `None` when the plan must fall back (cycle, self-dependency, or every task dropped from a non-empty plan).
  - `fallback_tasks(routes: Sequence[str]) -> list[dict]` — independent tasks `t1..tn`, `origin: "fallback"`, goal `""` (the note then says "answer the user's latest message").
  - `ready(tasks: Sequence[dict]) -> list[dict]` — pending tasks whose every dependency has status `done`, `failed`, `withdrew` or `skipped`.
  - `dead_inputs(task: dict, tasks: Sequence[dict]) -> tuple[list[str], bool]` — the ids of dependencies that failed/withdrew/were skipped, and whether **all** dependencies are dead (task must be `skipped`; a task without dependencies is never skipped).
  - `add_follow_ups(tasks: list[dict], follow_ups: Sequence[Task], available: Sequence[str], max_tasks: int) -> tuple[list[dict], list[dict], list[str]]` — returns (all tasks, the added ones with `origin: "follow_up"` and fresh ids continuing the numbering, goals dropped because of the cap).

- [ ] **Step 1: Write the failing tests** in `tests/test_team_plan.py`:
  - `test_unknown_or_unavailable_agent_is_dropped_with_its_dependents_edges`: plan `t1 docs`, `t2 ghost`, `t3 sql depends_on [t1, t2]`, available `["docs", "sql"]` -> agents `["docs", "sql"]`, sql's `depends_on == ["t1"]`.
  - `test_empty_plan_means_no_specialist`: `validate_plan(Plan(), ["sql"], 6) == []`.
  - `test_every_task_dropped_falls_back`: plan of only `ghost` tasks -> `None`.
  - `test_cycle_falls_back` (t1->t2->t1) and `test_self_dependency_falls_back` -> `None`.
  - `test_plan_is_capped`: 8 independent tasks, `max_tasks=6` -> 6 tasks, the first six in order.
  - `test_same_agent_twice_is_allowed`: two sql tasks with different goals both kept.
  - `test_duplicate_and_empty_ids_are_renumbered`: tasks with ids `"t1"`, `"t1"`, `""` -> ids `t1, t2, t3`.
  - `test_ready_waits_for_dependencies`: t2 depends on t1 (pending) -> `ready == [t1]`; after t1 `done` -> `[t2]`; independent t3 is ready with t1.
  - `test_dead_inputs`: dependency withdrew + one done -> `(["t1"], False)`; all dead -> `True`; no dependencies -> `([], False)`.
  - `test_follow_ups_get_fresh_ids_and_respect_the_cap`: 4 existing tasks, 3 follow-ups, cap 6 -> 2 added (`t5`, `t6`), third goal returned as dropped.
  - `test_invalid_follow_ups_are_dropped`: unknown agent, dependency on a missing id -> not added.
  - `test_settings_defaults`: `Settings().team_max_rounds == 2` and `Settings().team_max_tasks == 6` (construct with `_env_file=None`).

- [ ] **Step 2: Run** `uv run pytest tests/test_team_plan.py -v` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `app/orchestration/team_plan.py` and the settings. Cycle check: Kahn's algorithm over kept tasks.
- [ ] **Step 4: Run** `uv run pytest tests/test_team_plan.py -v` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: team plan models and validation`.

---

### Task 2: The plan call

Agent: implementer

**Files:**
- Modify: `app/orchestration/team.py` (add `ask_plan`), `app/orchestration/prompts.py` (add `PLAN_PROMPT`)
- Test: `tests/test_team.py`

**Interfaces:**
- Consumes: `Plan` (Task 1).
- Produces: `async def ask_plan(messages: list[AnyMessage], available: Sequence[tuple[AgentSpec, RouteInfo]], config: RunnableConfig) -> Plan` — one structured call on the orchestrator model; the human message holds the user's latest message, one line per available agent (`route`: `description`; review: `review`; sources: sorted `sources`; drawer when `spec.draws`), and each agent's `RouteInfo.prompt_note` (docs: file names).
- `PLAN_PROMPT` takes `{language}` and `{max_tasks}` only. Its rules (spec 1 and scenarios): pick only the agents the request needs, possibly none (empty `tasks` = no specialist); start small, later reviews can add tasks; a task needing another's output lists it in `depends_on`; independent tasks have none; a chart task depends on the task that produces its table; goals are one sentence each, written in `{language}`.

- [ ] **Step 1: Write the failing tests:**
  - `test_ask_plan_calls_the_orchestrator_model_once`: monkeypatch `team.get_chat_model` as `test_ask_board_calls_the_model_once` does; assert one call, schema `(Plan, "function_calling")`, the model asked is `settings.openrouter_orchestrator_model or None`, and the human message contains the question, `"sql"`, sql's `description`, and a docs `prompt_note` text passed in `available`.
  - `test_plan_prompt_formats`: `PLAN_PROMPT.format(language="English", max_tasks=6)` raises nothing and contains `"6"`.
- [ ] **Step 2: Run** `uv run pytest tests/test_team.py -k "ask_plan or plan_prompt" -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `ask_plan` and `PLAN_PROMPT` (load `writing-llm-document` first).
- [ ] **Step 4: Run** the same — Expected: PASS.
- [ ] **Step 5: Commit** `feat: team plan call`.

---

### Task 3: Workspace, plan node and waves

Agent: implementer

**Files:**
- Modify: `app/orchestration/team.py` (graph, state, `pick` -> `plan`, new `dispatch`, `speak`, `judge` -> `review`, `synthesize` transcript), `app/orchestration/prompts.py` (`TASK_MESSAGE` replaces `ROUND_MESSAGE` for planned tasks), `app/orchestration/router.py` (`AssistantState.team` comment: the workspace)
- Test: `tests/test_team.py`

**Interfaces:**
- Consumes: Task 1 functions, `ask_plan` (Task 2).
- Produces:
  - `TeamState.workspace: dict` replacing `team` + `speeches`. Shape (spec 4): `goal`, `tasks` (with `status`: pending|running|done|failed|withdrew|skipped, `origin`), `results` (`task_id -> {"text", "tools", "artifacts", "round", "agent"}`), `evidence` (list), `artifacts` (merged), `history` (list of `{"type": "wave"|"review"|..., ...}`), `rounds_at` (`meeting key -> int`), `synthesis` (`{"forced", "open_points", "not_done"}`), `question`. A reducer merges parallel `speak` results into `results`/`tasks` (keyed by task id) the way `merge_speeches` does now; a `RESET` marker at plan time empties it.
  - `speak` job: `{"task": id, "route", "round", "kind": "plan"|"challenge"|"conflict", "note", "messages"}`; emits `speaker` `{agent, task, ns}` and `spoke` with `task`.
  - Events: `plan` `{tasks, fallback}` once; `wave` `{wave, kind, tasks: [ids], agents}` per dispatch (replaces `round`); `review` (replaces `board`; Task 5 fills challenges).
  - `TASK_MESSAGE` with `{goal}`, `{inputs}`, `{missing}`, `{language}`: answer this task with your own sources only, with source and as-of date per figure; the inputs are other agents' results for this message; compare them with your own source and, when something does not match, say so with the value you found and where. `{inputs}` lists each dependency as `<task id> (<agent>): <text>` plus `tables: <ids>`; `{missing}` names dead inputs or is empty.
- Behaviour:
  - `plan`: small talk -> orchestrator as now (unchanged code path). Exactly one named speaker (`participants()` returns one non-drawer, nothing else) -> one task, no plan call. Otherwise `ask_plan`; `validate_plan`; `[]` -> orchestrator answers as in normal mode (same path as small talk); `None` or a plan-call exception (not rate limit/auth) -> `fallback_tasks([...participants routes...])` with `plan` event `fallback: true`.
  - `dispatch`: marks `skipped` every ready task whose inputs are all dead (no call); sends every other ready task (`status: running`) with `TASK_MESSAGE`; when nothing is pending goes to `synthesize`.
  - `review` (this task: board behaviour only, Task 5 adds challenges): runs per spec 2 "When there is no review call": intermediate wave -> only if a task of the wave had inputs or two live tasks of the wave share a pending dependent; last wave -> when at least one task is live (as `judge` now). Then `dispatch`.
  - Withdrawal per task exactly as `speak` decides it now.
  - `synthesize` reads live results instead of `_live(speeches, ...)`; transcript `additional_kwargs["team"]` = the workspace (minus internal `rounds_at`).
  - `RESET` per message: a second message starts with an empty workspace.

- [ ] **Step 1: Add a test fixture** in `tests/test_team.py`: an autouse fixture making `team.ask_plan` raise `RuntimeError` (so existing tests run the fallback plan, which is today's behaviour), plus a helper `_plan(monkeypatch, *plans)` that scripts `ask_plan` like `_board` scripts the board. Commit this fixture with the tests below (the plan call is new; existing tests keep their assertions).
- [ ] **Step 2: Write the failing tests:**
  - `test_dependent_task_waits_for_its_input`: plan `t1 docs`, `t2 sql depends_on [t1]` -> sql starts after docs ends (use `Fake` log times); sql's last message contains docs' text and `t1`.
  - `test_independent_tasks_share_a_wave`: plan `t1 docs`, `t2 sql [t1]`, `t3 research [t1]` -> sql and research overlap in time; `wave` events `[1: [t1], 2: [t2, t3]]`.
  - `test_task_note_carries_input_tables`: docs `Fake(table={"id": "tbl1", ...})`, sql depends on it -> sql's note contains `tbl1`.
  - `test_task_note_names_the_language`: Vietnamese message -> sql's note contains `reply_language` of it (Vietnamese).
  - `test_two_tasks_of_one_agent_stream_apart`: two sql tasks in one wave -> two `speaker` events with different `task` and `ns`.
  - `test_empty_plan_orchestrator_answers_alone`: `_plan(Plan())` -> no `wave` event, one orchestrator answer, one prompt in the orchestrator model.
  - `test_plan_failure_falls_back_to_participants`: default fixture -> `plan` event `fallback: true` and the tasks are `participants()`'s routes.
  - `test_one_named_speaker_skips_the_plan_call`: `SQL_QUESTION` with research `joins="mentioned"` (`_research_does_not_join`) -> `ask_plan` never awaited.
  - `test_task_with_all_inputs_dead_is_skipped`: t1 docs withdraws, t2 sql depends on t1 -> sql never starts, status `skipped`.
  - `test_task_with_some_inputs_dead_runs_and_is_told`: t3 depends on t1 (withdrew) and t2 (done) -> t3 runs, its note names t1 as missing.
  - `test_no_review_for_a_wave_without_inputs_or_meeting`: plan `t1 docs`, `t2 sql [t1]` -> exactly one review call (after wave 2), counted via `_board`'s script.
  - `test_second_message_starts_an_empty_workspace`: two team messages on one thread -> the second answer's transcript has only the second plan's tasks.
  - `test_transcript_is_the_workspace`: final answer `additional_kwargs["team"]` has `tasks`, `results`, `history`, `synthesis`.
- [ ] **Step 3: Run** `uv run pytest tests/test_team.py -v` — Expected: new tests FAIL; existing tests: note which fail.
- [ ] **Step 4: Implement** the nodes, state and `TASK_MESSAGE` (load `writing-llm-document` for the prompt).
- [ ] **Step 5: Run** `uv run pytest tests/test_team.py tests/test_team_plan.py -v` — Expected: PASS, except existing tests of removed behaviour (round events over all participants named as `round`, `board` event names): for each, decide with the spec whether the behaviour is removed (remove the test, list it in the commit) or renamed (stop and report; do not edit its assertions).
- [ ] **Step 6: Commit** `feat: team task graph: workspace, plan node and waves` with the removed-test list.

---

### Task 4: The chart task replaces the draw step

Agent: implementer

**Files:**
- Modify: `app/orchestration/team.py` (remove `draw`, `_drawing` uses, `fed_by` handling in `plan`/`pause`), `app/orchestration/prompts.py` (remove `DRAW_MESSAGE` if unused; keep `DRAWING_NOTE` only if still formatted somewhere)
- Test: `tests/test_team.py`

**Interfaces:**
- Consumes: workspace, `speak` job (Task 3).
- Behaviour (spec 1 "The drawer is a task"): a chart task runs like any task with `sources=NO_SOURCES` when its inputs carry tables (so `chart_sql` is hidden and it charts with `chart_table`) and `deny_tools=("chart_data",)` in that case; with no table in its inputs it keeps `chart_data` (figures written in the inputs). When the workspace has a chart task, the write-up orchestrator gets `deny_tools=("chart_table", "chart_data")` (today's `team_drawn`). The chart task's charts merge into the final answer's `additional_kwargs` and a `charts` event goes out after the write-up.
- `AgentSpec.fed_by`/`draws` stay in `base.py` only if still read (fallback plan: `participants()` still brings sql in with chart; the fallback makes chart a task depending on sql's task).

- [ ] **Step 1: Write the failing tests:**
  - `test_chart_task_charts_its_inputs_table`: plan `t1 sql (table tbl1)`, `t2 chart [t1]` -> chart's context `sources == NO_SOURCES`, `deny_tools == ("chart_data",)`; final answer carries chart's chart id; one `charts` event.
  - `test_chart_task_without_table_may_use_chart_data`: input has no table -> `deny_tools == ()`.
  - `test_write_up_cannot_chart_when_a_chart_task_exists`: the orchestrator's write-up context denies `chart_table` and `chart_data`.
  - `test_fallback_plan_makes_chart_depend_on_sql`: fallback with `"chart AAPL"` -> chart task's `depends_on` is sql's task id.
- [ ] **Step 2: Run** `uv run pytest tests/test_team.py -k chart -v` — Expected: FAIL.
- [ ] **Step 3: Implement**; remove the `draw` node.
- [ ] **Step 4: Run** `uv run pytest tests/test_team.py -v` — Expected: PASS; tests of the `draw` step (`test_drawer_*`, `test_no_drawer_no_draw`, `test_single_speaker_answer_stands_then_chart_draws`, `test_answer_without_data_draws_nothing`, `test_draw_message_formats_with_language`, `test_every_agent_withdraws_then_the_drawer_still_draws`, ...) are removed only where they test the removed step; ones whose behaviour the chart task keeps (one chart, no copied numbers) must pass unchanged or be reported.
- [ ] **Step 5: Commit** `feat: team chart task replaces the draw step` with the removed-test list and reason ("draw step removed by the task-graph spec, section 1").

---

### Task 5: Hand-off challenges and per-meeting-point rounds

Agent: implementer

**Files:**
- Modify: `app/orchestration/team.py` (`Challenge`, `Outcome`, `Review(Board)`, `cause` on `Conflict`/`Difference`, `review` node, `ask_board` returns `Review`), `app/orchestration/prompts.py` (`BOARD_PROMPT` becomes the review prompt; add `CHALLENGE_MESSAGE`; `CONFLICT_MESSAGE` stays for conflict rounds)
- Test: `tests/test_team.py`

**Interfaces:**
- Produces:
  - `Cause = Literal["definition", "date", "source", "error", "unknown"]`; `Conflict.cause: Cause = "unknown"`, `Difference.cause: Cause = "unknown"`.
  - `class Challenge(BaseModel)`: `to_task: str`, `by_task: str`, `question: str`, `evidence: str`.
  - `class Outcome(BaseModel)`: `task: str`, `revised: bool` — for a task that answered a challenge in the previous round (decision: the next review call says whether it revised or stood, so no extra call and no optional verdict line).
  - `class Review(Board)`: `challenges: list[Challenge] = []`, `outcomes: list[Outcome] = []`, `follow_ups: list[Task] = []` (Task 6 acts on follow-ups).
  - `ask_board(messages, answers, config, *, inputs: Mapping[str, list[str]] | None = None) -> Review` — answers are now `{"task", "agent", "text"}`; `inputs` maps a task id to the ids it received, written into the human message as `t2 (sql) received: t1`.
  - `clean(review, speakers)` keeps working on `Review` (challenges kept only when both tasks exist and are live).
  - Meeting keys in `workspace["rounds_at"]`: hand-off `"<to_task>-><by_task>"`; meeting conflict `"meet:" + ",".join(sorted(task ids))`.
- Behaviour (spec 3):
  - A challenge whose key is under `TEAM_MAX_ROUNDS` sends the producer one `challenge` round with `CHALLENGE_MESSAGE` (`{question}`, `{evidence}`, `{own}`, `{language}`): re-check your source; correct your result or keep it and say why.
  - The next review's `outcomes`: `revised` -> every done task that used the producer's output gets status `pending` again (runs next wave, `kind: "plan"`); not revised -> an open point `"<to_task> stands: <question>"` in `synthesis.open_points`.
  - Meeting conflicts with `fixable_by` -> conflict round for those tasks with `CONFLICT_MESSAGE`, counted under the meeting key.
  - Past the cap for a key -> its open points go to `synthesis.open_points`, `forced` as now.
  - Review failure -> empty `Review`, `forced`, dispatch continues.

- [ ] **Step 1: Write the failing tests:**
  - `test_challenge_gives_the_producer_one_round`: t1 docs, t2 sql [t1]; review 1 has `Challenge(to_task="t1", by_task="t2", ...)` -> docs runs a second time with a note containing the question and evidence; `wave` event `kind: "challenge"`.
  - `test_revised_output_reruns_only_its_users`: t1 docs, t2 sql [t1], t3 research (independent); after the challenge round the review says `Outcome(task="t1", revised=True)` -> sql runs again, research does not.
  - `test_output_that_stands_becomes_an_open_point`: `revised=False` -> no rerun; the write-up note contains the question.
  - `test_a_meeting_point_gets_at_most_two_rounds`: the review challenges t1 from t2 every time -> docs runs exactly 1 + 2 times; then the write-up runs with `forced`.
  - `test_cap_counts_per_meeting_point`: two different hand-offs each get their own 2 rounds.
  - `test_meeting_conflict_follows_board_rules`: a `Difference` without dates -> becomes a conflict (`clean`), conflict round for `fixable_by` tasks.
  - `test_cause_defaults_to_unknown`: `Conflict(routes=[], claim="c").cause == "unknown"`.
  - `test_review_prompt_and_challenge_message_format`: `.format(language=...)` and `CHALLENGE_MESSAGE.format(question=..., evidence=..., own=..., language=...)` raise nothing.
  - Update the existing `_board` helper only by allowing `Review` objects (a `Board` it returns is converted with `Review(**board.model_dump())` in code, so existing tests keep their `Board(...)` values).
- [ ] **Step 2: Run** `uv run pytest tests/test_team.py -v` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** (load `writing-llm-document` for the prompts; the review prompt keeps the board rules of `BOARD_PROMPT` and adds: extract a challenge only when a task's answer itself states a mismatch with an input; report each previously challenged task's outcome; set `cause` for every conflict and difference).
- [ ] **Step 4: Run** `uv run pytest tests/test_team.py tests/test_team_plan.py -v` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: team hand-off challenges, per-meeting-point rounds`.

---

### Task 6: Follow-ups

Agent: implementer

**Files:**
- Modify: `app/orchestration/team.py` (`review` acts on `follow_ups`; last-wave review rule), `app/orchestration/prompts.py` (review prompt: when to add follow-ups)
- Test: `tests/test_team.py`

**Interfaces:**
- Consumes: `add_follow_ups` (Task 1), `Review.follow_ups` (Task 5).
- Behaviour (spec 2 "Follow-ups"): valid follow-ups are added as pending tasks (`origin: "follow_up"`) and a `plan_updated` event `{tasks: [added]}` goes out; follow-ups dropped by the cap go to `synthesis.not_done`; a follow-up with the same agent and normalised goal as an existing task is dropped; the last wave is reviewed even with one live task when `len(tasks) < TEAM_MAX_TASKS`.
- Review prompt rule: add a follow-up only when a result reveals work the plan lacks and an available agent's sources can do it (with examples written as `<placeholders>`, not realistic values).

- [ ] **Step 1: Write the failing tests:**
  - `test_follow_up_runs_with_its_inputs`: plan `t1 docs`; review returns `follow_ups=[Task(id="x", agent="sql", goal="check the claim", depends_on=["t1"])]` -> sql runs in wave 2, its note contains docs' text; `plan_updated` event lists `t2`.
  - `test_follow_ups_past_the_cap_are_named`: `TEAM_MAX_TASKS=2`, plan has 2 tasks, review adds one -> not run, write-up note names its goal.
  - `test_repeated_follow_up_is_dropped`: a follow-up equal to an existing task (agent + goal case/space-insensitive) -> not added, no loop.
  - `test_last_wave_with_one_task_is_reviewed_below_the_cap`: single-task plan (docs) -> one review call; with the plan at the cap -> as before (lone live task still reviewed as `judge` does now; assert the follow-up is not added at the cap).
  - `test_failed_review_adds_no_follow_ups`: review raises -> no `plan_updated`, write-up runs.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/test_team.py tests/test_team_plan.py -v` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: team follow-up tasks from the review`.

---

### Task 7: Write-up rules and budget stop

Agent: implementer

**Files:**
- Modify: `app/orchestration/prompts.py` (`TEAM_MESSAGE`: `{open_points}`, `{not_done}`, causes in differences/conflicts, observed vs explanation rule), `app/orchestration/team.py` (`synthesize` fills them; `dispatch` stops on budget)
- Test: `tests/test_team.py`

**Interfaces:**
- Behaviour:
  - `TEAM_MESSAGE` (spec 5): lists results as `<task id> (<agent>): <text>`, differences and conflicts with their `cause`, open points, not-done tasks; rules: name the cause of each disagreement; separate what was observed in sql/docs results from possible explanations from research or reasoning, never state an explanation as an observation; name open points and work not done.
  - Budget (spec 7): `speak` catches `CallBudgetExceeded` (`app.agents.remote`) and records the task as `failed` with `budget: True`; `dispatch` sends nothing more once any result has `budget: True` and goes to `synthesize`, which names the pending tasks in `not_done`.
  - The one-live-answer shortcut stays: one live result from a directly answering agent, no conflicts, no open points, not forced -> that answer stands.

- [ ] **Step 1: Write the failing tests:**
  - `test_write_up_names_causes_open_points_and_not_done`: scripted review with a conflict `cause="date"` and an open point; budget not hit; the `TEAM RESULT` note contains `date`, the open point and `observed`-rule text.
  - `test_write_up_names_the_language`: Vietnamese message -> the note names Vietnamese.
  - `test_budget_stops_new_waves`: t1 raises `CallBudgetExceeded`, t2 depends on t1, t3 independent in a later wave (follow-up) -> no further agent runs; write-up note lists the not-run tasks.
  - `test_team_message_formats`: `TEAM_MESSAGE.format(...)` with every placeholder.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** (load `writing-llm-document`).
- [ ] **Step 4: Run** `uv run pytest tests/test_team.py -v` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: team write-up names causes, open points and unfinished work`.

---

### Task 8: Pause and resume on the workspace

Agent: implementer

**Files:**
- Modify: `app/orchestration/team.py` (`pause`)
- Test: `tests/test_team.py`

**Interfaces:**
- Behaviour (spec 7): a review question -> `needs_human`, `pause` (`interrupt` first, nothing before it); resume: the reply joins the thread; an available agent the reply names that has no task gets a task `origin: "reply"`, no dependencies, goal `""` -> one wave -> review; otherwise back to `review`. A free-text reply brings nobody in. `remember_turn` and `_chat_input` are unchanged.

- [ ] **Step 1:** Run the existing pause tests (`test_needs_human_pauses_and_resume_continues`, `test_reply_to_question_naming_a_new_agent_brings_it_in`, `test_reply_never_brings_in_an_unavailable_agent`, `test_reply_to_a_question_applies_withdrawal_to_the_new_round`, `test_chat_input_resumes_a_paused_team_turn`) — they describe kept behaviour and must pass unchanged. Add `test_reply_task_has_origin_reply`: the new agent's task in the transcript has `origin == "reply"`.
- [ ] **Step 2: Run** — Expected: the new test FAILS (others may fail after Task 3's rename; fix code, not tests).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/test_team.py -v` — Expected: PASS.
- [ ] **Step 5: Commit** `feat: team pause and resume on the workspace`.

---

### Task 9: Stream events and UI

Agent: implementer

**Files:**
- Modify: `app/api/routes_chat.py` (`TEAM_EVENTS`: `plan`, `plan_updated`, `wave`, `review`, `spoke`, `synthesize`, `charts`, `needs_human`; `speaker` keeps mapping `ns -> agent`), `app/static/app.js` (`newTurn`, `renderStatus`, `renderActivity`, `teamActivity`, the event switch at `case "round"`/`"board"`), `app/static/i18n.js` (EN/VI strings for wave, challenge, follow-up, plan)
- Test: `tests/test_chat_events.py`

**Interfaces:**
- UI (spec 6): status `Wave n/m · <agent badges>` (m = tasks' wave count so far, grows with `plan_updated`), `"<by agent> questions <to agent>"` on a review with challenges, `+ <agent>: <goal>` per follow-up, afterwards `<n> waves · <k> challenges resolved`. Activity popup: plan list at top (`t2 sql: <goal> ← t1`, follow-ups marked), each wave's task answers and tool calls, each review with challenges and outcomes. `teamActivity` reads the workspace shape and the old `rounds`/`boards` shape.

- [ ] **Step 1: Write the failing tests** in `tests/test_chat_events.py`: `plan`, `plan_updated`, `wave`, `review` chunks pass through `_events_from` as SSE events; `spoke` keeps `task`; a `speaker` `{agent, task, ns}` maps tokens of that namespace to the agent; `round`/`board` no longer emitted by the team (assert the new names only).
- [ ] **Step 2: Run** `uv run pytest tests/test_chat_events.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** server and UI.
- [ ] **Step 4: Run** `uv run pytest tests/test_chat_events.py tests/test_team.py -v` — Expected: PASS. Then start the app (`scripts\dev_db.ps1`, `docker compose up -d --build` through WSL, `uv run uvicorn app.api.main:app --loop asyncio:SelectorEventLoop`) and check in a browser that a team message shows the plan, waves and a review, and that an old team chat still opens its Activity popup. Report what was seen; if the stack can't start, say so.
- [ ] **Step 5: Commit** `feat: team task graph events and UI`.

---

### Task 10: Benchmark

Agent: implementer

**Files:**
- Modify: `scripts/bench_chat.py`
- Create: fixtures next to the existing bench fixtures (`FIXTURES` in `bench_chat.py`): a report with checkable claims (B), two short files that disagree on a figure (D), notes on a general topic (A), a file with a planted wrong stock figure (planted error)
- Test: `tests/test_bench_chat.py`

**Interfaces:**
- Produces:
  - Team scenarios (12-15) in `SCENARIOS`, each with `expected = {"required": [...], "optional": [...]}`: no specialist; research only; docs only; sql + chart; all agents; A-E; planted error; a needed follow-up.
  - `choice(expected: dict, took_part: Sequence[str]) -> dict` -> `{"precision", "recall", "missing_required"}`; took_part = agents of the transcript's tasks (plan + follow-ups + reply).
  - `contribution(transcript: dict, answer: str) -> dict[str, dict]` -> per agent `{"live": bool, "used": bool}` (`used`: one of its evidence claims or a cited source appears in the answer).
  - Planted-error check: the scenario passes when the transcript's reviews hold a challenge or conflict naming the planted figure's task; follow-up check: the expected agent appears with `origin == "follow_up"`.
  - `--ablate`: rerun each team scenario without each participating agent (team request with that route unavailable) and record a judge comparison (one structured call on `OPENROUTER_JUDGE_MODEL`: which answer is better and why); off by default.
- Constraints: no comparison of judge models; keep the existing `Result`/timing output; the script keeps `loop_factory=asyncio.SelectorEventLoop` on Windows.

- [ ] **Step 1: Write the failing tests:** `choice` (missing required -> recall < 1 and listed; extra optional does not lower precision; extra non-listed lowers precision); `contribution` (withdrawn -> `live False`; claim text in answer -> `used True`); every team scenario has `expected`.
- [ ] **Step 2: Run** `uv run pytest tests/test_bench_chat.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest tests/test_bench_chat.py -v` — Expected: PASS. Do not run the benchmark itself (spends quota); the user runs it.
- [ ] **Step 5: Commit** `feat: team benchmark: choice, contribution, planted error, ablation`.

---

### Task 11: Docs

Agent: implementer (CLAUDE.md is read by an LLM: load `writing-llm-document`)

**Files:**
- Modify: `ARCHITECTURE.md` (section 11: three layers, plan, waves, checks, follow-ups, workspace, settings), `CLAUDE.md` (**Team mode** section: replace the round-table description with the task graph; update settings and measured-calls lines that no longer hold), `.claude/rules/changelog.md` (version list: `0.5.0` agent team, image agent takes the next minor when it deploys)

- [ ] **Step 1:** Edit the three files from the spec and the code as built (names from Tasks 1-10).
- [ ] **Step 2:** `uv run pytest` (full suite after `scripts\dev_db.ps1`) — Expected: PASS, no skipped DB tests.
- [ ] **Step 3: Commit** `docs: team task graph in ARCHITECTURE.md and CLAUDE.md`.
