from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.core.config import OPENROUTER_BASE_URL, get_settings

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


def get_chat_model(model: str | None = None, *, streaming: bool = True, exact_model: bool = False) -> ChatOpenAI:
    s = get_settings()
    model = model or s.openrouter_chat_model
    # OpenRouter falls back server-side (`models`), inside the same request. A client-side
    # fallback would send extra requests, and on the free tier every request counts.
    fallbacks = [m for m in (f.strip() for f in s.openrouter_fallback_model.split(",")) if m and m != model and not exact_model]
    extra_body = {"models": [model, *fallbacks]} if fallbacks else {}
    # Paid models are routed to the cheapest provider by default, often an fp4 build, and
    # heavier quantization makes Vietnamese answers slip into other scripts more often.
    quantizations = [q.strip() for q in s.openrouter_quantizations.split(",") if q.strip()]
    # exact_model: this model only. Closed models (Claude, GPT) list no quantization, so the filter leaves
    # them no provider, and a fallback would silently answer in their place.
    if quantizations and not exact_model:
        extra_body["provider"] = {"quantizations": quantizations}
    # Within that filter the cheapest provider is often the slowest (14 vs 130 tok/s measured).
    if s.openrouter_provider_sort:
        extra_body.setdefault("provider", {})["sort"] = s.openrouter_provider_sort
    return OpenRouterChat(
        model=model,
        api_key=s.openrouter_api_key,
        base_url=OPENROUTER_BASE_URL,
        default_headers=_HEADERS,
        # Any sampling randomness lets weaker models slip single words of another script
        # (Chinese, Korean, Thai) into Vietnamese answers.
        temperature=0,
        streaming=streaming,
        stream_usage=True,  # the last streamed chunk carries tokens and cost
        extra_body=extra_body or None,
        # Retries also count against the free quota and can't help once the daily cap is hit.
        max_retries=1,
    )


def get_embeddings() -> OpenAIEmbeddings:
    s = get_settings()
    return OpenAIEmbeddings(
        model=s.openrouter_embed_model,
        api_key=s.openrouter_api_key,
        base_url=OPENROUTER_BASE_URL,
        default_headers=_HEADERS,
        # Non-OpenAI models: send raw text, not tiktoken token ids.
        check_embedding_ctx_length=False,
        chunk_size=64,
    )
