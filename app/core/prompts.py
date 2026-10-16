import re

from langchain_core.messages import AnyMessage, HumanMessage

LANGUAGE_RULE = (
    "LANGUAGE: Always reply in the language of the user's latest message. "
    "If they write Vietnamese, answer in natural Vietnamese with correct diacritics; "
    "if English, answer in English. Sources may be in a different language than the "
    "question; translate what you found instead of quoting it untranslated."
)

MEMORY_RULE = (
    "MEMORY: Facts about this user from earlier chats are listed below; use them. "
    "What the user tells you is remembered automatically after your reply, so never say "
    "you can't remember it. "
    "Only call `search_memory` if the notes below don't already answer it. "
    "PAST CHATS: Recent activity from the user's other chats may be listed below. Use it "
    "only when the user refers to earlier chats or questions (\"what did I ask before?\", "
    "\"like last time\"); otherwise ignore it. For older or more specific past chats, call "
    "`search_chat_history`. Past answers may be outdated (e.g. stock prices), so re-check "
    "data with your tools before reusing it."
)

# System prompt of the memory extractor (app/core/memory.py, extract_memories); never formatted.
EXTRACT_PROMPT = """You pick out durable facts about the user from one chat turn, so later chats can use them.

Rules:
1. Save only facts about the user that stay true across chats: identity, work, goals, preferences (answer style, language, topics) and investment profile (risk tolerance, horizon, holdings, constraints).
2. Save a conclusion the assistant drew about the user only when the user's own answers in this turn support it, e.g. the result of a questionnaire the user filled in.
3. Do not save questions, requests, market data, or facts about other people or companies.
4. Do not save secrets: passwords, ID, card or account numbers, health details.
5. Do not save anything the user asks you not to remember.
6. Write each fact as one short English sentence of the form "User <fact>", whatever language the chat is in.
7. Return an empty list when the turn has nothing to remember. Most turns have nothing."""

USER_SECTION = """

YOU ARE TALKING TO: {user}"""

MEMORIES_SECTION = """

WHAT YOU KNOW ABOUT THIS USER (from earlier chats; may be empty):
{memories}"""

RECENT_CHATS_SECTION = """

RECENT ACTIVITY IN THE USER'S OTHER CHATS (most recently used chat first):
{recent_chats}"""

_VIETNAMESE = re.compile(r"[ăâđêôơưàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ]", re.IGNORECASE)


def reply_language(messages: list[AnyMessage]) -> str:
    """The language the answer must be in, from the user's latest message. Named outright (the
    app speaks English and Vietnamese only): "the user's language" left weak models to guess, and
    Vietnamese sources or chats around the question made them answer English questions in Vietnamese."""
    question = next((m.text for m in reversed(messages) if isinstance(m, HumanMessage)), "")
    return "Vietnamese" if _VIETNAMESE.search(question) else "English"
