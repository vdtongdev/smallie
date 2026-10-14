"""Every entry the router can pick. The first one is the default when nothing else decides."""

from app.agents.base import AgentSpec, message_routes
from app.agents.chart.agent import SPEC as CHART
from app.agents.docs.agent import SPEC as DOCS
from app.agents.research.agent import SPEC as RESEARCH
from app.agents.sql.agent import SPEC as SQL

AGENTS: tuple[AgentSpec, ...] = (DOCS, SQL, RESEARCH, CHART)
ROUTES = tuple(spec.route for spec in AGENTS)
MESSAGE_ROUTES = message_routes(AGENTS)  # AIMessage.name -> route
SILENT_STEPS = frozenset(step for spec in AGENTS for step in spec.silent_steps)  # not streamed as tokens
STEPS = {spec.route: frozenset(spec.steps) for spec in AGENTS if spec.steps}  # route -> steps shown in the UI


def get(route: str) -> AgentSpec | None:
    return next((spec for spec in AGENTS if spec.route == route), None)
