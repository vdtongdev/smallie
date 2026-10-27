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

> **Superseded in part (2026-10-24):** the author → critic → refine relay below was built, measured and replaced by the round table in "Phase C v2". Kept from it: team mode and `TEAM_MODE`, `AgentSpec.review`/`sources`, the judge as one structured call, the four conflict rules, `interrupt()`/resume, `TEAM_MAX_MESSAGE_MODEL_CALLS`, the transcript on `additional_kwargs["team"]`, custom stream events. Where the two differ, v2 wins.

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
action: Literal["approve", "critique", "revise", "needs_human"]  # judge₀ may also revise (e.g. after the user's clarification)
critic: Literal[<allowed routes>] | None   # built per request
issues: list[str]                          # for refine; ≤ 5
question: str                              # for needs_human
```

### Choosing the critic: generic, no pairs

- New optional `AgentSpec.review: str`: one line on what the agent can verify (research: facts and figures against the web, Wikipedia, arXiv; sql: stock and company figures against the market database; docs: claims about the user's files; chart: the query and numbers behind a chart). Empty = never a critic.
- Allowed critics per request: routes with `review`, available to this user (`route_info` not `None`, e.g. docs only with files), not the draft's author. Their `review` lines go into the judge's prompt; the `Literal` is built from them.
- Code checks the verdict: for `critique` or `revise`, a critic outside the allowed list or equal to the author becomes the first allowed critic. With no allowed critics, `critique` becomes `approve` and `revise` refines once, then synthesizes.
- Turn order: `refine` goes straight to `critique` while `turns < TEAM_MAX_TURNS`; a refine that reaches the cap goes to `synthesize` (forced; the last refinement is unreviewed). So the default cap allows critique, refine, critique, refine.
- A new agent becomes a reviewer by setting `review`. Nothing else changes.

### Independent critics and conflicts (added after the first measurement)

Measured on 2026-10-23 (free models, "AAPL vs MSFT market cap"): the judge picked chart to review sql both times. Chart reads the same database, so its critique could only confirm, and one run spent a second round (12 calls, 59 s) without changing the answer. The critic's chart also showed up twice in the reply.

- **Different sources only.** New `AgentSpec.sources: frozenset[str]` (docs `user_files`, sql and chart `market_db`, research `web`). A critic whose sources are all among the author's is not allowed. An orchestrator draft's sources are those of the agents it asked (recorded on its answer as `additional_kwargs["asked"]`); a draft from the model alone has none, so every critic is allowed.
- **No source is trusted by kind.** The market data and the user's files can be wrong (stale data, a wrong query, a file with wrong figures on purpose); nothing wins because of where it came from.
- **Debate only on a conflict.** After a critique the judge sorts the result:
  - agree → `approve`;
  - a difference both sides explain (as-of date, definition, scope) → `approve` with `explanation`; the answer states both values, each with its source and date, and why they differ;
  - a conflict the author can fix with its own tools (wrong query, wrong ticker, a claim its data doesn't support) → `revise` with `issues`;
  - a conflict nobody in the team can fix → `approve` with `explanation`; the answer shows both sides and says they disagree, without picking a winner.
- `Verdict` gains `explanation: str`. Critics state the source and date of every figure they check; sql states the as-of date of its figures (`trade_date`, profile `updated_at`).
- A critic's tool calls and charts stay in its discussion entry; they never reach the answer.

### Guardrails

- **Turn cap:** `TEAM_MAX_TURNS` (default 4) internal messages. At the cap a `revise` becomes a forced synthesis (`verdicts[-1].forced = true`).
- **Human pause:** `needs_human` calls `interrupt({"question": ...})`. The next `POST /api/chat/team` on that conversation finds the pending interrupt in the state snapshot (`assistant.aget_state(config)`; the exact attribute is checked against the pinned LangGraph version) and streams `Command(resume=<message>)` instead of a new input. The reply joins the thread as a human message; the judge runs again with it.
- **Budget:** `TEAM_MAX_MESSAGE_MODEL_CALLS` (default 30) replaces `MAX_MESSAGE_MODEL_CALLS` for team-mode messages (the `RemoteAgent` check reads the one that applies). A full two-round debate is about 12–25 calls: judge₀ 1, per round critique 2–5 + judge 1 + refine 2–5, synthesis 1. At ~20 requests/min on the free tier one team message can take over a minute.
- No `fallback` node in team mode; the judge covers a draft that found nothing.
- `no_repeats` and `MAX_ASKS` still apply inside the orchestrator's own runs.

### What enters the thread

Only the synthesized answer joins `messages`. The draft, critiques and verdicts go on its `additional_kwargs["team"]` (as charts do), so they show on reload and never reach later prompts. `langchain_openai` already drops unknown `additional_kwargs` keys.

### SSE events

New, written by the team nodes with LangGraph's `get_stream_writer()` (stream mode `custom`) and passed through by `_to_events`:

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

## Phase C v2: round table (replaces the relay)

### Why

The relay's evaluation on 2026-10-24 (free models, five team scenarios after the source fixes): 7 calls / 38 s for a stock question, but 126–156 s whenever research took part, because every step waits for the previous one; agents never see each other's work; research "independently" re-ran `run_sql` on the author's own table, because `sources` labelled agents but didn't limit their tools; the write-up re-emitted the whole draft (about 46 s); two English questions got Vietnamese drafts or answers; live and reload views differed; `needs_human` never fired.

### Flow

```
message ─► pick ─┬─ nobody picked ─────────────────────────────► orchestrator answers alone (as normal mode)
                 └─ participants ─► round 1: speak × N (parallel, own sources only)
                                        ▼
                                      judge ─┬─ agree / differences explained ─► write-up ─► END
                                             ├─ needs_human ─► interrupt() ─► judge
                                             └─ fixable conflicts & rounds < TEAM_MAX_ROUNDS ─► conflict round: speak × involved ─► judge
                                                (at the cap: write-up shows the open conflicts)
```

- **pick** (code, no model call). Team = every registered agent (phase A: `room_agents`) available to this user (`route_info` not `None`). An agent joins when the message mentions it (`AgentSpec.is_mentioned`); of an `overrides` pair only the winner joins. `AgentSpec.joins = "with_others"` (research) joins whenever any other agent participates; default `"mentioned"`. Nobody mentioned → the orchestrator answers alone, same cost as normal mode.
- **speak** (LangGraph `Send`, one task per agent, in parallel). Round 1: thread + hidden `ROUND_MESSAGE` (answer from your own sources only; cite source and as-of date; say plainly when your sources hold nothing on it; reply in the named language). Conflict round: only the agents in a conflict's `routes`, with hidden `CONFLICT_MESSAGE` (the other side's claim and evidence; correct yours, or say why yours stands).
- **Source scoping.** Every tool carries a `source` tag in its metadata (`run_sql`/`chart_sql` `market_db`, `search_documents` `user_files`, web and Wikipedia/arXiv tools `web`; memory and chart-from-data tools untagged). `AgentContext.sources` holds the sources an agent may use on this request (its spec's `sources` in team mode, empty = no limit otherwise); a middleware in `build_agent` hides tools whose tag isn't in it. Runs on the agent servers.
- **judge** (one structured call, orchestrator model): `Board = {agreements: [str], differences: [{routes, explanation, sides: [{route, value, source, as_of}]}], conflicts: [{routes, claim, fixable_by, issue}], question: str}`. Rules kept: a difference is explained only when both sides pull data from verified sources, including a date, and the explanation fits the size of the gap; an undated or unsourced figure that disagrees with dated sources is a conflict (code moves any difference with a side lacking `source` or `as_of` to `conflicts`); when several independent dated sources agree against one, the issue and the write-up say so;  no source is right because of its kind; only a conflict with someone in `fixable_by` (an involved route that can fix it with its own tools) gets another round; `question` only for information only the user has. Code drops `fixable_by` routes outside the conflict's `routes`.
- **write-up.** One participant and no conflict → its answer is the reply (no synthesis call). Otherwise the orchestrator writes one answer from the board (`TEAM_MESSAGE`): each source's value with its source and date, the explanations, open conflicts without picking a winner.
- **Language.** Every reply follows the language of the message it answers; when the user switches language mid-conversation, the next reply switches too (user decision, 2026-10-25). A stored preference such as a remembered "prefers Vietnamese" never overrides it. `ROUND_MESSAGE`, `CONFLICT_MESSAGE` and `TEAM_MESSAGE` name the reply language from `reply_language(messages)`, and the memories section says a stored language preference doesn't change the reply language. Traced cause of the drift: such a memory fact plus draft prompts that named no language; the draft's tokens then streamed into the answer bubble (fixed by round tokens going to the Activity popup).
- **Caps.** `TEAM_MAX_ROUNDS=4` conflict rounds after round 1 (replaces `TEAM_MAX_TURNS`); the evaluation measures what it costs. `TEAM_MAX_MESSAGE_MODEL_CALLS=30` stays the backstop: a call refused by it fails that agent's turn, and a failed turn forces the write-up with what stayed open. `round` numbers all rounds (1 = the answer round) and `max_rounds` is the total possible, `TEAM_MAX_ROUNDS + 1`. A failed board call (other than rate limit or auth errors) also forces the write-up from the answers so far. A team reply carries the tables and charts of every participant's answers (as `additional_kwargs`, like charts today).

### Events

Inside a round every `token`, `tool_call` and `tool_result` carries `"agent": "<route>"` (from the `speak` task's namespace); outside rounds they keep today's shape. If per-task namespaces can't be mapped to agents reliably, the fallback is: tool events live, each answer sent whole when its task ends.

```jsonc
{"type": "round", "round": 1, "max_rounds": 5, "kind": "answer|conflict", "agents": ["sql", "research"]}
{"type": "board", "round": 1, "agreements": ["…"],
 "differences": [{"routes": ["sql", "research"], "explanation": "…"}],
 "conflicts": [{"routes": ["sql", "docs"], "claim": "…", "fixable_by": ["sql"], "issue": "…"}], "forced": false}
{"type": "synthesize"}                          // only when the orchestrator write-up runs; a lone participant's standing answer sends none (its text arrives in `spoke`)
{"type": "needs_human", "question": "…"}
```

`team` and `verdict` events are removed.

### UI: minimal chat, activity popup (every chat, team or not)

- **Main window:** while running, one status line above the reply (agent badges, spinner, e.g. `Round 2 of 5 · resolving a conflict · sql, docs`; in normal mode the agent and its step). When done it becomes a short summary (e.g. `Team · 2 rounds · sql, research · 1 difference explained`), then the answer with charts and citations, and an "Activity" button with a step count.
- **Activity popup** (`<dialog>`, like the Readme dialog): the full log of that reply. Team mode, per round and agent: answer text, tool calls (name, arguments), tool results, then the board; the pause question if any. Normal mode: the tool calls and results that today render inline.
- **Reload matches live:** team transcript in `additional_kwargs["team"]` = `{rounds: [{round, kind, answers: [{agent, text, tools: [{name, args, result}]}]}], boards: [...], participants, forced}`; normal mode rebuilds from the thread's tool messages. The reply's badge is whoever answered (orchestrator, or the single participant).
- Relay-era transcripts (two dev chats) may render without a discussion.

### Evaluation

The five team bench scenarios plus `team_conflict`: a small uploaded file stating a wrong market cap, asked together with that ticker, so docs and sql conflict. Measure calls, latency, rounds and conflicts at `TEAM_MAX_ROUNDS=4`; live UI pass (status line, popup, reload); the language-drift trace; another try at `needs_human`.

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
