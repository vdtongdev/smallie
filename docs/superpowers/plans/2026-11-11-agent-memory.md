# Agent memory (MEMORY.md) Implementation Plan

> **For agentic workers:** executed with the foreman loop (one agent per task, verifier after each). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the MEMORY prompt layer its own file and let each agent keep per-user working notes, stored in the knowledge graph and learned by the existing extraction call.

**Architecture:** `memories` and `recent_chats` move from `USER.md` to a new `MEMORY.md`, which also gets a `notes` section. Notes are `note` nodes linked from an `agent` node (`noted`) and to what they mention (`about`) in `graph_nodes`/`graph_edges`. `extract_facts` returns notes beside facts and runs after every finished turn; the chat's frozen snapshot carries them to `build_agent`, which renders the notes of the agent it builds.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy Core, pgvector, Pydantic, LangChain `create_agent`, pytest (`pytest-asyncio` auto mode), uv.

**Spec:** `docs/superpowers/specs/2026-11-11-agent-memory-design.md`

**Worktree:** `D:\Projects\smallie\.claude\worktrees\agent-memory`, branch `0.7.0/agent-memory`. Run every command from there. Before DB tests: `powershell -ExecutionPolicy Bypass -File scripts\dev_db.ps1`. A skipped `requires_db` test is not a pass.

## Global Constraints

- Every graph row, query and API call is keyed by `user_id`.
- An agent with no notes gets the same system prompt bytes as before this branch, apart from the one new sentence in `MEMORY_RULE`.
- `MEMORIES_SECTION` and `RECENT_CHATS_SECTION` keep their names and their bytes.
- No model call is added on the reply path. The only added call is the background extraction on turns a specialist answered.
- Every new field of a structured-output schema has a default (trap `structured-output-required-fields`).
- Agent servers never write: notes are saved only by the main app.
- Sync DB and embedding work called from async code goes through `asyncio.to_thread`.
- Prompt text (`MEMORY.md`, `AGENTS.md`, `EXTRACT_PROMPT`): load the `writing-llm-document` skill first. No example a model could copy as real data (trap `copyable-prompt-examples`); examples in English (trap `reply-language`); `MEMORY.md` sections are formatted, so literal braces are doubled; `EXTRACT_PROMPT` is never formatted (trap `prompt-format-braces`).
- Layer files never start with a blank line and end with exactly one newline.
- DB tests create users with `make_user` (`tests/test_graph.py`) and touch only those users' rows; the dev database is shared (trap `tests-shared-dev-db`).
- Tests are written from the spec. A failing test is fixed in the code, never in the test (`.claude/rules/testing.md`).
- Constants (all in `app/core/graph.py`): `RECALL_NOTES = 10`, `MAX_NOTES = 3` (per extraction call), `NOTE_CHARS = 200`, `NOTED = "noted"`, `ABOUT = "about"`, `SAME_NOTE = 0.85` (provisional until task 7 measures it).

## Review Focus

- A model returns `notes` on a turn with no known agent (past-chat mining, an unmapped message name): the facts are saved, the notes are dropped, nothing raises (task 3 test).
- A note names the user's file or the user ("the user", "me"): `about` resolves to the file node or the User node, never a new entity called "User" (task 2 test).
- Two agents learn the same sentence: each has its own note node; deleting one agent's note leaves the other's (task 2 test).
- A note edge at the User node (`note -[about]-> User`) must not show up in the prompt's facts, in `search_memory` results or as a fact row of `/api/memories` (task 2 tests).
- Agent containers built before this branch run old code and would ignore `agent_notes`: rebuild them from the worktree with `-p smallie` before the manual check (task 7).

---

### Task 1: MEMORY.md layer file

Agent: implementer

**Files:**
- Create: `app/core/layers/MEMORY.md`
- Modify: `app/core/layers/USER.md` (remove `memories`, `recent_chats`), `app/core/layers/AGENTS.md` (section `memory`), `app/core/prompts.py`
- Test: `tests/test_prompt_files.py`, `tests/test_prompt_layers.py`

**Interfaces:**
- Produces: `NOTES_SECTION: str` in `app/core/prompts.py`, starting with `"\n\n"` like its neighbours, with exactly one placeholder `{notes}`. `MEMORIES_SECTION`, `RECENT_CHATS_SECTION` unchanged in name and value.

- [ ] **Step 1: Record today's values.** Before any edit, print `repr(MEMORIES_SECTION)` and `repr(RECENT_CHATS_SECTION)` (`uv run python -c "from app.core.prompts import *; print(repr(MEMORIES_SECTION)); print(repr(RECENT_CHATS_SECTION))"`) and paste both literals into the test of step 2.

- [ ] **Step 2: Write the failing tests** in `tests/test_prompt_files.py`:

```python
def test_memory_layer_has_its_own_file():
    sections = load_sections(LAYERS / "MEMORY.md", ("notes", "memories", "recent_chats"))
    assert set(sections) == {"notes", "memories", "recent_chats"}
    load_sections(LAYERS / "USER.md", ("user", "profile"))  # raises if memories/recent_chats are still there


def test_moved_sections_keep_their_bytes():
    assert MEMORIES_SECTION == <literal from step 1>
    assert RECENT_CHATS_SECTION == <literal from step 1>


def test_notes_section_has_one_slot():
    assert NOTES_SECTION.startswith("\n\n")
    assert NOTES_SECTION.format(notes="- x").count("- x") == 1


def test_memory_rule_tells_agents_to_follow_their_notes():
    assert "NOTES" in MEMORY_RULE
```

Add `MEMORY.md` to the existing tests that walk every layer file (no leading blank line, one trailing newline).

- [ ] **Step 3: Run them.** `uv run pytest tests/test_prompt_files.py -v`. Expected: the four new tests FAIL.

- [ ] **Step 4: Move and write the text.** Move the `memories` and `recent_chats` sections verbatim into `MEMORY.md`, in the order `notes`, `memories`, `recent_chats`. Load `writing-llm-document`, then write:
  - the `notes` section: a capitalised heading in the style of the other sections that says these are this agent's own notes on how this user wants its work done, then `{notes}`;
  - one sentence appended to the `memory` section of `AGENTS.md`: follow the notes under that heading unless the user's latest message says otherwise. Use the same heading words in both places.

  In `prompts.py`: load `USER.md` with `("user", "profile")`, `MEMORY.md` with the three names, define `NOTES_SECTION = "\n\n" + ...`.

- [ ] **Step 5: Run.** `uv run pytest tests/test_prompt_files.py tests/test_prompt_layers.py tests/test_orchestrator.py -v`. Expected: PASS.

- [ ] **Step 6: Commit** `Move memories and recent chats to MEMORY.md and add the notes section`.

---

### Task 2: Notes in the graph

Agent: implementer

**Files:**
- Modify: `app/core/graph.py`, `app/core/db.py` (the `kind` column comment only), `scripts/reindex.py`
- Test: `tests/test_graph.py`

**Interfaces:**
- Produces, in `app/core/graph.py`:
  - `class AgentNote(BaseModel)`: `text: str` (1 to `NOTE_CHARS` characters), `about: list[str] = []`.
  - `save_notes(user_id: str, agent: str, notes: list[AgentNote], conversation_id: str | None = None) -> int` (notes saved; `agent` is a route).
  - `recall_notes(user_id: str, limit: int = RECALL_NOTES) -> dict[str, str]` (route -> bullet list, `"- <text>"` lines, newest first by the `noted` edge's `updated_at`; routes without notes are absent).
  - `list_notes(user_id: str) -> list[dict]` with keys `id` (the `noted` edge's id), `text`, `source`, `created_at`, `agent`.
  - `list_facts` rows gain `"agent": None`. `delete_fact(user_id, edge_id)` keeps its signature and also works on a `noted` edge.

- [ ] **Step 1: Write the failing tests** in `tests/test_graph.py`, all `@requires_db` with `make_user` and `embeddings` (give `embeddings.aliases` two wordings of one note to make them near-duplicates; different texts get unrelated fake vectors):

```python
def test_a_note_becomes_agent_and_note_nodes_with_edges(make_user, embeddings):
    user = make_user()
    assert graph.save_notes(user, "sql", [AgentNote(text="Limit queries to one exchange", about=["HOSE"])], "c1") == 1
    assert node_names(user, "agent") == ["sql"]
    assert node_names(user, "note") == ["Limit queries to one exchange"]
    assert node_names(user) == ["HOSE"]
    assert count(graph_edges, user, graph_edges.c.relation == "noted", graph_edges.c.conversation_id == "c1") == 1
    assert count(graph_edges, user, graph_edges.c.relation == "about") == 1


def test_a_note_about_nothing_or_about_the_user_links_the_user_node(make_user, embeddings):
    # about=[] and about=["the user"]: one `about` edge to the kind "user" node each, no entity named "the user"

def test_a_note_about_a_file_links_its_file_node(make_user, embeddings):
    # add_document(user, "plan.pdf"); about=["plan.pdf"] -> the about edge's target is the file node

def test_a_reworded_note_replaces_the_old_one(make_user, embeddings):
    # aliases: "Use HOSE only" -> "Limit queries to one exchange"; save both for "sql"
    # -> node_names(user, "note") == ["Use HOSE only"], one `noted` edge

def test_a_different_note_is_added(make_user, embeddings):          # two texts -> two note nodes
def test_two_agents_keep_their_own_copy_of_the_same_note(make_user, embeddings):
    # same text for "sql" and "chart" -> two note nodes; delete sql's noted edge with delete_fact
    # -> recall_notes(user) == {"chart": "- <text>"}

def test_recall_notes_is_per_agent_newest_first_and_capped(make_user, embeddings):
    # 12 notes for "sql", 1 for "chart" -> 10 lines for sql, the last saved first; chart has 1

def test_notes_are_scoped_per_user(make_user, embeddings):          # other user's recall_notes == {}
def test_deleting_a_note_removes_its_node(make_user, embeddings):
    # delete_fact(user, <noted edge id>) is True; no note node, no about edge left; the agent node may stay

def test_notes_never_show_up_as_facts(make_user, embeddings):
    # one fact (User holds FPT) and one note with about=[] (edge at the User node)
    # recall_facts, recall_profile()[0], search_facts(user, <note text>) hold neither the note text nor "noted"/"about"
    # [r["text"] for r in list_facts(user)] == ["User holds FPT"]; list_notes(user)[0]["agent"] == "sql"
```

- [ ] **Step 2: Run.** `uv run pytest tests/test_graph.py -v`. Expected: the new tests FAIL (`save_notes` missing).

- [ ] **Step 3: Implement.**
  - `save_notes`: embed note texts and non-user, non-file `about` names in one `_embed` call outside the transaction (as `save_facts` does). Per note, in one transaction: get or create the `agent` node (`name` = route); find the note node by exact `norm` = `f"{agent}:{normalize(text)}"`, else the nearest `note` node that this agent `noted` with cosine similarity >= `SAME_NOTE` and update its `name`, `norm` and `embedding` and delete its `about` edges, else create it; `_link` agent -`noted`-> note (the upsert refreshes `updated_at`) with `source="chat"` and `conversation_id`; `_link` note -`about`-> each resolved name (user words -> User node, file name -> file node, else `_entity`), or the User node when `about` is empty. `_get_or_create` computes `norm` from the name today: give it an optional `norm` argument.
  - `_drop_orphans`: first delete `note` nodes of the user that no `noted` edge points to, then orphan entities as today.
  - `_facts_query`: leave out edges whose source or target node has kind `agent` or `note`.
  - `search_facts`: pick nearest nodes among nodes whose kind is not `note`.
  - `extract_from_turns`' `known()`: leave `noted` and `about` out of the relations list.
  - `scripts/reindex.py`: re-embed nodes of kind `entity` and `note`.

- [ ] **Step 4: Run.** `uv run pytest tests/test_graph.py -v`. Expected: all PASS, none skipped.

- [ ] **Step 5: Commit** `Store agent notes as note nodes in the knowledge graph`.

---

### Task 3: Extraction returns notes, on every turn

Agent: implementer

**Files:**
- Modify: `app/core/graph.py` (`FactUpdate`, `extract_facts`, `extract_from_turns`), `app/core/prompts.py` (`EXTRACT_PROMPT`), `app/api/routes_chat.py` (`remember_turn`)
- Test: `tests/test_graph.py`, `tests/test_isolation_and_memory.py`

**Interfaces:**
- Consumes: `AgentNote`, `save_notes` (task 2); `MESSAGE_ROUTES` in `app/orchestration/orchestrator.py` (`AIMessage.name` -> route, orchestrator included).
- Produces: `FactUpdate.notes: list[AgentNote]` (default empty, cut to `MAX_NOTES` like `first_ones` cuts facts); `extract_facts(user_id, question, answer, callbacks, source="chat", conversation_id=None, agent: str | None = None) -> int` and the same new last parameter on `extract_from_turns`. The return value stays the number of facts kept.

- [ ] **Step 1: Write the failing tests.**

In `tests/test_graph.py`:

```python
def test_fact_update_parses_without_notes_and_keeps_three():
    assert FactUpdate.model_validate({"facts": []}).notes == []
    assert len(FactUpdate.model_validate({"notes": [{"text": f"n{i}"} for i in range(5)]}).notes) == 3
    with pytest.raises(ValidationError):
        AgentNote(text="x" * 201)


@requires_db
async def test_extract_facts_saves_notes_under_the_answering_agent(make_user, embeddings, monkeypatch):
    # fake model as in test_extract_facts_saves_what_the_model_returns, returning one fact and one note
    # await graph.extract_facts(user, "q", "a", [], agent="sql")
    # -> graph.recall_notes(user) == {"sql": "- <note text>"}; the human message sent to the model names the agent "sql"


@requires_db
async def test_notes_without_a_known_agent_are_dropped_and_facts_kept(make_user, embeddings, monkeypatch):
    # same fake reply, agent=None -> returns 1 (the fact), recall_notes(user) == {}


def test_extract_prompt_describes_notes():
    assert "notes" in EXTRACT_PROMPT
    assert "{" not in EXTRACT_PROMPT
```

In `tests/test_isolation_and_memory.py`, with the `env` fixture the way `tests/test_snapshot.py::test_two_turns_send_the_same_system_prompt` drives a chat, and `app.api.routes_chat.extract_facts` monkeypatched with a recorder:

```python
def test_a_turn_a_specialist_answered_is_extracted_once_with_its_route(env, monkeypatch):
    # script the turn so the final text is an AIMessage named "sql_agent"
    # -> the recorder was called once, with agent="sql"

def test_an_orchestrator_turn_is_extracted_with_the_orchestrator_route(env, monkeypatch):
    # -> called once, with agent="orchestrator"
```

- [ ] **Step 2: Run.** `uv run pytest tests/test_graph.py tests/test_isolation_and_memory.py -v`. Expected: the new tests FAIL.

- [ ] **Step 3: Implement.**
  - `FactUpdate.notes` with its validator; update the class docstring (it is the tool description the model sees) to cover notes.
  - `extract_from_turns`: when `agent` is set, add a line to the human message that names the agent that wrote the answer. After the call: return 0 only when there are neither facts nor notes; save facts as today; when `agent` is set and there are notes, `await asyncio.to_thread(save_notes, user_id, agent, update_.notes, conversation_id)`.
  - `EXTRACT_PROMPT` (load `writing-llm-document` first): one new numbered rule. Content: `notes` holds how the user wants the named agent to do its work (a correction, a default, a format, a scope), each written as one short instruction to that agent in English; `about` lists the names it mentions; return no notes when the message names no agent; facts about the user stay triples; most turns have no note. Keep rule 4's relation list intact (`test_every_standard_relation_is_named_in_the_prompt`).
  - `remember_turn`: remove the `== ORCHESTRATOR` condition and its comment; call `extract_facts(..., agent=MESSAGE_ROUTES.get(_answered_by(messages)))` whenever `answer` is non-empty.

- [ ] **Step 4: Run.** `uv run pytest tests/test_graph.py tests/test_isolation_and_memory.py tests/test_usage.py -v`. Expected: PASS.

- [ ] **Step 5: Commit** `Extract agent notes with the facts, after every finished turn`.

---

### Task 4: Notes reach the prompt

Agent: implementer

**Files:**
- Modify: `app/core/memory.py` (`AgentContext`), `app/core/snapshot.py`, `app/agents/factory.py`, `app/agents/{docs,sql,chart,research}/agent.py`, `app/orchestration/orchestrator.py`
- Test: `tests/test_snapshot.py`, `tests/test_prompt_layers.py`, `tests/test_remote.py`

**Interfaces:**
- Consumes: `recall_notes` (task 2), `NOTES_SECTION` (task 1).
- Produces: `AgentContext.agent_notes: dict[str, str]` (`field(default_factory=dict)`); snapshot key `agent_notes`; `build_agent(..., route: str = "")`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_snapshot.py
@requires_db
async def test_snapshot_carries_each_agents_notes(...):
    # save_notes(user, "sql", [...]) -> (await build_snapshot(None, user, conv))["agent_notes"] == {"sql": "- <text>"}

@requires_db
async def test_a_snapshot_saved_before_agent_notes_is_rebuilt(...):
    # a recent saved snapshot with "profile" but no "agent_notes" -> chat_snapshot returns one that has the key

# tests/test_prompt_layers.py (build the agents the way the file's existing tests capture a system prompt)
def test_an_agent_sees_only_its_own_notes():
    # context.agent_notes = {"sql": "- SQL NOTE", "chart": "- CHART NOTE"}
    # sql agent's system prompt contains "- SQL NOTE" and not "- CHART NOTE"; the notes text sits before MEMORIES_SECTION's heading

def test_no_notes_leaves_the_prompt_unchanged():
    # system prompt with agent_notes={} == system prompt with agent_notes={"chart": "- CHART NOTE"} for the sql agent

def test_every_agent_passes_its_route():
    # for docs, sql, chart, research and the orchestrator: with agent_notes={<route>: "- MINE"} the built prompt contains "- MINE"

# tests/test_remote.py
def test_agent_notes_travel_to_the_agent_server():
    # the request body RemoteAgent sends holds context["agent_notes"] == {"sql": "- x"}, and the server side
    # rebuilds an AgentContext with the same dict
```

- [ ] **Step 2: Run.** `uv run pytest tests/test_snapshot.py tests/test_prompt_layers.py tests/test_remote.py -v`. Expected: the new tests FAIL.

- [ ] **Step 3: Implement.**
  - `build_snapshot`: add `recall_notes` to the `asyncio.gather` (through `asyncio.to_thread`) and the key `agent_notes` to the returned dict.
  - `chat_snapshot`: reuse a saved snapshot only when it has both `profile` and `agent_notes`.
  - `build_agent`: after `PROFILE_SECTION`, `if notes := context.agent_notes.get(route): prompt += NOTES_SECTION.format(notes=notes)`.
  - Pass `route=` from each agent: `"docs"`, `"sql"`, `"chart"`, `"research"` (the researcher), `ORCHESTRATOR`.
  - `scripts/bench_chat.py` and `routes_chat.py` build `AgentContext(**snapshot)`, so the key name carries the field; check both still construct.

- [ ] **Step 4: Run the whole suite.** `uv run pytest`. Expected: PASS, no DB test skipped.

- [ ] **Step 5: Commit** `Render each agent's notes from the chat snapshot`.

---

### Task 5: Notes in /api/memories

Agent: implementer

**Files:**
- Modify: `app/api/routes_memories.py`
- Test: `tests/test_isolation_and_memory.py`

**Interfaces:**
- Consumes: `list_notes`, `list_facts`, `delete_fact` (task 2).

- [ ] **Step 1: Write the failing tests** (`@requires_db`, with the `env` client):

```python
def test_memories_lists_notes_with_their_agent(env, ...):
    # one fact and one sql note -> GET /api/memories returns both; the note row has agent == "sql", the fact row agent is None;
    # rows are ordered newest first by created_at

def test_deleting_a_note_through_the_api(env, ...):
    # DELETE /api/memories/<note id> -> 204; the note is gone from the list; every chat snapshot of the user is cleared

def test_another_users_note_cannot_be_deleted(env, ...):
    # user B deletes user A's note id -> 404; A still has it
```

- [ ] **Step 2: Run.** Expected: FAIL (notes missing from the list).
- [ ] **Step 3: Implement.** `list_memories` returns facts and notes merged, sorted by `created_at` descending; both reads in one `asyncio.to_thread` each. `forget` is unchanged. Update the module docstring.
- [ ] **Step 4: Run.** `uv run pytest tests/test_isolation_and_memory.py tests/test_snapshot.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `List and delete agent notes through /api/memories`.

---

### Task 6: Docs

Agent: drafter

**Files:**
- Modify: `ARCHITECTURE.md`, `.claude/rules/memory.md`, `.claude/rules/agents.md`, `CLAUDE.local.md` is not touched (its `app/core/layers/*.md` pattern already covers `MEMORY.md`).

Load `writing-llm-document` for the two `.claude/rules` files. No tests.

- [ ] **Step 1:** `ARCHITECTURE.md`: the MEMORY layer file and its three sections; node kinds `agent` and `note`, edges `noted` and `about`; extraction after every finished turn, returning notes; `/api/memories` lists notes.
- [ ] **Step 2:** `.claude/rules/memory.md`: node kinds; `save_notes` (agent-prefixed `norm`, `SAME_NOTE`, `about` resolution), `recall_notes`, `list_notes`, the readers' exclusion of note edges; the snapshot key `agent_notes` and its stale check; replace "after a finished turn whose answer came from the orchestrator ... skipped to save calls" with extraction on every finished turn and the route mapping; remove the BOOTSTRAP "Known gap" sentence; state the known limit (notes go to the agent that wrote the final answer; research gets none).
- [ ] **Step 3:** `.claude/rules/agents.md`: `build_agent(route=...)`, `AgentContext.agent_notes`, and that a new agent passes its route.
- [ ] **Step 4: Commit** `Document agent memory`.

---

### Task 8: The extractor names the note it replaces

Added 2026-11-11 after Task 7 Step 1 found that embedding similarity cannot tell a reworded note from a different one (spec section 2, "Duplicates and corrections", and section 3, `replaces`). Runs before the rest of Task 7.

Agent: implementer

**Files:**
- Modify: `app/core/graph.py`, `app/core/prompts.py` (`EXTRACT_PROMPT` rule 12 only)
- Test: `tests/test_graph.py`

**Interfaces:**
- `AgentNote.replaces: int | None = None`, with a `mode="before"` validator that turns anything that is not a whole number >= 1 (also a numeric string is accepted as its number) into `None`.
- `current_notes(user_id: str, agent: str, limit: int = RECALL_NOTES) -> list[tuple[str, str]]`: (note node id, text) of this agent's notes for this user, newest first by the `noted` edge's `updated_at`; the same notes and order as `recall_notes(user_id)[agent]`.
- `save_notes(user_id, agent, notes, conversation_id=None, current: list[str] | None = None) -> int`: `current` is the list of note node ids in the order they were shown to the extractor; `note.replaces == n` means `current[n - 1]`.
- `SAME_NOTE = 0.96`, with the measured ranges from the spec in its comment (model `baai/bge-m3`, date 2026-11-11, "re-measure after changing the embedding model").

- [ ] **Step 1: Write the failing tests** in `tests/test_graph.py` (`@requires_db` except the schema test; `make_user`, `embeddings`; texts get unrelated fake vectors unless aliased):

```python
def test_replaces_accepts_a_number_and_ignores_anything_else():
    assert AgentNote(text="x", replaces=2).replaces == 2
    assert AgentNote(text="x", replaces="2").replaces == 2
    for bad in (0, -1, "second", 1.5, None, [1]):
        assert AgentNote(text="x", replaces=bad).replaces is None


def test_a_note_replaces_the_one_the_extractor_named(make_user, embeddings):
    # save "Sort results by date ascending" then "Limit results to 50 rows" for "sql"
    # ids = [i for i, _ in graph.current_notes(user, "sql")]            # newest first: [limit, sort]
    # save_notes(user, "sql", [AgentNote(text="Sort results by date descending", replaces=2)], current=ids)
    # -> recall_notes(user)["sql"] lists exactly "Sort results by date descending" (now newest) and "Limit results to 50 rows"

def test_replaces_outside_the_list_adds_a_note(make_user, embeddings):
    # one existing note; replaces=5 with current=ids -> two notes, nothing raises

def test_replaces_cannot_touch_another_agents_or_users_note(make_user, embeddings):
    # current holds the id of chart's note, and in a second case of another user's sql note;
    # save for "sql" with replaces=1 -> that other note is unchanged and sql has the new note as an added one

def test_replaces_without_a_list_is_ignored(make_user, embeddings):
    # replaces=1, current=None -> the note is added

def test_same_text_wins_and_the_named_note_is_removed(make_user, embeddings):
    # notes A and B exist; save text equal to A with replaces pointing at B -> only A is left

def test_different_instructions_are_both_kept_below_the_threshold(make_user, embeddings):
    # two unrelated-vector texts, no replaces -> two notes (the embedding backstop does not merge them)

async def test_the_extractor_sees_the_agents_notes_numbered(make_user, embeddings, monkeypatch):
    # two sql notes and one chart note exist; fake model records its messages and returns a note with replaces=1
    # await graph.extract_facts(user, "q", "a", [], agent="sql")
    # -> the human message lists the two sql notes as "1. <newest>" and "2. <older>", not the chart note;
    #    afterwards sql still has two notes and the newest one is the returned text

async def test_no_notes_list_without_an_agent(make_user, embeddings, monkeypatch):
    # agent=None -> the human message holds no numbered notes list

def test_extract_prompt_explains_replaces():
    assert "replaces" in EXTRACT_PROMPT and "{" not in EXTRACT_PROMPT
```

- [ ] **Step 2: Run.** `uv run pytest tests/test_graph.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
  - `current_notes`, built on the existing notes query.
  - `extract_from_turns`: when `agent` is set, read `current_notes` in the same `asyncio.to_thread(known)` call, add the numbered list to the human message under one heading line (names go into the message, never a template), and pass `current=[ids]` to `save_notes`.
  - `save_notes`: the order of the spec's "Duplicates and corrections" bullet. A named id is used only if it is a note node this agent of this user `noted` (check in SQL with `user_id` and the agent node). Two notes of one call naming the same number: the first takes it, the second is added.
  - `EXTRACT_PROMPT` rule 12 (load `writing-llm-document` first): add that the message may list the agent's current notes, numbered; set `replaces` to the number of the note that a new note corrects or restates; do not return a current note that still holds unchanged. No example values, no braces.
  - `SAME_NOTE = 0.96` and its comment.
- [ ] **Step 4: Run.** `uv run pytest tests/test_graph.py tests/test_isolation_and_memory.py tests/test_snapshot.py -q -rs`, then `uv run pytest -q`. Expected: only the known fresh-database failures.
- [ ] **Step 5: Commit** `Let the extractor name the note a new note replaces`.

---

### Task 7: Measurement

Agent: implementer. **Each run that calls a real model or the embedding API needs the user's yes first; ask before every one.**

**Files:**
- Modify: `app/core/graph.py` (`SAME_NOTE` and its comment), `scripts/bench_chat.py` (new scenario)
- Test: `tests/test_bench_chat.py`

- [x] **Step 1: `SAME_NOTE`.** Done 2026-11-11: the groups overlap (numbers in spec section 2), which led to Task 8. Original step: With a scratchpad script (not committed), embed with the real embedding model at least 10 pairs of notes that give the same instruction in other words and 10 pairs that differ but share a topic, print each pair's cosine similarity, and set `SAME_NOTE` between the two groups. Write the measured ranges in the constant's comment, as `SAME_ENTITY` has. If the groups overlap, stop and report the numbers.
- [ ] **Step 2: Baseline.** `uv run python -m scripts.bench_chat` on `origin/main` (a second worktree or the main checkout, untouched) and keep the output.
- [ ] **Step 3: Scenario.** Add a `bench_chat` scenario with two chats of one fresh user: in chat 1 the user asks the sql agent a question and corrects how it should work; chat 2 asks a question the correction applies to. The check: after chat 1, `recall_notes(user)` has a `sql` entry; chat 2's answer follows it. Add its name to the scenario list test in `tests/test_bench_chat.py`.
- [ ] **Step 4: After run.** Rebuild the agent containers from the worktree (`docker compose -p smallie up -d --build --no-deps docs sql research chart`, through WSL, WARP and SoftEther off) and check the new code is inside one (`docker exec smallie-sql-1 grep -n "agent_notes" /deps/smallie/app/agents/factory.py`). Run `bench_chat` on the branch.
- [ ] **Step 5: Report.** Per scenario, before and after: model calls per message on the reply path (expected: equal), background extraction calls (expected: one more on messages a specialist answered), tokens and spend per chat. These numbers go into the PR description.
- [ ] **Step 6: Commit** `Measure SAME_NOTE and add the agent-notes benchmark scenario`.

After task 7 the user checks the running app by hand, in English and Vietnamese (spec section 7, item 4), before the PR is opened.
