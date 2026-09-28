import logfire
from qdrant_client import QdrantClient
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.services.retrieval.embedding import embed_query

# Single shared Qdrant client for the lifetime of the process.
if settings.QDRANT_URL:
    client = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
else:
    # Fallback to local file-based storage so vectors persist between ingestion and API runs
    client = QdrantClient(path="local_qdrant_db")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    reraise=True,
    before_sleep=before_sleep_log(logfire, "warning"),
)
def _search(query: str, limit: int) -> list[dict]:
    """
    Embed the query and run a cosine similarity search against the Qdrant collection.

    Returns a list of dicts with 'content', 'source', and 'score' keys.
    The limit parameter controls how many candidates are returned before reranking.
    """
    query_vector = embed_query(query)
    response = client.query_points(
        collection_name=settings.QDRANT_COLLECTION,
        query=query_vector,
        limit=limit,
        with_payload=True,
    )
    return [
        {
            "content": point.payload.get("text", ""),
            "source": point.payload.get("source", "Unknown"),
            "score": point.score,
        }
        for point in response.points
    ]


def search_enterprise_knowledge(query: str, limit: int = 8) -> list[dict]:
    """
    Public search entry point with graceful degradation.

    Wraps _search with a try/except so that a Qdrant outage returns an empty
    list rather than crashing the request. The LLM node handles empty context
    by answering from conversation history where possible.
    """
    try:
        return _search(query, limit)
    except Exception as exc:
        logfire.error(f"Qdrant search failed after retries: {exc}")
        return []
