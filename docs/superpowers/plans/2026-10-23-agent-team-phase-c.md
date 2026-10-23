# Agent Team, Phase C (debate loop) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. In this repo the foreman loop runs it (implementer per task, verifier after each).

**Goal:** A per-conversation "team mode" in which one agent drafts, a judge-chosen peer critiques, the author refines (at most 4 internal turns), and the orchestrator writes one reply; the run pauses for the user when the judge needs a clarification.

**Architecture:** A `team` subgraph (`app/orchestration/team.py`) on `AssistantState`, entered when `AgentContext.agent == "team"`. Its nodes call the existing `RemoteAgent`s and the orchestrator node function; one structured judge call decides each round. Team events go out on LangGraph's `custom` stream mode; the pause is LangGraph `interrupt()` resumed by `Command(resume=...)`.

**Tech Stack:** Python 3.12, LangGraph 1.2.14 (`StateSnapshot.interrupts`, `get_stream_writer`, `interrupt`, `Command`), LangChain 1.4.4, Pydantic, FastAPI SSE, plain JS UI.

**Spec:** `docs/superpowers/specs/2026-10-23-agent-team-design.md` (phase C sections). Phase A gets its own plan after the spike is tried.

## Global Constraints

- Branch `0.5.0/agent-team`; release 0.5.0. Don't commit the user's staged `.claude/settings.json`.
- No new agent roles. No hardcoded agent pairs: critics come from `AgentSpec.review` only.
- Every decision is a structured tool-call field (`Verdict`), never an optional model step or magic string (traps `weak-models-optional-steps`, `extra-model-calls`).
- Inner node names: `delegate`, `judge`, `critique`, `refine`, `pause`, `synthesize`. Never a route name, `tools`, `orchestrator` or `run` (trap `workflow-node-names`).
- Only the synthesized answer (and a pause question plus the user's reply) join `messages`; the transcript goes on the answer's `additional_kwargs["team"]`.
- Settings: `TEAM_MAX_TURNS=4`, `TEAM_MAX_MESSAGE_MODEL_CALLS=30`.
- LLM-read text (prompts, `review` lines) is written with the `writing-llm-document` skill; prompt constants contain no `{}` other than real `.format` fields (trap `prompt-format-braces`), reply-language rules follow trap `reply-language`.
- Run `scripts\dev_db.ps1` before tests; skipped DB tests are not a pass.

## Review Focus

1. **Second message in a finished team conversation:** the old `team` state must not leak (a stale draft or note in the new turn). Pinned by `test_second_turn_starts_a_fresh_debate` (Task 3).
2. **Reply while paused, then a reload:** the question and the reply both show, in order, and the debate continues. Pinned by `test_needs_human_pauses_and_resume_continues` (Task 3) and `test_serialize_exposes_team_transcript` (Task 5).
3. **The judge names the author or an unknown route as critic, or no reviewer is available** (user without files, author is research): there must be no crash or self-review. Pinned by the `coerce` tests (Task 2) and `test_no_reviewers_approves_without_critique` (Task 3).
4. **A specialist fails mid-debate** (remote error or budget reached): the user still gets a reply built from the latest draft, not a 500. Pinned by `test_failed_critic_still_synthesizes` (Task 3).
5. **A non-team conversation is untouched:** `auto` chats never enter `team`, and their event stream is unchanged. Pinned by the existing `tests/test_orchestrator.py` and `tests/test_chat_events.py` passing unchanged (every task's verification step).

---

### Task 1: Settings and `AgentSpec.review`

**Files:**
- Modify: `app/core/config.py` (Settings), `.env.example`
- Modify: `app/agents/base.py` (`AgentSpec`)
- Modify: `app/agents/{docs,sql,research,chart}/agent.py` (`SPEC`), and their `prompts.py` if the review line lives there as `REVIEW`, next to `TOOL_DESCRIPTION`
- Test: `tests/test_registry.py`

**Interfaces:**
- Produces: `Settings.team_max_turns: int = 4`, `Settings.team_max_message_model_calls: int = 30`; `AgentSpec.review: str = ""` (empty = never a critic); `TEAM_MODE = "team"` in `app/core/memory.py` next to `AgentContext` (the `agent` value for team mode; core can't import orchestration).

- [ ] **Step 1: Write the failing tests** in `tests/test_registry.py`:

```python
def test_every_agent_can_review():
    assert all(spec.review for spec in AGENTS)

def test_team_settings_defaults():
    s = Settings(_env_file=None, openrouter_api_key="x")  # follow the file's existing Settings construction
    assert (s.team_max_turns, s.team_max_message_model_calls) == (4, 30)
```

- [ ] **Step 2: Run them, expect FAIL** (`AttributeError: review`): `uv run pytest tests/test_registry.py -v`
- [ ] **Step 3: Add the field and settings; write one `review` line per agent** (load `writing-llm-document`): research checks facts and figures against the web, Wikipedia and arXiv; sql checks stock and company figures against the market database; docs checks claims about the user's files; chart checks the query and numbers behind a chart. Document both settings in `.env.example`.
- [ ] **Step 4: Run `uv run pytest tests/test_registry.py tests/test_router.py -v`, expect PASS**
- [ ] **Step 5: Commit** `feat: AgentSpec.review and team settings`

### Task 2: Judge, verdict and critic choice (pure parts of `team.py`)

**Files:**
- Create: `app/orchestration/team.py` (this task: the pure helpers only)
- Modify: `app/orchestration/prompts.py` (`JUDGE_PROMPT`, `CRITIQUE_MESSAGE`, `REFINE_MESSAGE`, `TEAM_MESSAGE`)
- Test: `tests/test_team.py`

**Interfaces:**
- Consumes: `AgentSpec.review`, `available_agents` (`app/orchestration/router.py`), `get_chat_model` (`app/core/llm.py`).
- Produces:
  - `TEAM = TEAM_MODE` (route, graph node, `AgentContext.agent` value, hidden-message name)
  - `class Verdict(BaseModel)`: `action: Literal["approve","critique","revise","needs_human"]`, `critic: str | None = None`, `issues: list[str]` (≤ 5, truncate in a validator), `question: str = ""`
  - `def allowed_critics(available: list[tuple[AgentSpec, RouteInfo]], author: str) -> list[AgentSpec]`: specs with `review`, available, `route != author`, in registry order
  - `def coerce(verdict: Verdict, allowed: list[AgentSpec]) -> Verdict`: the spec's verdict rules (below)
  - `async def ask_judge(messages: list[AnyMessage], team: dict, allowed: list[AgentSpec], config: RunnableConfig) -> Verdict`: one `get_chat_model(settings.openrouter_orchestrator_model or None).with_structured_output(verdict_model(allowed), method="function_calling")` call; system `JUDGE_PROMPT` (lists the allowed critics' `route: review` lines, the turn count and cap), human message = the latest user message + draft + critiques so far. Returns `coerce(...)`. The call passes `config` so the usage counter sees it.
  - `def verdict_model(allowed: list[AgentSpec]) -> type[Verdict]`: `Verdict` subclass whose `critic` is `Literal[<allowed routes>] | None` (`pydantic.create_model`); plain `Verdict` when `allowed` is empty.

`coerce` rules:

| verdict | allowed empty | allowed non-empty |
|---|---|---|
| `critique` | → `approve` | critic not in allowed → `allowed[0].route` |
| `revise` | critic → `None` | critic not in allowed → `allowed[0].route` |
| `approve` / `needs_human` | unchanged, critic → `None` | unchanged, critic → `None` |
| `needs_human` with empty `question` | → `approve` | → `approve` |

- [ ] **Step 1: Write the failing tests** in `tests/test_team.py`: `test_allowed_critics_skip_author_unavailable_and_reviewless`, `test_coerce_replaces_unknown_or_self_critic`, `test_coerce_without_reviewers` (critique → approve; revise keeps action, critic None), `test_coerce_needs_human_without_question_approves`, `test_verdict_model_limits_critic_to_allowed` (`pydantic.ValidationError` for a route not in the list), `test_issues_capped_at_five`. Build `AgentSpec`s with `dataclasses.replace(SPEC, ...)` from the registry.
- [ ] **Step 2: Run `uv run pytest tests/test_team.py -v`, expect FAIL** (module missing)
- [ ] **Step 3: Implement the helpers and the four prompt constants** (load `writing-llm-document`). `CRITIQUE_MESSAGE`: check this draft with your tools; list concrete problems with evidence, or say it holds; reply in the user's language. `REFINE_MESSAGE`: fix these issues in your answer; return the whole corrected answer. `TEAM_MESSAGE`: the team's final draft, critiques and open issues; write the reply from them, call no tools; if `forced`, say what stayed unresolved. `.format` fields only: `{draft}`, `{critiques}`, `{issues}`, `{language}`.
- [ ] **Step 4: Run `uv run pytest tests/test_team.py -v`, expect PASS**
- [ ] **Step 5: Commit** `feat: team judge verdict and critic choice`

### Task 3: The `team` subgraph and its wiring

**Files:**
- Modify: `app/orchestration/team.py` (`build_team`)
- Modify: `app/orchestration/router.py` (`AssistantState`, `build_router`)
- Modify: `app/orchestration/orchestrator.py` (`orchestrator` node reads `team_note`)
- Modify: `app/orchestration/graph.py` (`build_assistant`)
- Test: `tests/test_team.py`

**Interfaces:**
- Consumes: Task 2 helpers; `agents: Mapping[str, Runnable | RemoteAgent]` (both have `ainvoke(input, config, *, context)`); `spec.result(state)`; `collect(messages)` (`app/agents/artifacts.py`); `obvious_route`, `available_agents`; the orchestrator node function returned by `build_orchestrator`.
- Produces:
  - `AssistantState.team: NotRequired[dict]` (the spec's team state plus `artifacts: dict`), `AssistantState.team_note: NotRequired[str]` (never returned by a node; only put into the state dict a team node passes to the orchestrator function)
  - `def build_team(specs, agents, orchestrator) -> CompiledStateGraph`, compiled with `name=TEAM`, on `AssistantState`
  - Router: `context.agent == TEAM` → `{"route": TEAM, "direct": False}`
  - Orchestrator node: `if note := state.get("team_note"): hidden.append(HumanMessage(note, name=TEAM))`
  - Stream events (via `get_stream_writer()`), exactly the spec's: `{"type":"team","phase","agent","turn","max_turns"}` at the start of `delegate`/`critique`/`refine`/`synthesize`; `{"type":"verdict","action","critic","issues","turn","forced"}` from `judge` (and for a forced stop); `{"type":"needs_human","question"}` from `judge` before routing to `pause`.

Node behaviour (each node returns only a `team` update unless noted):

- `delegate`: reset `team` to a fresh dict (turns 0, empty lists). If `obvious_route` gives a route whose spec `answers_directly`, invoke that agent on the thread; `author` = route, `draft` = `spec.result`. Otherwise call the orchestrator function with the state; if its `Command.goto` is a route, invoke that agent (author = route), else author = `orchestrator`, draft = the answer text. Keep `collect(...)` of the run in `team.artifacts`.
- `judge`: `allowed_critics(available, author)` → `ask_judge` → write the `verdict` event. Route: `approve` → `synthesize`; `needs_human` → append `AIMessage(question, name=ORCHESTRATOR)` to `messages`, write `needs_human`, go to `pause`; `critique` → `critique`; `revise` → `refine` if `turns < team_max_turns`, else `synthesize` with `forced=True`.
- `critique`: invoke the critic on `[*messages, HumanMessage(CRITIQUE_MESSAGE..., name=TEAM)]`; append `{critic, text: spec.result(state), turn}`; `turns += 1`; go to `judge`.
- `refine`: specialist author: invoke it with `REFINE_MESSAGE`; orchestrator author: call the orchestrator function with `team_note=REFINE_MESSAGE...` (a handoff is not followed; the draft stays). `turns += 1`. Next: `critique` if critic set and `turns < max`, else `synthesize` (`forced` when `turns >= max`).
- `pause`: `reply = interrupt({"question": team["question"]})`; return `messages=[HumanMessage(reply)]`; go to `judge`.
- `synthesize`: call the orchestrator function with `team_note=TEAM_MESSAGE...`; take its answer (if it hands off, use the draft as the answer, named `ORCHESTRATOR`); merge `team.artifacts` into its `additional_kwargs` (list values concatenated per key) and set `additional_kwargs["team"] = {"author", "draft", "critiques", "verdicts", "turns"}`; return it in `messages`; END.
- A specialist call that raises (except `openai.RateLimitError`/`AuthenticationError`, which propagate as today) goes to `synthesize`: add `{"critic": route, "text": "failed: <ExcName>", turn}` to critiques; mark forced.
- Graph: `graph.add_node(TEAM, build_team(AGENTS, agents, orchestrator))`, `graph.add_edge(TEAM, END)`, `TEAM` in the router's destinations.

- [ ] **Step 1: Write the failing tests** in `tests/test_team.py`, using the `_assistant` pattern from `tests/test_orchestrator.py` (scripted `per_agent` models, `local_agents()`), `monkeypatch.setattr(team, "ask_judge", <async fake popping scripted Verdicts>)`, and a context with `agent="team"`; stream with `stream_mode=["messages","updates","custom"]`:
  - `test_router_sends_team_mode_to_team`
  - `test_approve_at_first_judge_skips_critique`: one `team` delegate event, one verdict, then synthesize; the thread gains exactly one AIMessage with `additional_kwargs["team"]["critiques"] == []`
  - `test_one_round_then_approve`: phases delegate, critique (research), refine, critique, synthesize; turns 3
  - `test_cap_forces_synthesis`: the judge always says revise → phases end critique, refine, critique, refine, synthesize; last verdict `forced` true; turns 4
  - `test_needs_human_pauses_and_resume_continues`: the first stream ends with `needs_human`; `aget_state(...).interrupts` is non-empty; streaming `Command(resume="2024")` continues to synthesize; thread order: user, question AI, "2024" human, answer
  - `test_no_reviewers_approves_without_critique`: a judge returning `critique` with every agent's `review` blanked ends without a critique phase
  - `test_failed_critic_still_synthesizes`: a critic agent whose `ainvoke` raises `RuntimeError` → a reply still comes, forced
  - `test_second_turn_starts_a_fresh_debate`: the second user message's transcript holds only its own critiques
  - `test_only_the_answer_joins_the_thread`: no draft or critique text in `messages`
- [ ] **Step 2: Run `uv run pytest tests/test_team.py -v`, expect FAIL**
- [ ] **Step 3: Implement `build_team` and the wiring as above**
- [ ] **Step 4: Run `uv run pytest tests/test_team.py tests/test_orchestrator.py tests/test_router.py tests/test_fallback.py -v`, expect PASS**
- [ ] **Step 5: Commit** `feat: team debate subgraph`

### Task 4: Team call budget

**Files:**
- Modify: `app/agents/remote.py` (`RemoteAgent.ainvoke`)
- Test: `tests/test_remote.py`

**Interfaces:**
- Produces: the budget is `settings.team_max_message_model_calls` when `context.agent == "team"`, else `settings.max_message_model_calls`; the `CallBudgetExceeded` message names the setting that applied.

- [ ] **Step 1: Write the failing test** `test_team_mode_uses_the_team_budget`: with a counter at `max_message_model_calls`, a team context still calls the agent; at `team_max_message_model_calls` it raises `CallBudgetExceeded` mentioning `TEAM_MAX_MESSAGE_MODEL_CALLS`. Follow the existing budget test in the file for setup.
- [ ] **Step 2: Run `uv run pytest tests/test_remote.py -v`, expect FAIL**
- [ ] **Step 3: Implement** with `TEAM_MODE` from `app/core/memory.py` (importing `app.orchestration.team` here would invert the layers)
- [ ] **Step 4: Run `uv run pytest tests/test_remote.py -v`, expect PASS**
- [ ] **Step 5: Commit** `feat: team messages use TEAM_MAX_MESSAGE_MODEL_CALLS`

### Task 5: Chat route, events and reload

**Files:**
- Modify: `app/api/routes_chat.py` (`chat`, `_to_events`, `remember_turn` guard)
- Modify: `app/api/routes_conversations.py` (`_serialize`)
- Modify: `scripts/bench_chat.py` (stream mode list and one team scenario)
- Test: `tests/test_chat_events.py`, `tests/test_team.py`

**Interfaces:**
- Consumes: `TEAM_MODE`; `StateSnapshot.interrupts`.
- Produces:
  - `chat()`: `kind == TEAM_MODE` accepted (else unchanged 404 rule); `stream_mode=["messages","updates","custom"]`; input is `Command(resume=body.message)` when `kind == TEAM_MODE` and `(await assistant.aget_state(config)).interrupts`, else today's messages dict. Context `agent=kind` as today.
  - `async def _chat_input(assistant, config, kind: str, message: str) -> dict | Command`: the choice above, testable alone.
  - `_to_events("custom", chunk, ns)`: returns `[chunk]` when `chunk` is a dict whose `type` is `team`, `verdict` or `needs_human`, else `[]`.
  - `remember_turn` and `save_turn` are skipped when the turn ended paused (state has interrupts after the stream).
  - `_serialize`: assistant messages gain `"team": message.additional_kwargs.get("team")`.
  - `bench_chat`: scenario `team` (a sql-style question in team mode) with context `agent="team"`.

- [ ] **Step 1: Write the failing tests**: `test_custom_team_events_pass_through` and `test_other_custom_chunks_are_dropped` (`tests/test_chat_events.py`); `test_chat_input_resumes_a_paused_team_turn` and `test_chat_input_is_a_message_otherwise` (`tests/test_team.py`, using a graph from Task 3 paused by a `needs_human` verdict); `test_serialize_exposes_team_transcript`.
- [ ] **Step 2: Run `uv run pytest tests/test_chat_events.py tests/test_team.py -v`, expect FAIL**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run `uv run pytest -q`, expect PASS** (whole suite, DB up)
- [ ] **Step 5: Commit** `feat: team mode in the chat route and events`

### Task 6: UI

**Files:**
- Modify: `app/static/index.html`, `app/static/app.js`, `app/static/i18n.js`, `app/static/style.css`

**Interfaces:**
- Consumes: SSE `team`/`verdict`/`needs_human`; `GET /conversations` rows' `kind`; `_serialize`'s `team`.
- Produces (behaviour, from the spec's UI section):
  - "Team mode" switch next to "New chat"; it chooses `POST /api/chat/team` vs `/api/chat/auto` for a new conversation; an opened conversation uses its own `kind`. Remember the switch in `localStorage` (`team_mode`, wrapped in `localGet`/`localSet`).
  - Sidebar icon for `kind == "team"`.
  - Per reply, a `<details>` "Team discussion · N rounds" above the answer; a `team` event opens a new entry (agent badge + phase label); tokens go to the latest entry until phase `synthesize`, whose tokens go to the normal bubble; `verdict` adds a line (action, critic, issues; "forced stop" when `forced`). Open while streaming, closed at `synthesize`.
  - `needs_human`: the question as an assistant message with a "waiting for you" mark; the input placeholder switches to the answer prompt until the next send.
  - Reload: rebuild the section from `message.team`.
  - i18n keys, EN and VI: `teamMode`, `teamDiscussion` (with round count), `phaseDelegate`, `phaseCritique`, `phaseRefine`, `phaseSynthesize`, `verdictApprove`, `verdictRevise`, `verdictCritique`, `verdictNeedsHuman`, `forcedStop`, `waitingForYou`, `answerPlaceholder`.

- [ ] **Step 1: Implement**
- [ ] **Step 2: Check it in the browser (the `run` skill):** start the stack (`scripts\dev_db.ps1`, compose up, uvicorn); with team mode on, ask a stock question: the discussion section fills phase by phase and then collapses, the answer shows below, and a reload shows the same; switch to VI and check the labels; send a question the judge must clarify (e.g. "compare their revenue" with no company named) and check the pause and the resume. Take screenshots for the report.
- [ ] **Step 3: Commit** `feat: team mode UI`

### Task 7: Docs and measurement

**Files:**
- Modify: `ARCHITECTURE.md`, `CLAUDE.md`, `.claude/rules/changelog.md`

- [ ] **Step 1: Update docs** (CLAUDE.md and `.claude/rules` with `writing-llm-document`): CLAUDE.md gets a "Team mode" paragraph (subgraph nodes, `AgentSpec.review`, the judge, `team_note`, custom events, interrupt and resume, the two settings) and the new `AgentSpec` field in the registry paragraph; ARCHITECTURE.md gets the team flow and events; changelog rule: `0.5.0`: agent team, `0.6.0`: image agent, later ones `0.7.0`.
- [ ] **Step 2: Measure:** `uv run python -m scripts.bench_chat --scenario team` (check the flag name in the script), record calls and latency in the report (it spends free quota; ask before running if the quota is low).
- [ ] **Step 3: Commit** `docs: team mode`
