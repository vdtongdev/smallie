from functools import lru_cache
from urllib.parse import urlparse

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.core.config import OPENROUTER_BASE_URL, get_settings
from app.core.endpoint import check_endpoint
from app.core.model_settings import load_settings

# OpenRouter uses these optional headers to show the app name in its dashboard.
_HEADERS = {"HTTP-Referer": "http://localhost:8000", "X-Title": "smallie"}


class OpenRouterChat(ChatOpenAI):
    """ChatOpenAI that keeps OpenRouter's `usage.cost` (USD), which langchain_openai drops,
    as `response_metadata["cost"]` (app/core/usage.py totals it)."""

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        generation = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        cost = (chunk.get("usage") or {}).get("cost")
        if generation is not None and cost is not None:
            generation.message.response_metadata["cost"] = cost
        return generation


class NoModelSettings(Exception):
    def __init__(self, message: str = "Set your API key in Settings to start chatting."):
        super().__init__(message)


@lru_cache(maxsize=256)
def _build_chat_model(base_url: str, model: str, api_key: str, streaming: bool) -> ChatOpenAI:
    check_endpoint(base_url)
    return OpenRouterChat(
        model=model,
        api_key=api_key,
        base_url=base_url,
        default_headers=_HEADERS if urlparse(base_url).hostname == "openrouter.ai" else None,
        # At 0 a reply that starts repeating keeps repeating; higher values let weaker models
        # slip single words of another script (Chinese, Korean, Thai) into Vietnamese answers.
        temperature=get_settings().chat_temperature,
        streaming=streaming,
        stream_usage=True,  # the last streamed chunk carries tokens and cost
        # Retries also count against the user's quota and can't help once a cap is hit.
        max_retries=1,
        # A reply that loops (the same tool calls again and again) ends here instead of after 131k tokens.
        max_tokens=get_settings().max_output_tokens,
    )


def get_chat_model(user_id: str, *, streaming: bool = True) -> ChatOpenAI:
    """The user's own model server, from their saved settings only."""
    saved = load_settings(user_id)
    if saved is None:
        raise NoModelSettings()
    return _build_chat_model(saved.base_url, saved.model, saved.api_key, streaming)


def placeholder_model() -> ChatOpenAI:
    """Build-time stand-in for create_agent; calls on it fail on purpose."""
    return ChatOpenAI(model="unset", api_key="unset", base_url="https://invalid.invalid/v1")


def get_embeddings() -> OpenAIEmbeddings:
    s = get_settings()
    return OpenAIEmbeddings(
        model=s.openrouter_embed_model,
        api_key=s.embed_api_key,
        base_url=OPENROUTER_BASE_URL,
        default_headers=_HEADERS,
        # Non-OpenAI models: send raw text, not tiktoken token ids.
        check_embedding_ctx_length=False,
        chunk_size=64,
    )
