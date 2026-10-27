"""The logged-in user's own model server: endpoint, model and API key (app/core/model_settings.py).
PUT checks the connection with one GET /models before saving. The key is never returned."""

import asyncio
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, field_validator

from app.core.auth import get_current_user
from app.core.endpoint import UnsafeEndpoint, check_endpoint
from app.core.model_settings import delete_settings, key_hint, load_settings, save_settings

router = APIRouter(tags=["model-settings"])

_transport: httpx.AsyncBaseTransport | None = None  # tests swap in an httpx.MockTransport


class ModelSettingsBody(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None

    @field_validator("base_url", "model", "api_key")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("base_url", "model")
    @classmethod
    def _not_empty(cls, value):
        if not value:
            raise ValueError("must not be empty")
        return value


def _refuse(message: str):
    raise HTTPException(400, message)


async def _listed_models(base_url: str, key: str) -> list[str]:
    url = base_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, transport=_transport) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {key}"})
    except httpx.TimeoutException:
        _refuse("The endpoint did not answer within 10 seconds.")
    except httpx.HTTPError as exc:
        _refuse(f"Could not reach the endpoint ({type(exc).__name__}).")
    if response.status_code in (401, 403):
        _refuse("The key was rejected by this endpoint. Check that key and endpoint come from the same provider.")
    if not response.is_success:
        _refuse(f"The endpoint answered HTTP {response.status_code} to GET /models.")
    try:
        data = response.json()["data"]
        if not isinstance(data, list):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        _refuse("The endpoint did not answer like an OpenAI-compatible server (GET /models has no data list).")
    return [item.get("id") for item in data if isinstance(item, dict)]


@router.get("/model-settings")
async def get_model_settings(user_id: str = Depends(get_current_user)):
    saved = await asyncio.to_thread(load_settings, user_id)
    if saved is None:
        raise HTTPException(404, "No model settings")
    return {"base_url": saved.base_url, "model": saved.model, "key_hint": key_hint(saved.api_key)}


@router.put("/model-settings")
async def put_model_settings(body: ModelSettingsBody, user_id: str = Depends(get_current_user)):
    try:
        await asyncio.to_thread(check_endpoint, body.base_url)
    except UnsafeEndpoint as exc:
        _refuse(str(exc))
    key = body.api_key
    if key is None:
        saved = await asyncio.to_thread(load_settings, user_id)
        key = saved.api_key if saved else None
    if not key:
        _refuse("API key required")
    if key.startswith("sk-or-") and urlparse(body.base_url).hostname != "openrouter.ai":
        _refuse("This looks like an OpenRouter key; use https://openrouter.ai/api/v1")
    if key.startswith("sk-ant-"):
        _refuse("This looks like an Anthropic key; use https://api.anthropic.com/v1/")
    listed = await _listed_models(body.base_url, key)
    await asyncio.to_thread(save_settings, user_id, body.base_url, body.model, body.api_key)
    warning = None if body.model in listed else "This server doesn't list that model; check the name."
    return {"saved": True, "warning": warning}


@router.delete("/model-settings", status_code=204)
async def delete_model_settings(user_id: str = Depends(get_current_user)):
    await asyncio.to_thread(delete_settings, user_id)
    return Response(status_code=204)
