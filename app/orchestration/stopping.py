"""Stopping a running reply: what a stopped chat looks like in its checkpoint, and how it resumes."""

import re

from langchain_core.messages import HumanMessage

from app.agents.base import STEER
from app.core.memory import TEAM_MODE
from app.core.prompts import STEER_MESSAGE

RESUME_WORDS = ("resume", "continue", "go on", "tiếp tục", "làm tiếp", "tiếp đi")
RESUME_MAX_WORDS = 5
_RESUME = re.compile(r"(?<!\w)(?:" + "|".join(RESUME_WORDS) + r")(?!\w)", re.IGNORECASE)


def is_stopped(snapshot) -> bool:
    """A cut-off run has steps left (`next`) and is not waiting on a question (`interrupts`)."""
    return bool(getattr(snapshot, "next", ())) and not snapshot.interrupts


def is_resume(message: str) -> bool:
    """A short message with a resume word continues the run as it was; any other message is a note."""
    return len(message.split()) <= RESUME_MAX_WORDS and bool(_RESUME.search(message))


async def add_note(assistant, config, note: str) -> None:
    """Add the user's note and the hidden steer message to a stopped run's thread. No `as_node`: the
    steps left stay, and the cut-off step reruns with the note. The update goes to the graph that
    holds the cut-off step: the team's own checkpoint for a team turn, so its earlier waves stay."""
    snapshot = await assistant.aget_state(config, subgraphs=True)
    target = next((task.state.config for task in snapshot.tasks
                   if task.name == TEAM_MODE and hasattr(task.state, "config")), config)
    await assistant.aupdate_state(
        target, {"messages": [HumanMessage(note), HumanMessage(STEER_MESSAGE, name=STEER)]})


def stop_namespace(user_id: str) -> tuple[str, ...]:
    return ("users", user_id, "stopped")


async def load_stop(store, user_id: str, conversation_id: str) -> dict | None:
    """The model calls a stopped chat's run already spent, or None."""
    item = await store.aget(stop_namespace(user_id), conversation_id)
    return item.value if item else None


async def save_stop(store, user_id: str, conversation_id: str, model_calls: int) -> None:
    await store.aput(stop_namespace(user_id), conversation_id, {"model_calls": model_calls})


async def clear_stop(store, user_id: str, conversation_id: str) -> None:
    await store.adelete(stop_namespace(user_id), conversation_id)
