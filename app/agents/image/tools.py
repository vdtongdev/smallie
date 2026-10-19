import asyncio

from langchain.tools import ToolRuntime, tool

from app.agents.docs.tools import NO_PASSAGES, search_with_graph
from app.core.memory import AgentContext

NO_IMAGES = "No matching images in the user's files."


@tool
async def search_images(query: str, runtime: ToolRuntime[AgentContext], entity: str = "") -> str:
    """Search the readings of the user's uploaded images: photos, screenshots, scans, charts and pictures
    inside PDF or Word files. Each reading is the description and text written down when the image was
    uploaded. Returns passages headed [filename] or [filename, p. N].

    Args:
        query: A focused search phrase. Works in English or Vietnamese.
        entity: The person, company, place, product or file name the question is about, if any. The
            search then looks first in the files linked to it. Leave empty otherwise.
    """
    found = await asyncio.to_thread(search_with_graph, runtime.context.user_id, query, entity, "image")
    return NO_IMAGES if found == NO_PASSAGES else found
