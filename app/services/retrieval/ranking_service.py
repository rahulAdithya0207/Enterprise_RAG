import time

import logfire
import requests
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from app.config import settings

_JINA_RERANK_URL = "https://api.jina.ai/v1/rerank"
_JINA_RERANK_MODEL = "jina-reranker-v3"

# Lazy singleton — created on first call to rerank_documents.
_ranker = None


class _JinaReranker:
    """Wraps the Jina Reranker API for cross-encoder reranking of candidate documents."""

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[str]:
        """
        Score and reorder documents by their relevance to the query.

        Sends all candidate documents to the Jina cross-encoder in a single
        POST request. The API returns results sorted by relevance_score descending.
        If a result item is missing its document text, we fall back to looking
        up the original text by index.
        """
        resp = requests.post(
            _JINA_RERANK_URL,
            headers={
                "Authorization": f"Bearer {settings.JINA_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": _JINA_RERANK_MODEL,
                "query": query,
                "documents": documents,
                "top_n": top_n,
                "return_documents": True,
            },
            timeout=60,
        )
        resp.raise_for_status()

        reranked = []
        for item in resp.json().get("results", [])[:top_n]:
            text = item.get("document")
            if text is None:
                idx = item.get("index")
                if idx is not None and 0 <= idx < len(documents):
                    text = documents[idx]
            if text is not None:
                reranked.append(text)
        return reranked


def _get_ranker() -> _JinaReranker:
    """Return the shared reranker instance, creating it on first call."""
    global _ranker
    if _ranker is None:
        logfire.info("Initializing Jina Reranker v3 client.")
        _ranker = _JinaReranker()
    return _ranker


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    reraise=True,
    before_sleep=before_sleep_log(logfire, "warning"),
)
def _rerank_with_retry(query: str, documents: list[str], top_n: int) -> list[str]:
    """Call the Jina Reranker with automatic retry on transient HTTP failures."""
    return _get_ranker().rerank(query, documents, top_n)


def rerank_documents(query: str, documents: list[str], top_n: int = 5) -> list[str]:
    """
    Re-score and filter the candidate document list using the Jina cross-encoder.

    If JINA_API_KEY is absent or reranking fails after all retries, the original
    Qdrant result order is preserved and the first top_n items are returned.
    This ensures the Responder always receives some context even in a degraded state.
    """
    if not documents:
        return []
    if not settings.JINA_API_KEY:
        logfire.warning("JINA_API_KEY not set — skipping reranking, using raw Qdrant order.")
        return documents[:top_n]

    t0 = time.time()
    logfire.info(f"Sending {len(documents)} candidates to Jina Reranker.")
    try:
        result = _rerank_with_retry(query, documents, top_n)
        logfire.info(f"Reranking completed in {time.time() - t0:.2f}s.")
        return result
    except Exception as exc:
        logfire.error(f"Reranking failed after retries ({exc}). Falling back to Qdrant order.")
        return documents[:top_n]
