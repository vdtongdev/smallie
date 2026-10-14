# Changelog

## 0.0.1 - Initial Release
- **Docs Agent**: RAG over uploaded PDFs/DOCX with pgvector indexing, citations with page numbers
- **SQL Agent**: Natural language queries against stock market schema with read-only role
- UI: Bilingual (EN/VI) chat interface with file upload, conversation history, Readme modal
- Memory: Short-term (LangGraph checkpointer), long-term (pgvector semantic store), cross-chat history
- Auth: Account system with username/password, admin panel for user management
- Stock data: Daily market data refresh via yfinance (weekday schedule)
- Deployment: Google Cloud Run with Neon Postgres

## 0.0.2 - Project Restructure for multiple agents
- **Project restructure**: `app/` is now split into `core/`, `agents/`, `features/`, `orchestration/` and `api/`, so a new agent needs no router, API, UI or `main.py` changes
  - Agent registry: each agent is an `AgentSpec` (route, router description, UI labels, keyword hints, per-user routing data) listed in `app/agents/registry.py`; the router, graph, chat/conversation routes and UI badges are driven by it
  - Agent packages: each agent lives in `app/agents/<name>/` with `agent.py` (build + `SPEC`), `tools.py` and `prompts.py`; the shared agent builder moved to `app/agents/factory.py`
  - Features: documents and market data moved to `app/features/<name>/`, each declaring its API routes, an optional startup hook (market scheduler) and per-user cleanup (document chunks and uploads), so `main.py` and account deletion loop over `app/features/registry.py`
  - Core: config, LLM, database, memory, accounts, auth, conversations and the shared prompts moved to `app/core/`
  - Orchestration: the router and assistant graph moved to `app/orchestration/`; routing behavior and the router prompt are unchanged
## 0.1.0 - Research Agent and Writer Agent
- **Research Agent**: Multi-step workflow (researcher → report writer) with deep-linked citations, MCP tool integration (Tavily, Wikipedia, arXiv)
- **Writer Agent**: Formatting results from Research agent to write properly structured answers.
- **Fix: foreign words in Vietnamese answers**: weaker models (mostly the Nemotron fallback, which answered most requests while the free Gemma was rate-limited) slipped single words of other scripts into Vietnamese answers, e.g. Korean 독者 ("reader"), Thai เด่น ("standing out"), Chinese 半 ("half")
  - Temperature lowered from 0.2 to 0, so the model is less likely to pick unlikely wrong-language words
  - `OPENROUTER_FALLBACK_MODEL` accepts a comma-separated list tried in order; the default puts the free Gemma 4 26B (good at Vietnamese) before Nemotron
  - Note: this only lowers the chance of untranslated words; it doesn't fully fix it. The cause is the models themselves, so Nemotron (and other weak-in-Vietnamese models) can still slip in words, e.g. Russian, whenever it answers

## 0.2.0 - Orchestrator
- **Orchestrator** (`app/orchestration/orchestrator.py`): the front door for every message that doesn't clearly point to one agent
  - Answers general questions itself (explanations, maths, writing, small talk), with a quick web search (Tavily or Wikipedia) when the facts may have changed
  - Hands a message off to one specialist (`handoff`), which then replies directly with the whole chat; a first step that asks just one specialist is turned into a handoff, because free models rarely choose it themselves
  - Asks several specialists in parallel (`ask_docs`, `ask_sql`, `ask_research`) and writes the combined answer with citations
  - Says plainly what it can't do (act outside the chat, real-time data, files not uploaded, images…) instead of guessing
  - Only its final answer is saved to the chat, so the specialists' results don't grow later prompts
- **Router without a model call**: obvious messages (a ticker, hint words, a file name) still go straight to their agent; everything else goes to the orchestrator. `OPENROUTER_ROUTER_MODEL` is replaced by `OPENROUTER_ORCHESTRATOR_MODEL` and `ORCHESTRATOR_MAX_MODEL_CALLS`
- **Research writer removed**: the researcher only gathers findings; the orchestrator writes them up with the writer's formats and citation rules, which are added to its prompt only while it writes up results
- **Benchmark** (`scripts/bench_chat.py`): time to first event and first token, total time and model calls per scenario on the real models
- **Questions for several specialists**: stock figures and anything in the user's files must come from the database and documents agents, so a mixed question (e.g. "which of the most valuable tech companies fits my resume?") asks both in parallel and gets one combined answer
  - The router no longer sends a message to one agent when it also mentions another agent's data (a word of one of the user's file names or "resume"/"CV" next to a ticker); `RouteInfo.mentions`
  - Asking a single agent isn't turned into a handoff then, and the orchestrator's prompt names the mentioned agents
  - Combining rules: one answer to the actual question, conclusion first, parts connected, each fact keeps its citation; docs is asked again (naming the file) before saying a listed file has nothing
- **Web search alongside Wikipedia**: the researcher runs a web search in the same step as every Wikipedia search (except for arXiv questions); the orchestrator's quick lookups do the same and prefer the newest results

## 0.3.0 - Chart agent
- **Chart agent** (`app/agents/chart/`): line, bar, area, pie and scatter charts of the stock database, of an earlier query result ("plot that") or of numbers from another answer, with a 1-3 sentence takeaway
  - `chart_sql` writes the query and the chart in one call (~2 model calls, 9-16 s per chart in testing); `chart_table` reuses a result already in the chat without querying again; `chart_data` charts a few inline numbers
  - Data never passes through the model: query results and charts travel as hidden tool artifacts, the model only sees a short summary (first/last/min/max per series)
  - Routing: "chart", "plot", "graph", "biểu đồ", "đồ thị", "vẽ biểu đồ" go straight to it, also with a ticker ("chart AAPL"); a ticker alone still goes to the database agent
- **Table artifacts**: `run_sql` keeps the whole result (up to 2000 rows) as a table artifact while the model still sees the first `SQL_MAX_ROWS` rows; the orchestrator keeps its turn's tables and charts on its answer, so charts survive a reload and "plot that" works after a combined answer
- The orchestrator can chart the results of the agents it asked (e.g. database figures next to a resume)
- UI (`app/static/charts.js`): chart cards with a validated colour palette in light and dark mode, crosshair tooltips, a data table and PNG download
- Shared `SQL_RULES` for every agent that queries the stock database, now with a rule for PostgreSQL's `round(x::numeric, 2)`
