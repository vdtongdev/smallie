import re

from langchain_core.messages import AnyMessage, HumanMessage

LANGUAGE_RULE = (
    "LANGUAGE: Always reply in the language of the user's latest message. "
    "If they write Vietnamese, answer in natural Vietnamese with correct diacritics; "
    "if English, answer in English. Sources may be in a different language than the "
    "question; translate what you found instead of quoting it untranslated."
)

MEMORY_RULE = (
    "MEMORY: You can remember things about this user across all their chats. "
    "When the user shares a durable fact or preference (name, role, company, preferred "
    "language or answer style, recurring goals), call `save_memory` with one short, "
    "self-contained sentence. Do not save one-off questions or secrets such as passwords. "
    "Only call `search_memory` if the notes below don't already answer it. "
    "Every tool call costs an extra step, so when you call `save_memory`, do it in the same "
    "step as your other tool calls rather than as a separate step. "
    "PAST CHATS: Recent activity from the user's other chats may be listed below. Use it "
    "only when the user refers to earlier chats or questions (\"what did I ask before?\", "
    "\"like last time\"); otherwise ignore it. For older or more specific past chats, call "
    "`search_chat_history`. Past answers may be outdated (e.g. stock prices), so re-check "
    "data with your tools before reusing it."
)

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
