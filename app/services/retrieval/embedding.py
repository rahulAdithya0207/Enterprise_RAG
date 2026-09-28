import logfire
import requests
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from app.config import settings

BATCH_SIZE = 64
_EMBEDDING_DIM = 1024
_JINA_EMBEDDING_URL = "https://api.jina.ai/v1/embeddings"
_JINA_MODEL = "jina-embeddings-v3"
_FALLBACK_MODEL = "mixedbread-ai/mxbai-embed-large-v1"

# These module-level variables track which embedding backend is active.
# _model_type is set to "jina" or "fallback" on first use.
_active_model = None
_model_type: str | None = None


def _load_fallback_model():
    """Download and load the local mxbai SentenceTransformer model."""
    from sentence_transformers import SentenceTransformer
    logfire.info(f"Loading local fallback embedding model: {_FALLBACK_MODEL}")
    return SentenceTransformer(_FALLBACK_MODEL)


def _probe_jina() -> bool:
    """
    Send a single test embedding to the Jina API to verify the key and endpoint.
    Returns True if the API responds successfully, False otherwise.
    """
    if not settings.JINA_API_KEY:
        logfire.info("JINA_API_KEY not set — will use local fallback embeddings.")
        return False
    try:
        resp = requests.post(
            _JINA_EMBEDDING_URL,
            headers={"Authorization": f"Bearer {settings.JINA_API_KEY}", "Content-Type": "application/json"},
            json={"model": _JINA_MODEL, "task": "retrieval.query", "normalized": True, "input": ["probe"]},
            timeout=30,
        )
        resp.raise_for_status()
        if not resp.json().get("data"):
            raise RuntimeError("Jina returned empty data on probe")
        logfire.info("Jina Embeddings API is reachable — using jina-embeddings-v3 (1024-dim).")
        return True
    except Exception as exc:
        logfire.warning(f"Jina probe failed: {exc}. Falling back to local model.")
        return False


def _init():
    """Choose and initialize the embedding backend on first use."""
    global _active_model, _model_type
    if _model_type is not None:
        return
    if _probe_jina():
        _model_type = "jina"
    else:
        _active_model = _load_fallback_model()
        _model_type = "fallback"


def get_embedding_dim() -> int:
    """Return the vector dimension for the active embedding model (always 1024)."""
    _init()
    return _EMBEDDING_DIM


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    reraise=True,
    before_sleep=before_sleep_log(logfire, "warning"),
)
def _embed_jina_batch(texts: list[str], task: str) -> list[list[float]]:
    """POST a single batch of texts to the Jina Embeddings API."""
    resp = requests.post(
        _JINA_EMBEDDING_URL,
        headers={"Authorization": f"Bearer {settings.JINA_API_KEY}", "Content-Type": "application/json"},
        json={"model": _JINA_MODEL, "task": task, "normalized": True, "input": texts},
        timeout=60,
    )
    resp.raise_for_status()
    results = sorted(resp.json().get("data", []), key=lambda x: x.get("index", 0))
    return [item["embedding"] for item in results]


def _embed_via_jina(texts: list[str], task: str) -> list[list[float]]:
    """Split texts into batches and embed each batch via the Jina API."""
    embeddings: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i: i + BATCH_SIZE]
        with logfire.span("Jina embed batch", start=i, size=len(batch)):
            embeddings.extend(_embed_jina_batch(batch, task))
    return embeddings


def _embed_via_fallback(texts: list[str]) -> list[list[float]]:
    """Split texts into batches and embed each batch using the local SentenceTransformer."""
    embeddings: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i: i + BATCH_SIZE]
        with logfire.span("Fallback embed batch", start=i, size=len(batch)):
            embeddings.extend(_active_model.encode(batch, show_progress_bar=False).tolist())
    return embeddings


def _embed(texts: list[str], task: str) -> list[list[float]]:
    """
    Route embedding requests to Jina API or the local fallback model.

    If the Jina API raises an exception at runtime (after initialization),
    the module switches permanently to the local fallback model for the
    remainder of the process lifetime.
    """
    global _active_model, _model_type

    _init()
    if _model_type == "jina":
        try:
            return _embed_via_jina(texts, task)
        except Exception as exc:
            logfire.error(f"Jina API failed at runtime ({exc}). Switching to local fallback.")
            _active_model = _load_fallback_model()
            _model_type = "fallback"
    return _embed_via_fallback(texts)


def embed_query(query: str) -> list[float]:
    """Embed a single query string for retrieval."""
    return _embed([query], task="retrieval.query")[0]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of document passages for indexing."""
    return _embed(texts, task="retrieval.passage")
