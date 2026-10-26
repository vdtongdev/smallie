# Docs refresh (0.5.1)

Goal: remove outdated and redundant statements from the docs found in the 2026-10-26 docs review. Docs-only, no code changes. Branch `0.5.1/docs-refresh` (from `0.5.0/agent-team`, because `main` does not have the 0.5.0 docs yet).

## Task 1: fix the docs

CLAUDE.md (load the `writing-llm-document` skill first):
1. Memory: "Pydantic is the filter (snake_case relation, ≤ 5 triples)" -> ≤ 8 (`MAX_FACTS = 8`, `app/core/graph.py:51`).
2. Agent construction: `AgentContext(user_id, user, memories, recent_chats, agent, notes)` -> add `mentioned`, `sources`, `failed` (`app/core/memory.py:44-52`).
3. "Its section 10 tracks the 0.4.0 remote-agents phases" -> section 10 describes the remote agents (0.4.0, released).

ARCHITECTURE.md:
4. Line 5: drop "work in progress"; 0.4.0 is released and deployed.
5. §10 heading "Remote Agents (0.4.0, in progress)" -> "Remote Agents (0.4.0)". Update README.md's link anchor to match (`#10-remote-agents-040`).
6. §10: remove the "Status" table (phases, loop audit) and shorten "Messages and context: before and after" to a description of how it works now (no 0.3.x column). Keep every current fact in it, including that only `messages`, `findings` and `tools` reach the parent state (`app/agents/remote.py:166`).
7. §2 step 2: the `AgentContext(...)` list, same fields as item 2.
8. §7 Performance: "Total: roughly 5–20 s" is wrong; replace with the measured 126–156 s when research took part (§11, relay measurement 2026-10-24) or drop the line. Say where the number comes from.
9. §4 Cost: drop "Free tier cost: ~$0.000005–$0.00003 per message" (no source; free models cost $0).
10. §11 measured table: column header "Normal relay (replaced design)" -> "Relay (replaced design)".

README.md:
11. Mention team mode: one line in the intro (switch next to the message box; agents answer independently, a judge compares), its settings `TEAM_MAX_ROUNDS`, `TEAM_MAX_MESSAGE_MODEL_CALLS`, `OPENROUTER_JUDGE_MODEL` in "Models and cost" with the measured call counts (6 calls stock question, 9 with a conflict), and a link to ARCHITECTURE.md §11 in "Architecture".

DEPLOYING.md:
12. §2 step 4: the main app's `cloudrun.env.yaml` needs only `TAVILY_API_KEY` (the orchestrator's web search); `MCP_SERVERS` / `EXTERNAL_DATABASE_URL` go only in `cloudrun.agent.research-secrets.yaml` (§3 step 3).
13. Remove §4 "Upgrading a database from before the knowledge graph" (one-time migration already run in production; `scripts.build_graph` stays documented in CLAUDE.md). Renumber later sections and fix links to them.
14. Remove §7 "Using your own database" (duplicate of README.md's section).

CHANGELOG.md:
15. 0.4.0: "steps in README \"Deploying\"" -> "steps in `DEPLOYING.md`".

## Task 2: LangSmith line and settings.json (user decisions, 2026-10-26)

1. CHANGELOG.md line 125 (0.4.0 Limits): the user checked LangSmith: a server's run shows nested under the main app's trace on the same thread, and also as its own trace. Replace "in LangSmith a server's run is its own trace, not nested under the main app's; " with "in LangSmith a server's run shows under the main app's trace and also as its own trace; ".
2. `.gitignore`: append `.claude/settings.json` (CRLF like the rest of the file). The file stays on disk, untracked.

Out of scope (user decides): LangSmith nesting contradiction (CHANGELOG 0.4.0 limit vs ARCHITECTURE §12), "0.6.0: image agent" in `.claude/rules/changelog.md`, old plans in `docs/superpowers/plans/`.

Acceptance: each item applied; no broken intra-repo markdown links (check every `](` link to a .md anchor); `git diff --stat` touches only CLAUDE.md, ARCHITECTURE.md, README.md, DEPLOYING.md, CHANGELOG.md (plus this plan). One commit.
