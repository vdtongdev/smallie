# Changelog

## 0.0.1 - Initial Release
- **Docs Agent**: RAG over uploaded PDFs/DOCX with pgvector indexing, citations with page numbers
- **SQL Agent**: Natural language queries against stock market schema with read-only role
- UI: Bilingual (EN/VI) chat interface with file upload, conversation history, Readme modal
- Memory: Short-term (LangGraph checkpointer), long-term (pgvector semantic store), cross-chat history
- Auth: Account system with email/password, admin panel for user management
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
