"""Entry point of an agent server (langgraph.json): serves the one agent named by AGENT_ROUTE.

Every agent server runs the same image; only AGENT_ROUTE differs. The graph is wrapped in a
one-node `run` graph that counts the run's model calls, tokens and cost and returns them as
`usage`: the main app can't see calls made here, and its loop guard (MAX_MESSAGE_MODEL_CALLS,
app/agents/remote.py) and usage box need them.
"""

import asyncio
import os
from contextlib import AsyncExitStack
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import merge_configs
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime

from app.agents import registry
from app.agents.base import ToolRecorder
from app.agents.remote import RUN_NODE
from app.core.memory import AgentContext, open_history_store, set_history_store
from app.core.model_settings import require_secret
from app.core.usage import UsageCounter

# shortcut: never closed; the process lifetime is the server's (MCP sessions, the store's pool live on it).
_STACK = AsyncExitStack()
_GRAPH = None
_BUILD = asyncio.Lock()


class ServerState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    findings: str
    usage: dict
    tools: list


def reporting_graph(agent):
    """`agent` behind a `run` node that adds its usage to the result. Not named after a route or
    `tools`: the main app's event filter reads those names in the namespace (AP-004)."""

    async def run(state: ServerState, config: RunnableConfig, runtime: Runtime[AgentContext]) -> dict:
        counter, recorder = UsageCounter(), ToolRecorder()
        # merge, not replace: the run's stream handlers stay, so tokens and tool events still flow.
        result = await agent.ainvoke({"messages": state["messages"]}, merge_configs(config, {"callbacks": [counter, recorder]}),
                                     context=runtime.context)
        # Only the new messages: `add_messages` already holds the input, and a whole-thread update
        # would be streamed to the main app as tool events again.
        update = {"messages": result["messages"][len(state["messages"]):], "usage": counter.totals(), "tools": recorder.tools}
        if "findings" in result:
            update["findings"] = result["findings"]
        return update

    graph = StateGraph(ServerState, context_schema=AgentContext)
    graph.add_node(RUN_NODE, run)
    graph.add_edge(START, RUN_NODE)
    graph.add_edge(RUN_NODE, END)
    return graph.compile(name=getattr(agent, "name", None) or "agent")


async def make_graph():
    """Built once per process, after the agent's async setup (`prepare`, e.g. MCP tools).
    langgraph-api calls this factory on every run, and two first runs may arrive together."""
    global _GRAPH
    async with _BUILD:
        if _GRAPH is None:
            _GRAPH = await _build()
    return _GRAPH


async def _build():
    require_secret()
    route = os.environ.get("AGENT_ROUTE", "")
    spec = registry.get(route)
    if spec is None:
        raise ValueError(f"AGENT_ROUTE={route!r} is not a registered agent (one of {', '.join(registry.ROUTES)})")
    if spec.prepare:
        await spec.prepare(_STACK)
    set_history_store(await open_history_store(_STACK))
    return reporting_graph(spec.build())
