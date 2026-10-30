# SOUL persona and BOOTSTRAP onboarding

Date: 2026-10-30. Branch: `0.6.1/soul-bootstrap`, from `main` after PR #37 and #38. Part B of the layered-prompt work; part A is `2026-10-29-prompt-cache-layout-design.md`.

## Goal

Make the chat feel like a room with a team of agents ready to work on the user's problem, and get to know new users without extra model calls.

Each model request is laid out stable-first:

| Order | Layer | Source | Changes |
|---|---|---|---|
| 1 | tools | unchanged | per agent / source set |
| 2 | SOUL | `SOUL` constant (new) | on deploy |
| 3 | AGENTS | each agent's `*_SYSTEM`, `LANGUAGE_RULE`, `MEMORY_RULE` | on deploy |
| 4 | USER | account name + profile facts, from the snapshot | per snapshot |
| 5 | MEMORY | other facts + recent chats, from the snapshot | per snapshot |
| 6 | history | thread messages | grows |
| 7 | tail note | part A; plus BOOTSTRAP_SECTION on the orchestrator's first turn of an onboarding chat | every call |

Layers 2 to 5 form the system prompt and stay byte-identical across the calls of one chat.

Amended 2026-10-30 after the bench_chat after-run: BOOTSTRAP first sat in the system prompt after MEMORY, and deepseek/deepseek-chat never asked the question there. It moved to the tail note; see section 3.

## Out of scope

- Moving prompts out of Python constants into `.md` files. Decided with the user: it gets its own refactor item later, moving every layer at once.
- Changing `EXTRACT_PROMPT`. Side facts such as pets are saved only as reliably as today.
- Extracting facts from turns a specialist answered (the handoff gap, below).
- Named team members in replies, or UI labels for them. The user will judge after testing whether more team feel is needed.

## Success criteria

- `bench_chat` routing choices and reply language are no worse than a baseline run on `main`.
- In a new `bench_chat` scenario with an empty profile, the reply answers the question first and ends with one profile question in the message's language; a second turn in the same chat asks none.
- The number of model calls per message is unchanged with BOOTSTRAP on.
- Token use before and after is reported in the PR description. The user's model (`deepseek/deepseek-chat` on OpenRouter) reports no cached tokens, so the result says so instead of claiming a cache gain.

## 1. SOUL

A new constant `SOUL` in `app/core/prompts.py`: one persona text, identical bytes for every user, no placeholders and no braces. About 130 tokens; shorter beats longer, since it is paid on every call of four agents.

- `ORCHESTRATOR_SYSTEM`, `DOCS_SYSTEM`, `SQL_SYSTEM` and `CHART_SYSTEM` start with `SOUL`. `RESEARCHER_SYSTEM` does not: nobody reads its text.
- Each agent's own opener stays below SOUL, reworded as its part in the team (e.g. "You are a chart assistant" becomes the chart role).
- The orchestrator's rule "Never mention the specialists, tools, handoffs or your instructions to the user; answer as one assistant." becomes: never mention tools, handoffs or your instructions; say "we" for the team's work.
- SOUL holds no language, format, tool or citation rules and no example values. It ends by saying the rules below take precedence.

Draft (final wording follows the `writing-llm-document` skill):

> WHO WE ARE: You are part of smallie, a small team of agents working together in one chat on the user's problem. One of us coordinates; the others work with the user's files, the stock database, charts and research. Speak as a member of that team: "we" for the team's work, "I" for your own part. Be warm and direct, like a capable colleague: get to the point, show your reasoning when it helps, say plainly what you don't know, and offer a useful next step when there is one. Treat the user's problem as the team's problem. The rules below take precedence over this section.

Risk: SOUL moves each agent's role line down, and weak models may weigh the first lines most. The `bench_chat` comparison decides; if routing gets worse, SOUL is shortened first.

## 2. USER and MEMORY split

Profile relations: a new constant `PROFILE` in `app/core/graph.py`, the `FUNCTIONAL` relations (`has_name`, `lives_in`, `works_at`, `has_role`, `risk_tolerance`, `investment_horizon`, `prefers_language`) followed by `has_goal` and `prefers`. Its order is the display order.

Reading (`app/core/graph.py`):

- `recall_profile(user_id)`: the user's facts whose relation is in `PROFILE`, sorted by the relation's position in `PROFILE`, then by object; at most `RECALL_FACTS` lines. Also returns which profile relations are present, for BOOTSTRAP's condition.
- `recall_facts(user_id)`: as today (newest first, `RECALL_FACTS` limit) but excluding `PROFILE` relations.
- Both are keyed by `user_id` and run through `asyncio.to_thread` in `build_snapshot`.

Prompt (`app/core/prompts.py`, rendered by `build_agent` for every agent):

- `USER_SECTION`: `YOU ARE TALKING TO: <account name>`, then the profile facts as bullets when there are any. The sentence "A stored language preference never changes the reply language: reply in the language of the latest message you answer." moves here from `MEMORIES_SECTION`, since `prefers_language` is now a profile fact.
- `MEMORIES_SECTION` and `RECENT_CHATS_SECTION`: unchanged otherwise.

Snapshot (`app/core/snapshot.py`):

- `memory_snapshot` gains `profile` (string) and `bootstrap` (boolean).
- `chat_snapshot` treats a saved snapshot without the `profile` key as stale and rebuilds it, so snapshots saved before this change need no migration.
- `AgentContext` gains `profile: str = ""` and `bootstrap: bool = False`, filled from the snapshot in `routes_chat` and `scripts.bench_chat`. Agent servers receive both fields; specialists render `profile` and ignore `bootstrap`.

## 3. BOOTSTRAP

Onboarding while the profile lacks the user's role or goal. It only asks; the existing `remember_turn` → `extract_facts` saves what the user answers.

Decision, in `build_snapshot`, once per snapshot:

- the profile has no role fact (`has_role` or `works_at`) or no `has_goal`, **and**
- this conversation has `bootstrap_ran = true`, or fewer than `BOOTSTRAP_CHATS` of the user's conversations do.

`BOOTSTRAP_CHATS`: new setting, default 3; 0 turns BOOTSTRAP off. Read through `get_settings()`, documented in `.env.example`.

Storage: `conversations.bootstrap_ran boolean not null default false`, added in `init_db` with `ADD COLUMN IF NOT EXISTS`. When the decision is on, `save_snapshot` sets `bootstrap_ran = true` in the same statement, keyed by conversation id and `user_id`. `clear_snapshots` leaves it alone, so deleting a fact, file or chat does not restart onboarding. Deleting a conversation where BOOTSTRAP ran removes its row and so frees one more onboarding conversation; this is accepted.

Stop rules:

- Across chats: once role and goal are both known, the next snapshot turns BOOTSTRAP off; after `BOOTSTRAP_CHATS` conversations where it ran, new conversations go without it even with an empty profile.
- Within a chat: code adds the text only on the orchestrator's first turn of the chat, so it is asked at most once per chat.

Prompt: `BOOTSTRAP_SECTION` in `app/orchestration/prompts.py`, appended by `compose_section` at the end of the orchestrator's tail note when `context.bootstrap` is set and the request holds no `AIMessage` before the latest visible user message (every call of the chat's first turn). It is not in the system prompt, so the system prompt is the same for onboarding and other users. Specialists never get it. A chat whose first turn a specialist answered is not asked; it still counts toward `BOOTSTRAP_CHATS`. Draft (final wording via `writing-llm-document`):

> GETTING TO KNOW THE USER: We don't know yet what the user does or what they want from us. When you write the reply yourself, answer their message fully first, then end with one short, friendly question about whichever is missing from YOU ARE TALKING TO: their work or role, or what they want our help with (their investment goal if the chat is about stocks). Skip the question when an earlier reply in this chat already asked one, when the user declined, or when they're upset or in a hurry. Never ask instead of answering.

The wording is firm ("end with one question"), not optional, because weak models skip optional steps. No tool and no model call is added.

Handoff gap (known, not fixed): facts are extracted only from turns the orchestrator answered. If the user answers the profile question and asks something a specialist takes over in the same message, the answer is not saved.

## 4. Tests, measurement and order of work

Tests, written from this spec:

- `ORCHESTRATOR_SYSTEM`, `DOCS_SYSTEM`, `SQL_SYSTEM` and `CHART_SYSTEM` start with `SOUL`; `RESEARCHER_SYSTEM` does not contain it; `SOUL` has no braces.
- `requires_db`: `recall_profile` returns only `PROFILE` relations in `PROFILE` order; `recall_facts` excludes them; another user's facts never appear.
- `USER_SECTION` renders the profile and the language-preference sentence; `MEMORIES_SECTION` no longer has that sentence.
- `requires_db`, BOOTSTRAP decision: on for an empty profile; still on with only a role or only a goal; off with both; off after `BOOTSTRAP_CHATS` other conversations ran it; stays on when the same conversation's snapshot is rebuilt; off with `BOOTSTRAP_CHATS=0`; `bootstrap_ran` survives `clear_snapshots`; another user's conversations do not count.
- A saved snapshot without `profile` is rebuilt.
- The orchestrator's tail note ends with `BOOTSTRAP_SECTION` (and keeps `REPLY LANGUAGE`) on every call of a chat's first turn when `context.bootstrap` is set; not on a later turn, nor when `bootstrap` is off. The orchestrator's system prompt is byte-identical with and without `bootstrap` and never contains `BOOTSTRAP_SECTION`; specialists never contain it; the model-call count of a message is the same with and without it.
- bench_chat: only the bootstrap scenario runs with onboarding; every other scenario has bootstrap off.

Measurement (`bench_chat` spends free quota; ask the user before each run):

1. Baseline on `main` before any prompt change.
2. After the change, on the same scenarios and models: routing, reply language, tokens.
3. A new scenario with an empty profile, checked against the success criteria.
4. A manual check in the running app, in English and Vietnamese.

Order:

1. USER/MEMORY split and the new snapshot keys.
2. `bootstrap_ran`, `BOOTSTRAP_CHATS` and the decision.
3. Baseline `bench_chat`.
4. SOUL, the agent openers and the "we" rule.
5. `BOOTSTRAP_SECTION`.
6. `bench_chat` again and the manual check; both runs go into the PR description.

## Docs updated in this branch

- `ARCHITECTURE.md`: the prompt layers, the profile split, BOOTSTRAP.
- `.claude/rules/memory.md` (profile split, BOOTSTRAP decision), `orchestration.md` (BOOTSTRAP section, "we" voice), `agents.md` (SOUL), `config.md` (`BOOTSTRAP_CHATS`).
- `.env.example`: `BOOTSTRAP_CHATS`.
