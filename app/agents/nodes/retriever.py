import logfire

from app.agents.state import AgentState
from app.services.retrieval.qdrant_service import search_enterprise_knowledge
from app.services.retrieval.ranking_service import rerank_documents


def retrieve_node(state: AgentState):
    """
    Fetch relevant document chunks from Qdrant, then rerank them semantically.

    First, pulls the top-15 candidates via cosine similarity search on the
    Qdrant collection. Then sends all 15 to the Jina Reranker API, which
    re-scores them against the query using a cross-encoder model and returns
    the top 5 most relevant chunks. Only those 5 are passed to the Responder.

    State reads:  current_query, plan
    State writes: documents, status, plan
    """
    query = state["current_query"]

    with logfire.span("Retriever — knowledge search"):
        logfire.info(f"Vector search query: {query}")
        candidates = search_enterprise_knowledge(query, limit=15)
        logfire.info(f"Qdrant returned {len(candidates)} candidates.")

        raw_texts = [doc["content"] for doc in candidates]

        with logfire.span("Retriever — semantic reranking"):
            top_chunks = rerank_documents(query, raw_texts, top_n=5)
            logfire.info("Reranking complete — kept top 5 chunks.")

        formatted = [f"CONTENT: {chunk}" for chunk in top_chunks]

    return {
        "documents": formatted,
        "status": "Relevant context retrieved.",
        "plan": state["plan"] + ["Context Retrieved from Knowledge Base"],
    }
