import logfire

from app.agents.state import AgentState
from app.gateway import get_langchain_llm

# LangChain-compatible LLM routed through the Portkey gateway.
# Portkey handles retries, provider fallback, and semantic caching.
llm = get_langchain_llm(feature="planner")


def planner_node(state: AgentState):
    """
    Classify the user's intent by examining the full conversation history.

    If the latest message can be answered from prior context (e.g. a greeting
    or a follow-up referencing something already discussed), the node sets
    current_query to 'CONVERSATIONAL' so the graph bypasses retrieval entirely.

    For technical questions about Kubernetes, Intel hardware, or enterprise
    networking, the node rewrites the raw user message into a clean, specific
    search query optimised for the Qdrant vector store.

    State writes: current_query, status, plan
    """
    history = ""
    for msg in state["messages"][:-1]:
        role = "User" if msg["role"] == "user" else "Assistant"
        history += f"{role}: {msg['content']}\n"

    user_message = state["messages"][-1]["content"] if state["messages"] else ""

    prompt = f"""
    You are an intelligent routing assistant for an enterprise knowledge base.
    Your job is to analyse the conversation and decide whether a document search is needed.

    CONVERSATION HISTORY:
    {history}

    LATEST MESSAGE:
    "{user_message}"

    Rules:
    1. If the message is a greeting or can be answered from the conversation history alone,
       output exactly: CONVERSATIONAL
    2. If it is a technical question about Kubernetes, Intel hardware, or enterprise networking
       that requires retrieving documentation, output a concise, specific search query.

    Output ONLY 'CONVERSATIONAL' or the search query — nothing else.
    """

    with logfire.span("Planner — intent classification"):
        decision = llm.invoke(prompt).content.strip()
        logfire.info(f"Planner decision: {decision}")

    if decision == "CONVERSATIONAL":
        return {
            "current_query": "CONVERSATIONAL",
            "status": "Responding from conversation memory.",
            "plan": ["Intent: Conversational", "Retrieval: Skipped"],
        }

    return {
        "current_query": decision,
        "status": f"Technical query identified. Searching for: {decision}",
        "plan": ["Intent: Technical", f"Search Term: {decision}"],
    }
