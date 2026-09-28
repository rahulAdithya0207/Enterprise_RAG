from langchain_openai import ChatOpenAI
from openai import OpenAI
from portkey_ai import PORTKEY_GATEWAY_URL, createHeaders

from app.config import settings

# Portkey acts as a reverse proxy in front of the actual LLM providers.
# All routing rules (primary vs fallback, caching, retry policy) live inside
# a "saved config" on the Portkey dashboard, referenced here by its ID.
# This approach is required when block_inline_config is enabled on the workspace.


def _build_portkey_headers(feature: str = "rag") -> dict:
    """
    Assemble the Portkey request headers that activate the saved routing config.

    The config_id tells Portkey which saved config to load for this request.
    The metadata fields are forwarded to Portkey analytics and are visible
    per-request in the Portkey dashboard for debugging.
    """
    if not settings.PORTKEY_PRIMARY_CONFIG_ID:
        raise ValueError(
            "PORTKEY_PRIMARY_CONFIG_ID is missing from .env. "
            "Find the pc-... ID in the Portkey dashboard under Configs, "
            "or run: PYTHONPATH=. python scripts/list_portkey_configs.py"
        )
    return createHeaders(
        api_key=settings.PORTKEY_API_KEY,
        config_id=settings.PORTKEY_PRIMARY_CONFIG_ID,
        metadata={
            "feature": feature,
            "_user": "rag-system",
            "environment": "production",
        },
    )


# Synchronous OpenAI-SDK client pointed at the Portkey gateway URL.
# Portkey speaks the OpenAI API, so the SDK works without modification.
# The model string uses Portkey's virtual-slug format (@slug/alias) — the
# actual upstream model is configured on the Portkey dashboard, not here.
portkey_client = OpenAI(
    api_key=settings.PORTKEY_API_KEY,
    base_url=PORTKEY_GATEWAY_URL,
    default_headers=_build_portkey_headers(),
)


def get_langchain_llm(feature: str = "rag") -> ChatOpenAI:
    """
    Return a LangChain-compatible LLM that routes through the Portkey gateway.

    ChatOpenAI accepts a custom base_url (pointed at Portkey) and default_headers
    (carrying the Portkey auth and config reference). This makes it a drop-in
    for any LangChain node without code changes to those nodes.
    """
    return ChatOpenAI(
        api_key=settings.PORTKEY_API_KEY,
        base_url=PORTKEY_GATEWAY_URL,
        model=f"@{settings.PORTKEY_PRIMARY_SLUG}/{settings.PORTKEY_MODEL}",
        default_headers=_build_portkey_headers(feature),
    )


def extract_cache_status(response) -> str:
    """
    Try to read the x-portkey-cache-status header from an LLM response.

    The OpenAI SDK wraps responses in a parsed object that doesn't directly
    expose raw headers. We probe several common attribute paths where the SDK
    might store the underlying HTTP response and fall back to 'MISS' if none
    of them yield a header value.
    """
    for attr in ("_raw_response", "_response", "_http_response", "headers"):
        raw = getattr(response, attr, None)
        if raw is not None:
            headers = getattr(raw, "headers", None)
            if headers:
                status = headers.get("x-portkey-cache-status", "")
                if status:
                    return status.upper()
    return "MISS"
