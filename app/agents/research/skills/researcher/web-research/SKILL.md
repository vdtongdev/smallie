---
name: web-research
description: Searching the web well, for current events, news, products, companies and anything not in the user's files or the market database.
---
1. Plan 2–4 focused queries before searching: the core question, plus the angles a good answer needs (numbers, dates, who/what, opposing views).
2. Use the web search tool (`tavily_tavily_search`). Search in English, and also in Vietnamese when the topic is about Vietnam or the user writes Vietnamese.
3. For news or anything that changes, prefer recent results and note each result's date. Say "as of <date>" in the finding.
4. When a result looks important but its snippet is thin, read the page with the extract tool (`tavily_tavily_extract`) instead of guessing from the snippet. Extract at most 2–3 pages.
5. Prefer primary or reputable sources (official sites, filings, major outlets, papers) over blogs and forums. When sources disagree, record both, each with its source.
6. Stop once the question is covered. Don't keep searching for marginal details, because every search costs a step.
7. Every finding gets the source id of the page it came from (not the site's home page) and a quote copied from that page's result.
