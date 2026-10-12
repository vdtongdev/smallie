"""The assistant the UI talks to: a router that hands each message to the docs or SQL agent.

    START -> router -> docs | sql -> END

Both agents run as subgraphs on the same thread, so a conversation can switch between
them and each still sees the full history. The router makes one small model call, and
skips it when the choice is obvious (agent forced by the API, no documents uploaded, or
the message clearly points to one side, see `obvious_route`).
"""

import asyncio
import logging
import re
from typing import NotRequired

import openai
from langchain.agents import AgentState
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db import documents, get_engine
from app.documents.agent import build_docs_agent
from app.llm import get_chat_model
from app.memory import AgentContext
from app.prompts import ROUTER_SYSTEM
from app.sql.agent import build_sql_agent, get_readonly_engine

log = logging.getLogger(__name__)

ROUTES = ("docs", "sql")
AGENT_ROUTES = {"docs_agent": "docs", "sql_agent": "sql"}  # AIMessage.name -> route
HISTORY_MESSAGES = 6
HISTORY_CHARS = 400
MAX_LISTED_DOCUMENTS = 30

# Words that only fit one agent. Deliberately few: anything ambiguous goes to the model.
SQL_HINTS = re.compile(
    r"\b(stocks?|tickers?|market caps?|dividends?|p/e|stock prices?|share prices?|"
    r"cổ phiếu|cổ tức|vốn hóa|chứng khoán|mã ck)\b",
    re.IGNORECASE,
)
DOCS_HINTS = re.compile(r"\b(documents?|files?|pdfs?|docx|uploads?|uploaded|tài liệu|tệp)\b", re.IGNORECASE)
TICKER_LIKE = re.compile(r"\b[A-Z]{2,5}(?:[.-][A-Z])?\b")
MIN_STEM_CHARS = 4
_tickers: frozenset[str] = frozenset()


class AssistantState(AgentState):
    route: NotRequired[str]


def document_names(user_id: str) -> list[str]:
    """The user's uploaded files that are (or will soon be) searchable."""
    with get_engine().connect() as conn:
        return list(conn.execute(
            select(documents.c.filename)
            .where(documents.c.user_id == user_id, documents.c.status != "failed")
            .order_by(documents.c.created_at.desc())
        ).scalars())


def stock_tickers() -> frozenset[str]:
    """Tickers in market.stocks, cached after the first successful read."""
    global _tickers
    if not _tickers:
        try:
            with get_readonly_engine().connect() as conn:
                _tickers = frozenset(conn.execute(text("SELECT ticker FROM market.stocks")).scalars())
        except SQLAlchemyError as exc:
            log.warning("Could not load tickers for routing: %s", exc)
    return _tickers


def obvious_route(message: str, names: list[str]) -> str | None:
    """`docs` or `sql` when the message clearly points to one side only, else None."""
    lowered = message.lower()
    docs = bool(DOCS_HINTS.search(message)) or any(
        len(stem) >= MIN_STEM_CHARS and stem in lowered
        for stem in (name.rsplit(".", 1)[0].lower() for name in names)
    )
    sql = bool(SQL_HINTS.search(message))
    if not sql and (candidates := set(TICKER_LIKE.findall(message))):
        sql = bool(candidates & stock_tickers())
    return {(True, False): "docs", (False, True): "sql"}.get((docs, sql))


def previous_route(messages: list[BaseMessage]) -> str | None:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.name in AGENT_ROUTES:
            return AGENT_ROUTES[message.name]
    return None


def transcript(messages: list[BaseMessage]) -> str:
    """The latest user message plus a short, text-only view of the turns before it."""
    *history, latest = messages
    lines = []
    for message in history:
        if isinstance(message, HumanMessage) and message.text:
            lines.append(f"User: {message.text[:HISTORY_CHARS]}")
        elif isinstance(message, AIMessage) and message.text:
            agent = AGENT_ROUTES.get(message.name or "", "?")
            lines.append(f"Assistant ({agent}): {message.text[:HISTORY_CHARS]}")
    earlier = "\n".join(lines[-HISTORY_MESSAGES:]) or "(none)"
    return f"Conversation so far:\n{earlier}\n\nLatest message:\n{latest.text}"


def parse_route(reply: str, default: str) -> str:
    for word in re.findall(r"[a-z]+", reply.lower()):
        if word in ROUTES:
            return word
    return default


def build_router():
    model = get_chat_model(get_settings().openrouter_router_model or None, streaming=False)

    async def router(state: AssistantState, runtime: Runtime[AgentContext]) -> dict:
        context = runtime.context
        if context.agent in ROUTES:
            return {"route": context.agent}
        names = await asyncio.to_thread(document_names, context.user_id)
        if not names:
            return {"route": "sql"}  # nothing to search in the documents
        latest = state["messages"][-1].text
        if route := await asyncio.to_thread(obvious_route, latest, names):
            return {"route": route}

        previous = previous_route(state["messages"])
        default = previous or "docs"
        listed = ", ".join(names[:MAX_LISTED_DOCUMENTS]) + (" …" if len(names) > MAX_LISTED_DOCUMENTS else "")
        prompt = ROUTER_SYSTEM.format(documents=listed, previous=previous or "none")
        try:
            reply = await model.ainvoke([SystemMessage(prompt), HumanMessage(transcript(state["messages"]))])
        except openai.APIError as exc:
            # A failed routing call shouldn't fail the chat; the agent call reports real outages.
            log.warning("Router call failed, using %s: %s", default, exc)
            return {"route": default}
        return {"route": parse_route(reply.text, default)}

    return router


def build_assistant(checkpointer, store):
    graph = StateGraph(AssistantState, context_schema=AgentContext)
    graph.add_node("router", build_router())
    graph.add_node("docs", build_docs_agent())
    graph.add_node("sql", build_sql_agent())
    graph.add_edge(START, "router")
    graph.add_conditional_edges("router", lambda state: state["route"], list(ROUTES))
    for route in ROUTES:
        graph.add_edge(route, END)
    return graph.compile(checkpointer=checkpointer, store=store, name="assistant")
