import re
from pathlib import Path

from langchain_core.messages import AnyMessage, HumanMessage


def load_prompt(path: Path) -> str:
    """Read a prompt file as LF text without its final newline."""
    text = path.read_text(encoding="utf-8-sig")
    return text[:-1] if text.endswith("\n") else text


def load_sections(path: Path, names: tuple[str, ...]) -> dict[str, str]:
    """Split a prompt file at `<!-- section: name -->` lines; the names must match exactly."""
    parts = re.split(r"^<!-- section: ([a-z_]+) -->$", load_prompt(path), flags=re.M)
    found = parts[1::2]
    if parts[0].strip() or sorted(found) != sorted(names):
        raise ValueError(f"{path}: sections {found} do not match {list(names)}")
    return {name: body.strip("\n") for name, body in zip(found, parts[2::2])}


LAYERS = Path(__file__).parent / "layers"

SOUL = load_prompt(LAYERS / "SOUL.md")

_rules = load_sections(LAYERS / "AGENTS.md", ("language", "memory"))
LANGUAGE_RULE = _rules["language"]
MEMORY_RULE = _rules["memory"]

# System prompt of the fact extractor (app/core/graph.py, extract_facts); never formatted.
# Every name in graph.STANDARD must appear in rule 4 (tests/test_graph.py checks it).
EXTRACT_PROMPT = """You pick out durable facts about the user from chat turns (one or more) and return them as (subject, relation, object) triples for the user's knowledge graph.

Rules:
1. Save only facts that stay true across chats: the user's identity, work, goals, preferences (answer style, language, topics), investment profile (risk tolerance, horizon, holdings, constraints), and what the user says about their own files.
2. Every triple must have the user or one of the user's files as its subject or object. Write the user as exactly "User" and make "User" the subject. Write a file name exactly as it appears in the list of the user's files.
3. Write subjects and objects as short names in English, keeping proper names as written.
4. Use one of these relations whenever it fits, with "User" as the subject:
   - one value each: has_name, lives_in, works_at, has_role, risk_tolerance, investment_horizon, prefers_language
   - several values: holds, interested_in, has_skill, studied_at, has_goal, prefers
   Otherwise reuse a relation from the list of relations already in the user's graph. Make up a new short English snake_case relation only when none of them fits.
5. Save one triple per fact. Do not save the same fact again with another relation.
6. Save a conclusion the assistant drew about the user only when the user's own answers in these turns support it, such as the result of a questionnaire the user filled in.
7. Do not save questions, requests, market data, or facts that touch neither the user nor their files.
8. Do not save secrets: passwords, ID, card or account numbers, health details.
9. Do not save anything the user asks you not to remember.
10. Return an empty list when the turns have nothing to remember. Most turns have nothing.
11. When the turns disagree, keep what the latest turn says."""

_user = load_sections(LAYERS / "USER.md", ("user", "profile", "memories", "recent_chats"))
USER_SECTION = "\n\n" + _user["user"]
PROFILE_SECTION = "\n\n" + _user["profile"]
MEMORIES_SECTION = "\n\n" + _user["memories"]
RECENT_CHATS_SECTION = "\n\n" + _user["recent_chats"]

_VIETNAMESE = re.compile(r"[ăâđêôơưàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ]", re.IGNORECASE)


def reply_language(messages: list[AnyMessage]) -> str:
    """The language the answer must be in, from the user's latest message. Named outright (the
    app speaks English and Vietnamese only): "the user's language" left weak models to guess, and
    Vietnamese sources or chats around the question made them answer English questions in Vietnamese."""
    question = next((m.text for m in reversed(messages) if isinstance(m, HumanMessage)), "")
    return "Vietnamese" if _VIETNAMESE.search(question) else "English"


# HumanMessage.name of the tail note: the per-call text at the end of every model request.
CALL_NOTE = "call_note"

LAST_CALL_NOTE = """LAST STEP FOR THIS MESSAGE. You have no tool calls left, so call no tool.
Write the answer now, from what you found so far.
Say briefly what you could not check.
Write the answer in {language}."""


SAME_RESULT = """This is the same result as your earlier call of {name}. It has nothing new.
Use what you already have.
Call {name} again only to look for something else."""
