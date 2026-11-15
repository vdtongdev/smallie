"""A solo question about an uploaded screenshot: the docs agent searches the files first and answers
in the language of the question, even when the user's profile and other chats are Vietnamese."""

from langchain_core.messages import AIMessage, HumanMessage

from app.agents import factory
from app.agents.docs import agent as docs_agent
from app.agents.docs import tools as docs_tools
from app.agents.research import agent as research_agent
from app.core.memory import AgentContext
from tests.scripted import ScriptedModel

READING = ("[Screenshot 2026-11-14 112705.png]\n[Image 1] CPU-Z SPD tab: Slot #1, Kingston 8 GB DDR4-3200 "
           "module KVR32N22S8/8, JEDEC timings.")
QUESTION = "what can you tell me about the screenshot"


class SkipsOptionalToolModel(ScriptedModel):
    """Like the live model on deepseek-chat-v3.1: calls a tool only when the call forces one,
    otherwise answers from what it has (the trace showed one call, no tool call, a refusal)."""

    forced: list = []

    def bind_tools(self, tools, **kwargs):
        self.forced.append(kwargs.get("tool_choice"))
        return super().bind_tools(tools, **kwargs)

    def _next(self, messages) -> AIMessage:
        forced = self.forced[-1] if self.forced else None
        if forced and forced != "none" and not any(m.type == "tool" for m in messages):
            return AIMessage("", tool_calls=[{"name": "search_documents", "args": {"query": "screenshot"}, "id": "c1"}])
        if any(m.type == "tool" for m in messages):
            return AIMessage("It is a CPU-Z SPD tab for a Kingston 8 GB DDR4-3200 module [Screenshot 2026-11-14 112705.png].")
        return AIMessage("I can't view images; which file is the screenshot in?")


# What the live request held (LangSmith run 01a1dff5-feda-7a72-99ad-f6043b23e805): the profile and
# the other chats are Vietnamese, the question is English.
VIETNAMESE_CONTEXT = dict(
    user="Tống Việt Dũng (username: tongvietdung)",
    profile="- User has name Dũng\n- User prefers language Vietnamese",
    recent_chats='Chat "xin chào" (last active 2026-11-11 20:45 UTC), oldest to newest:\n'
                 "  User: xin chào\n  Assistant: Xin chào anh Dũng! Rất vui được gặp lại anh.",
)


def _model(monkeypatch):
    model = SkipsOptionalToolModel(replies=iter(()), prompts=[], tools=[], forced=[])
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    monkeypatch.setattr(docs_tools, "search_with_graph", lambda user_id, query, entity="": READING)
    return model


async def test_solo_docs_agent_searches_before_answering_an_image_question(monkeypatch):
    model = _model(monkeypatch)
    agent = docs_agent.build_docs_agent()
    result = await agent.ainvoke({"messages": [HumanMessage(QUESTION)]},
                                 context=AgentContext(user_id="u", **VIETNAMESE_CONTEXT))
    answer = result["messages"][-1].text
    assert any(m.type == "tool" and m.name == "search_documents" for m in result["messages"]), answer
    assert "Kingston" in answer


async def test_english_question_gets_the_reply_language_named_in_the_request(monkeypatch):
    model = _model(monkeypatch)
    agent = docs_agent.build_docs_agent()
    await agent.ainvoke({"messages": [HumanMessage(QUESTION)]},
                        context=AgentContext(user_id="u", **VIETNAMESE_CONTEXT))
    request = model.prompts[0]
    after_the_question = "\n".join(m.text for m in request[request.index(next(m for m in request if m.text == QUESTION)):])
    assert "English" in after_the_question, after_the_question


async def test_solo_research_agent_is_not_forced_to_search_documents(monkeypatch):
    model = _model(monkeypatch)
    monkeypatch.setattr(research_agent, "describe_schema", lambda: "")
    workflow = research_agent.build_research_workflow()
    await workflow.ainvoke({"messages": [HumanMessage(QUESTION)]}, context=AgentContext(user_id="u"))
    assert model.forced and model.forced[0] != "search_documents", model.forced
