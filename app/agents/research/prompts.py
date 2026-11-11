from pathlib import Path

from app.core.prompts import load_prompt

ROUTE_DESCRIPTION = (
    "researches a topic across several sources (web search and news, Wikipedia, arXiv papers, "
    "external databases, plus the stock database and the user's files when relevant) and writes a "
    "summary or a structured report. Use it for current events, news, things outside the user's "
    "files and the stock data, and requests for research, reports, overviews or comparisons."
)

# The orchestrator's `ask_research` tool description.
TOOL_DESCRIPTION = (
    "Researches a question across web search, Wikipedia, arXiv, external databases, the stock database "
    "and the user's files, and returns cited findings for you to write up. Slow (several searches): use it "
    "for research, reports, overviews, news and topics that need several sources, not for one quick fact."
)

# Read by the judge that picks a critic for another agent's draft: what this agent can check.
REVIEW = (
    "Checks facts, dates and figures against web search, Wikipedia and arXiv papers, "
    "and reports which claims the sources support or contradict."
)

# The researcher's tail note: the date changes daily, so it stays out of the system prompt.
TODAY_NOTE = "Today is {today}."

# {web_rule}: WEB_ALONGSIDE_WIKI when web search loaded. The source list, the skills and the market
# schema depend on the request's sources, so `research_prompt_extra` appends them per request.
RESEARCHER_SYSTEM = load_prompt(Path(__file__).parent / "AGENTS.md")

SOURCES_HEADING = "\n\nYour sources:\n"
SCHEMA_HEADING = "\n\nMARKET DATABASE SCHEMA (for run_sql; end-of-day data, always schema-qualify tables):\n"

WEB_ALONGSIDE_WIKI = (
    "\n- Whenever you search Wikipedia, also run a web search (`tavily_tavily_search`) in the same step: "
    "Wikipedia gives the background, the web adds recent, practical and more specific sources (official sites, "
    "guidelines, news). Skip it only for questions about scientific papers (arXiv)."
)

SOURCE_LINES = {
    "tavily": "- Web search and page extraction (tavily_* tools).",
    "knowledge": "- Wikipedia (en/vi) and arXiv papers (knowledge_* tools).",
    "external_db": "- An external read-only PostgreSQL database (external_db_* tools: list_tables, describe_table, query).",
}
DOCUMENTS_LINE = "- The user's uploaded PDF/DOCX files (`search_documents`; cite as [filename, p. N]). Use it when the question is about their files or when their files could add context."
MARKET_LINE = "- The stock database: ~200 US stocks, profiles and daily prices (`run_sql`, schema below). Use it for exact stock numbers."
