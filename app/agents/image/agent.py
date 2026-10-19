import asyncio
import re
from pathlib import Path

from sqlalchemy import select

from app.agents.base import AgentSpec, RouteInfo
from app.agents.docs.agent import MAX_LISTED_DOCUMENTS, MIN_STEM_CHARS, make_mentions
from app.agents.factory import build_agent, search_first
from app.agents.image.prompts import IMAGE_SYSTEM, ROUTE_DESCRIPTION, TOOL_DESCRIPTION
from app.agents.image.tools import NO_IMAGES, search_images
from app.core.db import documents, get_engine
from app.features.documents.images import IMAGE_SUFFIXES

# "ảnh" alone is also the start of "ảnh hưởng" (influence), so that one is left out.
HINTS = re.compile(
    r"\b(images?|photos?|pictures?|screenshots?|diagrams?|hình ảnh|ảnh chụp|chụp màn hình|ảnh(?!\s+hưởng))\b",
    re.IGNORECASE,
)


def image_files(user_id: str) -> list[tuple[str, str | None, int]]:
    """(file name, card summary or None, images read) of the user's files that have image readings."""
    with get_engine().connect() as conn:
        return [tuple(row) for row in conn.execute(
            select(documents.c.filename, documents.c.summary, documents.c.image_count)
            .where(documents.c.user_id == user_id, documents.c.status != "failed", documents.c.image_count > 0)
            .order_by(documents.c.created_at.desc())
        )]


def is_image_file(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_SUFFIXES


async def route_info(user_id: str) -> RouteInfo | None:
    files = await asyncio.to_thread(image_files, user_id)
    if not files:
        return None  # nothing to search
    shown = [f"{name} ({summary})" if is_image_file(name) and summary else
             name if is_image_file(name) else f"{name} ({count} image{'s' if count > 1 else ''})"
             for name, summary, count in files[:MAX_LISTED_DOCUMENTS]]
    listed = ", ".join(shown) + (" …" if len(files) > MAX_LISTED_DOCUMENTS else "")
    # Only image files claim a message by name: "summarize report.pdf" stays with docs even when the
    # PDF has pictures; a question about them uses an image word ("the diagram in report.pdf").
    stems = [stem for stem in (Path(name).stem.lower() for name, _, _ in files if is_image_file(name))
             if len(stem) >= MIN_STEM_CHARS]
    return RouteInfo(
        prompt_note=f"The user's images and files with images: {listed}",
        matches=lambda message: any(stem in message.lower() for stem in stems),
        mentions=make_mentions(stems),  # "the quote" for tedlasso quote.jpg (docs leaves image names to us)
    )


def build_image_agent():
    return build_agent(name="image_agent", system_prompt=IMAGE_SYSTEM, tools=[search_images],
                       middleware=[search_first(search_images.name)])


SPEC = AgentSpec(
    route="image",
    description=ROUTE_DESCRIPTION,
    labels={"en": "Image agent", "vi": "Trợ lý hình ảnh"},
    build=build_image_agent,
    message_names=("image_agent",),
    hints=HINTS,
    route_info=route_info,
    tool_description=TOOL_DESCRIPTION,
    overrides=("docs",),
    no_result=lambda text: text == NO_IMAGES,
)
