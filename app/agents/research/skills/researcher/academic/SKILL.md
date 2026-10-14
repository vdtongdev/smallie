---
name: academic
description: Background, definitions and scientific literature, using Wikipedia and arXiv (knowledge_* tools).
---
1. For background and definitions, use `knowledge_wikipedia_search` and then `knowledge_wikipedia_page` on the best title. When a web search tool is available, run a web search in the same step as the Wikipedia search, so the answer also has recent and more specific sources. Use lang="vi" for Vietnamese topics, but English Wikipedia is usually more complete.
   For a list (works, products, members…), call `knowledge_wikipedia_page` with `section` set to the heading that has it; it also returns the articles linked from that section, so each item can cite its own page.
2. For research questions ("latest papers on…", "state of the art in…"), use `knowledge_arxiv_search` with English keywords. Note the title, first authors, date and link of the 3–5 most relevant papers, and the key claim of each from its abstract.
3. arXiv papers are preprints: say so if the user may treat them as established results.
4. Wikipedia is a starting point. For numbers that matter, confirm them with a primary source from web search when possible.
5. Tag findings with the Wikipedia section URL (from the heading above the fact) or arXiv link, and a quote, as in your instructions.
