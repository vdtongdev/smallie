from pathlib import Path

from app.core.prompts import LANGUAGE_RULE, MEMORY_RULE, SOUL, load_prompt

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

DOCS_SYSTEM = SOUL + "\n\n" + load_prompt(Path(__file__).parent / "AGENTS.md").format(
    language_rule=LANGUAGE_RULE, memory_rule=MEMORY_RULE)
