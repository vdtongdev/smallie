from types import SimpleNamespace

from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding

from app.agents.docs import tools as docs_tools
from app.agents.image import tools as image_tools
from app.core.config import get_settings
from app.core.memory import AgentContext
from tests.conftest import requires_db


@requires_db
def test_image_search_returns_only_the_users_image_readings(monkeypatch):
    from langchain_postgres import PGVector

    store = PGVector(embeddings=DeterministicFakeEmbedding(size=32), connection=get_settings().sqlalchemy_url,
                     collection_name="test_image_search", pre_delete_collection=True)
    store.add_documents([
        Document("cafe receipt total", metadata={"user_id": "alice", "filename": "notes.pdf", "document_id": "d1"}),
        Document("[Image 1]\ncafe receipt total", metadata={"user_id": "alice", "filename": "receipt.png",
                                                             "document_id": "d2", "kind": "image", "image": 1}),
        Document("[Image 1]\ncafe receipt total", metadata={"user_id": "bob", "filename": "bob.png",
                                                             "document_id": "d3", "kind": "image", "image": 1}),
    ])
    monkeypatch.setattr(docs_tools, "get_vector_store", lambda: store)
    try:
        found = docs_tools.search_user_chunks("alice", "cafe receipt total", kind="image")
        assert "receipt.png" in found and "notes.pdf" not in found and "bob.png" not in found
        everything = docs_tools.search_user_chunks("alice", "cafe receipt total")
        assert "receipt.png" in everything and "notes.pdf" in everything  # docs still searches every chunk
        # The graph picks a file without images: the retry over every file keeps the image filter.
        monkeypatch.setattr(docs_tools, "documents_for", lambda user_id, query, entity: ["d1"])
        found = docs_tools.search_with_graph("alice", "cafe receipt total", kind="image")
        assert "receipt.png" in found and "notes.pdf" not in found
    finally:
        store.delete_collection()


async def test_the_first_call_of_a_turn_must_search():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from app.agents.factory import search_first

    seen = []

    async def handler(request):
        seen.append(request.tool_choice)
        return "response"

    def request(*messages):
        return SimpleNamespace(messages=list(messages), tool_choice=None,
                               override=lambda **changes: SimpleNamespace(messages=list(messages), **changes))

    middleware = search_first("search_images")
    earlier = [HumanMessage("old question"), AIMessage("", tool_calls=[{"name": "search_images", "args": {}, "id": "1"}]),
               ToolMessage("old result", tool_call_id="1"), AIMessage("old answer")]
    await middleware.awrap_model_call(request(*earlier, HumanMessage("tháp nhu cầu của maslow có những gì")), handler)
    called = AIMessage("", tool_calls=[{"name": "search_images", "args": {}, "id": "2"}])
    await middleware.awrap_model_call(request(HumanMessage("q"), called, ToolMessage("found", tool_call_id="2")), handler)
    # Forced before this turn's first search (an earlier turn's search doesn't count); free after it.
    assert seen == ["search_images", None]


def test_the_image_and_docs_agents_are_forced_to_search(monkeypatch):
    from app.agents.docs import agent as docs_agent
    from app.agents.image import agent as image_agent

    for module, build in ((image_agent, image_agent.build_image_agent), (docs_agent, docs_agent.build_docs_agent)):
        built = {}
        monkeypatch.setattr(module, "build_agent", lambda **kwargs: built.update(kwargs))
        build()
        assert [m.name for m in built["middleware"]] == ["search_first"]


async def test_no_image_passages_is_its_own_result(monkeypatch):
    monkeypatch.setattr(image_tools, "search_with_graph", lambda *args: docs_tools.NO_PASSAGES)
    runtime = SimpleNamespace(context=AgentContext(user_id="u1"))
    assert await image_tools.search_images.coroutine("cat photo", runtime) == image_tools.NO_IMAGES
