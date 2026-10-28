"""Stopping a running reply: what a stopped chat looks like in its checkpoint, and how it resumes."""

import re

from langchain_core.messages import HumanMessage
from langgraph.constants import TASKS

from app.agents.base import STEER
from app.core.memory import TEAM_MODE
from app.core.prompts import STEER_MESSAGE
from app.orchestration.team import steer

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
    """Add the user's note and the hidden steer message to a stopped run's thread, so the cut-off step runs again
    with it. No `as_node`: the steps left stay. A team turn is updated in the team's own checkpoint, so its earlier
    waves stay, with `team.steer`'s `Command`: its `goto` names the cut-off step, which the team reaches by `Command`
    or `Send`, so an update with no `goto` would leave nothing to run."""
    messages = [HumanMessage(note), HumanMessage(STEER_MESSAGE, name=STEER)]
    snapshot = await assistant.aget_state(config, subgraphs=True)
    sub = next((task.state for task in snapshot.tasks if task.name == TEAM_MODE and hasattr(task.state, "config")), None)
    if sub is None:
        await assistant.aupdate_state(config, {"messages": messages})
        return
    saved = await assistant.checkpointer.aget_tuple(sub.config)
    sends = saved.checkpoint["channel_values"].get(TASKS, []) if saved else []
    await assistant.aupdate_state(sub.config, steer(sub.values, sub.next, sends, messages))


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
