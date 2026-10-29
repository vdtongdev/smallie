import re

from langchain_core.messages import AnyMessage, HumanMessage

LANGUAGE_RULE = (
    "LANGUAGE: Always reply in the language of the user's latest message. "
    "If they write Vietnamese, answer in natural Vietnamese with correct diacritics; "
    "if English, answer in English. Sources may be in a different language than the "
    "question; translate what you found instead of quoting it untranslated."
)

MEMORY_RULE = (
    "MEMORY: Facts about this user from earlier chats and their files are listed below; use them. "
    "What the user tells you is remembered automatically after your reply, so never say "
    "you can't remember it. "
    "Only call `search_memory` if the notes below don't already answer it. "
    "PAST CHATS: Recent activity from the user's other chats may be listed below. Use it "
    "only when the user refers to earlier chats or questions (\"what did I ask before?\", "
    "\"like last time\"); otherwise ignore it. For older or more specific past chats, call "
    "`search_chat_history`. Past answers may be outdated (e.g. stock prices), so re-check "
    "data with your tools before reusing it."
)

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

USER_SECTION = """

YOU ARE TALKING TO: {user}"""

MEMORIES_SECTION = """

WHAT YOU KNOW ABOUT THIS USER (from earlier chats and their files, newest first; may be empty):
{memories}

A stored language preference never changes the reply language: reply in the language of the latest message you answer."""

RECENT_CHATS_SECTION = """

RECENT ACTIVITY IN THE USER'S OTHER CHATS (most recently used chat first):
{recent_chats}"""

_VIETNAMESE = re.compile(r"[ăâđêôơưàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ]", re.IGNORECASE)


def reply_language(messages: list[AnyMessage]) -> str:
    """The language the answer must be in, from the user's latest message. Named outright (the
    app speaks English and Vietnamese only): "the user's language" left weak models to guess, and
    Vietnamese sources or chats around the question made them answer English questions in Vietnamese."""
    # Named messages are hidden notes to the model (findings, steer), never the user's words.
    question = next((m.text for m in reversed(messages) if isinstance(m, HumanMessage) and m.name is None), "")
    return "Vietnamese" if _VIETNAMESE.search(question) else "English"


STEER_MESSAGE = """STEER (not visible to the user). The user stopped the run that was answering their request, then sent the message just above.
Treat that message as a correction or addition to the request, not as a new question.
Finish the request with it."""


LAST_CALL_NOTE = """LAST STEP FOR THIS MESSAGE. You have no tool calls left, so call no tool.
Write the answer now, from what you found so far.
Say briefly what you could not check.
Write the answer in {language}."""


SAME_RESULT = """This is the same result as your earlier call of {name}. It has nothing new.
Use what you already have.
Call {name} again only to look for something else."""
