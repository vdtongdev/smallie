from fastapi import APIRouter, Depends

from app.agents.registry import AGENTS
from app.auth import get_current_user

router = APIRouter(tags=["agents"])


@router.get("/agents")
def list_agents(user_id: str = Depends(get_current_user)):
    """The agents the router can pick, with their badge labels per UI language."""
    return [{"route": spec.route, "labels": spec.labels} for spec in AGENTS]
