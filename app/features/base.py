"""A feature owns data the agents use (uploaded documents, stock prices) and plugs into the app here."""

from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass

from fastapi import APIRouter


@dataclass(frozen=True)
class Feature:
    name: str
    routers: tuple[APIRouter, ...] = ()  # mounted under /api
    # Called in the app lifespan, e.g. to start a background task; register cleanup on the stack.
    start: Callable[[AsyncExitStack], None] | None = None
    # Called when the admin deletes an account: remove everything the feature keeps for that user.
    delete_user_data: Callable[[str], Awaitable[None]] | None = None
