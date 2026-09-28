# logfire must be configured before any other import so that spans from
# all downstream modules are captured from the very start of the process.
import logfire

from app.config import settings

# EU tokens require a different ingest endpoint. We infer it from the token
# prefix so the same .env file works locally and inside a container.
_logfire_base_url = settings.LOGFIRE_BASE_URL
if not _logfire_base_url and settings.LOGFIRE_TOKEN:
    if settings.LOGFIRE_TOKEN.startswith("pylf_v2_eu_"):
        _logfire_base_url = "https://logfire-eu.pydantic.dev"

if settings.LOGFIRE_TOKEN:
    logfire.configure(
        token=settings.LOGFIRE_TOKEN,
        advanced=logfire.AdvancedOptions(base_url=_logfire_base_url) if _logfire_base_url else None,
    )

import functools
import time
import uuid
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from app.agents.graph import build_graph
from app.guardrails import guard, initialize_rails
from app.health import router as health_router
from app.logging import set_request_id
from app.services.health.connection_checker import check_all_connections, log_connection_summary

# Prometheus counters and histogram for /query observability
RAG_REQUESTS_TOTAL = Counter(
    "rag_requests_total",
    "Total number of /query requests by outcome",
    ["status"],
)
RAG_REQUEST_DURATION = Histogram(
    "rag_request_duration_seconds",
    "End-to-end /query latency in seconds",
)
GUARDRAILS_BLOCKS_TOTAL = Counter(
    "guardrails_blocks_total",
    "Count of /query requests blocked vs passed by guardrails",
    ["blocked"],
)

_security = HTTPBearer(auto_error=False)


class _DeferredLimiter:
    """
    Bridges the gap between route decoration time and limiter initialization time.

    Routes are decorated at module import, but the actual slowapi Limiter
    (either Redis-backed or in-memory) is only created during app startup.
    This class stores the rate-limit rule and resolves the live limiter from
    app.state at the moment each request arrives.
    """

    def limit(self, rule_fn):
        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                limiter = getattr(app.state, "limiter", None)
                if limiter is not None:
                    rule = rule_fn() if callable(rule_fn) else rule_fn
                    return limiter.limit(rule)(func)(*args, **kwargs)
                return func(*args, **kwargs)
            return wrapper
        return decorator


_deferred_limiter = _DeferredLimiter()


def _init_rate_limiter():
    """
    Set up the request rate limiter during app startup.

    Tries to connect to the Upstash Redis TLS endpoint for distributed rate
    limiting across replicas. If Redis is unreachable, falls back to a local
    in-memory counter — suitable for single-instance development only.
    """
    from limits.storage import RedisStorage
    from slowapi import Limiter
    from slowapi.errors import RateLimitExceeded
    from slowapi.extension import _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address

    try:
        storage = RedisStorage(settings.redis_url)
        if not storage.check() or not storage.storage.ping():
            raise ConnectionError("Redis ping failed — host is not reachable")
        app.state.limiter = Limiter(key_func=get_remote_address, storage_uri=settings.redis_url)
        app.state.rate_limiter_storage = "redis"
        logfire.info("Rate limiter initialized with Redis backend.")
    except Exception as exc:
        app.state.limiter = Limiter(key_func=get_remote_address)
        app.state.rate_limiter_storage = "memory"
        logfire.warning(f"Redis unavailable ({exc}). Using in-memory rate limiting instead.")

    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(_security)):
    """
    Enforce bearer-token auth when RAG_API_KEY is configured.
    If the env variable is absent, authentication is skipped entirely —
    this is intentional for local development but must not reach production.
    """
    if not settings.API_KEY:
        return None

    if not credentials or credentials.credentials != settings.API_KEY:
        logfire.warning("Rejected /query request — missing or invalid bearer token.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


app = FastAPI(title="Enterprise Agentic RAG API")
app.include_router(health_router)
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


@app.on_event("startup")
def startup_event():
    # Build NeMo rails, compile the LangGraph agent, then wire up rate limiting.
    initialize_rails()
    app.state.rag_agent = build_graph()
    _init_rate_limiter()

    # Check that all external services (Qdrant, Postgres, Redis, LLM gateway) are reachable.
    connection_results = check_all_connections()
    all_healthy = log_connection_summary(connection_results)
    if settings.STRICT_STARTUP and not all_healthy:
        failed = [name for name, r in connection_results.items() if not r.healthy]
        raise RuntimeError(f"STRICT_STARTUP: unhealthy services — {', '.join(failed)}")

    if not settings.API_KEY:
        logfire.warning("RAG_API_KEY is not set — /query is open to unauthenticated requests.")


class QueryRequest(BaseModel):
    q: str
    thread_id: Optional[str] = "default_user"


@app.get("/")
def home():
    return {"message": "Enterprise LangGraph RAG API is live."}


@app.get("/graph")
def get_graph_image(_api_key: str = Depends(verify_api_key)):
    """Render the compiled LangGraph agent graph as a Mermaid PNG."""
    try:
        png_bytes = app.state.rag_agent.get_graph().draw_mermaid_png()
        return Response(content=png_bytes, media_type="image/png")
    except Exception as exc:
        return {"error": f"Could not render graph: {exc}"}


@app.post("/query")
@_deferred_limiter.limit(lambda: f"{settings.RATE_LIMIT_PER_MINUTE}/minute")
def query(
    request: Request,
    body: QueryRequest,
    _api_key: str = Depends(verify_api_key),
):
    """
    Main RAG endpoint. Applies guardrails first — if a rail fires, returns
    immediately without touching the LangGraph pipeline. Otherwise, runs the
    full Planner → Retriever → Responder graph and returns the synthesized answer.
    """
    q = body.q
    thread_id = body.thread_id
    request_id = str(uuid.uuid4())
    set_request_id(request_id)

    start = time.perf_counter()
    with logfire.span("RAG /query", request_id=request_id, thread_id=thread_id):
        rail_fired, rail_response = guard(q)
        if rail_fired:
            GUARDRAILS_BLOCKS_TOTAL.labels(blocked="true").inc()
            RAG_REQUESTS_TOTAL.labels(status="blocked").inc()
            RAG_REQUEST_DURATION.observe(time.perf_counter() - start)
            logfire.info("Query blocked by guardrails.", request_id=request_id)
            return {
                "question": q,
                "answer": rail_response,
                "thought_process": ["Intent: Guardrails Fired", "Retrieval: Skipped"],
                "status": "Blocked by guardrails.",
                "sources": [],
            }

        GUARDRAILS_BLOCKS_TOTAL.labels(blocked="false").inc()

        try:
            initial_state = {
                "messages": [{"role": "user", "content": q}],
                "current_query": q,
                "documents": [],
                "plan": ["Start"],
                "status": "Initializing...",
            }
            config = {"configurable": {"thread_id": thread_id}}
            final_output = app.state.rag_agent.invoke(initial_state, config=config)

            RAG_REQUESTS_TOTAL.labels(status="success").inc()
            RAG_REQUEST_DURATION.observe(time.perf_counter() - start)
            logfire.info("RAG pipeline completed.", request_id=request_id, thread_id=thread_id)
            return {
                "question": q,
                "answer": final_output.get("final_answer"),
                "thought_process": final_output.get("plan"),
                "status": final_output.get("status"),
                "sources": final_output.get("documents", []),
            }
        except Exception as exc:
            RAG_REQUESTS_TOTAL.labels(status="error").inc()
            RAG_REQUEST_DURATION.observe(time.perf_counter() - start)
            logfire.error(f"RAG pipeline error: {exc}", request_id=request_id)
            return JSONResponse(
                status_code=500,
                content={
                    "request_id": request_id,
                    "status": "error",
                    "message": "Pipeline failed — please retry.",
                },
            )
