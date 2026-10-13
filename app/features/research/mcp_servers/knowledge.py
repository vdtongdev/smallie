"""MCP server "knowledge": public reference sources that need no API key (Wikipedia, arXiv)."""

import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlparse

import httpx
from mcp.server.fastmcp import FastMCP

USER_AGENT = "smallie-research/0.1 (https://github.com/vdtongdev/smallie)"  # Wikipedia asks for one
TIMEOUT = httpx.Timeout(15.0)
PAGE_CHARS = 6000  # article text returned to the model
SECTION_LINKS = 60  # linked articles listed for one section
ARXIV_URL = "https://export.arxiv.org/api/query"
ATOM = {"a": "http://www.w3.org/2005/Atom"}
LANGS = ("en", "vi")
_TAG = re.compile(r"<[^>]+>")
# Section headings in the plain-text extract: "== History ==", "=== Early life ===".
_HEADING = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)

mcp = FastMCP("knowledge")
# Tests swap this for an httpx.MockTransport.
transport: httpx.AsyncBaseTransport | None = None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT},
                             follow_redirects=True, transport=transport)


def _wiki_host(lang: str) -> str:
    return f"https://{lang if lang in LANGS else 'en'}.wikipedia.org"


def _wiki_url(lang: str, title: str, anchor: str = "") -> str:
    url = f"{_wiki_host(lang)}/wiki/{title.replace(' ', '_')}"
    return f"{url}#{anchor.replace(' ', '_')}" if anchor else url


async def _wiki_api(client: httpx.AsyncClient, lang: str, **params) -> dict:
    response = await client.get(f"{_wiki_host(lang)}/w/api.php",
                                params={**params, "format": "json", "formatversion": 2, "utf8": 1})
    response.raise_for_status()
    return response.json()


def _with_section_links(text: str, lang: str, title: str) -> str:
    """Markdown-style headings that carry their section's URL, so findings can cite a section."""
    def heading(m: re.Match) -> str:
        return f"{'#' * len(m[1])} {m[2]} ({_wiki_url(lang, title, m[2])})"
    return _HEADING.sub(heading, text)


def _section_text(extract: str, heading: str) -> str | None:
    """The part of the article under `heading`, including its subsections."""
    headings = list(_HEADING.finditer(extract))
    for i, m in enumerate(headings):
        if m[2].casefold() != heading.casefold():
            continue
        end = next((n.start() for n in headings[i + 1:] if len(n[1]) <= len(m[1])), len(extract))
        return extract[m.start():end].strip()
    return None


@mcp.tool()
async def wikipedia_search(query: str, lang: str = "en", limit: int = 5) -> str:
    """Search Wikipedia and return matching article titles with a short snippet.

    Args:
        query: What to look for.
        lang: "en" or "vi" Wikipedia.
        limit: Number of results (1-10).
    """
    async with _client() as client:
        data = await _wiki_api(client, lang, action="query", list="search", srsearch=query,
                               srlimit=max(1, min(limit, 10)))
    hits = data.get("query", {}).get("search", [])
    if not hits:
        return "No Wikipedia articles found."
    return "\n".join(
        f"- {hit['title']} ({_wiki_url(lang, hit['title'])}): {_TAG.sub('', hit.get('snippet', ''))}"
        for hit in hits
    )


async def _extract(client: httpx.AsyncClient, lang: str, title: str) -> tuple[str, str] | None:
    """(canonical title, plain text) of an article, following redirects."""
    data = await _wiki_api(client, lang, action="query", prop="extracts", explaintext=1, redirects=1, titles=title)
    page = (data.get("query", {}).get("pages") or [{}])[0]
    return (page["title"], page["extract"]) if page.get("extract") else None


async def wikipedia_article(url: str) -> str | None:
    """The whole article at a Wikipedia URL, formatted like `wikipedia_page` but not cut short:
    "Title (URL)", then the text with each heading's section URL. Not a tool: the research
    workflow uses it to find the section a cited fact is in."""
    parts = urlparse(url)
    lang = parts.netloc.split(".")[0]
    if not parts.netloc.endswith("wikipedia.org") or not parts.path.startswith("/wiki/"):
        return None
    async with _client() as client:
        found = await _extract(client, lang, unquote(parts.path[len("/wiki/"):]).replace("_", " "))
    if found is None:
        return None
    title, text = found
    return f"{title} ({_wiki_url(lang, title)})\n\n{_with_section_links(text, lang, title)}"


@mcp.tool()
async def wikipedia_page(title: str, lang: str = "en", section: str = "") -> str:
    """Plain text of a Wikipedia article (the beginning, if it is long). Each section heading
    carries the section's URL. With `section`, returns only that section plus the articles it
    links to (e.g. the pages of the books, films or people it lists).

    Args:
        title: Exact article title, e.g. from wikipedia_search.
        lang: "en" or "vi" Wikipedia.
        section: Optional section heading, e.g. "Works" or "Tác phẩm".
    """
    async with _client() as client:
        found = await _extract(client, lang, title)
        if found is None:
            return f"No Wikipedia article titled {title!r}."
        title, text = found
        if section:
            return await _wikipedia_section(client, lang, title, text, section)
    url = _wiki_url(lang, title)
    if len(text) <= PAGE_CHARS:
        return f"{title} ({url})\n\n{_with_section_links(text, lang, title)}"
    rest = [m[2] for m in _HEADING.finditer(text, PAGE_CHARS)]
    more = f"\n\nMore sections (call again with section=...): {', '.join(rest)}" if rest else ""
    return f"{title} ({url})\n\n{_with_section_links(text[:PAGE_CHARS], lang, title)} …{more}"


async def _wikipedia_section(client: httpx.AsyncClient, lang: str, title: str, extract: str, section: str) -> str:
    parsed = await _wiki_api(client, lang, action="parse", page=title, prop="sections")
    sections = parsed.get("parse", {}).get("sections", [])
    wanted = section.strip().casefold()
    names = [_TAG.sub("", s["line"]).casefold() for s in sections]
    # An exact heading first, else one containing the words asked for ("Tác phẩm" -> "Các tác phẩm").
    match = next((s for s, name in zip(sections, names) if name == wanted), None) or next(
        (s for s, name in zip(sections, names) if wanted in name), None)
    if match is None:
        names = ", ".join(_TAG.sub("", s["line"]) for s in sections)
        return f"No section {section!r} in {title}. Sections: {names}"
    heading = _TAG.sub("", match["line"])
    body = (_section_text(extract, heading) or "")[:PAGE_CHARS]
    links = await _wiki_api(client, lang, action="parse", page=title, prop="links", section=match["index"])
    linked = [link["title"] for link in links.get("parse", {}).get("links", [])
              if link.get("ns") == 0 and link.get("exists")][:SECTION_LINKS]
    lines = [f"{title}, section {heading} ({_wiki_url(lang, title, match['anchor'])})", "",
             _with_section_links(body, lang, title)]
    if linked:
        lines += ["", "Articles linked from this section:",
                  *(f"- {name} ({_wiki_url(lang, name)})" for name in linked)]
    return "\n".join(lines)


@mcp.tool()
async def arxiv_search(query: str, max_results: int = 5) -> str:
    """Search arXiv for scientific papers; returns title, authors, date, link and abstract.

    Args:
        query: Keywords (English works best).
        max_results: Number of papers (1-10).
    """
    params = {"search_query": f"all:{query}", "max_results": max(1, min(max_results, 10)),
              "sortBy": "relevance"}
    async with _client() as client:
        response = await client.get(ARXIV_URL, params=params)
        response.raise_for_status()
    return format_arxiv(response.text)


def format_arxiv(atom_xml: str) -> str:
    entries = ET.fromstring(atom_xml).findall("a:entry", ATOM)
    if not entries:
        return "No arXiv papers found."
    papers = []
    for entry in entries:
        title = " ".join(entry.findtext("a:title", "", ATOM).split())
        authors = [a.findtext("a:name", "", ATOM) for a in entry.findall("a:author", ATOM)]
        author_text = ", ".join(authors[:4]) + (" et al." if len(authors) > 4 else "")
        published = entry.findtext("a:published", "", ATOM)[:10]
        link = entry.findtext("a:id", "", ATOM)
        summary = " ".join(entry.findtext("a:summary", "", ATOM).split())
        papers.append(f"- {title} ({author_text}, {published}) {link}\n  {summary}")
    return "\n".join(papers)
