import json
from contextlib import AsyncExitStack

import httpx
import pytest

from app.core.config import get_settings
from app.core.mcp import open_mcp_tools
from app.features.research import sources
from app.features.research.mcp_servers import knowledge, postgres
from tests.conftest import requires_db

ARXIV_XML = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
  <id>http://arxiv.org/abs/2401.00001v1</id><published>2024-01-02T00:00:00Z</published>
  <title>Retrieval  Augmented
   Generation</title><summary> A survey of RAG. </summary>
  <author><name>A. One</name></author><author><name>B. Two</name></author>
</entry></feed>"""


WIKI_EXTRACT = """LangChain is a framework.

== History ==
Started in 2022.

== Notable works ==
LangGraph, an agent runtime.

=== Tools ===
Integrations.

== See also ==
Other."""


def _fake_api(request: httpx.Request) -> httpx.Response:
    if request.url.host == "export.arxiv.org":
        return httpx.Response(200, text=ARXIV_XML)
    if request.url.params.get("list") == "search":
        return httpx.Response(200, json={"query": {"search": [
            {"title": "LangChain", "snippet": 'a <span class="searchmatch">framework</span>'}]}})
    params = request.url.params
    if params.get("action") == "parse" and params.get("prop") == "sections":
        return httpx.Response(200, json={"parse": {"sections": [
            {"line": "History", "index": "1", "anchor": "History"},
            {"line": "Notable <i>works</i>", "index": "2", "anchor": "Notable_works"}]}})
    if params.get("action") == "parse":
        assert params.get("section") == "2"
        return httpx.Response(200, json={"parse": {"links": [
            {"ns": 0, "title": "LangGraph", "exists": True}, {"ns": 0, "title": "Red link", "exists": False},
            {"ns": 14, "title": "Category:AI", "exists": True}]}})
    return httpx.Response(200, json={"query": {"pages": [{"title": "LangChain", "extract": WIKI_EXTRACT}]}})


@pytest.fixture
def fake_web(monkeypatch):
    monkeypatch.setattr(knowledge, "transport", httpx.MockTransport(_fake_api))


async def test_knowledge_server_tools_over_mcp(fake_web):
    async with AsyncExitStack() as stack:
        tools = {t.name: t for t in await open_mcp_tools(stack, {}, {"knowledge": knowledge.mcp})}
        assert set(tools) == {"knowledge_wikipedia_search", "knowledge_wikipedia_page", "knowledge_arxiv_search"}

        found = await tools["knowledge_wikipedia_search"].ainvoke({"query": "langchain", "lang": "vi"})
        assert "LangChain (https://vi.wikipedia.org/wiki/LangChain): a framework" in _text(found)
        page = await tools["knowledge_wikipedia_page"].ainvoke({"title": "LangChain"})
        assert _text(page).startswith("LangChain (https://en.wikipedia.org/wiki/LangChain)")
        assert "## History (https://en.wikipedia.org/wiki/LangChain#History)" in _text(page)
        section = _text(await tools["knowledge_wikipedia_page"].ainvoke({"title": "LangChain", "section": "works"}))
        assert section.startswith("LangChain, section Notable works (https://en.wikipedia.org/wiki/LangChain#Notable_works)")
        assert "LangGraph, an agent runtime." in section and "Integrations." in section  # subsections included
        assert "History" not in section and "See also" not in section
        assert "- LangGraph (https://en.wikipedia.org/wiki/LangGraph)" in section
        assert "Red link" not in section and "Category:AI" not in section
        missing = _text(await tools["knowledge_wikipedia_page"].ainvoke({"title": "LangChain", "section": "Awards"}))
        assert "Sections: History, Notable works" in missing
        papers = await tools["knowledge_arxiv_search"].ainvoke({"query": "rag"})
        assert "Retrieval Augmented Generation (A. One, B. Two, 2024-01-02) http://arxiv.org/abs/2401.00001v1" in _text(papers)


async def test_wikipedia_article_is_whole_with_section_urls(fake_web):
    article = await knowledge.wikipedia_article("https://en.wikipedia.org/wiki/LangChain#History")
    assert article.startswith("LangChain (https://en.wikipedia.org/wiki/LangChain)")
    assert "## See also (https://en.wikipedia.org/wiki/LangChain#See_also)" in article
    assert await knowledge.wikipedia_article("https://example.com/wiki/LangChain") is None


async def test_unreachable_server_is_skipped(fake_web):
    remote = {"down": {"transport": "streamable_http", "url": "http://127.0.0.1:9/mcp"}}
    async with AsyncExitStack() as stack:
        tools = await open_mcp_tools(stack, remote, {"knowledge": knowledge.mcp})
    assert tools and all(t.name.startswith("knowledge_") for t in tools)


def test_remote_servers_from_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "tavily_api_key", "tvly-secret")
    monkeypatch.setattr(settings, "mcp_servers", json.dumps({"exa": {"transport": "streamable_http", "url": "https://x"}}))
    servers = sources.remote_servers()
    assert servers["tavily"]["headers"] == {"Authorization": "Bearer tvly-secret"}
    assert "tvly-secret" not in servers["tavily"]["url"]
    assert set(servers) == {"tavily", "exa"}

    monkeypatch.setattr(settings, "tavily_api_key", "")
    monkeypatch.setattr(settings, "mcp_servers", "not json")
    assert sources.remote_servers() == {}


@requires_db
async def test_external_db_server_is_read_only():
    # The app's own database stands in for "another Postgres".
    server = postgres.build_server(get_settings().sqlalchemy_url)
    async with AsyncExitStack() as stack:
        tools = {t.name: t for t in await open_mcp_tools(stack, {}, {"external_db": server})}
        assert "market.stocks" in _text(await tools["external_db_list_tables"].ainvoke({}))
        assert "ticker" in _text(await tools["external_db_describe_table"].ainvoke({"table": "market.stocks"}))
        rows = _text(await tools["external_db_query"].ainvoke({"sql": "SELECT 1 AS one"}))
        assert "| one |" in rows
        refused = _text(await tools["external_db_query"].ainvoke({"sql": "DELETE FROM market.stocks"}))
        assert refused.startswith("Error: only a single SELECT")
        # Read-only transactions catch what the statement check can't.
        sneaky = "WITH x AS (DELETE FROM market.stocks RETURNING ticker) SELECT * FROM x"
        assert "read-only" in _text(await tools["external_db_query"].ainvoke({"sql": sneaky}))


def _text(result) -> str:
    if isinstance(result, str):
        return result
    return "".join(block.get("text", "") for block in result)
