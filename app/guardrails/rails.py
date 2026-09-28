import logfire
from langchain_openai import ChatOpenAI
from nemoguardrails import LLMRails, RailsConfig

from app.config import settings
from app.guardrails.colang_rules import COLANG_CONTENT, RAIL_INDICATORS, YAML_CONTENT

# Singleton — initialized once at app startup via initialize_rails().
_rails: LLMRails | None = None


def initialize_rails() -> None:
    """
    Build the NeMo LLMRails instance and store it as a module-level singleton.

    The rails config is defined entirely in memory (COLANG_CONTENT + YAML_CONTENT)
    rather than from files on disk. A lightweight gpt-5-mini instance is used as
    the classifier LLM because speed matters more than depth at the gate layer.
    """
    global _rails
    from app.gateway.client import get_langchain_llm
    guard_llm = get_langchain_llm(feature="guardrails")
    config = RailsConfig.from_content(colang_content=COLANG_CONTENT, yaml_content=YAML_CONTENT)
    _rails = LLMRails(config, llm=guard_llm)
    logfire.info("NeMo Guardrails initialized.")


def guard(message: str) -> tuple[bool, str | None]:
    """
    Run the user message through the NeMo guardrail flows.

    Returns a tuple of (fired, response):
    - (True,  str) — a rail matched; the caller should return rail_response
                     immediately and skip the LangGraph pipeline entirely.
    - (False, None) — the message passed all checks; proceed normally.

    Detection works by checking whether the guardrail response text contains
    any of the sentinel phrases defined in RAIL_INDICATORS. Those phrases are
    unique enough that they cannot appear in a legitimate RAG answer.
    """
    if _rails is None:
        logfire.warning("Guardrails not initialized — skipping gate check.")
        return False, None

    with logfire.span("Guardrails check"):
        result = _rails.generate(messages=[{"role": "user", "content": message}])
        content = result.get("content", "") if isinstance(result, dict) else str(result)
        fired = any(indicator in content for indicator in RAIL_INDICATORS)

        if fired:
            logfire.info(f"Rail fired for query: '{message[:80]}'")
            return True, content

        logfire.info("Guardrails passed.")
        return False, None
