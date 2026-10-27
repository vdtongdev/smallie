# Changelog

Versions are `major.minor.patch`: the major version stays 0 for now, each minor version adds agents, and each patch groups 2-3 bug fixes.

## 0.1.0 - Docs agent and SQL agent
- **Docs Agent**: RAG over uploaded PDFs/DOCX with pgvector indexing, citations with page numbers
- **SQL Agent**: Natural language queries against stock market schema with read-only role
- UI: Bilingual (EN/VI) chat interface with file upload, conversation history, Readme modal
- Memory: Short-term (LangGraph checkpointer), long-term (pgvector semantic store), cross-chat history
- Auth: Account system with username/password, admin panel for user management
- Stock data: Daily market data refresh via yfinance (weekday schedule)
- Deployment: Google Cloud Run with Neon Postgres

## 0.2.0 - Research agent and writer agent
- **Research Agent**: Multi-step workflow (researcher → report writer) with deep-linked citations, MCP tool integration (Tavily, Wikipedia, arXiv)
- **Writer Agent**: Formatting results from Research agent to write properly structured answers.
- **Project restructure for multiple agents**: `app/` is now split into `core/`, `agents/`, `features/`, `orchestration/` and `api/`, so a new agent needs no router, API, UI or `main.py` changes
  - Agent registry: each agent is an `AgentSpec` (route, router description, UI labels, keyword hints, per-user routing data) listed in `app/agents/registry.py`; the router, graph, chat/conversation routes and UI badges are driven by it
  - Agent packages: each agent lives in `app/agents/<name>/` with `agent.py` (build + `SPEC`), `tools.py` and `prompts.py`; the shared agent builder moved to `app/agents/factory.py`
  - Features: documents and market data moved to `app/features/<name>/`, each declaring its API routes, an optional startup hook (market scheduler) and per-user cleanup (document chunks and uploads), so `main.py` and account deletion loop over `app/features/registry.py`
  - Core: config, LLM, database, memory, accounts, auth, conversations and the shared prompts moved to `app/core/`
  - Orchestration: the router and assistant graph moved to `app/orchestration/`

## 0.2.1 - Fixes
- **Fix: foreign words in Vietnamese answers**
  - Weaker models (mostly the Nemotron fallback, which answered most requests while the free Gemma was rate-limited) slipped single words of other scripts into Vietnamese answers, e.g. Korean 독者 ("reader"), Thai เด่น ("standing out"), Chinese 半 ("half")
    - Temperature lowered from 0.2 to 0, so the model is less likely to pick unlikely wrong-language words
    - `OPENROUTER_FALLBACK_MODEL` accepts a comma-separated list tried in order; the default puts the free Gemma 4 26B (good at Vietnamese) before Nemotron
    - Note: this only lowers the chance of untranslated words; it doesn't fully fix it. The cause is the models themselves, so Nemotron (and other weak-in-Vietnamese models) can still slip in words, e.g. Russian, whenever it answers
- **Fix: more foreign words from low-precision paid providers**
  - Paid models are routed to the cheapest provider by default, which for Gemma 4 31B is an fp4 build, and heavier quantization slips words of other scripts into Vietnamese answers more often
  - New `OPENROUTER_QUANTIZATIONS` (e.g. `bf16,fp8`) is sent as `provider.quantizations`; empty keeps any provider, which free endpoints need since they report no precision

## 0.3.0 - Orchestrator and chart agent
- **Orchestrator** (`app/orchestration/orchestrator.py`): the front door for every message that doesn't clearly point to one agent
  - Answers general questions itself (explanations, maths, writing, small talk), with a quick web search (Tavily or Wikipedia) when the facts may have changed
  - Hands a message off to one specialist (`handoff`), which then replies directly with the whole chat; a first step that asks just one specialist is turned into a handoff, because free models rarely choose it themselves
  - Asks several specialists in parallel (`ask_docs`, `ask_sql`, `ask_research`) and writes the combined answer with citations
  - Says plainly what it can't do (act outside the chat, real-time data, files not uploaded, images…) instead of guessing
  - Only its final answer is saved to the chat, so the specialists' results don't grow later prompts
- **Router without a model call**: obvious messages (a ticker, hint words, a file name) still go straight to their agent; everything else goes to the orchestrator. `OPENROUTER_ROUTER_MODEL` is replaced by `OPENROUTER_ORCHESTRATOR_MODEL` and `ORCHESTRATOR_MAX_MODEL_CALLS`
- **Research writer removed**: the researcher only gathers findings; the orchestrator writes them up with the writer's formats and citation rules, which are added to its prompt only while it writes up results
- **Questions for several specialists**: stock figures and anything in the user's files must come from the database and documents agents, so a mixed question (e.g. "which of the most valuable tech companies fits my resume?") asks both in parallel and gets one combined answer
  - The router no longer sends a message to one agent when it also mentions another agent's data (a word of one of the user's file names or "resume"/"CV" next to a ticker); `RouteInfo.mentions`
  - Asking a single agent isn't turned into a handoff then, and the orchestrator's prompt names the mentioned agents
  - Combining rules: one answer to the actual question, conclusion first, parts connected, each fact keeps its citation; docs is asked again (naming the file) before saying a listed file has nothing
- **Web search alongside Wikipedia**: the researcher runs a web search in the same step as every Wikipedia search (except for arXiv questions); the orchestrator's quick lookups do the same and prefer the newest results
- **Chart agent** (`app/agents/chart/`): line, bar, area, pie and scatter charts of the stock database, of an earlier query result ("plot that") or of numbers from another answer, with a 1-3 sentence takeaway
  - `chart_sql` writes the query and the chart in one call (~2 model calls, 9-16 s per chart in testing); `chart_table` reuses a result already in the chat without querying again; `chart_data` charts a few inline numbers
  - Data never passes through the model: query results and charts travel as hidden tool artifacts, the model only sees a short summary (first/last/min/max per series)
  - Routing: "chart", "plot", "graph", "biểu đồ", "đồ thị", "vẽ biểu đồ" go straight to it, also with a ticker ("chart AAPL"); a ticker alone still goes to the database agent
- **Table artifacts**: `run_sql` keeps the whole result (up to 2000 rows) as a table artifact while the model still sees the first `SQL_MAX_ROWS` rows; the orchestrator keeps its turn's tables and charts on its answer, so charts survive a reload and "plot that" works after a combined answer
- The orchestrator can chart the results of the agents it asked (e.g. database figures next to a resume)
- UI (`app/static/charts.js`): chart cards with a validated colour palette in light and dark mode, crosshair tooltips, a data table and PNG download
- Shared `SQL_RULES` for every agent that queries the stock database, now with a rule for PostgreSQL's `round(x::numeric, 2)`
- **Benchmark** (`scripts/bench_chat.py`): time to first event and first token, total time, model calls and output size per scenario on the real models

## 0.3.1 - Fixes
- **Fix: repeated chart requests queried the database again**
  - Asking for the same data again in a chat (repeated, reworded or as another chart type) now draws the earlier query result instead of querying again: the prompt lists which tables were already charted, and `chart_sql` reuses a table whose query is the same
- **Fix: slow research answers**
  - The researcher's notes were its slowest step (37-85 s), mostly spent writing full URLs and long quotes for every fact
  - It now cites pages by short ids (listed at the end of each tool result) with a length cap on its notes; code expands the ids before the deep links are built, so answers and links are unchanged. In testing the notes got 48% shorter and research answers about a third faster
  - Ids the model writes in an unexpected form are still expanded, and unknown ones are dropped, so the writer can't turn them into invented links
- **Fix: English research questions answered in Vietnamese**
  - Since the orchestrator took over the write-up, the English case was only "the language of the user's latest message" (and its last user-role message is the hidden findings), placed in a system prompt full of Vietnamese examples
    - `reply_language` now names "English" outright, and the findings message says which language to write in again, right before the model writes
    - The write-up examples are English (or paired English/Vietnamese) instead of Vietnamese only; `save_memory`'s example is no longer "User prefers answers in Vietnamese", which weak models could save as a real memory

## 0.3.2 - Fixes
- **Fix: a direct specialist that found nothing ended the turn**
  - A message the router sent straight to docs, sql or chart used to end with that agent's "couldn't find it"; now, when its reply has no answer (no final text, every tool result empty or an error, or a short "not found" reply), a `fallback` node removes the attempt from the thread and the orchestrator takes over with a summary of what was tried (the UI drops the streamed reply)
    - Never after a handoff or a forced route (`/api/chat/<route>`); the orchestrator gets no `handoff` while falling back
    - Loop guard for every orchestrator run (`no_repeats`): an identical tool call within one message is not run again, and each specialist is asked at most twice per message, the failed direct attempt included
- **Fix: LaTeX symbols shown raw in replies**
  - Models sometimes write math like `$\rightarrow$`, which the chat showed as raw text; known commands (arrows, comparisons, Greek letters, `\%`, `\text{}`) now become Unicode characters before rendering, and dollar amounts are left alone

## 0.3.3 - Token usage box
- **Token usage and cost in the sidebar**: a "Usage" box at the bottom of the chat list shows the account's total tokens (hover for input/output) and cost in USD as OpenRouter bills it, updated after every reply
  - Counts every model call of a message, including agents the orchestrator asks as tools; embedding calls (memories, chat history, documents) are not counted
  - Totals start with this release; `GET /api/usage` returns them, and deleting an account deletes them
  - Two of one account's chats finishing at the same moment can lose one message's numbers

## 0.3.4 - Knowledge graph and fixes
- **Knowledge graph per user** (`app/core/graph.py`) in place of sentence memories
  - Stored in plain Postgres tables (`graph_nodes`, `graph_edges`), created at startup; laid out so PostgreSQL 20's `CREATE PROPERTY GRAPH` can sit on top later (SQL/PGQ was pulled from PostgreSQL 19, and Neon offers up to 18)
  - Nodes are you, your files and the people, companies and other names around them; only facts that touch you or one of your files are kept
  - New values of one-value facts (name, where you live or work, role, risk tolerance, investment horizon, preferred language) replace the old ones
  - Names written slightly differently ("Acme Corp" / "ACME Corporation") become one node; aliases such as "Saigon" / "Ho Chi Minh City" stay separate
  - Your newest 20 facts go into every agent's prompt; recall is now a plain database lookup, one embedding call less per message
  - `GET/DELETE /api/memories` list and delete facts; deleting a chat keeps what was learned in it, deleting a file or an account removes its part of the graph
- **Document cards**: each upload gets one model call that reads the start of the file (6,000 characters) and stores its type, a one-line summary and up to 10 names it is about
  - A documents question about a person or company the graph links to a file searches that file first, then every file; the name can come from the model or from the question itself
  - The orchestrator sees each file's summary next to its name
  - Limit: names that appear only later in a file aren't linked, and a name in a question must be written in full ("Graham" doesn't match "Benjamin Graham"); the search then covers every file as before
- **`scripts/build_graph.py`**: run once after deploying; for each user it converts the old memories and mines every turn of every past chat (one call per 4 turns of a chat, about one call per chat), then writes cards for existing files
  - Each user is built once (`users.graph_built_at`; new accounts never need it), and a run stopped by the rate limit resumes where it stopped
  - It spends free-quota calls and prints how many before it starts
- **Fix: personal details and preferences were not remembered**
  - Saving depended on the free model choosing to call `save_memory`, which it rarely did: a risk-profile questionnaire answered in chat saved nothing
  - Now one model call after each reply the orchestrator writes picks out the facts about you (`extract_facts`); plain stock, chart and document lookups are skipped, so they cost no extra call. The agents no longer have a save tool
- **Fix: the same memory saved again and again**
  - Each save added a new item, so one fact repeated across chats filled the few memories shown to the agents; with the graph it was worded under different relation names instead (your name saved as `has_name`, `is_named` and `is`)
  - Known wordings now map to one relation, the extractor is told which relations to reuse, and each pair of nodes keeps one learned fact. On a real graph this cut 25 facts to 19
  - Limit: two genuinely different facts about the same pair, with relation names the model invented, keep only the newest
- **Fix: sample facts from a tool description could be saved as real memories**
  - The `save_memory` description gave "User's name is Alex" and "User works as a data analyst" as examples, which weak models could store as facts about you (same cause as the 0.3.1 fix)
  - The description first got a placeholder instead; the tool is now gone, since facts are extracted after replies
- `uv run python -m scripts.reindex` now also re-embeds the graph's names

## 0.3.5 - Fixes
- **Fix: replies streamed slowly with paid models**
  - With `OPENROUTER_QUANTIZATIONS=bf16,fp8`, OpenRouter picked the cheapest allowed provider, which was also one of the slowest: about 14 tokens/s for `google/gemma-4-31b-it`
  - New setting `OPENROUTER_PROVIDER_SORT` (`throughput`, `latency` or `price`; empty keeps OpenRouter's default). With `throughput` the same filter served 33 and 130 tokens/s in testing, at the same or lower cost per call
  - Provider speeds change over the day, so the numbers are a snapshot; free models are not affected

## 0.4.0 - Remote agents
- **Remote agents**: the docs, sql, chart and research agents each run as their own private LangGraph Agent Server; the main app keeps the router, orchestrator, chats and memory and calls them over HTTP
  - One image (`Dockerfile.agent`) serves every agent; `AGENT_ROUTE` picks which. Locally `docker compose up -d` runs all four on ports 2101–2104 next to Postgres and Redis; on Cloud Run each is its own private service (`smallie-<route>`, built with `cloudbuild.agent.yaml`), steps in `DEPLOYING.md`
  - New settings: `AGENT_URLS` (route → server URL; the app doesn't start without one for every agent), `SERVICE_TOKEN` (shared secret the servers check; a server with none refuses every call), `AGENT_TIMEOUT_S` (default 300), `MAX_MESSAGE_MODEL_CALLS` (default 30, a per-message backstop on top of the existing per-agent limits; parallel questions in one step can go one step over it)
  - Calls carry no conversation id: the main app's chat stays the only copy, and a call the app stops waiting for (timeout, chat closed) is cancelled on the server instead of spending calls nobody sees
  - On Cloud Run the services also require Google's ID token (`roles/run.invoker` for the main app's service account), so an agent URL can't be called from outside
  - The servers read your files, the knowledge graph and chat history as `agent_reader` (read-only, no accounts or chats) and keep their run queues in their own databases as `agent_server`; stock questions keep using `agent_readonly`. Web search keys (`TAVILY_API_KEY`, `MCP_SERVERS`, `EXTERNAL_DATABASE_URL`) reach only the research server
  - Same number of model calls per message as before: 17 vs 17 over 6 benchmark scenarios, worst case 6 for one message. No slowdown measurable locally; cold-start and warm latency on Cloud Run: measured after the first deploy
  - Limits: in LangSmith a server's run shows under the main app's trace and also as its own trace; the usage box counts the servers' calls, as before
- **LangSmith tracing** (`LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`), off by default: traces chats, with the user id in their metadata. Traces hold full prompts (your messages, file passages, memories)
- Local Postgres now runs in Docker inside WSL (`docker-compose.yml`, port 5433); `scripts\dev_db.ps1` starts it and keeps WSL running, so DB tests and the app no longer freeze when WSL stops
- `scripts.bench_chat` counts the agent servers' model calls too

## 0.5.0 - Agent team
- **Team mode** (`POST /api/chat/team`): a per-chat switch next to the message box. The orchestrator plans the question as tasks; each agent does its task from its own sources only (the database, your files, the web), in parallel when tasks are independent and one after another when one needs another's result. A judge reviews the results after each step, and the answer shows agreements, explained differences and open conflicts instead of hiding them
  - **Plan**: one model call writes the tasks (agent, goal, the tasks it depends on), at most `TEAM_MAX_TASKS` (default 6). If the call fails or the plan is unusable, every agent the message points to answers on its own. Small talk and a message naming one agent skip the plan call; a message that names no source brings in every available agent
  - **Hand-off challenges**: an agent that receives another's output and finds a mismatch with its own source sends it back once. If the producer revises, the tasks that used the old output run again
  - **Conflicts**: a difference counts as explained only when each side gives a source and an as-of date and the explanation fits the size of the gap; otherwise it is a conflict. A conflict an agent can fix with its own tools (a wrong query, a wrong name) gets another round for those agents only. The judge never trusts a source because of its kind: a database figure does not beat a file or a web page for being a database
  - **Follow-ups**: the same review call can add tasks the results call for (a file's revenue claim checked by sql), within the same task cap
  - **Chart task**: a chart asked for in team mode runs after the answer, draws from its final table and joins the answer; charts never change what the answer says
  - **Write-up**: the orchestrator writes one answer that names causes, open points, follow-ups dropped by the cap and tasks that never ran
  - **Pause**: the team can stop to ask you a question; your reply resumes it
  - **Source scoping**: each participant is limited to its own sources, and the agent servers refuse a call outside them, not only hide the tool
  - **Judge model**: new `OPENROUTER_JUDGE_MODEL` (this deployment uses `deepseek/deepseek-v4.1-flash`; empty uses the orchestrator's model). It answers itself, with no fallback model
  - **Settings**: new `TEAM_MAX_TASKS` (6), `TEAM_MAX_ROUNDS` (2 extra rounds per meeting point), `TEAM_MAX_MESSAGE_MODEL_CALLS` (30) and `OPENROUTER_JUDGE_MODEL`
  - **Replaced design**: the first version, a round table where every agent answered the same question and a board compared the answers, was replaced by the task graph. Its numbers are history: a stock question 6 calls, about 49 s; a planted wrong figure 9 calls, about 99 s, flagged; with the default judge model 29 calls, 189 s, not flagged. The relay built before it was removed
  - **Measured** (task graph, `scripts/bench_chat.py`, 2026-10-26; one run per scenario, free models for the agents): 15 scenarios, 100 model calls, no errors, no fallback to every agent. 2 calls when no specialist is needed, 4 for the stock question (26 s), 7 for the planted wrong figure (flagged), 15 with all agents. Choice precision and recall 1.00
  - **Limits**: team mode costs more calls and time than a normal answer; a large plan can reach the call limit before it finishes, and the write-up then says what is missing; a follow-up and a file-versus-database conflict are not yet seen in a live run; the pause rarely fires in testing; shared rooms are not built
- **File search by file name and card** (`search_documents`): naming one of your files in a question limits the search to that file. Each upload gets a card (type, summary, names), and its vector finds the files a question is about; each of the 3 nearest files keeps one passage slot, so a large file cannot crowd out a small one
  - Files uploaded before this release have no card vector until `uv run python -m scripts.build_graph` runs once after deploying (it skips the graph work for users already built, and spends one card call per file that has no card). Until then those files use the plain search
  - Limit: a name must be written in full in the question ("Graham" doesn't match "Benjamin Graham")
- **Status line and Activity popup** (every chat, not only team): one status line shows what the answer is doing now, and an Activity popup holds the full tool log (in team mode also the plan, each wave and each review). The inline tool lines are gone. A reloaded chat shows the same steps as the live view, and team answers keep their tables and charts
- **Fix: a failing tool ended the whole reply**
  - A tool that raised an exception stopped the agent's run, so the answer was lost
  - The agents' middleware (`tool_errors` in `app/agents/factory.py`) now returns the error as the tool's result. Interrupts and handoffs still pass through
- **Fix: the Activity popup did not say which agent ran a tool step**
  - When the orchestrator asked several specialists in parallel, their tool steps looked alike
  - Each tool step now names the agent behind it (`app/agents/delegation.py`, `app/api/routes_chat.py`, `app/static/app.js`)
- **Fix: browsers kept an old `app.js`**
  - Static files had no `Cache-Control`, so a browser could keep a days-old script after a deploy
  - Static files now send `no-cache` (`NoCacheStaticFiles` in `app/api/main.py`): the browser checks each time, and an unchanged file still comes back as a 304. `/api` is unchanged
- **Fix: English questions answered in Vietnamese after a language preference was stored**
  - A remembered preference ("prefers Vietnamese") in the orchestrator's memories outweighed the language of the message
  - The orchestrator now names the reply language, taken from the latest message, on every call, even over a remembered preference
- **Fix: one unexpected stream event ended the whole reply**
  - In the browser, a single malformed or unexpected stream event stopped the rest of the reply from showing. It is now skipped and the reply goes on
- Refactors and tooling
  - `AgentSpec.review`, `sources` and `joins` describe what each agent may do in team mode
  - `scripts.bench_chat` has team scenarios (stock, conflict, chart); its answer text matches what the chat bubble shows
  - The test suite ignores the developer's `.env` for the judge model
  - Testing rule (`.claude/rules/testing.md`): a failing test is fixed in the code, never weakened or changed to pass
