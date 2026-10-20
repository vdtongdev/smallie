"""Specialists running as their own LangGraph Agent Servers, called through `RemoteGraph`.

What comes back over the wire is JSON: messages arrive as dicts, both streamed and in the final
state. `as_message` turns them back into message objects. LangGraph's own conversion
(`add_messages`) moves `usage_metadata` into `additional_kwargs` (spike 2), which would lose the
token counts.
"""

from langchain_core.messages import (
    AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage, ToolMessage,
)

_MESSAGE_TYPES = {
    "ai": AIMessage, "AIMessageChunk": AIMessageChunk, "human": HumanMessage,
    "system": SystemMessage, "tool": ToolMessage,
}


def as_message(value: BaseMessage | dict) -> BaseMessage:
    """A message object, from a remote agent's JSON or as it is."""
    if isinstance(value, BaseMessage):
        return value
    cls = _MESSAGE_TYPES[value["type"]]
    fields = {key: item for key, item in value.items() if key in cls.model_fields}
    return cls.model_validate(fields)
