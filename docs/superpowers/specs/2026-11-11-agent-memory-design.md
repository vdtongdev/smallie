# Agent memory (MEMORY.md)

Date: 2026-11-11. Branch `0.7.0/agent-memory`, from `main` after PR #44 (prompt layer files). Status: design approved in chat, spec under review.

## Goal

Give the MEMORY layer its own file like SOUL, AGENTS, USER and BOOTSTRAP, and let each agent remember how one user wants it to work, with those notes stored in the existing knowledge graph.

Two parts:

1. **Split.** The `memories` and `recent_chats` sections move from `USER.md` to a new `MEMORY.md`. USER is who the user is; MEMORY is what the team has learned.
2. **Agent notes.** Each agent (orchestrator, docs, sql, chart, research) keeps short notes per user on how to do its work for them: corrections, defaults, formats, scopes. Example: the user tells the sql agent that "VN stocks" means one exchange only; in the next chat the sql agent applies it without being told.

## Decisions (made with the user, 2026-11-11)

- **Scope:** split plus agent notes. Rejected: split only; notes only; agents writing their own file through a tool (agent servers run as `agent_reader` and cannot write, and weak models skip optional tool calls).
- **Extraction runs on every finished turn**, not only turns the orchestrator answered. Cost: one more background model call per message a specialist answered. Rejected: a keyword check for corrections (misses notes); orchestrator turns only (most notes would never be learned).
- **Storage: note nodes in the graph** (approach A). Rejected: a text column on an agent -> User edge (not linked to entities); a separate `agent_notes` table (graph untouched).
- **Each agent sees only its own notes.** The orchestrator does not see the specialists' notes.
- **Notes are listed and deletable through `/api/memories`.** Amended 2026-11-11: the app has no memories dialog (the API has no UI caller), so this branch changes the API only; a dialog is its own item.
- **Version:** ships in `0.7.0` with the prompt layer files.

## 1. Layer files

```
app/core/layers/
  USER.md      # sections: user, profile
  MEMORY.md    # sections: notes, memories, recent_chats   (new file)
```

- `memories` and `recent_chats` move unchanged. `MEMORIES_SECTION` and `RECENT_CHATS_SECTION` keep their names in `app/core/prompts.py` and their bytes.
- New section `notes`, loaded as `NOTES_SECTION`, with one slot for the agent's notes as bullets. Its wording follows the `writing-llm-document` skill. It is formatted, so literal braces in it are doubled.
- Rendering order in `build_agent` (`app/agents/factory.py`): `user`, `profile`, `notes`, `memories`, `recent_chats`.
- `notes` is rendered only when the agent has notes. An agent without notes gets the same system prompt bytes as before this change.
- `MEMORY_RULE` (shared `AGENTS.md`, section `memory`) gains one sentence: follow the notes unless the latest message says otherwise. This changes every agent's system prompt once, on deploy.

## 2. Graph model

`graph_nodes` gains two kinds, `graph_edges` two relations. No new table and no new column.

| Kind | One per | `name` | `embedding` |
|---|---|---|---|
| `agent` | user and agent route (`orchestrator`, `docs`, `sql`, `chart`, `research`) | the route | none |
| `note` | note | the note text | of the text |

A note's `norm` is `<route>:<normalized text>`, so two agents never share a note node (the unique index is on user, kind and `norm`).

| Edge | Meaning | Carries |
|---|---|---|
| `agent -[noted]-> note` | this agent learned this note | `source = chat`, `conversation_id` |
| `note -[about]-> entity / file / User` | a name the note mentions | `source = chat` |

Rules:

- Every row is keyed by `user_id`, like the rest of the graph. An `agent` node is created when that agent's first note for the user is saved.
- **Names in `about`** resolve like fact objects in `save_facts`: user words to the User node, a file name to its file node, else an entity by normalized name, then by nearest embedding at or above `SAME_ENTITY`, else a new entity. A note with an empty `about` gets one `about` edge to the User node.
- **Duplicates:** before a note is saved, the nearest note of the same agent and user is looked up by embedding. At or above `SAME_NOTE` the old note's text, embedding and `about` edges are replaced and its `noted` edge is refreshed (newest wins); below it a new note node is made. `SAME_NOTE` is a constant in `app/core/graph.py`, measured like `SAME_ENTITY` (section 7) and re-measured after the embedding model changes.
- **Recall:** `recall_notes(user_id)` returns `{agent: bullets}`, the newest `RECALL_NOTES` (10) notes per agent by the `noted` edge's `updated_at`. SQL only, no model or embedding call.
- **Cleanup:** `_drop_orphans` also deletes a `note` node with no `noted` edge. An entity that only a note's `about` edge links stays, since it is linked.
- **Existing readers stay unchanged in what they return.** Today they would show note edges (`recall_facts` takes any edge at the User node, `list_facts` any edge not from a document, `search_facts` any node with an embedding), so `_facts_query` leaves out every edge with an `agent` or `note` node at either end, `search_facts` picks its nearest nodes among non-note nodes, and the extractor's list of relations already in the graph leaves out `noted` and `about`. `_keep_one_edge` runs only for fact pairs and never sees a note edge.
- `scripts.reindex` re-embeds note nodes together with entities.

## 3. Extraction

One call, as today; it returns notes beside facts.

- `FactUpdate` gains `notes: list[AgentNote] = []`. `AgentNote` has `text: str` and `about: list[str] = []`. Every new field has a default, so a reply without `notes` still parses. Pydantic limits: at most 3 notes per call, a note at most 200 characters.
- The model never names the agent. `remember_turn` (`app/api/routes_chat.py`) maps `_answered_by(messages)` (an `AIMessage.name` such as `sql_agent`) to its route through `MESSAGE_ROUTES` (`app/orchestration/orchestrator.py`) and passes the route to `extract_facts`; the extractor's human message says which agent wrote the answer, and code saves the notes under that route. A name with no route saves facts but no notes.
- Known limit: notes go to the agent that wrote the final answer. In team mode, and when the orchestrator asks a specialist as a tool and writes the answer itself (research always works this way), the notes are the orchestrator's. The research agent therefore gets no notes of its own for now.
- `remember_turn` drops the `== ORCHESTRATOR` condition: extraction runs after every finished turn with an answer. Paused turns are still skipped. Extraction stays best effort and its tokens still go into the usage totals.
- `EXTRACT_PROMPT` gains a rule for notes: a note says how the user wants this agent to do its work (a correction, a default, a format, a scope), written as an instruction to the agent in English; facts about the user stay triples; most turns have no note. Final wording follows `writing-llm-document`. The prompt stays unformatted.
- `save_facts` keeps its signature for facts; a new `save_notes(user_id, agent, notes, conversation_id)` writes the nodes and edges of section 2 in one transaction, through `asyncio.to_thread`.
- Side effect, wanted: the handoff gap noted in the SOUL/BOOTSTRAP spec closes. A profile answer given in a turn a specialist answered is now saved.
- `scripts.build_graph` is unchanged. Past chats do not say reliably which agent answered each turn, so they yield facts only.

## 4. Snapshot and context

- `build_snapshot` (`app/core/snapshot.py`) adds `agent_notes: {agent: bullets}` from `recall_notes`, run through `asyncio.to_thread`.
- `chat_snapshot` treats a saved snapshot without the `agent_notes` key as stale and rebuilds it, as it does for `profile`. No migration.
- `AgentContext` gains `agent_notes: dict[str, str]` (empty by default). The name `notes` is taken: it holds the orchestrator's per-request notes. `routes_chat` and `scripts.bench_chat` fill it from the snapshot; agent servers receive it with the rest of the context.
- `build_agent` gains a keyword `route: str = ""` and renders `NOTES_SECTION` with `context.agent_notes.get(route)`. Each `agent.py` passes its `SPEC` route, the orchestrator passes `ORCHESTRATOR`.
- Notes learned in a chat reach that chat's prompt when its snapshot is next rebuilt (after `MEMORY_SNAPSHOT_MINUTES` without a turn), and every new chat at once. Within the chat the correction is in the history, as with facts.

## 5. Memories API

- `GET /api/memories` lists notes beside facts. A note row has the `noted` edge's id, the note text, `agent` (the route) and `created_at`; fact rows gain `agent: null`.
- `DELETE /api/memories/{id}` on a `noted` edge deletes the edge; orphan cleanup then deletes the note node (its `about` edges cascade), and `clear_snapshots(user_id)` runs. Checked by `user_id`, like facts.
- No UI change: nothing in `app/static/` calls `/api/memories` today.

## 6. Tests

Written from this spec, before the code.

- Layer files: `MEMORY.md` loads with exactly `notes`, `memories`, `recent_chats`; `USER.md` with exactly `user`, `profile`; `MEMORIES_SECTION` and `RECENT_CHATS_SECTION` equal their values on `main` (7f30d7b).
- `requires_db`, graph: saving a note creates the `agent` and `note` nodes and the `noted` and `about` edges; an empty `about` links the User node; a near-duplicate note replaces the old one and a different note is added; `recall_notes` is per agent, newest first, at most `RECALL_NOTES`; another user's notes never appear; a note without a `noted` edge is dropped; `recall_profile`, `recall_facts` and fact rows of `list_facts` hold no note text.
- Extraction: a `FactUpdate` without `notes` parses; more than 3 notes or an over-long note is refused by the schema; notes are saved under the answering agent; an unknown agent name saves none; a turn a specialist answered runs extraction once.
- Prompt: the sql agent's system prompt holds sql's notes and not chart's; with no notes the system prompt equals the one built without `agent_notes`.
- Snapshot: a saved snapshot without `agent_notes` is rebuilt; deleting a note clears the user's snapshots.
- API: listing returns notes with their agent; deleting another user's note is refused.

DB tests create and delete only their own test users' rows (the dev database is shared).

## 7. Measurement

`bench_chat` spends quota; the user is asked before each run.

1. `SAME_NOTE`: embed pairs of notes that say the same thing in other words and pairs that differ, and pick the threshold between them. The pairs and numbers go in the PR.
2. `bench_chat` on `main` and on the branch, same scenarios and models: model calls per message and spend per chat. Expected: one more call on messages a specialist answered, none elsewhere. Reported in the PR description.
3. A new scenario: in chat 1 the user corrects the sql agent; chat 2 asks a question the note applies to and the answer follows it.
4. A manual check in the running app, in English and Vietnamese.

## Docs

- `ARCHITECTURE.md`: the MEMORY layer file, the two node kinds and edges, extraction on every turn.
- `.claude/rules/memory.md`: notes in the graph, `recall_notes`, `save_notes`, the snapshot key, the removed orchestrator-only condition and the closed handoff gap.
- `.claude/rules/agents.md`: `AgentContext.agent_notes` and the `notes` section.
- `CHANGELOG.md` at release: agent memory under `0.7.0`, with the measured cost.

## Out of scope

- Notes from past chats (`scripts.build_graph`).
- `search_memory` matching note text directly; notes are reached through the entities they are about.
- The orchestrator reading the specialists' notes.
- Agents saving notes through a tool.
- A memories dialog in the UI, and editing a note's text.
