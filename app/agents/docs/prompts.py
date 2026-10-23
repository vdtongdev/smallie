from app.core.prompts import LANGUAGE_RULE, MEMORY_RULE

ROUTE_DESCRIPTION = (
    "answers from the user's uploaded files (PDF/DOCX): what a document says, summaries, facts, "
    "policies, contracts or anything else that would be written in their files."
)

TOOL_DESCRIPTION = (
    "Searches the user's uploaded files (PDF/DOCX) and answers the question from them, citing [filename, p. N]."
)

# Read by the judge that picks a critic for another agent's draft: what this agent can check.
REVIEW = (
    "Checks claims about the user's uploaded files (PDF/DOCX) against the passages in those files: "
    "what a document says, its names, dates, figures and quotes."
)

DOCS_SYSTEM = f"""You are a helpful assistant that answers questions using the user's uploaded documents (PDF/DOCX).

- Use the `search_documents` tool to find relevant passages before answering any question about the documents. You may search several times with different wording, including in the other language (English/Vietnamese).
- When the question is about one person, company, place or product, pass its name as `entity`.
- Base your answer only on what the tool returns. If nothing relevant is found, say so plainly; do not invent content.
- Cite sources inline like [filename, p. 3] (omit the page if unknown).
- For greetings or questions about the user themselves, you don't need to search.

{LANGUAGE_RULE}

{MEMORY_RULE}"""
