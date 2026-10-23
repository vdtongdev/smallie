# Agent Team, Phase C Fixes Implementation Plan

> **For agentic workers:** run with the foreman loop (implementer per task, verifier after each). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Critics come from a different data source than the author, a debate starts only on a real conflict, no source is trusted by kind, and a critic's output stays in the discussion; then hunt for errors across more team scenarios.

**Architecture:** `AgentSpec.sources` tags filter the judge's allowed critics; the orchestrator records which agents it asked; `Verdict.explanation` carries "why the sources differ" into the write-up; the UI routes tool events of non-synthesize phases into the current discussion entry.

**Tech Stack:** as the phase C plan (`docs/superpowers/plans/2026-10-23-agent-team-phase-c.md`).

**Spec:** `docs/superpowers/specs/2026-10-23-agent-team-design.md`, section "Independent critics and conflicts".

## Global Constraints

- Everything in the phase C plan's Global Constraints still holds (branch `0.5.0/agent-team`, never commit `.claude/settings.json`, node names, `writing-llm-document` for prompts, traps `prompt-format-braces`, `reply-language`, `copyable-prompt-examples`, `weak-models-optional-steps`, `extra-model-calls`).
- No source is preferred because of its kind, in code or prompt.
- Source tags: docs `frozenset({"user_files"})`, sql `frozenset({"market_db"})`, chart `frozenset({"market_db"})`, research `frozenset({"web"})`.

## Review Focus

1. **Orchestrator draft built on sql:** chart must not be offered as its critic. Pinned by `test_orchestrator_draft_excludes_critics_of_asked_sources` (Task 1).
2. **Orchestrator draft from the model alone** (no agents asked): every reviewer stays allowed. Pinned by `test_model_only_draft_allows_every_critic` (Task 1).
3. **Compatible difference:** an `approve` with `explanation` must put the explanation into the write-up note and the transcript, with no extra round. Pinned by `test_approve_with_explanation_reaches_the_write_up` (Task 2).
4. **Critic chart leak:** no chart from a critique phase may end up on the final answer or render as an answer card. Pinned by `test_critic_artifacts_stay_off_the_answer` (Task 1) and the UI check (Task 3).
5. **Non-team chats unchanged:** the `asked` key on orchestrator answers changes no prompt (`langchain_openai` drops unknown `additional_kwargs`) and no existing test. Pinned by the existing suites passing unchanged.

---

### Task 1: Independent critics

**Files:**
- Modify: `app/agents/base.py` (`AgentSpec.sources`), the four `app/agents/*/agent.py` SPECs
- Modify: `app/orchestration/orchestrator.py` (answer gets `additional_kwargs["asked"]`)
- Modify: `app/orchestration/team.py` (`allowed_critics`, `delegate`, author sources in `team` state)
- Test: `tests/test_team.py`, `tests/test_registry.py`

**Interfaces:**
- Produces:
  - `AgentSpec.sources: frozenset[str] = frozenset()`
  - orchestrator answer: `additional_kwargs["asked"] = [<route>, ...]`, the routes of its `ask_<route>` tool calls this turn, in call order, no duplicates; absent when none
  - `team["author_sources"]: list[str]` set by `delegate` (and kept by `refine`): the author spec's sources, or for the orchestrator the union of the sources of the routes in `asked`
  - `def allowed_critics(available, author: str, author_sources: frozenset[str]) -> list[AgentSpec]`: as before, and also drops a spec whose `sources` is non-empty and a subset of `author_sources`
- Also confirm (and pin) that only the author's artifacts reach the answer: critique results' artifacts are never merged.

- [ ] **Step 1: Write the failing tests:** `test_every_agent_has_sources` (registry); `test_same_source_critic_is_dropped` (sql author → chart dropped, research and docs kept); `test_orchestrator_draft_excludes_critics_of_asked_sources` (orchestrator asked sql → chart dropped); `test_model_only_draft_allows_every_critic`; `test_orchestrator_answer_records_asked_routes` (in `tests/test_orchestrator.py`-style setup, asserting `additional_kwargs["asked"] == ["sql"]` after an `ask_sql` call); `test_critic_artifacts_stay_off_the_answer` (a critic whose run produces a chart artifact; the final answer's `additional_kwargs` has no such chart).
- [ ] **Step 2: Run `uv run pytest tests/test_team.py tests/test_registry.py tests/test_orchestrator.py -v`, expect the new tests to FAIL**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run `uv run pytest -q -rs` (DB up), expect PASS**
- [ ] **Step 5: Commit** `fix: team critics come from a different source`

### Task 2: Debate only on conflict

**Files:**
- Modify: `app/orchestration/team.py` (`Verdict.explanation`, carried into `verdicts`, the `verdict` event and the write-up note)
- Modify: `app/orchestration/prompts.py` (`JUDGE_PROMPT`, `CRITIQUE_MESSAGE`, `TEAM_MESSAGE`)
- Modify: `app/agents/sql/prompts.py` (state the as-of date of figures)
- Test: `tests/test_team.py`

**Interfaces:**
- Produces: `Verdict.explanation: str = ""`; verdict event and `team["verdicts"]` entries gain `explanation`; `TEAM_MESSAGE` gains `{explanations}` (the verdicts' non-empty explanations as a bulleted string, or `none`).
- Prompt rules (load `writing-llm-document`; no source preferred by kind):
  - `JUDGE_PROMPT`, after a critique: agree → approve; a difference both sides explain (as-of date, definition, scope) → approve and write the reason in `explanation`; a conflict the author can fix with its own tools (wrong query, wrong ticker or name, a claim its data doesn't support) → revise with `issues`; a conflict nobody can fix → approve and describe it in `explanation`. Never decide that one source is right because of what kind of source it is.
  - `CRITIQUE_MESSAGE`: for every figure or claim checked, give your source and its date; say whether a difference could come from date, definition or scope.
  - `TEAM_MESSAGE`: where explanations exist, give each source's value with its source and date and the explanation; for an unresolved conflict, show both sides and say the team could not settle it, without choosing one.
  - sql prompt: when the answer has prices or market data, state the date they are as of (`trade_date` for prices, the profile `updated_at` for profile figures such as market cap).
- Settings, `{draft}`/`{issues}` etc. keep working; check every `.format` call site.

- [ ] **Step 1: Write the failing tests:** `test_approve_with_explanation_reaches_the_write_up` (scripted judge: critique, then approve with explanation; the orchestrator's write-up prompt contains the explanation; the transcript's last verdict has it; no refine phase); `test_unfixable_conflict_does_not_revise` (approve with explanation after a critique ends in synthesize, turns 1); `test_prompts_format` (each changed constant formats with exactly its fields).
- [ ] **Step 2: Run `uv run pytest tests/test_team.py -v`, expect FAIL**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run `uv run pytest -q -rs`, expect PASS**
- [ ] **Step 5: Commit** `fix: team debates only on a real conflict`

### Task 3: Critic output stays in the discussion (UI)

**Files:**
- Modify: `app/static/app.js`, `app/static/style.css`

**Interfaces:**
- Consumes: SSE order (a `team` event opens a phase; `tool_call`/`tool_result` events arrive within it).
- Produces: while a non-`synthesize` phase is open, `tool_call`/`tool_result` lines render inside that phase's entry (compact, as the answer's tool lines do today) and `tool_result.charts` are not rendered as answer chart cards; from `synthesize` on, behaviour is today's. `verdict` lines show `explanation` when present. Non-team streams are unchanged.

- [ ] **Step 1: Implement**
- [ ] **Step 2: `node --check app/static/app.js`; `uv run pytest -q -rs`**
- [ ] **Step 3: Commit** `fix: team critic output stays in the discussion`

### Task 4: Error hunt

**Files:**
- Modify: `scripts/bench_chat.py` (team scenarios)
- Create (scratchpad only): the run report

**Interfaces:**
- Produces: bench scenarios `team` (existing), `team_research` (a recent-news question), `team_compound` (a question needing the market data and general knowledge, so the orchestrator authors), `team_vi` (a Vietnamese stock question), `team_ambiguous` (a comparison with no subject named, to try `needs_human`). All run with `agent="team"` (generalise the existing `name == "team"` check to the `team` prefix).

- [ ] **Step 1: Add the scenarios; run `uv run pytest -q -rs`; commit** `test: more team bench scenarios`
- [ ] **Step 2: Run** `uv run python -m scripts.bench_chat --only team,team_research,team_compound,team_vi,team_ambiguous --runs 1 --out <scratchpad>\bench_team_fixed.json` (spends free quota). Record per scenario: route, phases, critic chosen, verdicts with explanations, calls, latency, errors, answer language.
- [ ] **Step 3: Live UI pass** (`run` skill; if the browser tool can't attach, use a temp `--user-data-dir` or stop and report; never write into the user's real browser profile): one team stock question and one Vietnamese question; check the critic's tool lines sit inside its entry, no duplicate charts, explanations show, reload matches.
- [ ] **Step 4: Report** every error or wrong behaviour found, with the event sequence; fix nothing in this task.
