from fastapi import APIRouter, Depends

from app.agents.registry import AGENTS
from app.core.auth import get_current_user
from app.orchestration.orchestrator import LABELS
from app.orchestration.router import ORCHESTRATOR

router = APIRouter(tags=["agents"])


@router.get("/agents")
def list_agents(user_id: str = Depends(get_current_user)):
    """The orchestrator and the agents, with their badge labels (and workflow step labels) per UI language."""
    orchestrator = {"route": ORCHESTRATOR, "labels": LABELS, "steps": {}}
    return [orchestrator, *({"route": spec.route, "labels": spec.labels, "steps": spec.steps} for spec in AGENTS)]
