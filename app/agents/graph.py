import logfire
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.agents.nodes.planner import planner_node
from app.agents.nodes.responder import generate_node
from app.agents.nodes.retriever import retrieve_node
from app.agents.state import AgentState
from app.config import settings


def create_checkpointer() -> BaseCheckpointSaver:
    """
    Build a durable Postgres-backed checkpointer for conversation memory.

    LangGraph migrations include CREATE INDEX CONCURRENTLY, which Neon Postgres
    rejects inside a transaction. To work around this, we run the setup step on
    a separate autocommit connection rather than through the connection pool.

    If Postgres is unreachable at startup, we fall back to MemorySaver.
    MemorySaver keeps state only in process memory — acceptable for development,
    but any restart will lose all conversation history.
    """
    if not settings.NEON_DB_URL:
        logfire.info("NEON_DB_URL is not set. Using MemorySaver for local development.")
        return MemorySaver()

    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg_pool import ConnectionPool

        pool = ConnectionPool(
            conninfo=settings.postgres_uri,
            max_size=20,
            open=False,
            timeout=10,
            num_workers=3,
            check=ConnectionPool.check_connection,
            max_idle=240,
        )
        pool.open()
        # Verify the pool can hand out a connection before proceeding.
        conn = pool.getconn()
        pool.putconn(conn)

        # Run migrations on a dedicated autocommit connection.
        try:
            with PostgresSaver.from_conn_string(settings.postgres_uri) as setup_saver:
                setup_saver.setup()
        except Exception as exc:
            logfire.warning(f"Postgres setup failed ({exc}). Falling back to MemorySaver.")
            pool.close()
            return MemorySaver()

        logfire.info("Postgres checkpointer ready.")
        return PostgresSaver(pool)

    except Exception as exc:
        logfire.warning(
            f"Postgres unavailable ({exc}). Using MemorySaver — state will not survive restarts."
        )
        return MemorySaver()


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> StateGraph:
    """
    Assemble and compile the LangGraph RAG agent.

    The graph has three nodes — Planner, Retriever, and Responder — connected
    by a conditional edge that routes based on the Planner's intent decision.

    Args:
        checkpointer: Inject a specific checkpointer (e.g. MemorySaver in tests).
                      If None, the production Postgres checkpointer is used.
    """
    if checkpointer is None:
        checkpointer = create_checkpointer()

    workflow = StateGraph(AgentState)

    workflow.add_node("planner", planner_node)
    workflow.add_node("retriever", retrieve_node)
    workflow.add_node("responder", generate_node)

    def route_after_planner(state: AgentState):
        """Skip retrieval for conversational turns; run full RAG for technical queries."""
        if state["current_query"] == "CONVERSATIONAL":
            return "responder"
        return "retriever"

    workflow.set_entry_point("planner")
    workflow.add_conditional_edges(
        "planner",
        route_after_planner,
        {"retriever": "retriever", "responder": "responder"},
    )
    workflow.add_edge("retriever", "responder")
    workflow.add_edge("responder", END)

    return workflow.compile(checkpointer=checkpointer)
