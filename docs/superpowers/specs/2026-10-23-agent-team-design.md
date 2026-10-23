# Agent team design (0.5.0)

Date: 2026-10-23. Branch: `0.5.0/agent-team`. Status: approved.

## Goal

The specialists work as a team with the user in the loop: one agent drafts an answer, a peer critiques it with its own tools, the author refines it, and the orchestrator writes one final reply. A chat room is where this happens; later several people share one room.

Built in two phases:

- **Phase C (spike):** the debate loop in today's single-user chat, behind a per-conversation "team mode". Proves the loop, its cost and its UI before rooms exist.
- **Phase A:** shared rooms (members, the room's agent team, live fan-out) and room memory in the knowledge graph.

Both ship as 0.5.0 (an architecture change: how agents talk to each other). Image agent takes the next minor when it deploys.

## Constraints

- **No new agent roles.** The team is the existing specialists (docs, sql, chart, research) plus the orchestrator as lead.
- **Any future agent joins without code changes** outside its own package: no hardcoded agent pairs.
- **Free-tier models:** they skip optional steps and drop exact tokens (traps `weak-models-optional-steps`, `extra-model-calls`). Every decision the loop depends on is a structured tool-call field, never a hoped-for optional call or a magic string.
- **The checkpointer thread stays the only copy of a conversation.** Remote agents stay stateless `RemoteAgent` calls; no message broker between agents.
- **Everything stays `user_id`-scoped** (CLAUDE.md "Accounts and user isolation").

## Phase C: the debate loop

### Turning it on

- `POST /api/chat/team` starts or continues a team-mode conversation. `conversations.kind = 'team'` stores the mode (no schema change). `chat()` accepts `team` next to `auto` and the routes.
- `AgentContext.agent == "team"` makes the router return the `team` route. Normal chat is unchanged.

### The `team` subgraph (`app/orchestration/team.py`)

A `StateGraph` added to the assistant graph as node `team`. Its state is a `team` key on `AssistantState`, saved by the checkpointer:

```
team = {
  phase: "delegate" | "critique" | "refine" | "synthesize",
  author: <route> | "orchestrator",
  critic: <route> | None,
  draft: str,            # latest author answer
  critiques: [{critic, text, turn}],
  verdicts: [{action, critic, issues, turn, forced}],
  turns: int,            # internal agent-to-agent messages so far (critique and refine each count 1)
  question: str          # open needs_human question, if any
}
```

State machine:

```
delegate ──► judge₀ ─┬─ approve ─────────────────────────► synthesize ──► END
                     ├─ needs_human ─► interrupt() ─► judge₀
                     └─ critique(critic=X) ─► critique ─► judge ─┬─ approve ─► synthesize
                                                ▲                ├─ needs_human ─► interrupt() ─► judge
                                                │                ├─ revise (+ next critic) & turns < max ─► refine ─┐
                                                │                └─ revise & turns ≥ max ─► synthesize (forced)    │
                                                └──────────────────────────────────────────────────────────────────┘
```

| Node | Runs | Does |
|---|---|---|
| `delegate` | the existing route decision | An obvious specialist (`obvious_route`) answers, else the orchestrator (which may ask several via `ask_<route>`). Its final answer becomes `draft`, `author` is who wrote it. |
| `judge` | one structured call, orchestrator model | `with_structured_output(Verdict, method="function_calling")` (as `extract_facts` does). judge₀ runs right after `delegate`; later judges after each critique. |
| `critique` | the chosen critic (`RemoteAgent`) | Gets the thread plus a hidden `CRITIQUE_MESSAGE` with the draft: check it with your tools, list concrete problems or say it holds. Free text. |
| `refine` | the author | Gets the thread plus a hidden `REFINE_MESSAGE` with the draft and the judge's `issues`. Its answer replaces `draft`. Goes straight to the next `critique` (no judge in between). If the author is the orchestrator, it re-runs with the issues. |
| `synthesize` | the orchestrator node | Writes the reply under `COMPOSE_SECTION`, with draft, critiques and open issues in a hidden `TEAM_MESSAGE` (like `FINDINGS_MESSAGE`). A forced stop states what stayed unresolved. |

`Verdict` (Pydantic):

```python
action: Literal["approve", "critique", "revise", "needs_human"]  # judge₀ allows approve|critique|needs_human
critic: Literal[<allowed routes>] | None   # built per request
issues: list[str]                          # for refine; ≤ 5
question: str                              # for needs_human
```

### Choosing the critic: generic, no pairs

- New optional `AgentSpec.review: str`: one line on what the agent can verify (research: facts and figures against the web, Wikipedia, arXiv; sql: stock and company figures against the market database; docs: claims about the user's files; chart: the query and numbers behind a chart). Empty = never a critic.
- Allowed critics per request: routes with `review`, available to this user (`route_info` not `None`, e.g. docs only with files), not the draft's author. Their `review` lines go into the judge's prompt; the `Literal` is built from them.
- Code checks the verdict: a critic outside the allowed list or equal to the author becomes the first allowed critic; with no allowed critics judge₀ can only approve; `revise` before any critique becomes `critique`.
- A new agent becomes a reviewer by setting `review`. Nothing else changes.

### Guardrails

- **Turn cap:** `TEAM_MAX_TURNS` (default 4) internal messages. At the cap a `revise` becomes a forced synthesis (`verdicts[-1].forced = true`).
- **Human pause:** `needs_human` calls `interrupt({"question": ...})`. The next `POST /api/chat/team` on that conversation finds the pending interrupt in the state snapshot (`assistant.aget_state(config)`; the exact attribute is checked against the pinned LangGraph version) and streams `Command(resume=<message>)` instead of a new input. The reply joins the thread as a human message; the judge runs again with it.
- **Budget:** `TEAM_MAX_MESSAGE_MODEL_CALLS` (default 30) replaces `MAX_MESSAGE_MODEL_CALLS` for team-mode messages (the `RemoteAgent` check reads the one that applies). A full two-round debate is about 12–25 calls: judge₀ 1, per round critique 2–5 + judge 1 + refine 2–5, synthesis 1. At ~20 requests/min on the free tier one team message can take over a minute.
- No `fallback` node in team mode; the judge covers a draft that found nothing.
- `no_repeats` and `MAX_ASKS` still apply inside the orchestrator's own runs.

### What enters the thread

Only the synthesized answer joins `messages`. The draft, critiques and verdicts go on its `additional_kwargs["team"]` (as charts do), so they show on reload and never reach later prompts. `langchain_openai` already drops unknown `additional_kwargs` keys.

### SSE events

New, from `_to_events` (team node updates):

```jsonc
{"type": "team",    "phase": "delegate|critique|refine|synthesize", "agent": "<route>", "turn": 1, "max_turns": 4}
{"type": "verdict", "action": "approve|critique|revise|needs_human", "critic": "<route>|null", "issues": ["…"], "turn": 2, "forced": false}
{"type": "needs_human", "question": "…"}   // then `done`; the turn is paused
```

Tokens keep the `token` event; the UI writes them into the block of the latest `team` phase (phases run one at a time). Tool events stream as today. The namespace filter must not silence team phases: no inner node is named like a route or `tools` (trap `workflow-node-names`).

### UI (`app.js`, `i18n.js` EN/VI, `style.css`)

- A "Team mode" switch next to "New chat" sets the kind of the next new conversation; an existing conversation keeps its kind. The sidebar marks team chats.
- Per reply, a collapsible "Team discussion · N rounds" section above the answer: one entry per phase (agent badge, phase label, text) with verdict lines between them. Open while streaming, collapsed when synthesis starts. The answer streams into the normal bubble with charts and citations.
- `needs_human`: the question shows as an assistant message marked "waiting for you"; the input placeholder changes; sending resumes the debate.
- Reload: `_serialize` exposes `additional_kwargs["team"]`; the section is rebuilt from it.
- First version on purpose; adjust after use.

### Phase C tests

- `team.py` with in-process fake agents (`local_agents()` pattern): approve at judge₀ (no critique), one round then approve, cap reached → forced synthesis, `needs_human` → interrupt → resume, invalid critic coerced, no reviewers → approve only.
- Only the synthesized message joins `messages`; `additional_kwargs["team"]` holds the transcript.
- `_to_events` yields `team`/`verdict`/`needs_human` and keeps team phase tokens.
- Router: `agent="team"` → `team`. `chat()` accepts `team` and resumes a pending interrupt instead of starting a new turn.
- Budget: team messages use `TEAM_MAX_MESSAGE_MODEL_CALLS`.
- `scripts.bench_chat`: one team scenario to measure calls and latency on the real models.

## Phase A: shared rooms

A room is a `kind='team'` conversation with members. History stays in its checkpointer thread.

### Tables (`app/core/db.py`)

```
room_members (conversation_id FK→conversations ON DELETE CASCADE,
              user_id FK→users ON DELETE CASCADE,
              role 'owner' | 'member', joined_at,
              PRIMARY KEY (conversation_id, user_id))
room_agents  (conversation_id FK→conversations ON DELETE CASCADE, route,
              PRIMARY KEY (conversation_id, route))   -- no rows = every registered agent
```

No `rooms` table (the conversation is the room) and no `messages` table (it would be a second copy of the thread). Add one only if search or unread counts are needed.

### Behaviour

- **Authorship:** each human message carries `additional_kwargs = {"user_id", "name"}`; the UI shows the sender, the synthesizer knows who asked.
- **Agent team:** `room_agents` limits the router, the `ask_<route>` tools offered and the judge's critics to the room's agents. Roles and skills come from `AgentSpec` (`description`, `review`).
- **Access:** `get_conversation(user_id, id)` becomes owner-or-member; every conversation route goes through it. Deleting a room is owner-only.
- **Per-sender context:** each turn runs with `AgentContext.user_id = sender`: docs searches the sender's files, memories are the sender's. Files are not shared with the room.
- **One run per room:** a Postgres advisory lock on the conversation id; a send during a run gets `409` ("the team is still working") and the UI keeps the draft.
- **Fan-out:** the sender's request streams as today; other members subscribe to `GET /api/rooms/{id}/events` (SSE) and get the same events plus `{"type": "human", "user", "text"}`. In-process `asyncio` broadcast (room id → subscriber queues), marked `shortcut: single instance (--max-instances 1), move to Postgres LISTEN/NOTIFY when max-instances > 1`. Late joiners load history via the messages endpoint, then subscribe; clients refetch on `done`.
- **Human pause in a room:** any member's reply resumes, recorded with their name.
- **Invites:** the owner adds members by username, `POST /api/rooms/{id}/members`, capped by `ROOM_MAX_MEMBERS` (default 5).

### Room memory in the knowledge graph

- New node kind `room` in `graph_nodes` (carries `conversation_id` and title). Edges: `User -member_of-> Room`, `Room -<relation>-> Entity`. The keep rule becomes "touches the User, a file or a room node".
- Still one extraction call per turn (`remember_turn`, which runs after orchestrator answers, including team synthesis). In a room, `EXTRACT_PROMPT` gets a room subject: what the team settled or the room is about uses `"room"` as subject; words about the sender still mean the User node. `save_facts` resolves `"room"` to the room node.
- Room facts are written into every member's graph (one room node per member), so every query keeps its `user_id` filter. Personal facts go to the sender only.
- `save_turn` indexes each room turn under every member's `("users", user_id, "history")`; `load_recent_chats` includes rooms the user belongs to. So `search_memory` and `search_chat_history` answer room details from any other chat.
- The recall section lists the user's newest `member_of` rooms.

### Phase A tests

- Membership: a non-member gets 404 on every conversation route; a member can read, send and resume.
- Per-sender scoping: a docs search in a room uses the sender's files only.
- Lock: a second send during a run gets 409.
- Fan-out: a subscriber receives the run's events and the `human` event.
- Graph: a room fact lands in every member's graph, a personal fact only in the sender's; `search_memory` finds a room fact from another conversation.
- `room_agents` limits the critics and ask tools.

## Out of scope

- Memory cleanup when a room or chat is deleted, or when information changes (separate task).
- New agent roles; shared documents; per-room memory beyond the graph; typing indicators, read receipts, roles beyond owner/member.
- Pub/Sub between agents; multi-instance fan-out.

## Docs to update with the work

- `ARCHITECTURE.md`: the team loop, rooms, new tables and events.
- `CLAUDE.md`: team subgraph, `AgentSpec.review`, new settings, rooms and room memory.
- `.env.example`: `TEAM_MAX_TURNS`, `TEAM_MAX_MESSAGE_MODEL_CALLS`, `ROOM_MAX_MEMBERS`.
- `.claude/rules/changelog.md`: `0.5.0` = agent team, image agent = next minor.
