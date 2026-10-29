"""Specialists running as their own LangGraph Agent Servers, called through `RemoteGraph`.

What comes back over the wire is JSON: messages arrive as dicts, both streamed and in the final
state. `as_message` turns them back into message objects. LangGraph's own conversion
(`add_messages`) moves `usage_metadata` into `additional_kwargs` (spike 2), which would lose the
token counts.
"""

import asyncio
import base64
import json
import logging
import os
import time
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

from app.core.config import get_settings
from app.core.llm import NoModelSettings
from app.core.memory import TEAM_MODE, AgentContext
from app.core.usage import UsageCounter

log = logging.getLogger(__name__)

GRAPH = "agent"  # the graph name in langgraph.json; every agent server serves one
# The agent server's wrapper node (app/agents/server.py). Its update repeats what the agent's own
# steps already streamed, so the chat events skip it.
RUN_NODE = "run"
# Errors a remote specialist reports by name only, raised again as the exceptions the chat route
# turns into its "rate limit" / "invalid key" messages. Matched by name suffix, so
# langchain_openai's OpenAI-prefixed subclasses count too.
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
    cls = _MESSAGE_TYPES.get(value.get("type"))
    if cls is None:
        raise ValueError(f"unknown message type {value.get('type')!r}")
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


_METADATA_IDENTITY = ("http://metadata.google.internal/computeMetadata/v1/instance/"
                      "service-accounts/default/identity")
_TOKEN_TTL_S = 50 * 60  # Google ID tokens last an hour
_TOKEN_MARGIN_S = 5 * 60  # fetch a new one this long before the token's exp
_tokens: dict[str, tuple[str, float]] = {}  # audience -> (token, expiry on the monotonic clock)


def _token_ttl(token: str) -> float:
    """Seconds to keep `token`: until its exp claim minus the margin (the metadata server may hand
    back a cached token with minutes left), at most _TOKEN_TTL_S. No signature check: we only
    read our own token's lifetime."""
    try:
        payload = token.split(".")[1]
        exp = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"]
        return min(exp - time.time() - _TOKEN_MARGIN_S, _TOKEN_TTL_S)
    except (IndexError, ValueError, KeyError, TypeError):
        return _TOKEN_TTL_S


async def id_token(audience: str, *, transport: httpx.AsyncBaseTransport | None = None) -> str | None:
    """A Google ID token for calling the Cloud Run service at `audience` (its URL), from the metadata
    server; the agent services only accept callers with roles/run.invoker. None off Cloud Run
    (K_SERVICE unset), e.g. against the local compose stack."""
    if not os.environ.get("K_SERVICE"):
        return None
    audience = audience.rstrip("/")  # Cloud Run checks it against the bare service URL
    token, expires = _tokens.get(audience, ("", 0.0))
    if time.monotonic() < expires:
        return token
    async with httpx.AsyncClient(transport=transport, timeout=10) as client:
        response = await client.get(_METADATA_IDENTITY, params={"audience": audience},
                                    headers={"Metadata-Flavor": "Google"})
        response.raise_for_status()
    _tokens[audience] = (response.text, time.monotonic() + _token_ttl(response.text))
    return response.text


class CallBudgetExceeded(RuntimeError):
    """A chat message reached MAX_MESSAGE_MODEL_CALLS: no further specialist is called."""


class RemoteAgent:
    """A specialist on its own Agent Server, in place of its compiled graph: `node` is the
    assistant's graph node, `ainvoke` what `ask_<route>` calls (app/agents/delegation.py).

    Calls are stateless (no thread_id): the main app's thread is the only copy of a conversation.
    The context goes in the request body, since RemoteGraph doesn't forward the parent's runtime.
    """

    def __init__(self, spec, url: str, *, service_token: str, timeout_s: float, remote: Any = None):
        self.spec, self.url, self.timeout_s = spec, url, timeout_s
        self.remote = remote or RemoteGraph(
            GRAPH, client=make_client(url, service_token=service_token, timeout_s=timeout_s),
            distributed_tracing=True)

    async def node(self, state: dict, config: RunnableConfig, runtime: Runtime[AgentContext]) -> dict:
        return await self.ainvoke({"messages": state["messages"]}, config, context=runtime.context)

    async def ainvoke(self, input: dict, config: RunnableConfig | None = None, *, context: AgentContext) -> dict:
        config = dict(config or {})
        counters = _usage_counters(config)
        settings = get_settings()
        name, budget = (("TEAM_MAX_MESSAGE_MODEL_CALLS", settings.team_max_message_model_calls)
                        if context.agent == TEAM_MODE else ("MAX_MESSAGE_MODEL_CALLS", settings.max_message_model_calls))
        if any(counter.model_calls >= budget for counter in counters):
            raise CallBudgetExceeded(f"This message used its {budget} model calls ({name}); "
                                     f"the {self.spec.route} agent was not called.")
        # Only the keys RemoteGraph itself reads go on: checkpoint_ns (it drops it from the request and prefixes
        # streamed chunks with it) and the stream hook (it pushes the remote agent's chunks into the parent's stream;
        # it never reaches the request, being no primitive). LangGraph's other keys (thread_id, __pregel_resuming on a
        # resumed run) would make the server reject "configurable" sent together with "context".
        configurable = config.get("configurable", {})
        config["configurable"] = {k: configurable[k] for k in ("checkpoint_ns", "__pregel_stream") if k in configurable}
        headers = {"x-smallie-user-id": context.user_id}
        if token := await id_token(self.url):
            headers["Authorization"] = f"Bearer {token}"
        try:
            # The client's timeout is per read; keep-alives from a stuck run would never trip it.
            async with asyncio.timeout(self.timeout_s):
                # cancel: a run the main app stopped waiting for (timeout, chat closed) stops spending
                # tokens; the server default "continue" would finish it uncounted.
                output = await self.remote.ainvoke(input, config, context=asdict(context),
                                                   headers=headers, on_disconnect="cancel")
        except RemoteException as exc:
            raise _openai_error(exc, self.url) from exc
        if output is None:  # the run ended without sending its final state
            raise RuntimeError(f"The {self.spec.route} agent at {self.url} returned no result.")
        # The server counts its run's model calls (this process can't see them): the loop guard
        # above and the usage box both need them.
        if "usage" not in output:
            log.warning("The %s agent at %s reported no usage; its calls don't count toward the budget",
                        self.spec.route, self.url)
        for counter in counters:
            counter.add(output.get("usage") or {})
        new = [as_message(m) for m in output.get("messages", [])][len(input.get("messages", [])):]
        # Only these keys: anything else (route, direct, failed) could steer the parent graph.
        result = {"messages": new}
        for key in ("findings", "tools"):
            if key in output:
                result[key] = output[key]
        return result


def remote_agents(specs) -> dict[str, RemoteAgent]:
    """A RemoteAgent per spec, from AGENT_URLS. Every registered agent needs a URL: a missing one
    fails at startup, not at the first chat that routes to it."""
    s = get_settings()
    urls = s.agent_url_map
    if missing := [spec.route for spec in specs if not urls.get(spec.route)]:
        raise RuntimeError(f"AGENT_URLS has no URL for: {', '.join(missing)}. Set AGENT_URLS "
                           "(see .env.example) and start the agent servers.")
    return {spec.route: RemoteAgent(spec, urls[spec.route], service_token=s.service_token,
                                    timeout_s=s.agent_timeout_s) for spec in specs}


def _usage_counters(config: RunnableConfig) -> list[UsageCounter]:
    callbacks = config.get("callbacks") or []
    handlers = callbacks if isinstance(callbacks, list) else getattr(callbacks, "handlers", [])
    return [h for h in handlers if isinstance(h, UsageCounter)]


def _openai_error(exc: RemoteException, url: str) -> Exception:
    data = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {}
    if data.get("error") == "NoModelSettings":
        return NoModelSettings()
    name = str(data.get("error", ""))
    # endswith: langchain_openai's subclasses (OpenAIRateLimitError, OpenAIAuthenticationError)
    found = next((v for k, v in _OPENAI_ERRORS.items() if name.endswith(k)), None)
    if found:
        cls, status = found
        response = httpx.Response(status, request=httpx.Request("POST", url))
        return cls(f"{data['error']} from the {url} agent", response=response, body=None)
    return exc
