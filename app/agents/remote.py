"""Specialists running as their own LangGraph Agent Servers, called through `RemoteGraph`.

What comes back over the wire is JSON: messages arrive as dicts, both streamed and in the final
state. `as_message` turns them back into message objects. LangGraph's own conversion
(`add_messages`) moves `usage_metadata` into `additional_kwargs` (spike 2), which would lose the
token counts.
"""

from dataclasses import asdict
from typing import Any

import httpx
import openai
from langchain_core.messages import (
    AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage, ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.pregel.remote import RemoteException, RemoteGraph
from langgraph.runtime import Runtime
from langgraph_sdk.client import LangGraphClient

from app.core.memory import AgentContext
from app.core.usage import UsageCounter

GRAPH = "agent"  # the graph name in langgraph.json; every agent server serves one
# Errors a remote specialist reports by name only, raised again as the exceptions the chat route
# turns into its "rate limit" / "invalid key" messages.
_OPENAI_ERRORS = {"RateLimitError": (openai.RateLimitError, 429),
                  "AuthenticationError": (openai.AuthenticationError, 401)}

_MESSAGE_TYPES = {
    "ai": AIMessage, "AIMessageChunk": AIMessageChunk, "human": HumanMessage,
    "system": SystemMessage, "tool": ToolMessage,
}


def as_message(value: BaseMessage | dict) -> BaseMessage:
    """A message object, from a remote agent's JSON or as it is."""
    if isinstance(value, BaseMessage):
        return value
    cls = _MESSAGE_TYPES[value["type"]]
    fields = {key: item for key, item in value.items() if key in cls.model_fields}
    return cls.model_validate(fields)


def make_client(url: str, *, service_token: str, timeout_s: float,
                transport: httpx.AsyncBaseTransport | None = None) -> LangGraphClient:
    """An Agent Server client. Not `get_client`: that sends LANGSMITH_API_KEY along as x-api-key.
    httpx only retries failed connections, never a request the server received, so one call is
    one run (no repeated runs spending tokens)."""
    return LangGraphClient(httpx.AsyncClient(
        base_url=url,
        transport=transport or httpx.AsyncHTTPTransport(retries=1),
        timeout=httpx.Timeout(timeout_s, connect=10),
        headers={"x-service-token": service_token},
    ))


class RemoteAgent:
    """A specialist on its own Agent Server, in place of its compiled graph: `node` is the
    assistant's graph node, `ainvoke` what `ask_<route>` calls (app/agents/delegation.py).

    Calls are stateless (no thread_id): the main app's thread is the only copy of a conversation.
    The context goes in the request body, since RemoteGraph doesn't forward the parent's runtime.
    """

    def __init__(self, spec, url: str, *, service_token: str, timeout_s: float, remote: Any = None):
        self.spec, self.url = spec, url
        self.remote = remote or RemoteGraph(
            GRAPH, client=make_client(url, service_token=service_token, timeout_s=timeout_s),
            distributed_tracing=True)

    async def node(self, state: dict, config: RunnableConfig, runtime: Runtime[AgentContext]) -> dict:
        return await self.ainvoke({"messages": state["messages"]}, config, context=runtime.context)

    async def ainvoke(self, input: dict, config: RunnableConfig | None = None, *, context: AgentContext) -> dict:
        config = dict(config or {})
        # checkpoint_ns stays: RemoteGraph drops it from the request and prefixes streamed chunks with it.
        config["configurable"] = {k: v for k, v in config.get("configurable", {}).items() if k != "thread_id"}
        try:
            output = await self.remote.ainvoke(input, config, context=asdict(context),
                                               headers={"x-smallie-user-id": context.user_id})
        except RemoteException as exc:
            raise _openai_error(exc, self.url) from exc
        new = [as_message(m) for m in output.get("messages", [])][len(input.get("messages", [])):]
        for counter in _usage_counters(config):
            for message in new:
                if isinstance(message, AIMessage):
                    counter.count(message)
        # Only these keys: anything else (route, direct, failed) could steer the parent graph.
        result = {"messages": new}
        if "findings" in output:
            result["findings"] = output["findings"]
        return result


def _usage_counters(config: RunnableConfig) -> list[UsageCounter]:
    callbacks = config.get("callbacks") or []
    handlers = callbacks if isinstance(callbacks, list) else getattr(callbacks, "handlers", [])
    return [h for h in handlers if isinstance(h, UsageCounter)]


def _openai_error(exc: RemoteException, url: str) -> Exception:
    data = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {}
    if found := _OPENAI_ERRORS.get(data.get("error", "")):
        cls, status = found
        response = httpx.Response(status, request=httpx.Request("POST", url))
        return cls(f"{data['error']} from the {url} agent", response=response, body=None)
    return exc
