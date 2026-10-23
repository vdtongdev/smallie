"""Team mode: a judge decides after each draft whether a critic checks it (design:
docs/superpowers/specs/2026-10-23-agent-team-design.md). This module holds the pure helpers."""

from typing import Literal

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, create_model, field_validator

from app.agents.base import AgentSpec, RouteInfo
from app.core.config import get_settings
from app.core.llm import get_chat_model
from app.core.memory import TEAM_MODE
from app.core.prompts import reply_language
from app.orchestration.prompts import JUDGE_PROMPT

TEAM = TEAM_MODE


class Verdict(BaseModel):
    action: Literal["approve", "critique", "revise", "needs_human"]
    critic: str | None = None
    issues: list[str] = []
    question: str = ""

    @field_validator("issues")
    @classmethod
    def _cap(cls, issues: list[str]) -> list[str]:
        return issues[:5]


def verdict_model(allowed: list[AgentSpec]) -> type[Verdict]:
    """Verdict whose `critic` only accepts an allowed route (plain Verdict when none)."""
    if not allowed:
        return Verdict
    routes = Literal[tuple(spec.route for spec in allowed)]  # type: ignore[valid-type]
    return create_model("TeamVerdict", __base__=Verdict, critic=(routes | None, None))


def allowed_critics(available: list[tuple[AgentSpec, RouteInfo]], author: str) -> list[AgentSpec]:
    return [spec for spec, _ in available if spec.review and spec.route != author]


def coerce(verdict: Verdict, allowed: list[AgentSpec]) -> Verdict:
    """Make a verdict safe to route on: a critic outside `allowed` becomes the first allowed one."""
    update: dict = {}
    if verdict.action == "needs_human" and not verdict.question.strip():
        update = {"action": "approve", "critic": None}
    elif verdict.action in ("critique", "revise"):
        if not allowed:
            update = {"action": "approve" if verdict.action == "critique" else "revise", "critic": None}
        elif verdict.critic not in [spec.route for spec in allowed]:
            update = {"critic": allowed[0].route}
    else:
        update = {"critic": None}
    return verdict.model_copy(update=update)


async def ask_judge(messages: list[AnyMessage], team: dict, allowed: list[AgentSpec],
                    config: RunnableConfig) -> Verdict:
    """One model call; `config` carries the usage counter's callbacks."""
    settings = get_settings()
    question = next((m.text for m in reversed(messages) if isinstance(m, HumanMessage)), "")
    critiques = "\n\n".join(f"{c['critic']}: {c['text']}" for c in team.get("critiques", [])) or "none yet"
    system = JUDGE_PROMPT.format(
        critics="\n".join(f"- {spec.route}: {spec.review}" for spec in allowed) or "none: choose approve, revise or needs_human.",
        turns=team.get("turns", 0), max_turns=settings.team_max_turns, language=reply_language(messages))
    # Texts go into the message, not a template: drafts may contain braces.
    human = f"USER MESSAGE:\n{question}\n\nDRAFT:\n{team.get('draft', '')}\n\nCRITIQUES:\n{critiques}"
    judge = get_chat_model(settings.openrouter_orchestrator_model or None).with_structured_output(
        verdict_model(allowed), method="function_calling")
    return coerce(await judge.ainvoke([SystemMessage(system), HumanMessage(human)], config=config), allowed)
