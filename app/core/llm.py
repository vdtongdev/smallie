from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.core.config import OPENROUTER_BASE_URL, get_settings

# OpenRouter uses these optional headers to show the app name in its dashboard.
_HEADERS = {"HTTP-Referer": "http://localhost:8000", "X-Title": "smallie"}


def get_chat_model(model: str | None = None, *, streaming: bool = True) -> ChatOpenAI:
    s = get_settings()
    model = model or s.openrouter_chat_model
    # OpenRouter falls back server-side (`models`), inside the same request. A client-side
    # fallback would send extra requests, and on the free tier every request counts.
    fallbacks = [m for m in (f.strip() for f in s.openrouter_fallback_model.split(",")) if m and m != model]
    extra_body = {"models": [model, *fallbacks]} if fallbacks else None
    return ChatOpenAI(
        model=model,
        api_key=s.openrouter_api_key,
        base_url=OPENROUTER_BASE_URL,
        default_headers=_HEADERS,
        # Any sampling randomness lets weaker models slip single words of another script
        # (Chinese, Korean, Thai) into Vietnamese answers.
        temperature=0,
        streaming=streaming,
        extra_body=extra_body,
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
