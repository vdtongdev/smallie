import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.artifacts import artifacts_from
from app.orchestration.orchestrator import MESSAGE_ROUTES
from app.core.auth import get_current_user
from app.core.conversations import delete_conversation, get_conversation, get_conversation_usage, list_conversations
from app.core.memory import forget_conversation

router = APIRouter(tags=["conversations"])


@router.get("/conversations")
def conversations(kind: str | None = None, user_id: str = Depends(get_current_user)):
    return list_conversations(user_id, kind)


@router.get("/conversations/{conversation_id}/messages")
async def messages(conversation_id: str, request: Request, user_id: str = Depends(get_current_user)):
    conversation = await asyncio.to_thread(get_conversation, user_id, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found")
    state = await request.app.state.assistant.aget_state({"configurable": {"thread_id": conversation_id}})
    shown = [m for m in map(_serialize, state.values.get("messages", [])) if m]
    if state.interrupts:  # a paused team turn: its question is not in the thread yet
        shown.append({"role": "assistant", "agent": "orchestrator", "content": state.interrupts[0].value["question"],
                      "pending": True, "team": None, "tool_calls": [], "charts": []})
    return shown


@router.get("/conversations/{conversation_id}/usage")
async def usage(conversation_id: str, user_id: str = Depends(get_current_user)):
    result = await asyncio.to_thread(get_conversation_usage, user_id, conversation_id)
    if not result:
        raise HTTPException(404, "Conversation not found")
    return result


@router.delete("/conversations/{conversation_id}", status_code=204)
async def remove(conversation_id: str, request: Request, user_id: str = Depends(get_current_user)):
    if not await asyncio.to_thread(get_conversation, user_id, conversation_id):
        raise HTTPException(404, "Conversation not found")
    await request.app.state.checkpointer.adelete_thread(conversation_id)
    await forget_conversation(request.app.state.store, user_id, conversation_id)
    await asyncio.to_thread(delete_conversation, user_id, conversation_id)


def _serialize(message) -> dict | None:
    if isinstance(message, HumanMessage):
        return {"role": "user", "content": message.text}
    if isinstance(message, AIMessage):
        return {
            "role": "assistant",
            "agent": MESSAGE_ROUTES.get(message.name or ""),
            "content": message.text,
            "team": message.additional_kwargs.get("team"),
            "tool_calls": [{"name": c["name"], "args": c["args"]} for c in message.tool_calls],
            "charts": artifacts_from([message], "charts"),  # the orchestrator's charts of that turn
        }
    if isinstance(message, ToolMessage):
        return {"role": "tool", "name": message.name, "content": (message.text or str(message.content))[:4000],
                "charts": artifacts_from([message], "charts")}
    return None
