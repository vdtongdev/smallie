"""An agent as another agent's tool: `ask_<route>(question)` runs it on just that question.

The result is the agent's answer (`spec.result`) for the model, plus the tables and charts its
tools produced as the tool's artifact (app/agents/artifacts.py), so a caller can chart or keep
them without the model copying any data.
"""

import logging

import openai
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import HumanMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from app.agents.artifacts import collect
from app.agents.base import AgentSpec
from app.core.memory import AgentContext

log = logging.getLogger(__name__)

RESULT_CHARS = 6000  # of each answer; keeps the caller's write-up prompt small


def ask_tool_name(spec: AgentSpec) -> str:
    return f"ask_{spec.route}"


def make_ask_tool(spec: AgentSpec, agent: Runnable) -> BaseTool:
    async def ask(question: str, runtime: ToolRuntime[AgentContext]) -> tuple[str, dict]:
        try:
            # The tool's config carries the stream hook, so a remote agent's tool calls still show.
            # The route in the namespace names the agent behind its stream chunks (parallel asks share one `tools` node).
            config = runtime.config
            ns = config.get("configurable", {}).get("checkpoint_ns")
            if ns:
                config = {**config, "configurable": {**config["configurable"], "checkpoint_ns": f"{ns}|{spec.route}:{runtime.tool_call_id}"}}
            state = await agent.ainvoke({"messages": [HumanMessage(question)]}, config, context=runtime.context)
        except (openai.RateLimitError, openai.AuthenticationError):
            raise  # the chat route reports these to the user
        except Exception as exc:  # noqa: BLE001 - the caller can still answer without it
            log.exception("Delegation to %s failed", spec.route)
            return f"The {spec.route} agent failed ({type(exc).__name__}); it has no answer.", {}
        answer = spec.result(state)[:RESULT_CHARS] or f"The {spec.route} agent had no answer."
        return answer, collect(state.get("messages", []))

    ask.__doc__ = f"""{spec.tool_description}

    Args:
        question: A complete, self-contained question (the agent doesn't see the chat), in the user's language.
    """
    return tool(ask_tool_name(spec), parse_docstring=True, response_format="content_and_artifact")(ask)
