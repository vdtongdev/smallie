"""The assistant the UI talks to.

    START -> router -> <agent route> -> END                 (obvious: replies directly)
                    -> research -> orchestrator -> END      (obvious research: findings written up)
                    -> orchestrator -> END                  (answers itself or asks agents as tools)
                                    -> <agent route> -> END (handoff)

Agents run as subgraphs on the same thread, so a conversation can switch between them
and each still sees the full history. Agents are listed in app/agents/registry.py.
"""

from langgraph.graph import END, START, StateGraph

from app.agents.registry import AGENTS
from app.core.memory import AgentContext
from app.orchestration.orchestrator import build_orchestrator
from app.orchestration.router import ORCHESTRATOR, AssistantState, build_router


def build_assistant(checkpointer, store):
    agents = {spec.route: spec.build() for spec in AGENTS}
    orchestrator, targets = build_orchestrator(AGENTS, agents)
    graph = StateGraph(AssistantState, context_schema=AgentContext)
    graph.add_node("router", build_router(AGENTS))
    graph.add_node(ORCHESTRATOR, orchestrator, destinations=(*targets, END))
    for spec in AGENTS:
        graph.add_node(spec.route, agents[spec.route])
        # Entries that only gather material (research) hand it to the orchestrator to write up.
        graph.add_edge(spec.route, END if spec.answers_directly else ORCHESTRATOR)
    graph.add_edge(START, "router")
    graph.add_conditional_edges("router", lambda state: state["route"], [*agents, ORCHESTRATOR])
    return graph.compile(checkpointer=checkpointer, store=store, name="assistant")
