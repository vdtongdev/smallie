import asyncio
import json
import logging
from collections.abc import AsyncIterator

import openai
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from pydantic import BaseModel, Field
from langgraph.types import Command
from starlette.background import BackgroundTask

from app.core.accounts import get_profile
from app.agents.artifacts import artifacts_from
from app.agents.base import TOOL_RESULT_PREVIEW
from app.agents.registry import ROUTES, SILENT_STEPS, STEPS
from app.agents.remote import RUN_NODE, as_message
from app.orchestration.orchestrator import HANDOFF
from app.orchestration.router import ORCHESTRATOR
from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.conversations import create_conversation, get_conversation, list_conversations, touch_conversation
from app.core.graph import extract_facts, recall_facts
from app.core.memory import TEAM_MODE, AgentContext, load_recent_chats, save_turn, turns
from app.core.usage import UsageCounter, add_usage

router = APIRouter(tags=["chat"])
log = logging.getLogger(__name__)

TEAM_EVENTS = frozenset({"round", "board", "synthesize", "needs_human", "spoke", "charts"})  # custom events of a team turn


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


@router.post("/chat/{kind}")
async def chat(
    kind: str,
    body: ChatRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    if kind not in ("auto", TEAM_MODE) and kind not in ROUTES:
        raise HTTPException(404, "Unknown agent")
    if body.conversation_id:
        conversation = await asyncio.to_thread(get_conversation, user_id, body.conversation_id)
        if not conversation:
            raise HTTPException(404, "Conversation not found")
    else:
        conversation = await asyncio.to_thread(create_conversation, user_id, kind, body.message)

    assistant = request.app.state.assistant
    store = request.app.state.store
    config = {"configurable": {"thread_id": conversation["id"]}}
    cross_chat = get_settings().cross_chat_history
    finished = False
    usage = UsageCounter()

    async def events():
        nonlocal finished
        yield _sse({"type": "conversation", "id": conversation["id"], "title": conversation["title"]})
        try:
            memories, recent_chats, user = await asyncio.gather(
                asyncio.to_thread(recall_facts, user_id),
                _recent_chats(request, user_id, conversation["id"]) if cross_chat else _empty(),
                _user(user_id),
            )
            stream = assistant.astream(
                await _chat_input(assistant, config, kind, body.message),
                config={**config, "callbacks": [usage], "metadata": {"user_id": user_id}},  # filter traces per user
                context=AgentContext(user_id=user_id, user=user, memories=memories,
                                     recent_chats=recent_chats, agent=kind),
                stream_mode=["messages", "updates", "custom"],
                subgraphs=True,
            )
            async for event in _events_from(stream):
                yield _sse(event)
            finished = True
        except openai.RateLimitError:
            yield _sse({"type": "error", "code": "rate_limit",
                        "message": "OpenRouter rate limit reached. Wait a minute or check your daily free quota."})
        except openai.AuthenticationError:
            yield _sse({"type": "error", "code": "auth", "message": "Invalid or missing OPENROUTER_API_KEY."})
        except Exception as exc:  # noqa: BLE001 - surface the failure to the UI
            log.exception("Agent run failed")
            yield _sse({"type": "error", "code": "internal", "message": str(exc)[:500]})
        finally:
            await asyncio.to_thread(touch_conversation, conversation["id"])
            try:  # failed and cut-off runs still spent tokens
                await add_usage(store, user_id, usage)
            except Exception:  # noqa: BLE001 - usage is best effort
                log.exception("Saving token usage failed")
        yield _sse({"type": "done"})

    async def remember_turn():
        # Runs after the response is sent, so the embedding and extraction calls don't delay "done".
        if not finished:
            return
        try:
            snapshot = await assistant.aget_state(config)
            messages = snapshot.values.get("messages", [])
        except Exception:  # noqa: BLE001 - history and memory are best effort
            log.exception("Reading the finished turn failed")
            return
        if snapshot.interrupts:  # paused on a question: nothing to remember yet
            return
        pairs = turns(messages)
        answer = pairs[-1][1] if pairs else ""
        if cross_chat:
            try:
                await save_turn(store, user_id, conversation, body.message, answer)
            except Exception:  # noqa: BLE001
                log.exception("Saving chat history failed")
        # Only the orchestrator's turns: plain data lookups (sql, chart, docs) rarely say anything
        # about the user and would cost an extra model call each.
        if answer and _answered_by(messages) == ORCHESTRATOR:
            extraction = UsageCounter()
            try:
                await extract_facts(user_id, body.message, answer, [extraction], conversation_id=conversation["id"])
            except Exception:  # noqa: BLE001
                log.exception("Extracting facts failed")
            try:
                await add_usage(store, user_id, extraction)
            except Exception:  # noqa: BLE001
                log.exception("Saving token usage failed")

    return StreamingResponse(events(), media_type="text/event-stream", background=BackgroundTask(remember_turn),
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _chat_input(assistant, config, kind: str, message: str) -> dict | Command:
    """A paused team turn takes the message as the answer to its question; else a new user message."""
    if kind == TEAM_MODE and (await assistant.aget_state(config)).interrupts:
        return Command(resume=message)
    return {"messages": [{"role": "user", "content": message}]}


def _answered_by(messages: list) -> str | None:
    """Name of the AIMessage that holds the turn's final text."""
    return next((m.name for m in reversed(messages) if isinstance(m, AIMessage) and m.text), None)


async def _empty() -> str:
    return ""


async def _user(user_id: str) -> str:
    profile = await asyncio.to_thread(get_profile, user_id)
    if not profile or not profile["username"]:
        return ""
    return f"{profile['name'] or profile['username']} (username: {profile['username']})"


async def _recent_chats(request: Request, user_id: str, current_id: str) -> str:
    """Latest turns of the user's other chats; a failure here must not break the chat."""
    try:
        others = [c for c in await asyncio.to_thread(list_conversations, user_id) if c["id"] != current_id]
        return await load_recent_chats(request.app.state.checkpointer, others)
    except Exception:  # noqa: BLE001
        log.exception("Loading recent chats failed")
        return ""


async def _events_from(stream) -> AsyncIterator[dict]:
    """SSE events for a `subgraphs=True` stream, plus a `step` event whenever a workflow
    moves to another of its steps listed in its spec's `steps`."""
    current = None
    speakers: dict[str, str] = {}  # "speak:<task id>" -> route, from the team's internal speaker event
    async for namespace, mode, chunk in stream:
        if mode == "custom" and isinstance(chunk, dict) and chunk.get("type") == "speaker":
            speakers[chunk["ns"].rsplit("|", 1)[-1]] = chunk["agent"]  # ns is "team:<id>|speak:<task id>"
            continue
        # The team is a subgraph node, so `speak:<id>` sits after its parent segment; inner depth varies too.
        at = next((i for i, segment in enumerate(namespace) if segment in speakers), None)
        agent = speakers[namespace[at]] if at is not None else None
        if agent:
            namespace = namespace[at:]  # keep `speak` so the chunk still counts as inside a subgraph
        else:
            step = _step(namespace)
            if step and step != current:
                current = step
                yield {"type": "step", "agent": step[0], "name": step[1]}
        for event in _to_events(mode, chunk, namespace, in_round=bool(agent)):
            yield {**event, "agent": agent} if agent and event["type"] in ("token", "tool_call", "tool_result") else event


def _step(namespace: tuple[str, ...]) -> tuple[str, str] | None:
    """(route, step) for chunks from inside a workflow step listed in its spec's `steps`."""
    # An agent server's wrapper node sits between the route and its steps.
    names = [name for name in (segment.split(":", 1)[0] for segment in namespace) if name != RUN_NODE]
    if len(names) < 2:
        return None
    route, step = names[:2]
    return (route, step) if step in STEPS.get(route, ()) else None


def _message(value):
    """A message object; a remote agent's chunks are JSON dicts. None for a type the UI doesn't
    show (a remote RemoveMessage, say), so one odd message can't end the whole reply."""
    try:
        return as_message(value)
    except ValueError:
        return None


def _to_events(mode: str, chunk, namespace: tuple[str, ...] = (), in_round: bool = False) -> list[dict]:
    """SSE events for one stream chunk. `namespace` is () for the assistant graph itself and
    non-empty for chunks from inside an agent subgraph. `in_round`: the chunk is a team
    speaker's, whose research notes are its answer, so silent steps stream too."""
    if mode == "messages":
        message, metadata = _message(chunk[0]), chunk[1]
        if isinstance(message, AIMessageChunk) and metadata.get("langgraph_node") == "model" and message.text:
            nodes = [segment.split(":", 1)[0] for segment in namespace]
            # A researcher's notes, or an agent the orchestrator called as a tool: the
            # orchestrator writes the answer from their results, so only it streams.
            if "tools" in nodes or (not in_round and any(node in SILENT_STEPS for node in nodes)):
                return []
            return [{"type": "token", "text": message.text}]
        return []

    if mode == "custom":
        return [chunk] if isinstance(chunk, dict) and chunk.get("type") in TEAM_EVENTS else []

    # mode == "updates": {node_name: state_update}
    events = []
    for node, update in chunk.items():
        if not isinstance(update, dict) or (namespace and node == RUN_NODE):
            continue
        if not namespace:
            # Top level: only the router's choice or the orchestrator's handoff is new. The
            # agent nodes' messages were already streamed from inside their subgraph.
            if update.get("failed"):
                # The router's agent found no answer: the UI drops its reply, the orchestrator takes over.
                events.append({"type": "fallback", "agent": update["failed"]})
            if update.get("route"):
                events.append({"type": "agent", "name": update["route"]})
            continue
        messages = update.get("messages", [])
        for message in map(_message, messages if isinstance(messages, list) else [messages]):
            if isinstance(message, AIMessage):
                events += [{"type": "tool_call", "name": c["name"], "args": c["args"]}
                           for c in message.tool_calls if c["name"] != HANDOFF]  # shown as the agent badge
            elif isinstance(message, ToolMessage) and message.name != HANDOFF:
                event = {"type": "tool_result", "name": message.name,
                         "content": (message.text or str(message.content))[:TOOL_RESULT_PREVIEW]}
                if charts := artifacts_from([message], "charts"):
                    event["charts"] = charts  # drawn by the UI; query results (tables) stay server-side
                events.append(event)
    return events
