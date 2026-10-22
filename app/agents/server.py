"""Entry point of an agent server (langgraph.json): serves the one agent named by AGENT_ROUTE.

Every agent server runs the same image; only AGENT_ROUTE differs. The graph is wrapped in a
one-node `run` graph that counts the run's model calls, tokens and cost and returns them as
`usage`: the main app can't see calls made here, and its loop guard (MAX_MESSAGE_MODEL_CALLS,
app/agents/remote.py) and usage box need them.
"""

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
from app.core.memory import AgentContext, open_history_store, set_history_store
from app.core.usage import UsageCounter

# shortcut: never closed; the process lifetime is the server's (MCP sessions, the store's pool live on it).
_STACK = AsyncExitStack()
_GRAPH = None


class ServerState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    findings: str
    usage: dict


def reporting_graph(agent):
    """`agent` behind a `run` node that adds its usage to the result. Not named after a route or
    `tools`: the main app's event filter reads those names in the namespace (AP-004)."""

    async def run(state: ServerState, config: RunnableConfig, runtime: Runtime[AgentContext]) -> dict:
        counter = UsageCounter()
        # merge, not replace: the run's stream handlers stay, so tokens and tool events still flow.
        result = await agent.ainvoke({"messages": state["messages"]}, merge_configs(config, {"callbacks": [counter]}),
                                     context=runtime.context)
        update = {"messages": result["messages"], "usage": counter.totals()}
        if "findings" in result:
            update["findings"] = result["findings"]
        return update

    graph = StateGraph(ServerState, context_schema=AgentContext)
    graph.add_node("run", run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return graph.compile(name=getattr(agent, "name", None) or "agent")


async def make_graph():
    """Built once per process, after the agent's async setup (`prepare`, e.g. MCP tools)."""
    global _GRAPH
    if _GRAPH is None:
        route = os.environ.get("AGENT_ROUTE", "")
        spec = registry.get(route)
        if spec is None:
            raise ValueError(f"AGENT_ROUTE={route!r} is not a registered agent (one of {', '.join(registry.ROUTES)})")
        if spec.prepare:
            await spec.prepare(_STACK)
        set_history_store(await open_history_store(_STACK))
        _GRAPH = reporting_graph(spec.build())
    return _GRAPH
