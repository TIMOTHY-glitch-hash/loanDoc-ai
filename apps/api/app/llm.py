"""Provider wiring, in one place.

Both LLM callers (extraction and memo generation) build their model here so the
provider choice is configuration rather than code. `ChatOpenAI` speaks to any
OpenAI-compatible gateway, so pointing `OPENAI_BASE_URL` at OpenRouter runs the
whole pipeline on a free-tier model without touching the agents.

Structured output is the part that actually differs between providers: OpenAI
supports tool/function calling, while many gateway-hosted open models only honour
a JSON-schema response format. That choice is therefore also configuration
(`LLM_STRUCTURED_OUTPUT_METHOD`) instead of an assumption baked into the agents.
"""

from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.config import Settings

#: OpenRouter asks callers to identify themselves; it also unlocks attribution on
#: their dashboard, which is useful when the free tier rate-limits.
_OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://github.com/TIMOTHY-glitch-hash/loanDoc-ai",
    "X-Title": "LoanDoc AI",
}


def build_chat_model(settings: Settings, *, temperature: float) -> ChatOpenAI:
    """Construct the chat model for the configured provider."""
    kwargs: dict[str, Any] = {
        "model": settings.openai_model,
        "temperature": temperature,
        "timeout": settings.openai_timeout_seconds,
        "max_retries": settings.openai_max_retries,
        "api_key": SecretStr(settings.openai_api_key),
    }

    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
        if "openrouter.ai" in settings.openai_base_url:
            kwargs["default_headers"] = _OPENROUTER_HEADERS

    return ChatOpenAI(**kwargs)


def structured_output_kwargs(settings: Settings) -> dict[str, Any]:
    """Arguments for ``with_structured_output`` for the configured provider."""
    return {"method": settings.llm_structured_output_method}
