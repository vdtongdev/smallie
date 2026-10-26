"""Picks who handles each message, without a model call.

A registered agent when the choice is obvious: forced by the API, or the message clearly
points to exactly one agent (its hint words, a file name, a ticker) and mentions no other
(e.g. "my resume" next to a ticker). Everything else goes to the orchestrator, which answers
itself, hands off to one agent or asks several of them.
"""

import asyncio
from collections.abc import Sequence
from typing import NotRequired

from langchain.agents import AgentState
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.runtime import Runtime

from app.agents import registry
from app.agents.base import AgentSpec, RouteInfo
from app.core.memory import TEAM_MODE, AgentContext

ORCHESTRATOR = "orchestrator"  # graph node, route, SSE `agent` name and AIMessage.name of the front door


class AssistantState(AgentState):
    route: NotRequired[str]
    findings: NotRequired[str]  # research findings waiting for the orchestrator to write them up
    # The router picked `route` itself (not forced, not a handoff): if it finds no answer, the
    # orchestrator takes over (app/orchestration/fallback.py).
    direct: NotRequired[bool]
    failed: NotRequired[str]  # the route that gave up, for the orchestrator
    attempt: NotRequired[str]  # what it tried, for the orchestrator
    team: NotRequired[dict]  # the team debate's state (app/orchestration/team.py); reset by each new message
    team_drawn: NotRequired[bool]  # a drawer charts the write-up (set with `team_note`)
    team_note: NotRequired[str]  # a hidden note for the orchestrator; only ever put in the state a team node passes it


def previous_route(messages: list[BaseMessage], routes: dict[str, str]) -> str | None:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.name in routes:
            return routes[message.name]
    return None


async def available_agents(user_id: str, specs: Sequence[AgentSpec]) -> list[tuple[AgentSpec, RouteInfo]]:
    infos = await asyncio.gather(*(spec.info(user_id) for spec in specs))
    return [(spec, info) for spec, info in zip(specs, infos) if info is not None]


def mentioned_routes(message: str, available: list[tuple[AgentSpec, RouteInfo]]) -> tuple[str, ...]:
    """Agents whose data the message clearly or partly points to (hints, matches, mentions)."""
    return tuple(spec.route for spec, info in available if spec.is_mentioned(message, info))


def obvious_route(message: str, available: list[tuple[AgentSpec, RouteInfo]]) -> str | None:
    """The route when the message clearly points to exactly one agent and mentions no other,
    else None (the orchestrator then asks every agent it needs)."""
    matched = [spec for spec, info in available if spec.is_obvious(message, info)]
    routes = {spec.route for spec in matched}
    # One agent, or one that overrides all the others ("chart AAPL": chart over sql).
    winners = [spec.route for spec in matched if routes - {spec.route} <= set(spec.overrides)]
    if len(winners) != 1 or set(mentioned_routes(message, available)) - routes:
        return None
    return winners[0]


def build_router(specs: Sequence[AgentSpec] = registry.AGENTS):
    async def router(state: AssistantState, runtime: Runtime[AgentContext]) -> dict:
        context = runtime.context
        if context.agent == TEAM_MODE:
            return {"route": TEAM_MODE, "direct": False}
        if any(spec.route == context.agent for spec in specs):
            return {"route": context.agent, "direct": False}  # the user picked it: no fallback
        available = await available_agents(context.user_id, specs)
        latest = state["messages"][-1].text
        route = await asyncio.to_thread(obvious_route, latest, available)
        return {"route": route or ORCHESTRATOR, "direct": bool(route)}

    return router
