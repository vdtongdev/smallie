import asyncio
import json
import logging

import openai
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.core.accounts import get_profile
from app.agents.registry import ROUTES
from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.conversations import create_conversation, get_conversation, list_conversations, touch_conversation
from app.core.memory import AgentContext, load_recent_chats, recall, save_turn, turns

router = APIRouter(tags=["chat"])
log = logging.getLogger(__name__)

TOOL_RESULT_PREVIEW = 4000


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
    if kind != "auto" and kind not in ROUTES:
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

    async def events():
        nonlocal finished
        yield _sse({"type": "conversation", "id": conversation["id"], "title": conversation["title"]})
        try:
            memories, recent_chats, user = await asyncio.gather(
                recall(store, user_id, body.message),
                _recent_chats(request, user_id, conversation["id"]) if cross_chat else _empty(),
                _user(user_id),
            )
            stream = assistant.astream(
                {"messages": [{"role": "user", "content": body.message}]},
                config=config,
                context=AgentContext(user_id=user_id, user=user, memories=memories,
                                     recent_chats=recent_chats, agent=kind),
                stream_mode=["messages", "updates"],
                subgraphs=True,
            )
            async for namespace, mode, chunk in stream:
                for event in _to_events(mode, chunk, namespace):
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
        yield _sse({"type": "done"})

    async def remember_turn():
        # Runs after the response is sent, so the embedding call doesn't delay "done".
        if not (finished and cross_chat):
            return
        try:
            state = await assistant.aget_state(config)
            pairs = turns(state.values.get("messages", []))
            answer = pairs[-1][1] if pairs else ""
            await save_turn(store, user_id, conversation, body.message, answer)
        except Exception:  # noqa: BLE001 - history is best effort
            log.exception("Saving chat history failed")

    return StreamingResponse(events(), media_type="text/event-stream", background=BackgroundTask(remember_turn),
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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


def _to_events(mode: str, chunk, namespace: tuple[str, ...] = ()) -> list[dict]:
    """SSE events for one stream chunk. `namespace` is () for the assistant graph itself and
    non-empty for chunks from inside an agent subgraph."""
    if mode == "messages":
        message, metadata = chunk
        if isinstance(message, AIMessageChunk) and metadata.get("langgraph_node") == "model" and message.text:
            return [{"type": "token", "text": message.text}]
        return []

    # mode == "updates": {node_name: state_update}
    events = []
    for node, update in chunk.items():
        if not isinstance(update, dict):
            continue
        if not namespace:
            # Top level: only the router's choice is new. The agent nodes' updates repeat
            # messages already streamed from inside their subgraph.
            if node == "router" and update.get("route"):
                events.append({"type": "agent", "name": update["route"]})
            continue
        for message in update.get("messages", []):
            if isinstance(message, AIMessage):
                events += [{"type": "tool_call", "name": c["name"], "args": c["args"]} for c in message.tool_calls]
            elif isinstance(message, ToolMessage):
                events.append({"type": "tool_result", "name": message.name,
                               "content": str(message.content)[:TOOL_RESULT_PREVIEW]})
    return events
