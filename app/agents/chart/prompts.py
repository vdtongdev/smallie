from pathlib import Path

from app.core.prompts import SOUL, load_prompt

ROUTE_DESCRIPTION = (
    "draws charts (line, bar, area, pie, scatter) of the stock database's data or of numbers already "
    "in the chat, with a short takeaway."
)

# {sql_rules}: SQL_RULES of the sql agent; {schema}: the market tables (describe_schema()).
CHART_SYSTEM = SOUL + "\n\n" + load_prompt(Path(__file__).parent / "AGENTS.md")

TABLES_SECTION = """

TABLES IN THIS CHAT (query results you can chart with `chart_table`, newest last):
{tables}"""

# Read by the judge that picks a critic for another agent's draft: what this agent can check.
REVIEW = (
    "Checks the query and the numbers behind a chart: whether the data drawn matches the question and "
    "the stock database."
)
