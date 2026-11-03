# Prompt layers as Markdown files

Date: 2026-11-03. Branch `0.7.0/prompt-files`. Status: design approved in chat, spec under review.

## Goal

Move the text of every prompt layer out of Python string constants into Markdown files named after the layer (SOUL, AGENTS, USER, BOOTSTRAP), so the persona and agent instructions are easy to read, tune and diff. The file tree mirrors the layer design agreed on 2026-10-29: tools -> SOUL -> AGENTS -> USER -> MEMORY -> BOOTSTRAP -> history -> tail note.

This is a refactor: every model request must stay byte-for-byte the same as on `main` (3f872b1).

## Decisions

- **Approach: load at import.** Each file is read once when its `prompts.py` is imported, into the constant names used today. Rejected: a per-request `PromptStack` (hot edits only help in dev, the agent servers bake `app/` into the image anyway, a mid-chat edit would change a chat's system prompt and break the cache layout, and it costs a disk read per call with no speed gain).
- **USER.md is a template, not data.** The knowledge graph stays the only store of user facts. USER.md holds the headings and rules with `{user}`, `{profile}`, `{memories}`, `{recent_chats}` slots; the per-chat frozen snapshot (`app/core/snapshot.py`) fills them in `build_agent` as now. Fact extraction keeps writing triples to the graph; nothing writes to USER.md.
- **MEMORY.md means the agent's own memory** (per agent, per user), a new feature. It is out of scope here (see "Next item"); no MEMORY.md file is created in this branch.
- **AGENTS.md is shared plus per agent.** A shared file holds the rules every agent gets; each agent has its own file for its role, tools and schema.
- **Version:** the user classed this as an architecture change, so it ships in minor `0.7.0` (shared with whatever else deploys in that release, per `.claude/rules/changelog.md`).

## File layout

```
app/core/layers/               # not app/core/prompts/: a folder of that name would shadow prompts.py
  SOUL.md                      # SOUL
  AGENTS.md                    # sections: language (LANGUAGE_RULE), memory (MEMORY_RULE)
  USER.md                      # sections: user, profile, memories, recent_chats
app/orchestration/AGENTS.md    # ORCHESTRATOR_SYSTEM body (after SOUL)
app/orchestration/BOOTSTRAP.md # BOOTSTRAP_SECTION body
app/agents/docs/AGENTS.md      # DOCS_SYSTEM body
app/agents/sql/AGENTS.md       # SQL_SYSTEM body
app/agents/chart/AGENTS.md     # CHART_SYSTEM body
app/agents/research/AGENTS.md  # RESEARCHER_SYSTEM (no SOUL: the researcher's text is never shown to the user)
```

Both Docker images copy all of `app/` and `.dockerignore` excludes only `CLAUDE.local.md`, so the files ship without build changes.

## Loader

Two functions in `app/core/prompts.py`:

- `load_prompt(path) -> str`: reads the file as UTF-8 and removes the single trailing newline an editor adds.
- `load_sections(path) -> dict[str, str]`: splits a file at lines of the form `<!-- section: <name> -->` and returns each section's text, trimmed of the blank line after the marker and of the trailing newline. A file with a marker missing or duplicated raises at import.

Files never start with a blank line. Separators that constants carry today (the leading `"\n\n"` of `USER_SECTION`, `PROFILE_SECTION`, `MEMORIES_SECTION`, `RECENT_CHATS_SECTION`, `BOOTSTRAP_SECTION`, and the `"\n\n"` between SOUL and an agent body) stay in Python, for example:

```python
SOUL = load_prompt(LAYERS / "SOUL.md")
SQL_SYSTEM = SOUL + "\n\n" + load_prompt(HERE / "AGENTS.md")
```

Rules carried over unchanged:

- Constant names stay the same, so `agent.py`, `app/agents/factory.py` and `app/orchestration/orchestrator.py` do not change.
- Placeholders (`{today}`, `{agents}`, `{web_search}`, `{language_rule}`, `{memory_rule}`, `{sql_rules}`, `{schema}`, the USER slots) stay in the files and are still filled by the existing `.format()` calls. Literal braces in a formatted file are doubled; files that are never formatted (SOUL, BOOTSTRAP, the shared AGENTS sections) keep single braces.
- Each agent file keeps its `{language_rule}` / `{memory_rule}` slots, so the agent still decides where the shared rules sit (SQL puts the schema after them).
- `DOCS_SYSTEM` is an f-string today; its `prompts.py` formats the loaded text with `language_rule` and `memory_rule` instead, so its value is unchanged.

## What stays in Python

Text that code builds per call or per tool, not a layer: `REPLY_LANGUAGE`, `LAST_CALL_NOTE`, `SAME_RESULT`, `CALL_NOTE`, `COMPOSE_SECTION`, the TEAM_* / ROUND / CONFLICT / CHALLENGE / TASK / DRAW / FINDINGS / ATTEMPT messages, `PLAN_PROMPT`, `DIFFICULTY_LEVELS`, the short notes (`UNAVAILABLE_NOTE`, `MENTIONS_NOTE`, ...), `ROUTE_DESCRIPTION`, `TOOL_DESCRIPTION`, `REVIEW`, `EXTRACT_PROMPT`, `SQL_RULES`, `TABLES_SECTION`, and the researcher's source lines and headings.

## Proof of no change

1. **One-off identity check.** A script in the scratchpad (not committed) imports every layer constant (`SOUL`, `LANGUAGE_RULE`, `MEMORY_RULE`, `USER_SECTION`, `PROFILE_SECTION`, `MEMORIES_SECTION`, `RECENT_CHATS_SECTION`, `ORCHESTRATOR_SYSTEM`, `BOOTSTRAP_SECTION`, `DOCS_SYSTEM`, `SQL_SYSTEM`, `CHART_SYSTEM`, `RESEARCHER_SYSTEM`) and writes them to JSON. Run it on `main` and on the branch; the two outputs must be identical. The verifier quotes the diff result.
2. **New permanent tests** (`tests/test_prompt_files.py`):
   - every layer file listed above exists and loads;
   - every expected section of the shared AGENTS.md and USER.md is found, and a missing or duplicated marker raises;
   - no layer file starts with a blank line, and `load_prompt` drops exactly one trailing newline.
3. **Existing tests pass unchanged**, among them `tests/test_prompt_layers.py` and `tests/test_orchestrator.py`.
4. **No `bench_chat` run:** identical bytes mean identical model input, so calls per message do not change.

## Docs

- `CLAUDE.md`: the uvicorn command gains `--reload-include "*.md"` so saving a layer file reloads the app in dev (`--reload` watches only `.py`).
- `ARCHITECTURE.md`: where the layer files live and how they are loaded.
- `.claude/rules/agents.md`: a new agent adds its own `AGENTS.md` next to `agent.py`.
- `CLAUDE.local.md`: the `writing-llm-document` scope gains the layer `.md` files.
- `.claude/rules/changelog.md`: the minor list gains this refactor under `0.7.0` when it is released.

## Out of scope

- Changing any prompt wording.
- Moving the per-call messages and descriptions listed under "What stays in Python".
- Fixing the orchestrator's date being set at startup (open bug, separate branch).

## Next item: agent memory (MEMORY.md)

Brainstormed as its own spec after this one ships. Agreed so far: each agent keeps notes on working with one user, stored per `user_id` and agent. Known constraints: agent servers run as `agent_reader` and cannot write, so the main app writes the notes; to avoid an extra model call per turn, consider extending the existing fact-extraction call to return them; the notes join USER in the per-chat frozen snapshot and fill a MEMORY.md template slot in `build_agent`.
