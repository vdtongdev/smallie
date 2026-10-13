"""Every entry the router can pick. The first one is the default when nothing else decides."""

from app.agents.base import AgentSpec, message_routes
from app.documents.agent import SPEC as DOCS
from app.sql.agent import SPEC as SQL

AGENTS: tuple[AgentSpec, ...] = (DOCS, SQL)
ROUTES = tuple(spec.route for spec in AGENTS)
MESSAGE_ROUTES = message_routes(AGENTS)  # AIMessage.name -> route


def get(route: str) -> AgentSpec | None:
    return next((spec for spec in AGENTS if spec.route == route), None)
