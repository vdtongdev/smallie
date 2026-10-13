"""The assistant the UI talks to: a router that hands each message to one registered agent.

    START -> router -> <agent route> -> END

Agents run as subgraphs on the same thread, so a conversation can switch between them
and each still sees the full history. Agents are listed in app/agents/registry.py.
"""

from langgraph.graph import END, START, StateGraph

from app.agents.registry import AGENTS
from app.core.memory import AgentContext
from app.orchestration.router import AssistantState, build_router


def build_assistant(checkpointer, store):
    graph = StateGraph(AssistantState, context_schema=AgentContext)
    graph.add_node("router", build_router(AGENTS))
    for spec in AGENTS:
        graph.add_node(spec.route, spec.build())
        graph.add_edge(spec.route, END)
    graph.add_edge(START, "router")
    graph.add_conditional_edges("router", lambda state: state["route"], [spec.route for spec in AGENTS])
    return graph.compile(checkpointer=checkpointer, store=store, name="assistant")
