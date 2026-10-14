import re

from langchain.agents.middleware import ModelRequest

from app.agents.artifacts import artifacts_from, table_note
from app.agents.base import AgentSpec
from app.agents.chart.prompts import CHART_SYSTEM, ROUTE_DESCRIPTION, TABLES_SECTION
from app.agents.chart.tools import chart_data, chart_sql, chart_table
from app.agents.factory import build_agent
from app.agents.sql import agent as sql_agent
from app.agents.sql.prompts import SQL_RULES
from app.core.prompts import LANGUAGE_RULE, MEMORY_RULE

# Chart words. A ticker alone points to sql; "chart AAPL" matches both, and chart overrides sql.
# Bare "vẽ" also means drawing a picture, so only "vẽ biểu đồ"/"vẽ đồ thị" count.
HINTS = re.compile(
    r"\b(charts?|graphs?|plot(?:s|ted|ting)?|visuali[sz](?:e|ation)|biểu đồ|đồ thị|vẽ (?:biểu|đồ))\b",
    re.IGNORECASE,
)


def tables_section(request: ModelRequest) -> str:
    """The query results already in the chat and how they were charted, so "plot that" or a
    repeated request reuses them instead of querying again."""
    tables = artifacts_from(request.messages, "tables")
    charts = artifacts_from(request.messages, "charts")
    return TABLES_SECTION.format(tables=table_note(tables, charts)) if tables else ""


def build_chart_agent():
    system_prompt = CHART_SYSTEM.format(
        sql_rules=SQL_RULES, language_rule=LANGUAGE_RULE, memory_rule=MEMORY_RULE,
        schema=sql_agent.describe_schema(),  # the sql agent's schema description, read the same way
    )
    return build_agent(name="chart_agent", system_prompt=system_prompt,
                       tools=[chart_sql, chart_table, chart_data], prompt_extra=tables_section)


SPEC = AgentSpec(
    route="chart",
    description=ROUTE_DESCRIPTION,
    labels={"en": "Chart agent", "vi": "Trợ lý biểu đồ"},
    build=build_chart_agent,
    message_names=("chart_agent",),
    hints=HINTS,
    overrides=("sql",),
)
