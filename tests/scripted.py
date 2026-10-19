"""Chat models for tests: scripted replies instead of OpenRouter."""

import json
import re
from collections.abc import Iterator

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


class ScriptedModel(BaseChatModel):
    """Replies with the scripted messages in order, whatever the agent; streams word by word."""

    replies: Iterator[AIMessage]
    prompts: list = []
    tools: list = []  # the tools offered on each call (as OpenAI tool dicts)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        from langchain_core.utils.function_calling import convert_to_openai_tool

        self.tools.append([convert_to_openai_tool(t) for t in tools])
        return self

    def _next(self, messages) -> AIMessage:
        return next(self.replies)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.prompts.append(messages)
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.prompts.append(messages)
        reply = self._next(messages)
        if reply.tool_calls:
            chunks = [{"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                      for i, c in enumerate(reply.tool_calls)]
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=chunks))
            return
        for piece in filter(None, re.split(r"(\s+)", reply.content)):
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=piece))
            if run_manager:
                run_manager.on_llm_new_token(piece, chunk=chunk)
            yield chunk


class PerAgentModel(ScriptedModel):
    """Scripted replies per agent, picked by a phrase of that agent's system prompt, so agents
    that run in parallel (the orchestrator's delegations) each get their own script."""

    scripts: dict[str, Iterator[AIMessage]] = {}

    def _next(self, messages) -> AIMessage:
        system = messages[0].content if messages else ""
        for phrase, replies in self.scripts.items():
            if phrase in system:
                return next(replies)
        raise AssertionError(f"no script for prompt: {system[:80]!r}")


def scripted(*replies: AIMessage) -> ScriptedModel:
    return ScriptedModel(replies=iter(replies), prompts=[], tools=[])


def per_agent(**scripts: list[AIMessage]) -> PerAgentModel:
    """per_agent(orchestrator=[...], sql=[...]): keys are agents, matched by PROMPT_PHRASES."""
    return PerAgentModel(replies=iter(()), prompts=[], tools=[],
                         scripts={PROMPT_PHRASES[name]: iter(replies) for name, replies in scripts.items()})


PROMPT_PHRASES = {
    "orchestrator": "You are smallie, the user's assistant.",
    "sql": "You are a data analyst assistant.",
    "docs": "answers questions using the user's uploaded documents",
    "research": "You are a research assistant.",
    "chart": "You are a chart assistant.",
    "image": "You are an image assistant.",
}
