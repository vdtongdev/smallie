# Team mode: task graph (plan, waves, checks, shared workspace)

Date: 2026-10-26. Status: approved design, not built. Version: 0.5.0 (part of the agent-team release, which is not merged into `main` yet).
Builds on: `docs/superpowers/specs/2026-10-23-agent-team-design.md` ("Phase C v2: round table") and the team fixes on `0.5.2/team-fixes`; this branch (`0.5.0/team-task-graph`) starts from there.

## Why

The round table (`pick -> speak x N -> judge -> synthesize -> draw`) lets every agent answer the same question from its own source and has a board compare the answers. That is a panel debating, not a team working together. The goal: agents split the work, one agent's output becomes another's input, independent work runs at the same time, the plan grows when results call for more work, and every agent still questions the input it receives so the final answer is the best the team can give.

## Three layers

1. **Intent and planning.** The orchestrator identifies the goal, decomposes the request into tasks and picks only the agents it needs, possibly none (section 1). After each wave it may add follow-up tasks based on what the results revealed (section 2).
2. **Agent execution.** sql, research, docs and chart run tasks independently or through task dependencies, in waves (section 1), and question the input they receive (section 3).
3. **Shared workspace.** One state object holds task state, results, evidence, artifacts, execution history and the final synthesis; the planner, the reviews and the write-up all read it (section 4). The chat messages stay in the thread.

## Scenarios

The design is general; these scenarios guide it and the benchmark (section 8). Agents marked optional join only when the request or an earlier result calls for them.

- **A. Research a topic using multiple sources** ("Give me a comprehensive explanation of this topic."): research searches external sources, docs checks uploaded materials (parallel); the write-up compares the findings, names contradictions and cites. sql joins only if the user asks for internal data or research reveals a database question (follow-up); chart only if a visualisation adds value.
- **B. Analyse an uploaded document with external verification** ("Review this report and tell me whether its claims are supported."): docs extracts the report's claims; research verifies them independently (depends on docs); the write-up flags unsupported statements. chart optional if the report has suitable numbers; sql optional for a comparison with internal data.
- **C. Investigate data and explain the result** ("Find interesting patterns in this dataset and explain them."): sql explores the structured data, chart visualises the patterns (depends on sql), research looks for possible external explanations, docs for context in the user's files; the write-up separates observed patterns from possible explanations. With the current database the patterns are stock-related; with another database the same flow works for another subject.
- **D. Resolve conflicting information** ("These two sources disagree. Determine why."): docs extracts the claims, research checks the original or newer sources, sql checks structured records when the disagreement concerns database facts; the write-up says whether the discrepancy comes from definitions, dates, sources or real errors (`cause`, section 5).
- **E. Adaptive investigation** ("Investigate this problem and find the most likely explanation."): the plan starts with the most relevant agent; follow-ups add work as results come in (a document reveals a numerical claim -> sql checks it; research mentions internal evidence -> docs searches for it), run independent checks in parallel, or request a verification step.
- Example used below, **portfolio review**: docs extracts holdings; sql (returns) and research (news) run in parallel on the tickers; chart draws sql's table:

```
              ┌──▶ t2 sql: returns since buy dates ──┬──▶ t4 chart: returns per holding
t1 docs: holdings ┤                                    ├──▶ orchestrator write-up
              └──▶ t3 research: news on holdings ────┘
```

The chain and today's round table are special cases: a chain is a graph where each task depends on the previous one; the round table is a graph of independent tasks that all meet at the write-up. This design replaces the round table's `pick -> speak -> judge` loop; `participants()` stays as the fallback plan.

## 1. Plan and execution

### The plan

One structured call by the orchestrator model (the way `ask_board` calls the judge), input: the user message, the available agents (`description`, `review`, `sources`), the user's file names. The prompt says to pick only the agents the request needs and to start small: follow-ups (section 2) add work later.

```python
class Task(BaseModel):
    id: str                 # "t1", "t2", ...
    agent: str              # route
    goal: str               # what this task must produce
    depends_on: list[str] = []

class Plan(BaseModel):
    tasks: list[Task]       # at most TEAM_MAX_TASKS; empty = no specialist needed
```

Code validates the plan before anything runs:
- tasks for unknown or unavailable agents are dropped, and so is every dependency on a dropped task;
- **an empty plan means no specialist is needed**: the orchestrator answers as in normal mode (a handoff is run on the thread, as `pick` does for small talk now);
- a cycle, a failed call or a plan whose tasks were all dropped -> fallback plan: `participants()` as independent tasks (today's behaviour), `plan` event with `fallback: true`;
- more than `TEAM_MAX_TASKS` tasks -> capped;
- the same agent may appear in several tasks with different goals.

No planning call: small talk (`is_small_talk`, the orchestrator answers as now) and exactly one named speaker (a single task).

### Waves

A `dispatch` node sends every pending task whose dependencies are done, one `Send("work")` per task (`work` is today's `speak`, extended with a task id). A wave ends when all its tasks have returned (LangGraph runs a step's Sends together), then control goes through the review (section 2) back to `dispatch`. When no task is pending, `dispatch` goes to `synthesize`.

Each task receives the thread, a hidden note with its goal (and its `RouteInfo.prompt_note`, as `_round_notes` does now), and the results of the tasks it depends on from the workspace: their text plus the ids of their tables, so it reuses tables via the artifacts and no model copies data.

Trade-off accepted for v1: a fast task waits for the slowest task of its wave (in the example, chart waits for research). This costs time, never model calls. Waves keep `Send`, the per-Send namespace the `speaker` event relies on, and checkpoints for `pause`. If the wait matters in practice, `dispatch` can later run tasks itself with `asyncio` (start each when its inputs are ready) without changing plan, checks or events.

### The drawer is a task

chart becomes a task that depends on the write-up: its `depends_on` is the fixed id `answer`, and `dispatch` sends it after `synthesize` with the final answer as its input. It charts only data that survived the reviews: the answer's table (from sql) with `chart_table`, or, when the answer has no table, the figures written in the answer (e.g. research's figures when sql found nothing) with `chart_data`. An answer with no data gets one line saying so and no chart. Its charts merge into the final answer (same message id). This replaces the separate `draw` step and the drawer's `fed_by`; the cost stays one chart-agent run. The single-chart rule stays: the write-up orchestrator gets `chart_table`/`chart_data` denied (`deny_tools`) when the workspace has a chart task, and the chart task gets `chart_data` denied when the answer carries tables.

## 2. Review after each wave

One **structured review call per wave** (the `ask_board` model and settings: `OPENROUTER_JUDGE_MODEL`, `exact_model`) reads the wave's results with their inputs and the workspace, and returns the checks (section 3) and the follow-ups:

```python
class Challenge(BaseModel):
    to_task: str        # whose output is questioned
    by_task: str        # who found the problem
    question: str
    evidence: str       # the receiver's own value and source

class Review(Board):    # agreements, differences, conflicts, question: as Board now
    challenges: list[Challenge] = []
    follow_ups: list[Task] = []
```

### Follow-ups (adaptive planning)

A follow-up is a new task the results call for, e.g. "docs found a revenue claim -> sql checks it", "research mentions an internal memo -> docs searches for it", "sql found a pattern -> chart draws it". It may depend on finished tasks. Follow-ups pass the same code checks as the plan (unknown or unavailable agent dropped, dependencies only on existing tasks, no cycles) and count toward `TEAM_MAX_TASKS`; past the cap they are dropped and the write-up names them as not done. Follow-ups cost no extra call: they come from the review call that runs anyway. A wave whose review is skipped (below) gets no follow-ups.

### When there is no review call

No review call when the wave's tasks took no input and meet nothing yet (wave 1 of the example), or when at most one task is live (as now). Exception: when that wave is the last one (no pending task), the review still runs with one live task, as the round-table board did, so the plan can grow (scenario E starts with one agent; at `TEAM_MAX_TASKS` its follow-ups are dropped and named), except when the other tasks withdrew: then the one live answer stands without a review call, as now (those agents already searched their sources and found nothing).

## 3. Checks

Two kinds, both from the review call. Weak models skip optional tools and optional verdict lines (AP-006), so:
- **the agent provides the content**: a task with inputs gets the note "compare the input with your own source; if something does not match, say so in your answer with the value you found and where you found it";
- **the review call extracts it reliably** with structured output, reading each receiver's answer next to the input it got.

### Hand-off checks (receiver questions producer)

- A challenge gives the producer one round with `CHALLENGE_MESSAGE` (question, evidence, its own earlier output). It re-checks its source and revises or stands by its output.
- Revised: the tasks that already used the old output run once more on the new one (only those). Their re-run may raise a new challenge, within the cap below.
- Stands: the dispute goes into the write-up as an open point (like `forced` today).

### Meeting checks (parallel results meet)

Where a wave's tasks feed the same next task, or the write-up, the review compares them under today's board rules: no decision by kind of source, a difference is explained only when every side has a source and an as-of date and the explanation fits the gap, `clean` enforces the source/date part in code, `routes`/`fixable_by` are restricted as now. A conflict with `fixable_by` starts a conflict round for those tasks. The last meeting point (all final tasks into the write-up) is today's board.

### Rounds cap

A **meeting point** is one place a check happens: a hand-off (A -> B) or a place where parallel results meet. Each meeting point gets at most `TEAM_MAX_ROUNDS` extra rounds (challenge and conflict rounds together); the setting keeps its name, its default becomes **2** (was 4) and it now counts per meeting point. Past the cap its open points go into the write-up. The plan's normal waves and follow-up tasks do not count. `TEAM_MAX_MESSAGE_MODEL_CALLS` (30) stays the overall backstop. Accepted: a large plan with many meeting points spends many calls; small plans spend few.

### Order in one wave

```
dispatch -> work x N (parallel) -> review --+-- challenges/conflicts -> re-run producers (and dependents of a revised output) -> review
                                            +-- question for the user -> pause
                                            +-- follow-ups -> added as pending tasks
                                            +-- clean -> dispatch (next wave, or synthesize when nothing is pending)
```

Cost of the portfolio example: plan 1 + docs + sql + research + chart + about 2 reviews + write-up 1, about 4 calls more than the agents alone. A challenge adds 1 call for the producer plus one per re-run dependent; a follow-up adds its agent's calls.

Not in v1: checks in code (e.g. a ticker missing from `market.stocks`); sql finds that itself when its query returns nothing, so it surfaces as a challenge.

## 4. Shared workspace

One state key `workspace` replaces `team` and `speeches` of `TeamState`:

```python
workspace = {
    "goal": str,                 # the planner's reading of the request
    "tasks": [{"id", "agent", "goal", "depends_on", "status", "origin"}],
        # status: pending | running | done | failed | withdrew | skipped
        # origin: plan | follow_up | fallback | reply (named in a pause reply)
    "results": {task_id: {"text", "tools", "artifacts", "round"}},  # newest per task; older ones stay in history
    "evidence": [{"task", "claim", "source", "as_of"}],              # extracted by the reviews (Side, Challenge.evidence)
    "artifacts": {"tables": [...], "charts": [...]},                 # merged, as `_merge` does now
    "history": [...],            # every wave, review, challenge and outcome, in order
    "synthesis": {"forced", "open_points", "not_done"},
}
```

- The planner (for a reply after `pause`), every review and the write-up read the workspace; agents get only their own task's slice (goal plus their inputs' results).
- It is reset at the start of every message (as `RESET` does now); nothing of an earlier message carries over.
- The final answer carries it as the transcript in `additional_kwargs["team"]` (with the turn's tables/charts), so a reload rebuilds the Activity popup.

## 5. Write-up rules

The `synthesize` step keeps today's behaviour (one live answer from a directly answering agent with no conflicts stands; otherwise the orchestrator writes from `TEAM_MESSAGE`), with these additions:
- `Conflict` and `Difference` get a `cause` field: `definition | date | source | error | unknown`; the write-up names the cause (scenario D);
- the write-up separates **observed** (from sql or docs results) from **possible explanation** (from research or reasoning) and never states an explanation as an observation (scenario C);
- open points, tasks that never ran and follow-ups dropped by the cap are named.

These are prompt rules plus one field; a weak model can still blur the two.

## 6. Events and UI

Starting point; the user will adjust after testing.

| Event | Now | New |
|---|---|---|
| `plan` | none | once after planning: `tasks` (id, agent, goal, depends_on), `fallback: true` on the fallback plan |
| `plan_updated` | none | after a review adds follow-ups: the new `tasks` |
| `round` | `round`, `kind`, `agents` | becomes `wave`: `wave`, `kind` (`plan`/`challenge`/`conflict`), `tasks` |
| `spoke` | `agent`, `text`, `tools` | adds `task` |
| `board` | Board + `forced` | becomes `review`: the Review incl. `challenges` and `follow_ups`, `forced` |
| `speaker` (internal) | `{agent, ns}` | `{agent, task, ns}`, so two tasks of one agent in a wave stream apart |
| `synthesize`, `charts`, `needs_human` | as now | as now |

Status line: `Wave 2/3 · sql, research` while working (the total grows with follow-ups); a challenge shows `sql questions docs`; a follow-up shows `+ sql: check revenue claim`; afterwards `3 waves · 1 challenge resolved`.

Activity popup (`renderActivity`): the plan as a list at the top (`t2 sql: returns ← t1`; follow-ups marked, no graph drawing in v1); then each wave with its tasks' answers and tool calls; each review with its challenges ("sql -> docs: close was $850, is the buy price split-adjusted?") and outcome (revised / stands).

Reload: `additional_kwargs["team"]` holds the workspace instead of `rounds`/`boards`. `teamActivity` reads both shapes so team chats saved by the round table still show their rounds.

## 7. Errors and limits

| Case | Behaviour |
|---|---|
| Plan call fails, plan invalid, every task dropped | fallback plan (`participants()`, independent tasks), `plan.fallback: true` |
| Plan legitimately empty | no specialist: the orchestrator answers as in normal mode |
| A follow-up is invalid or over the cap | dropped; over the cap it is named in `synthesis.not_done` |
| A task fails or withdraws | as now: a withdrawn task is left out of reviews and the write-up; a failed task stays in them with its failure text and forces the write-up (`forced`) |
| Every input of a task failed or withdrew | task `skipped`, no call; with some inputs missing it runs and its note names the missing ones |
| Review call fails | empty review with `forced` (as the board now), no follow-ups; dispatch continues |
| `TEAM_MAX_MESSAGE_MODEL_CALLS` reached (`CallBudgetExceeded`) | no new waves; the write-up runs on what finished and names the tasks that never ran and the open disputes. Check the counter between waves explicitly if `work` swallows the exception as a normal failure |
| `RateLimitError` / `AuthenticationError` | propagate, as now |
| Review has a question for the user | `pause`/`interrupt` as now; resume goes back to that review; a reply naming a new agent adds a task (`origin: reply`) with no dependencies, runs one wave, then reviews again |
| Plan too large | at most `TEAM_MAX_TASKS` (default 6), follow-ups included; waves are bounded by tasks |

Honest limits: a challenge exists only if the receiver wrote the mismatch down; one review call returning checks and follow-ups is a large structured output, not yet measured on the judge model (`deepseek/deepseek-v4.1-flash`); if the benchmark shows it dropping follow-ups or missing planted errors, follow-ups move to their own call (+1 per wave); waves make fast tasks wait for slow ones; with 2 rounds per meeting point a large plan can hit the 30-call backstop before finishing, and the write-up then says what is missing.

## 8. Tests and benchmark

### Tests

Written from this design, in `tests/test_team.py` with local fake agents:
- plan: unknown/unavailable agent dropped, dependency on a dropped task removed, cycle -> fallback, every task dropped -> fallback, failed call -> fallback, empty plan -> orchestrator answers alone (no fallback), more than `TEAM_MAX_TASKS` -> capped, one named speaker or small talk -> no planning call;
- waves: a task waits for its dependencies, independent tasks share a wave, the note carries the dependencies' text and table ids, two tasks of one agent in a wave stream apart;
- follow-ups: a review's follow-up runs in a later wave with its dependencies' results; invalid follow-ups are dropped; follow-ups past `TEAM_MAX_TASKS` are dropped and named; the last wave of a plan below the cap is reviewed even with one live task;
- checks: a challenge gives the producer one round; a revised output re-runs only the tasks that used it; an output that stands becomes an open point; a meeting point never gets more than 2 rounds; a meeting conflict follows the board rules (`clean`); no review call for a wave without inputs or meeting points (unless it is the last wave below the cap);
- workspace: reset per message; task statuses; the transcript round-trips through `additional_kwargs["team"]`;
- errors: every row of section 7;
- events (`tests/test_chat_events.py`): `plan`, `plan_updated`, `wave`, `spoke.task`, `review`, `speaker.task`; reload builds the popup from both transcript shapes.

Removed: tests of round-table behaviour this design replaces (the `draw` step and `fed_by`, rounds over all participants at once), each named with its reason in the commit. `participants()` stays (fallback), so its tests stay.

### Benchmark

Team scenarios in `scripts/bench_chat.py` on the real models, about 12-15 requests, not one finance case:
- categories: no specialist; research only; docs only; sql + chart; all agents; scenarios A-E;
- fixtures: a report with checkable claims (B), two sources that disagree (D), a paper or notes on a general topic (A), plus the stock database (C);
- each request lists its expected agents as `required` and `optional`.

Measured per request:
- **choice**: precision and recall of the agents that took part (plan plus follow-ups) against `required ∪ optional`; a missing required agent is an error, an added optional one is not;
- **contribution (every run, cheap)**: each agent stayed live (not withdrawn) and the final answer uses its evidence (traced through the workspace's evidence and the answer's citations);
- **contribution by ablation (opt-in `--ablate`)**: the request is rerun without each participating agent in turn and a judge model compares the answers; the real test of whether an agent improves the answer, but it multiplies the calls by about the number of agents, so it runs only when asked;
- **checks with the combined review**: requests with a planted error (a wrong figure in an uploaded file, as in the earlier board measurement) and with a needed follow-up, run on the configured judge (`deepseek/deepseek-v4.1-flash`, single review call); measured: the planted error is flagged (challenge or conflict) and the follow-up appears. No comparison with other judge models;
- calls and latency, as `bench_chat` measures now.

Docs in the same branch: `ARCHITECTURE.md` section 11 and the **Team mode** section of `CLAUDE.md`.

## Settings

- `TEAM_MAX_TASKS` (new, default 6, follow-ups included).
- `TEAM_MAX_ROUNDS` (default 4 -> 2; now per meeting point).
- `TEAM_MAX_MESSAGE_MODEL_CALLS` (30, unchanged), `OPENROUTER_JUDGE_MODEL` (used for the review call, unchanged).
