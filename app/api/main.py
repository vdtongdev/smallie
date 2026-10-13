import logging
import secrets
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import auth
from app.accounts import ensure_admin
from app.api import routes_admin, routes_agents, routes_chat, routes_conversations, routes_memories
from app.config import get_settings
from app.db import init_db
from app.features.registry import FEATURES
from app.memory import open_persistence
from app.orchestration.graph import build_assistant

logging.basicConfig(level=logging.INFO)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # stay logged in for 30 days


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.openrouter_api_key:
        logging.warning("OPENROUTER_API_KEY is not set; chat and document indexing will fail.")
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    ensure_admin(settings.app_password)
    async with AsyncExitStack() as stack:
        checkpointer, store = await open_persistence(stack)
        app.state.store = store
        app.state.checkpointer = checkpointer
        app.state.assistant = build_assistant(checkpointer, store)
        for feature in FEATURES:
            if feature.start:
                feature.start(stack)
        yield


def _session_secret() -> str:
    s = get_settings()
    if s.session_secret:
        return s.session_secret
    if s.app_password:
        raise RuntimeError("SESSION_SECRET must be set when APP_PASSWORD is set (see .env.example).")
    logging.warning("SESSION_SECRET is not set; everyone is logged out when the server restarts.")
    return secrets.token_urlsafe(32)


app = FastAPI(title="smallie", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret(),
    session_cookie="smallie_session",
    max_age=SESSION_MAX_AGE,
    same_site="lax",
    https_only=get_settings().session_https_only,
)
app.include_router(auth.router, prefix="/api")
app.include_router(routes_admin.router, prefix="/api")
app.include_router(routes_agents.router, prefix="/api")
app.include_router(routes_chat.router, prefix="/api")
app.include_router(routes_conversations.router, prefix="/api")
app.include_router(routes_memories.router, prefix="/api")
for feature in FEATURES:
    for feature_router in feature.routers:
        app.include_router(feature_router, prefix="/api")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
