# Team mode: task graph (plan, waves, checks)

Date: 2026-10-26. Status: approved design, not built. Version: 0.5.0 (part of the agent-team release, which is not merged into `main` yet).
Builds on: `docs/superpowers/specs/2026-10-23-agent-team-design.md` ("Phase C v2: round table") and the team fixes on `0.5.2/team-fixes`; this branch (`0.5.0/team-task-graph`) starts from there.

## Why

The round table (`pick -> speak x N -> judge -> synthesize -> draw`) lets every agent answer the same question from its own source and has a board compare the answers. That is a panel debating, not a team working together. The goal: agents split the work, one agent's output becomes another's input, independent work runs at the same time, and every agent still questions the input it receives so the final answer is the best the team can give.

Target scenario: **portfolio review from an uploaded statement** ("Here's my brokerage statement. How is my portfolio doing, and why?"):

```
              ┌──▶ t2 sql: returns since buy dates ──┬──▶ t4 chart: returns per holding
t1 docs: holdings ┤                                    ├──▶ orchestrator write-up
              └──▶ t3 research: news on holdings ────┘
```

docs extracts the holdings, sql and research both need only the tickers and run in parallel, chart draws from sql's table, the orchestrator writes the brief. sql and research may question docs ("close that day was $850, is the buy price split-adjusted?"); their results are compared where they meet.

The chain and today's round table are special cases: a chain is a graph where each task depends on the previous one; the round table is a graph of independent tasks that all meet at the write-up. This design replaces the round table's `pick -> speak -> judge` loop; `participants()` stays as the fallback plan.

## 1. Plan and execution

### The plan

One structured call by the orchestrator model (the way `ask_board` calls the judge), input: the user message, the available agents (`description`, `review`, `sources`), the user's file names.

```python
class Task(BaseModel):
    id: str                 # "t1", "t2", ...
    agent: str              # route
    goal: str               # what this task must produce
    depends_on: list[str] = []

class Plan(BaseModel):
    tasks: list[Task]       # at most TEAM_MAX_TASKS
```

Code validates the plan before anything runs:
- tasks for unknown or unavailable agents are dropped, and so is every dependency on a dropped task;
- a cycle, an empty plan or a failed call -> fallback plan: `participants()` as independent tasks (today's behaviour), `plan` event with `fallback: true`;
- more than `TEAM_MAX_TASKS` tasks -> capped;
- the same agent may appear in several tasks with different goals.

No planning call: small talk (`is_small_talk`, the orchestrator answers as now) and exactly one named speaker (a single task).

### Waves

A `dispatch` node sends every task whose dependencies are done, one `Send("work")` per task (`work` is today's `speak`, extended with a task id). A wave ends when all its tasks have returned (LangGraph runs a step's Sends together), then control returns to `dispatch` (via the review, section 2).

Each task receives the thread, a hidden note with its goal (and its `RouteInfo.prompt_note`, as `_round_notes` does now), and the outputs of the tasks it depends on: their text plus the ids of their tables, so it reuses tables via the artifacts and no model copies data.

Trade-off accepted for v1: a fast task waits for the slowest task of its wave (in the example, chart waits for research). This costs time, never model calls. Waves keep `Send`, the per-Send namespace the `speaker` event relies on, and checkpoints for `pause`. If the wait matters in practice, `dispatch` can later run tasks itself with `asyncio` (start each when its inputs are ready) without changing plan, checks or events.

### The drawer is a task

chart becomes a normal task that depends on the task producing its table (usually sql). This replaces the `draw` step and `fed_by`. The single-chart rule stays: the write-up orchestrator gets `chart_table`/`chart_data` denied (`deny_tools`) when the plan has a chart task, and the chart task gets `chart_data` denied when its input carries tables.

## 2. Checks

Two kinds, both through **one structured review call per wave** (the `ask_board` model and settings: `OPENROUTER_JUDGE_MODEL`, `exact_model`). Weak models skip optional tools and optional verdict lines (AP-006), so:
- **the agent provides the content**: a task with inputs gets the note "compare the input with your own source; if something does not match, say so in your answer with the value you found and where you found it";
- **the review call extracts it reliably** with structured output, reading each receiver's answer next to the input it got.

```python
class Challenge(BaseModel):
    to_task: str        # whose output is questioned
    by_task: str        # who found the problem
    question: str
    evidence: str       # the receiver's own value and source

class Review(Board):    # agreements, differences, conflicts, question: as Board now
    challenges: list[Challenge] = []
```

### Hand-off checks (receiver questions producer)

- A challenge gives the producer one round with `CHALLENGE_MESSAGE` (question, evidence, its own earlier output). It re-checks its source and revises or stands by its output.
- Revised: the tasks that already used the old output run once more on the new one (only those).
- Stands: the dispute goes into the write-up as an open point (like `forced` today).

### Meeting checks (parallel results meet)

Where a wave's tasks feed the same next task, or the write-up, the same review call compares them under today's board rules: no decision by kind of source, a difference is explained only when every side has a source and an as-of date and the explanation fits the gap, `clean` enforces the source/date part in code, `routes`/`fixable_by` are restricted as now. A conflict with `fixable_by` starts a conflict round for those tasks. The last meeting point (all final tasks into the write-up) is today's board.

### Rounds cap

A **meeting point** is one place a check happens: a hand-off (A -> B) or a place where parallel results meet. Each meeting point gets at most `TEAM_MAX_ROUNDS` extra rounds (challenge and conflict rounds together); the setting keeps its name, its default becomes **2** (was 4) and it now counts per meeting point. Past the cap its open points go into the write-up. The plan's normal waves do not count. `TEAM_MAX_MESSAGE_MODEL_CALLS` (30) stays the overall backstop. Accepted: a large plan with many meeting points spends many calls; small plans spend few.

### Order in one wave

```
dispatch -> work x N (parallel) -> review --+-- challenges/conflicts -> re-run producers (and dependents of a revised output) -> review
                                            +-- question for the user -> pause
                                            +-- clean -> dispatch (next wave) ... -> synthesize
```

No review call when the wave's tasks took no input and meet nothing yet (wave 1 of the example), or when at most one task is live (as now).

Cost of the example: plan 1 + docs + sql + research + chart + about 2 reviews + write-up 1, about 4 calls more than the agents alone. A challenge adds 1 call for the producer plus one per re-run dependent.

Not in v1: checks in code (e.g. a ticker missing from `market.stocks`); sql finds that itself when its query returns nothing, so it surfaces as a challenge.

## 3. Events and UI

Starting point; the user will adjust after testing.

| Event | Now | New |
|---|---|---|
| `plan` | none | once after planning: `tasks` (id, agent, goal, depends_on), `fallback: true` on the fallback plan |
| `round` | `round`, `kind`, `agents` | becomes `wave`: `wave`, `kind` (`plan`/`challenge`/`conflict`), `tasks` |
| `spoke` | `agent`, `text`, `tools` | adds `task` |
| `board` | Board + `forced` | becomes `review`: the Review incl. `challenges`, `forced` |
| `speaker` (internal) | `{agent, ns}` | `{agent, task, ns}`, so two tasks of one agent in a wave stream apart |
| `synthesize`, `charts`, `needs_human` | as now | as now |

Status line: `Wave 2/3 · sql, research` while working; a challenge shows `sql questions docs`; afterwards `3 waves · 1 challenge resolved`.

Activity popup (`renderActivity`): the plan as a list at the top (`t2 sql: returns ← t1`, no graph drawing in v1); then each wave with its tasks' answers and tool calls; each review with its challenges ("sql -> docs: close was $850, is the buy price split-adjusted?") and outcome (revised / stands).

Reload: `additional_kwargs["team"]` holds `plan`, `waves`, `reviews` instead of `rounds`/`boards`. `teamActivity` reads both shapes so team chats saved by the round table still show their rounds.

## 4. Errors and limits

| Case | Behaviour |
|---|---|
| Plan call fails, plan invalid or empty | fallback plan (`participants()`, independent tasks), `plan.fallback: true` |
| A task fails or withdraws | as now: `failed`/`withdrew` entry, left out of reviews and write-up |
| Every input of a task failed or withdrew | task skipped, no call; with some inputs missing it runs and its note names the missing ones |
| Review call fails | empty review with `forced` (as the board now); dispatch continues |
| `TEAM_MAX_MESSAGE_MODEL_CALLS` reached (`CallBudgetExceeded`) | no new waves; the write-up runs on what finished and names the tasks that never ran and the open disputes. Check the counter between waves explicitly if `work` swallows the exception as a normal failure |
| `RateLimitError` / `AuthenticationError` | propagate, as now |
| Review has a question for the user | `pause`/`interrupt` as now; resume goes back to that review; a reply naming a new agent adds a task with no dependencies, runs one wave, then reviews again |
| Plan too large | at most `TEAM_MAX_TASKS` (default 6); waves are bounded by tasks |

Honest limits: a challenge exists only if the receiver wrote the mismatch down; waves make fast tasks wait for slow ones; with 2 rounds per meeting point a large plan can hit the 30-call backstop before finishing, and the write-up then says what is missing.

## 5. Tests

Written from this design, in `tests/test_team.py` with local fake agents:
- plan validation: unknown/unavailable agent dropped, dependency on a dropped task removed, cycle -> fallback, more than `TEAM_MAX_TASKS` -> capped, failed call -> fallback, one named speaker or small talk -> no planning call;
- waves: a task waits for its dependencies, independent tasks share a wave, the note carries the dependencies' text and table ids, two tasks of one agent in a wave stream apart;
- checks: a challenge gives the producer one round; a revised output re-runs only the tasks that used it; an output that stands becomes an open point; a meeting point never gets more than 2 rounds; a meeting conflict follows the board rules (`clean`); no review call for a wave without inputs or meeting points;
- errors: every row of section 4;
- events (`tests/test_chat_events.py`): `plan`, `wave`, `spoke.task`, `review`, `speaker.task`; reload builds the popup from both transcript shapes.

Removed: tests of round-table behaviour this design replaces (the `draw` step and `fed_by`, rounds over all participants at once), each named with its reason in the commit. `participants()` stays (fallback), so its tests stay.

Bench: a `portfolio` scenario in `scripts/bench_chat.py` with a small holdings PDF fixture, measuring calls and latency on the real models.

Docs in the same branch: `ARCHITECTURE.md` section 11 and the **Team mode** section of `CLAUDE.md`.

## Settings

- `TEAM_MAX_TASKS` (new, default 6).
- `TEAM_MAX_ROUNDS` (default 4 -> 2; now per meeting point).
- `TEAM_MAX_MESSAGE_MODEL_CALLS` (30, unchanged), `OPENROUTER_JUDGE_MODEL` (used for the review call, unchanged).
