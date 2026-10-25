# Agent Team, Round Table Implementation Plan

> **For agentic workers:** run with the foreman loop (implementer per task, verifier after each). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the author → critic → refine relay with a round table: participants picked in code answer in parallel from their own sources only, one judge call sorts the answers into agreements, explained differences and conflicts, only fixable conflicts get another parallel round (cap 4), and the chat shows only a status line while the full log lives in an Activity popup.

**Architecture:** `team.py` becomes `pick → speak (Send per agent) → judge → [speak …] → synthesize`, plus the existing `pause`. Tools carry a `source` tag and a `build_agent` middleware hides tools outside `AgentContext.sources`. `_to_events` tags round events with the speaking agent. The UI replaces inline tool lines (all chats) with a status line and a popup.

**Tech Stack:** as before; LangGraph `Send` for the fan-out.

**Spec:** `docs/superpowers/specs/2026-10-23-agent-team-design.md`, section "Phase C v2: round table".

## Global Constraints

- Phase C plan constraints still hold: branch `0.5.0/agent-team`; `.claude/settings.json` is untracked, never `git add -A` / `git add .` / `commit -a`, check `git show --stat HEAD`; `writing-llm-document` for every prompt/docstring; traps `prompt-format-braces`, `reply-language`, `copyable-prompt-examples`, `weak-models-optional-steps`, `extra-model-calls`, `workflow-node-names`, `browser-check-real-profile`.
- Kept from the relay (don't rewrite): `TEAM_MODE`, `AgentSpec.review` and `sources`, `additional_kwargs["asked"]`, `TEAM_MAX_MESSAGE_MODEL_CALLS` and the `RemoteAgent` budget, `_chat_input` resume, `remember_turn` pause guard, `needs_human`/`pending` handling.
- Inner node names: `pick`, `speak`, `judge`, `pause`, `synthesize`.
- Settings: `TEAM_MAX_ROUNDS=4` replaces `TEAM_MAX_TURNS` (code, `.env.example`, tests).
- No source is preferred by kind, in code or prompt.

## Review Focus

1. **Parallel tokens:** two agents streaming at once must never mix in one UI block. Pinned by `test_round_events_carry_the_agent` (Task 4).
2. **Scoped tools on the server:** research in a team round must not see `run_sql`/`search_documents`; outside team mode nothing is hidden. Pinned by `test_tools_outside_sources_are_hidden` and `test_no_sources_hides_nothing` (Task 2).
3. **One participant, no conflict:** no synthesis call; its answer is the reply, badge = that agent. Pinned by `test_single_participant_answer_stands` (Task 3).
4. **A conflict whose `fixable_by` names an agent outside it, or nobody:** no conflict round for it; the write-up shows it. Pinned by `test_unfixable_conflict_goes_to_write_up` (Task 3).
5. **Non-team chats:** same answers and events as before except tool events move from inline to the popup. Pinned by the existing orchestrator/router/fallback/chat-event tests passing unchanged.

---

### Task 1: Spike: per-agent streams under `Send`, and the language drift

**Mode:** diagnose (no product code committed).

**Files:**
- Create (scratchpad only): a throwaway script; the findings go in the report.

- [ ] **Step 1:** Build a two-node test graph (in a scratchpad script, with the `tests/scripted.py` models and `local_agents()`): a node that returns `[Send("speak", {"route": r, ...}) for r in ("sql", "research")]`, and a `speak` node that invokes `agents[route]`. Stream it with `stream_mode=["messages","updates","custom"], subgraphs=True` and record, per chunk, the namespace and `metadata` (`langgraph_node`, `langgraph_checkpoint_ns`, task ids). Answer: can every `messages`/`updates` chunk from inside a `speak` task be mapped to its `route` with no guessing? Try both: the `speak` node writing a custom `{"type":"speaker","agent":route}` event carrying its own `checkpoint_ns` from `config`, and reading `route` from the task's input via metadata. Do the same with one `RemoteAgent` over the real compose `sql` server if the stack is up.
- [ ] **Step 2:** Trace the language drift: replay `team_compound` and `team_ambiguous` messages (from `scripts/bench_chat.py`) through the current team graph with the bench's admin context, logging every prompt's language-related parts (system language rule, `MEMORIES_SECTION`, `RECENT_CHATS_SECTION`, the hidden notes) and which call first produced Vietnamese. Real models: at most these two messages, once.
- [ ] **Step 3: Report:** the mapping mechanism that works (with chunk samples), or that none does (then Task 4 uses the spec's fallback); the drift's cause with the prompt evidence.

### Task 2: Source scoping and `joins`

**Files:**
- Modify: `app/core/memory.py` (`AgentContext.sources: tuple[str, ...] = ()`)
- Modify: `app/agents/factory.py` (`scope_tools` middleware, always added in `build_agent`)
- Modify: tool definitions: `app/agents/sql/tools.py` (`run_sql`), `app/agents/chart/tools.py` (`chart_sql`), `app/agents/docs/tools.py` (`search_documents`), research MCP tools (tag where `_mcp_tools` is filled in `app/agents/research/agent.py`, and the orchestrator's web tools)
- Modify: `app/agents/base.py` (`AgentSpec.joins: Literal["mentioned", "with_others"] = "mentioned"`), `app/agents/research/agent.py` SPEC (`joins="with_others"`)
- Test: `tests/test_factory.py` (or the file that tests `build_agent`; create if none), `tests/test_registry.py`

**Interfaces:**
- Produces:
  - `TOOL_SOURCE = "source"`: metadata key; a tool's tag is `tool.metadata[TOOL_SOURCE]` (`"market_db"`, `"user_files"`, `"web"`); untagged tools are never hidden
  - `scope_tools` (`wrap_model_call` middleware): when `request.runtime.context.sources` is non-empty, removes tools whose tag is set and not in it; otherwise passes through
  - `AgentContext.sources` travels to the agent servers inside `asdict(context)` (already how context is sent)
- [ ] **Step 1: Write the failing tests:** `test_tools_outside_sources_are_hidden` (an agent built with a tagged `market_db` tool, a tagged `web` tool and an untagged tool; context `sources=("web",)`; the model's bound tools are the web and untagged ones; use the scripted model's recorded `tools`), `test_no_sources_hides_nothing`, `test_every_data_tool_has_a_source` (each tool above carries its tag), `test_research_joins_with_others`.
- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement**
- [ ] **Step 4:** `uv run pytest -q -rs`, expect PASS
- [ ] **Step 5: Commit** `feat: tools carry a source; team requests limit them`

### Task 2b: Testing rule (user request, 2026-10-25)

**Files:**
- Create: `.claude/rules/testing.md` (loaded like the other `.claude/rules/*.md`)

The rule, in the user's words: "Never create a test, or adjust a test so that it can be passed." Meaning: a test states the behaviour the spec or the user asked for, written before or independent of the code; never write a test shaped around what the code happens to do, and never weaken, loosen, skip or delete a failing test (or its assertions, fixtures or inputs) to make it pass. When a test fails, fix the code; if the test itself contradicts the spec, stop and report it instead of changing it. Write it with `writing-llm-document`.

- [ ] **Step 1:** write the rule file
- [ ] **Step 2:** commit `docs: testing rule` (explicit path only)

From this task on, every implementer and verifier brief quotes the rule, and verifiers check test diffs against it.

### Task 3: Round-table graph

**Files:**
- Modify (rewrite internals): `app/orchestration/team.py`
- Modify: `app/orchestration/prompts.py` (`ROUND_MESSAGE`, `CONFLICT_MESSAGE`, `BOARD_PROMPT` replacing `JUDGE_PROMPT`, `TEAM_MESSAGE`; remove `CRITIQUE_MESSAGE`, `REFINE_MESSAGE` and constants only they used)
- Modify: `app/core/config.py`, `.env.example` (`team_max_rounds: int = 4`, drop `team_max_turns`)
- Test: `tests/test_team.py` (replace the relay graph tests; keep the pick/source/budget/resume/serialize tests that still apply)

**Interfaces:**
- Consumes: Task 2 (`AgentContext.sources`, `AgentSpec.joins`), Task 1's mapping finding (the `speak` node writes whatever Task 4 needs, e.g. a `speaker` custom event).
- Produces:
  - `def participants(available: list[tuple[AgentSpec, RouteInfo]], message: str) -> list[AgentSpec]`: mentioned agents, `overrides` applied, plus `joins == "with_others"` agents when any other joined; registry order
  - `class Board(BaseModel)`: `agreements: list[str]`, `differences: list[Difference]`, `conflicts: list[Conflict]`, `question: str = ""`; `Difference(routes: list[str], explanation: str)`; `Conflict(routes: list[str], claim: str, fixable_by: list[str], issue: str)`; validator drops `fixable_by` entries not in `routes` and routes not among the round's speakers
  - `async def ask_board(messages, answers: list[dict], config) -> Board`: one `with_structured_output(Board, method="function_calling")` call with `BOARD_PROMPT`
  - Team state `team = {participants: [route], round: int, rounds: [{round, kind, answers: [{agent, text, tools}]}], boards: [board dicts], forced: bool, artifacts: dict}`
  - `speak` receives `{"route", "kind", "note"}` via `Send`, runs the agent with `AgentContext.sources = spec.sources` and the hidden note, appends its answer (and its tool calls/results as `{name, args, result}` from the run's messages, result ≤ `TOOL_RESULT_PREVIEW`) to the current round via a reducer that merges parallel writes; a failing agent records `"failed: <ExcName>"` as its text and marks `forced`
  - `judge` writes `round`'s `board` event, then: `question` → existing `needs_human` path; conflicts with non-empty `fixable_by` and `round < team_max_rounds` → `Send("speak", …)` to each route in those conflicts' `routes` (deduplicated) with `CONFLICT_MESSAGE`, emitting a `round` event (`kind="conflict"`); else → `synthesize` (`forced` when conflicts with fixers remained at the cap)
  - `synthesize`: one participant, no conflicts, not forced → that agent's text becomes the reply (`AIMessage`, `name` = the agent's message name) with its artifacts; else orchestrator with `team_note = TEAM_MESSAGE...` as now; both carry `additional_kwargs["team"]`; emits `{"type":"synthesize"}` first
  - Nobody picked → the node runs the orchestrator as in normal mode (no team transcript)
  - Events written: `round` at each round start, `board` after each judge, `synthesize`, `needs_human`
- `external_db` tag (user decision, 2026-10-25): `tag_mcp` in `app/agents/research/agent.py` tags `external_db_*` MCP tools `external_db`, and research's SPEC `sources` becomes `frozenset({"web", "external_db"})`. Nobody uses an external database today (only the stock database exists); this keeps the subset rule correct if one is configured later. Test: `test_external_db_tools_are_tagged` (with a fake `external_db_query` tool).
- Language (user decision, 2026-10-25): every reply follows the language of the message it answers; a stored language preference never overrides it. Add one line to the memories section (`MEMORIES_SECTION` / `MEMORY_RULE` in `app/core/prompts.py`) saying so. Test: `test_memories_section_keeps_message_language`.
- Prompts (`writing-llm-document`): `ROUND_MESSAGE` and `CONFLICT_MESSAGE` per the spec, each naming `{language}`; `BOARD_PROMPT` keeps the four conflict rules and "never right by kind"; `TEAM_MESSAGE` fields `{answers}`, `{differences}`, `{conflicts}`, `{language}` (`none` when empty).

- [ ] **Step 1: Write the failing tests:** `test_pick_mentioned_with_others_and_overrides`, `test_nobody_mentioned_orchestrator_answers_alone`, `test_round_one_runs_participants_in_parallel` (both agents' fakes record start/end; overlap), `test_single_participant_answer_stands` (no orchestrator call; reply named after the agent), `test_agreement_writes_up_once`, `test_fixable_conflict_runs_a_conflict_round_for_its_routes_only`, `test_unfixable_conflict_goes_to_write_up`, `test_cap_forces_write_up_after_four_conflict_rounds`, `test_failed_speaker_forces_write_up`, `test_needs_human_pauses_and_resume_continues` (adapted), `test_second_turn_starts_fresh`, `test_only_the_answer_joins_the_thread`, `test_transcript_holds_rounds_tools_and_boards`, `test_prompts_format`.
- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement**
- [ ] **Step 4:** `uv run pytest -q -rs`, expect PASS (existing non-team suites unchanged)
- [ ] **Step 5: Commit** `feat: team round table replaces the relay`

### Task 1 findings (2026-10-25, spike scripts in the session scratchpad)

- **Mapping works** (corrected 2026-10-25: in the real assistant graph `speak` sits under the `team` node, so the speaker `ns` is `team:<id>|speak:<tid>` and chunks arrive at `("team:<id>", "speak:<tid>", ...)`; key by the `speak:` segment and find it anywhere in the namespace; the spike's toy graph had no parent node): every chunk from a `speak` task carries a `"speak:<task_id>"` segment, unique per parallel task. `speak` writes `{"type": "speaker", "agent": route, "ns": config["configurable"]["checkpoint_ns"]}` before invoking the agent; it reaches the stream at namespace `()` before any inner chunk. Local and real remote sql: 0 unmapped chunks. Match on `namespace[0]` only (inner depth varies: `(speak:T,)`, `(speak:T, gather:G)`, `(speak:T, run:R)`). The message-metadata namespace is useless for remote agents (it is the server's own).
- **So in Task 4:** the `ns -> agent` dict lives in `_events_from` (stateful); strip the `speak:` segment before the `tools` / `SILENT_STEPS` / `_step` filters (`_step` would otherwise read `speak` as a route); remote `messages` chunks are dicts (`_message`/`as_message` already handle them; test it).
- **Language drift cause:** the admin's memory fact "User prefers language Vietnamese" plus the first-draft prompts naming no language; the write-up (which names it) answered in English, but the draft's tokens had already streamed into the answer bubble. Round tokens going to the Activity popup (Task 5) and `ROUND_MESSAGE` naming `{language}` (Task 3) address both; whether a stored language preference should override the message language is a product decision for the user.

### Task 4: Round events

**Files:**
- Modify: `app/api/routes_chat.py` (`_to_events`, `_events_from`, `TEAM_EVENTS`), `app/api/routes_conversations.py` (`_serialize`: badge for a single-participant reply via `MESSAGE_ROUTES`)
- Test: `tests/test_chat_events.py`

**Interfaces:**
- Consumes: Task 1's mapping, Task 3's events.
- Produces: inside a `speak` task, `token`/`tool_call`/`tool_result` gain `"agent": route`; `TEAM_EVENTS = {"round", "board", "synthesize", "needs_human"}` (plus any internal mapping event, consumed and not forwarded); events outside rounds unchanged.
- [ ] **Step 1: Write the failing tests:** `test_round_events_carry_the_agent` (two interleaved speak tasks' chunks → each event tagged with the right agent), `test_events_outside_rounds_are_unchanged`, `test_team_custom_events_pass_through` (new types), `test_relay_event_types_are_gone`.
- [ ] **Step 2–4:** fail, implement, `uv run pytest -q -rs` pass
- [ ] **Step 5: Commit** `feat: round events name the speaking agent`

### Task 5: UI: status line and Activity popup

**Files:**
- Modify: `app/static/index.html`, `app/static/app.js`, `app/static/i18n.js`, `app/static/style.css`

**Interfaces:**
- Consumes: Task 4 events; `_serialize` (`team` transcript, tool messages).
- Produces (spec "UI: minimal chat, activity popup"):
  - per reply: a status line (agent badges + spinner while running; summary when done), the answer, an "Activity" button with a step count; no inline tool lines in any chat
  - one `<dialog id="activityDialog">` filled per reply: team mode grouped by round → agent (answer text, tool calls with args, results) → board; normal mode the reply's tool calls/results in order; the pause question
  - live events are kept per reply in memory so the popup works while streaming; reload builds the same from `message.team` or the tool messages
  - i18n EN/VI for every new string; remove now-unused relay strings
  - keep: "New chat" disabled while streaming, "connection lost", charts, `linkCitations`, theming tokens, `localGet`/`localSet`
- [ ] **Step 1: Implement**
- [ ] **Step 2:** `node --check app/static/app.js`; `uv run pytest -q -rs`
- [ ] **Step 3: Commit** `feat: status line and activity popup`

### Task 6: Evaluation

**Files:**
- Modify: `scripts/bench_chat.py` (`team_conflict` scenario and its setup)
- Create: `scripts/bench_data/team_conflict.md` or similar small fixture stating a wrong market cap for one ticker (generic wording, clearly a test file), uploaded for the bench user before the scenario and deleted after

**Steps:**
- [ ] **Step 1:** add the scenario (the message names the file and the ticker), `uv run pytest -q -rs`, commit `test: team conflict scenario`
- [ ] **Step 2:** rebuild the agent servers (`docker compose up -d --build`, through PowerShell + WSL), confirm healthy
- [ ] **Step 3:** `uv run python -m scripts.bench_chat --only team,team_research,team_compound,team_vi,team_ambiguous,team_conflict --runs 1 --out <scratchpad>\bench_round_table.json` (spends free quota; once), with the custom events logged per scenario (reuse the scratchpad wrapper `bench_events.py`)
- [ ] **Step 4:** live UI pass (`run` skill, temp browser profile only): a team stock question, a Vietnamese one, the conflict one; status line, popup live and after reload, no chat clutter
- [ ] **Step 5: Report** per scenario: participants, rounds, conflicts, calls, first token, total, language, errors; compare with the relay (7 calls / 38 s; 126–156 s with research); list problems by severity; fix nothing
