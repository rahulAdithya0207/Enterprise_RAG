import logfire
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from app.agents.state import AgentState
from app.config import settings
from app.gateway import extract_cache_status, portkey_client


def generate_node(state: AgentState):
    """
    Synthesize the final answer using retrieved context and conversation history.

    Two prompt modes are used:
    - CONVERSATIONAL: builds a prompt from chat history only (no document context).
    - TECHNICAL: injects the top reranked document chunks before the question.

    The LLM call goes through portkey_client (the raw OpenAI-SDK client pointed
    at the Portkey gateway). Using the raw client instead of the LangChain wrapper
    lets us inspect the response headers and detect whether Portkey served the
    answer from its semantic cache, which gets surfaced in the plan log.

    Retries on transient LLM failures are handled by _call_llm with exponential backoff.

    State reads:  current_query, messages, documents, plan
    State writes: final_answer, status, plan, messages
    """
    query = state["current_query"]

    history_str = ""
    for msg in state["messages"][:-1]:
        role = "User" if msg["role"] == "user" else "Assistant"
        history_str += f"{role}: {msg['content']}\n"

    user_msg = state["messages"][-1]["content"] if state["messages"] else ""

    if query == "CONVERSATIONAL":
        logfire.info("Generating conversational reply from memory.")
        prompt = f"""
        You are a helpful Enterprise IT Assistant.
        Answer the user's latest message using the conversation history below.

        CONVERSATION HISTORY:
        {history_str}

        LATEST MESSAGE:
        "{user_msg}"
        """
    else:
        logfire.info("Generating technical answer from retrieved context.")
        # Cap total context to avoid exceeding the LLM's token budget.
        max_chars = 25000
        context = ""
        for doc in state["documents"]:
            if len(context) + len(doc) < max_chars:
                context += doc + "\n\n"
            else:
                logfire.warning("Context window cap reached — truncating document list.")
                break

        prompt = f"""
        You are a Senior Technical Architect specialising in Kubernetes, Intel hardware,
        and enterprise networking. Answer the question using only the technical context below.

        TECHNICAL CONTEXT:
        {context}

        CONVERSATION HISTORY:
        {history_str}

        USER QUESTION:
        "{user_msg}"
        """

    with logfire.span("Responder — LLM synthesis"):
        try:
            response = _call_llm(prompt)
            content = response.choices[0].message.content
            cache_status = extract_cache_status(response)

            if cache_status == "HIT":
                logfire.info("Portkey cache hit — response served instantly.")
                plan_update = state["plan"] + ["Cache: Hit"]
                status = "Answered from cache."
            else:
                logfire.info("LLM response generated.")
                plan_update = state["plan"]
                status = "Answer generated."

            return {
                "final_answer": content,
                "status": status,
                "plan": plan_update,
                "messages": [{"role": "assistant", "content": content}],
            }

        except Exception as exc:
            logfire.error(f"LLM call failed after all retries: {exc}")
            raise


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    reraise=True,
    before_sleep=before_sleep_log(logfire, "warning"),
)
def _call_llm(prompt: str):
    """
    Call the LLM through the Portkey gateway with retry on transient errors.

    The model string uses Portkey's virtual-slug routing format:
    @<slug>/<model-alias>. The slug maps to a provider config defined in the
    Portkey dashboard — the underlying model (e.g. gpt-4o-mini or claude-haiku)
    is set there, not in this codebase.
    """
    return portkey_client.chat.completions.create(
        model=f"@{settings.PORTKEY_PRIMARY_SLUG}/{settings.PORTKEY_MODEL}",
        messages=[{"role": "user", "content": prompt}],
    )
