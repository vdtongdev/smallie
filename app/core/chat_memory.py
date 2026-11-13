"""Chat memory: what a chat's agents fetched, saved per step into `chat_data` by the main app and
rendered as one note for every later agent call in the chat (spec 2026-11-13-chat-memory-design.md)."""

import json
import logging
from collections.abc import Sequence

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from app.agents.base import MEMORY_TOOL_NAMES, is_error
from app.core.config import get_settings
from app.core.db import chat_data, get_engine

log = logging.getLogger(__name__)

HEADING = "Data fetched earlier in this chat (oldest first):"
SHORTENED = "(result shortened)"
ARGS_CHARS = 300


def entries_from(messages: Sequence[AnyMessage], findings: str | None = None) -> list[dict]:
    """One entry per tool call paired with its result: `{tool, args, content, artifacts}`. Memory
    tools and error results are skipped; research's `findings` become one `findings` entry."""
    limit = get_settings().chat_data_entry_chars
    results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
    entries = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            result = results.get(call["id"])
            if result is None or call["name"] in MEMORY_TOOL_NAMES:
                continue
            text = result.text or str(result.content)
            if is_error(text):
                continue
            artifacts = result.artifact if isinstance(result.artifact, dict) else {}
            entries.append({"tool": call["name"], "args": call["args"], "content": text[:limit], "artifacts": artifacts})
    if findings and findings.strip():
        entries.append({"tool": "findings", "args": {}, "content": findings[:limit], "artifacts": {}})
    return entries


def save_entries(user_id: str, conversation_id: str, turn: int | None, step: str, agent: str, entries: list[dict]) -> int:
    """Write the entries as rows; returns how many. A chat deleted meanwhile drops them (logged)."""
    if not entries:
        return 0
    rows = [
        {
            "user_id": user_id, "conversation_id": conversation_id, "turn": turn, "step": step,
            "agent": e.get("agent", agent), "tool": e["tool"], "args": e.get("args") or {},
            "content": e["content"], "artifacts": e.get("artifacts") or {},
        }
        for e in entries
    ]
    try:
        with get_engine().begin() as conn:
            conn.execute(insert(chat_data), rows)
    except IntegrityError:
        log.warning("chat data not saved: chat %s no longer exists", conversation_id)
        return 0
    return len(rows)


def _header(row) -> str:
    args = json.dumps(row.args, ensure_ascii=False)[:ARGS_CHARS] if row.args else ""
    parts = [f"[turn {row.turn if row.turn is not None else '?'} | {row.agent} | {row.tool}] {args}".rstrip()]
    tables = [f"{t['id']} ({len(t.get('rows', []))} rows)" for t in row.artifacts.get("tables", [])]
    charts = [str(c["id"]) for c in row.artifacts.get("charts", [])]
    if tables:
        parts.append("tables: " + ", ".join(tables))
    if charts:
        parts.append("charts: " + ", ".join(charts))
    return "; ".join(parts)


def _rows(user_id: str, conversation_id: str) -> list:
    query = select(chat_data).where(chat_data.c.user_id == user_id, chat_data.c.conversation_id == conversation_id)
    with get_engine().connect() as conn:
        return list(conn.execute(query.order_by(chat_data.c.id)))


def render(user_id: str, conversation_id: str) -> str:
    """The chat's data as a note, at most `chat_data_chars` long: header lines for every entry that
    fits, then the newest entries' content in full while the budget lasts. "" without rows."""
    rows = _rows(user_id, conversation_id)
    if not rows:
        return ""
    budget = get_settings().chat_data_chars - len(HEADING) - 1
    short = [f"{_header(r)} {SHORTENED}" for r in rows]
    omitted_note = "(%d earlier entries left out)"
    kept = len(rows)
    used = sum(len(s) + 1 for s in short)
    while kept > 0 and used + (len(omitted_note) + 6 if kept < len(rows) else 0) > budget:
        kept -= 1
        used -= len(short[len(rows) - kept - 1]) + 1
    first = len(rows) - kept
    blocks = short[first:]
    left = budget - used - (len(omitted_note) + 6 if first else 0)
    for i in range(len(rows) - 1, first - 1, -1):
        body = rows[i].content
        header = _header(rows[i])
        extra = len(header) + 1 + len(body) - len(blocks[i - first])
        if extra <= left:
            blocks[i - first] = f"{header}\n{body}"
            left -= extra
        else:
            if i == len(rows) - 1:  # the newest is always shown, cut to fit
                room = left - (len(header) + 1 - len(blocks[i - first])) - len(SHORTENED) - 2
                if room > 0:
                    blocks[i - first] = f"{header}\n{body[:room]} {SHORTENED}"
            break  # the budget is spent: every older entry keeps only its header
    lines = [HEADING]
    if first:
        lines.append(omitted_note % first)
    note = "\n".join(lines + blocks)
    if not blocks or len(note) > get_settings().chat_data_chars:  # budget too small for any entry
        return ""
    return note


def chat_tables(user_id: str, conversation_id: str) -> tuple[list[dict], list[dict]]:
    """(tables, charts) saved in the chat, oldest first, each id once."""
    tables, charts, seen = [], [], set()
    for row in _rows(user_id, conversation_id):
        for kind, found in (("tables", tables), ("charts", charts)):
            for item in row.artifacts.get(kind, []):
                if (kind, item["id"]) not in seen:
                    seen.add((kind, item["id"]))
                    found.append(item)
    return tables, charts
