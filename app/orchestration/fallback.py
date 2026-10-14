"""When an agent the router picked finds no answer, the orchestrator takes over.

    <agent route> -> fallback -> orchestrator -> END    (the agent gave up: `AgentSpec.gave_up`)
                  -> END                                (it answered, or it was forced or handed off to)

`fallback` removes the agent's attempt from the thread (its "couldn't find it" reply would
otherwise stay in later prompts and be named as the previous answer) and passes a summary of
what it tried, so the orchestrator doesn't run the same searches again. Its top-level update
becomes a `fallback` SSE event, so the UI drops the reply it already streamed. The orchestrator
can't hand the message back (it gets no `handoff` tool then) and asks each specialist at most
`MAX_ASKS` times per message, counting the failed attempt (`no_repeats` in orchestrator.py).
"""

import json

from langchain_core.messages import AIMessage, AnyMessage, RemoveMessage, ToolMessage
from langgraph.graph import END

from app.agents.base import MEMORY_TOOL_NAMES, AgentSpec, turn_messages
from app.orchestration.router import ORCHESTRATOR, AssistantState

FALLBACK = "fallback"  # graph node
ARGS_CHARS = 200
RESULT_CHARS = 300
REPLY_CHARS = 600


def after(spec: AgentSpec):
    """The edge after a directly answering agent: END, or `fallback` when it gave up."""

    def next_node(state: AssistantState) -> str:
        if state.get("direct") and spec.gave_up(turn_messages(state["messages"])):
            return FALLBACK
        return END

    return next_node


def attempt_summary(messages: list[AnyMessage]) -> str:
    """The agent's tool calls with the start of their results, and its reply."""
    results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
    lines = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            if call["name"] in MEMORY_TOOL_NAMES:
                continue
            args = json.dumps(call["args"], ensure_ascii=False)[:ARGS_CHARS]
            result = results.get(call["id"])
            text = " ".join((result.text if result else "(no result)").split())[:RESULT_CHARS]
            lines.append(f"- {call['name']}({args}) -> {text}")
    last = messages[-1] if messages else None
    reply = last.text[:REPLY_CHARS] if isinstance(last, AIMessage) and not last.tool_calls and last.text else "(none)"
    return "\n".join([*(lines or ["- no tool calls"]), f"Its reply: {reply}"])


def fallback(state: AssistantState) -> dict:
    attempt = turn_messages(state["messages"])
    return {
        "route": ORCHESTRATOR,
        "direct": False,
        "failed": state["route"],
        "attempt": attempt_summary(attempt),
        "messages": [RemoveMessage(id=m.id) for m in attempt],
    }
