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
