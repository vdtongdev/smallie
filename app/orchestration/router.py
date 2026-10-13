"""Picks which registered agent answers each message.

Skips the model call when the choice is obvious: agent forced by the API, only one agent
has anything to offer this user (e.g. no documents uploaded), or the message clearly
points to exactly one agent (its hint words, a file name, a ticker). Otherwise one small,
non-streaming call with a prompt built from the available agents' descriptions.
"""

import asyncio
import logging
import re
from collections.abc import Sequence
from typing import NotRequired

import openai
from langchain.agents import AgentState
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from app.agents import registry
from app.agents.base import AgentSpec, RouteInfo, message_routes
from app.core.config import get_settings
from app.core.llm import get_chat_model
from app.core.memory import AgentContext
from app.core.prompts import ROUTER_SYSTEM

log = logging.getLogger(__name__)

HISTORY_MESSAGES = 6
HISTORY_CHARS = 400


class AssistantState(AgentState):
    route: NotRequired[str]


def previous_route(messages: list[BaseMessage], routes: dict[str, str]) -> str | None:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.name in routes:
            return routes[message.name]
    return None


def transcript(messages: list[BaseMessage], routes: dict[str, str]) -> str:
    """The latest user message plus a short, text-only view of the turns before it."""
    *history, latest = messages
    lines = []
    for message in history:
        if isinstance(message, HumanMessage) and message.text:
            lines.append(f"User: {message.text[:HISTORY_CHARS]}")
        elif isinstance(message, AIMessage) and message.text:
            agent = routes.get(message.name or "", "?")
            lines.append(f"Assistant ({agent}): {message.text[:HISTORY_CHARS]}")
    earlier = "\n".join(lines[-HISTORY_MESSAGES:]) or "(none)"
    return f"Conversation so far:\n{earlier}\n\nLatest message:\n{latest.text}"


def parse_route(reply: str, routes: Sequence[str], default: str) -> str:
    for word in re.findall(r"[a-z]+", reply.lower()):
        if word in routes:
            return word
    return default


async def available_agents(user_id: str, specs: Sequence[AgentSpec]) -> list[tuple[AgentSpec, RouteInfo]]:
    infos = await asyncio.gather(*(spec.info(user_id) for spec in specs))
    return [(spec, info) for spec, info in zip(specs, infos) if info is not None]


def obvious_route(message: str, available: list[tuple[AgentSpec, RouteInfo]]) -> str | None:
    """The route when the message clearly points to exactly one agent, else None."""
    matched = [spec.route for spec, info in available if spec.is_obvious(message, info)]
    return matched[0] if len(matched) == 1 else None


def router_prompt(available: list[tuple[AgentSpec, RouteInfo]], previous: str | None) -> str:
    routes = [spec.route for spec, _ in available]
    choices = " or ".join([", ".join(routes[:-1]), routes[-1]]) if len(routes) > 1 else routes[0]
    agents = "\n".join(f"- {spec.route}: {spec.description}" for spec, _ in available)
    if notes := [info.prompt_note for _, info in available if info.prompt_note]:
        agents += "\n\n" + "\n".join(notes)
    return ROUTER_SYSTEM.format(choices=choices, agents=agents, previous=previous or "none")


def build_router(specs: Sequence[AgentSpec] = registry.AGENTS):
    model = get_chat_model(get_settings().openrouter_router_model or None, streaming=False)
    routes = message_routes(specs)

    async def router(state: AssistantState, runtime: Runtime[AgentContext]) -> dict:
        context = runtime.context
        if any(spec.route == context.agent for spec in specs):
            return {"route": context.agent}
        available = await available_agents(context.user_id, specs)
        if len(available) <= 1:  # e.g. no documents uploaded: only the SQL agent is left
            return {"route": (available[0][0] if available else specs[0]).route}
        latest = state["messages"][-1].text
        if route := await asyncio.to_thread(obvious_route, latest, available):
            return {"route": route}

        allowed = [spec.route for spec, _ in available]
        previous = previous_route(state["messages"], routes)
        default = previous if previous in allowed else allowed[0]
        prompt = router_prompt(available, previous)
        try:
            reply = await model.ainvoke([SystemMessage(prompt), HumanMessage(transcript(state["messages"], routes))])
        except openai.APIError as exc:
            # A failed routing call shouldn't fail the chat; the agent call reports real outages.
            log.warning("Router call failed, using %s: %s", default, exc)
            return {"route": default}
        return {"route": parse_route(reply.text, allowed, default)}

    return router
