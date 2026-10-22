"""The assistant the UI talks to.

    START -> router -> <agent route> -> END                 (obvious: replies directly)
                                     -> fallback -> orchestrator -> END  (it found no answer)
                    -> research -> orchestrator -> END      (obvious research: findings written up)
                    -> orchestrator -> END                  (answers itself or asks agents as tools)
                                    -> <agent route> -> END (handoff)

Each agent node is a RemoteAgent (its own Agent Server, app/agents/remote.py) that gets the
thread's messages with every call, so a conversation can switch between agents and each still
sees the full history; this thread is the only copy. Agents are listed in app/agents/registry.py.
"""

from collections.abc import Mapping

from langgraph.graph import END, START, StateGraph

from app.agents.registry import AGENTS
from app.agents.remote import RemoteAgent, remote_agents
from app.core.memory import AgentContext
from app.orchestration.fallback import FALLBACK, after, fallback
from app.orchestration.orchestrator import build_orchestrator
from app.orchestration.router import ORCHESTRATOR, AssistantState, build_router


def build_assistant(checkpointer, store, agents: Mapping | None = None):
    """`agents`: route -> compiled graph or RemoteAgent; default the agent servers in AGENT_URLS
    (tests pass in-process graphs)."""
    agents = remote_agents(AGENTS) if agents is None else agents
    orchestrator, targets = build_orchestrator(AGENTS, agents)
    graph = StateGraph(AssistantState, context_schema=AgentContext)
    graph.add_node("router", build_router(AGENTS))
    graph.add_node(ORCHESTRATOR, orchestrator, destinations=(*targets, END))
    graph.add_node(FALLBACK, fallback)
    graph.add_edge(FALLBACK, ORCHESTRATOR)
    for spec in AGENTS:
        agent = agents[spec.route]
        graph.add_node(spec.route, agent.node if isinstance(agent, RemoteAgent) else agent)
        if spec.answers_directly:
            graph.add_conditional_edges(spec.route, after(spec), [FALLBACK, END])
        else:
            # Entries that only gather material (research) hand it to the orchestrator to write up.
            graph.add_edge(spec.route, ORCHESTRATOR)
    graph.add_edge(START, "router")
    graph.add_conditional_edges("router", lambda state: state["route"], [*agents, ORCHESTRATOR])
    return graph.compile(checkpointer=checkpointer, store=store, name="assistant")
