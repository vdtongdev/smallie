"""Agent Server custom auth (langgraph.json): only the main app, holding SERVICE_TOKEN, may call."""

import hmac

from langgraph_sdk import Auth

from app.core.config import get_settings

auth = Auth()


def _header(headers: dict, name: str) -> str:
    value = headers.get(name.encode()) or headers.get(name) or b""
    return value.decode() if isinstance(value, bytes) else value


@auth.authenticate
async def authenticate(headers: dict) -> dict:
    """The user is whoever the main app says (x-smallie-user-id), so the token must match; an
    unset SERVICE_TOKEN refuses everyone rather than letting anyone in."""
    token = get_settings().service_token
    if not token or not hmac.compare_digest(_header(headers, "x-service-token"), token):
        raise Auth.exceptions.HTTPException(status_code=401, detail="missing or wrong service token")
    return {"identity": _header(headers, "x-smallie-user-id") or "main-app"}
