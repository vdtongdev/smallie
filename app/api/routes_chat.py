import asyncio
import json
import logging
from collections.abc import AsyncIterator

import openai
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.background import BackgroundTask

from app.core.accounts import get_profile
from app.agents.artifacts import artifacts_from
from app.agents.registry import ROUTES, SILENT_STEPS, STEPS
from app.orchestration.orchestrator import HANDOFF
from app.orchestration.router import ORCHESTRATOR
from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.conversations import create_conversation, get_conversation, list_conversations, touch_conversation
from app.core.db import documents, get_engine
from app.core.graph import extract_facts, recall_facts
from app.core.memory import AgentContext, load_recent_chats, save_turn, turns
from app.core.usage import UsageCounter, add_usage

router = APIRouter(tags=["chat"])
log = logging.getLogger(__name__)

TOOL_RESULT_PREVIEW = 4000
MAX_ATTACHMENTS = 5
ATTACHED = "Attached: "  # line the UI shows as chips (app.js addUserMessage)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None
    # Document ids uploaded from the chat box; they are ordinary uploads, available in every chat.
    attachments: list[str] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)


def attachment_names(user_id: str, ids: list[str]) -> list[str]:
    """File names of the user's attached documents, in the given order. 404 for an id that isn't
    one of theirs (without naming it), 409 for one that isn't searchable yet."""
    if not ids:
        return []
    with get_engine().connect() as conn:
        rows = {row.id: row for row in conn.execute(select(documents.c.id, documents.c.filename, documents.c.status)
                                                     .where(documents.c.user_id == user_id, documents.c.id.in_(ids)))}
    names = []
    for document_id in ids:
        row = rows.get(document_id)
        if row is None:
            raise HTTPException(404, "Attachment not found")
        if row.status == "processing":
            raise HTTPException(409, f"{row.filename} is still being read")
        if row.status == "failed":
            raise HTTPException(409, f"{row.filename} could not be read")
        names.append(row.filename)
    return names


def with_attachments(message: str, names: list[str]) -> str:
    """The message the agents see: the file names make the router and the search point at those files."""
    return f"{message}\n\n{ATTACHED}{', '.join(names)}" if names else message


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


@router.post("/chat/{kind}")
async def chat(
    kind: str,
    body: ChatRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
):
    if kind != "auto" and kind not in ROUTES:
        raise HTTPException(404, "Unknown agent")
    # Checked before a new conversation is created, so a bad attachment leaves no empty chat behind.
    message = with_attachments(body.message, await asyncio.to_thread(attachment_names, user_id, body.attachments))
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
                {"messages": [{"role": "user", "content": message}]},
                config={**config, "callbacks": [usage]},
                context=AgentContext(user_id=user_id, user=user, memories=memories,
                                     recent_chats=recent_chats, agent=kind),
                stream_mode=["messages", "updates"],
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
            messages = (await assistant.aget_state(config)).values.get("messages", [])
        except Exception:  # noqa: BLE001 - history and memory are best effort
            log.exception("Reading the finished turn failed")
            return
        pairs = turns(messages)
        answer = pairs[-1][1] if pairs else ""
        if cross_chat:
            try:
                await save_turn(store, user_id, conversation, message, answer)
            except Exception:  # noqa: BLE001
                log.exception("Saving chat history failed")
        # Only the orchestrator's turns: plain data lookups (sql, chart, docs) rarely say anything
        # about the user and would cost an extra model call each.
        if answer and _answered_by(messages) == ORCHESTRATOR:
            extraction = UsageCounter()
            try:
                await extract_facts(user_id, message, answer, [extraction], conversation_id=conversation["id"])
            except Exception:  # noqa: BLE001
                log.exception("Extracting facts failed")
            try:
                await add_usage(store, user_id, extraction)
            except Exception:  # noqa: BLE001
                log.exception("Saving token usage failed")

    return StreamingResponse(events(), media_type="text/event-stream", background=BackgroundTask(remember_turn),
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
    async for namespace, mode, chunk in stream:
        step = _step(namespace)
        if step and step != current:
            current = step
            yield {"type": "step", "agent": step[0], "name": step[1]}
        for event in _to_events(mode, chunk, namespace):
            yield event


def _step(namespace: tuple[str, ...]) -> tuple[str, str] | None:
    """(route, step) for chunks from inside a workflow step listed in its spec's `steps`."""
    if len(namespace) < 2:
        return None
    route, step = (segment.split(":", 1)[0] for segment in namespace[:2])
    return (route, step) if step in STEPS.get(route, ()) else None


def _to_events(mode: str, chunk, namespace: tuple[str, ...] = ()) -> list[dict]:
    """SSE events for one stream chunk. `namespace` is () for the assistant graph itself and
    non-empty for chunks from inside an agent subgraph."""
    if mode == "messages":
        message, metadata = chunk
        if isinstance(message, AIMessageChunk) and metadata.get("langgraph_node") == "model" and message.text:
            nodes = [segment.split(":", 1)[0] for segment in namespace]
            # A researcher's notes, or an agent the orchestrator called as a tool: the
            # orchestrator writes the answer from their results, so only it streams.
            if "tools" in nodes or any(node in SILENT_STEPS for node in nodes):
                return []
            return [{"type": "token", "text": message.text}]
        return []

    # mode == "updates": {node_name: state_update}
    events = []
    for node, update in chunk.items():
        if not isinstance(update, dict):
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
        for message in update.get("messages", []):
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
