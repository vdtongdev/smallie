# Agent Team, Round Table Fixes Implementation Plan

> **For agentic workers:** run with the foreman loop (implementer per task, verifier after each). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Fix what the round-table evaluation (2026-10-25) found: source scoping that doesn't work on the agent servers, a board that calls an undated contradicting figure "explained", a reload view that differs from live, and language drift when the orchestrator answers alone; then re-run the evaluation.

**Architecture:** no new parts. Fixes land in `scope_tools`/server context handling, the `Board` model and prompt, a final per-speaker event plus the transcript, the orchestrator's prompt extra, and the bench.

**Tech Stack:** as before.

**Spec:** `docs/superpowers/specs/2026-10-23-agent-team-design.md`, "Phase C v2: round table" (updated in this plan's tasks where noted).

## Global Constraints

- All constraints of `docs/superpowers/plans/2026-10-24-agent-team-round-table.md` hold, including `.claude/rules/testing.md` ("Never create a test, or adjust a test so that it can be passed"), quoted in every brief.
- Bug fixes follow the foreman bug workflow: reproduce with a failing test first, then fix.

## Review Focus

1. **Scoping on the server:** a real agent-server run with `sources=("web",)` must not offer `run_sql`/`search_documents` to the model. Pinned by `test_server_run_hides_tools_outside_sources` (Task 1) and the live bench (Task 6).
2. **Undated contradicting figure:** a difference with a side lacking source or date becomes a conflict. Pinned by `test_difference_without_source_or_date_becomes_conflict` (Task 2).
3. **Reload equals live:** the transcript holds every tool call a speaker made, including research's inner ones. Pinned by `test_transcript_holds_research_tool_calls` (Task 3).
4. **Language when the orchestrator answers alone:** every orchestrator call names the latest message's language. Pinned by `test_orchestrator_names_reply_language_every_call` (Task 4).

---

### Task 1: Source scoping on the agent servers (bug)

**Mode:** diagnose first, then fix.

**Files:** to be found by the diagnosis; likely `app/agents/factory.py` (`scope_tools`), `app/agents/server.py` (how the run's context is built), `app/agents/research/agent.py` (`gather` passing context to the researcher). Test: `tests/test_agent_server.py` or `tests/test_scope_tools.py`.

**Observed:** in 4 of 6 bench scenarios research called `run_sql` (up to 8 times) and `search_documents` inside team rounds, although `speak` set `AgentContext.sources = {"web", "external_db"}`. All unit tests pass in-process `AgentContext` objects. Hypothesis (unverified): on the server the context arrives as a dict, or the nested researcher run doesn't get it, so `scope_tools` sees no `sources`.

- [ ] **Step 1 (diagnose):** reproduce with a test that goes through the server path as langgraph-api does: build the graph with `app/agents/server.py`'s `reporting_graph(spec.build())` for research (scripted model recording bound tools) and invoke it with the context as the JSON dict `RemoteAgent` sends (`asdict(AgentContext(..., sources=("web",)))` after a JSON round trip). Name it `test_server_run_hides_tools_outside_sources`; it must fail now. Find the root cause.
- [ ] **Step 2:** fix the root cause once in shared code (don't special-case research).
- [ ] **Step 3:** `uv run pytest -q -rs`; rebuild the agent servers (`docker compose up -d --build` through PowerShell + WSL); one real team message (a stock question) through the bench `team` scenario with the events wrapper: research's round-1 tool calls contain no `run_sql`/`search_documents`.
- [ ] **Step 4:** commit `fix: team source scoping works on the agent servers`

### Task 2: Board: an undated contradicting figure is a conflict

**Files:** `app/orchestration/team.py` (`Difference`, `clean`), `app/orchestration/prompts.py` (`BOARD_PROMPT`, `TEAM_MESSAGE`), spec "judge" bullet. Test: `tests/test_team.py`.

**Interfaces:**
- `Difference` gains `sides: list[Side]`, `Side(route: str, value: str, source: str, as_of: str)`; `explanation` stays.
- `clean` moves a difference to `conflicts` (as `Conflict(routes, claim=<the values>, fixable_by=[], issue="a side has no source or date")`) when any side has an empty `source` or `as_of`. Code enforces it; the prompt also says it.
- `BOARD_PROMPT`: a difference counts as explained only when both sides give a source and a date and the explanation fits the size of the gap (a date gap explains a small move, not a multiple); otherwise it is a conflict. When several independent dated sources agree against one, say so in the conflict's `issue`. Still never right by kind.
- `TEAM_MESSAGE`: for an open conflict where independent dated sources agree against one, show all values and say which ones agree and which one stands alone, without calling a source right by its kind.
- Spec: update the "judge" bullet with the sides rule.

- [ ] **Step 1:** failing tests `test_difference_without_source_or_date_becomes_conflict`, `test_dated_sourced_difference_stays_explained`, `test_prompts_format` updated for any new field
- [ ] **Step 2:** implement (`writing-llm-document` for prompts)
- [ ] **Step 3:** `uv run pytest -q -rs`; commit `fix: team board needs a source and date to call a difference explained`

### Task 3: Reload equals live

**Files:** `app/orchestration/team.py` (`speak`), research's server result if needed (`app/agents/server.py`, `app/agents/research/agent.py`), `app/api/routes_chat.py` (`TEAM_EVENTS`), `app/static/app.js`. Tests: `tests/test_team.py`, `tests/test_chat_events.py`.

**Observed:** live popup 21 steps vs 6 after reload; research's tool calls missing after reload (its inner tool messages never reach `speak`'s result); live research text shows source ids, reload shows expanded URLs (live shows raw tokens, the transcript holds the final findings).

**Interfaces:**
- `speak`'s transcript entry holds every tool call the speaker made, including a workflow's inner ones (research's researcher). If the server result doesn't carry them, the agent server returns them (e.g. a `tools` key next to `usage`, built from the run's tool messages, each `{name, args, result ≤ TOOL_RESULT_PREVIEW}`), and `RemoteAgent` passes that key through.
- At the end of each `speak`, a custom event `{"type": "spoke", "agent", "round", "text", "tools"}` carries the final entry; added to `TEAM_EVENTS`. The UI replaces that agent's live text and tools in the popup with it, so live ends identical to reload.

- [ ] **Step 1:** failing tests `test_transcript_holds_research_tool_calls`, `test_spoke_event_carries_the_final_entry`
- [ ] **Step 2:** implement backend, then UI (`node --check`)
- [ ] **Step 3:** `uv run pytest -q -rs`; commit `fix: team transcript and live popup hold the same steps`

### Task 4: Language when the orchestrator answers alone

**Files:** `app/orchestration/orchestrator.py` (`compose_section`), `app/orchestration/prompts.py` if a line is added. Test: `tests/test_orchestrator.py` (new test only).

**Observed:** "Which one is better?" (English) got a Vietnamese answer; nobody was picked, so the orchestrator answered as in normal mode, whose deciding call names no language and the user's memories hold a Vietnamese preference. The same happens in normal chats.

**Interfaces:** `compose_section` names the reply language from `reply_language(messages)` on every call, not only when composing (one short line; trap `reply-language`).

- [ ] **Step 1:** failing test `test_orchestrator_names_reply_language_every_call` (a first, non-composing call's system prompt names English for an English message, with a memory saying the user prefers Vietnamese)
- [ ] **Step 2:** implement; `uv run pytest -q -rs`; commit `fix: the orchestrator names the reply language on every call`

### Task 5: Bench answer text

**Files:** `scripts/bench_chat.py` (only).

**Observed:** bench `answer` strings joined tokens of every agent (e.g. research's "FINDINGS:" notes appended to the answer), while the UI answer was clean.

- [ ] **Step 1:** the bench's answer text uses only tokens without `agent` (the answer bubble's rule); for a single-participant reply with no `synthesize`, the speaker's final text (from the `spoke` event, Task 3)
- [ ] **Step 2:** `uv run pytest -q -rs`; commit `test: bench answer text matches the answer bubble`

### Task 6: Re-run the evaluation

- [ ] **Step 1:** rebuild the agent servers; run the bench `--only team,team_research,team_compound,team_vi,team_ambiguous,team_conflict --runs 1` with the events wrapper (free quota, once)
- [ ] **Step 2:** live UI pass (temp browser profile only): the conflict question (file flagged as disagreeing with dated sources), the popup live vs reload counts, an English question in a chat whose user memory prefers Vietnamese
- [ ] **Step 3:** report per scenario (participants, rounds, board, tool calls per agent per round, calls, latency, language, errors) against the 2026-10-25 run; problems by severity; fix nothing

## Known limitation kept

`needs_human` hasn't fired in any run: when nobody is picked the orchestrator asks clarifying questions itself, and the board rarely needs the user. Not in this plan.
