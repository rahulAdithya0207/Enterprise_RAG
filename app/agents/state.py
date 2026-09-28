import operator
from typing import Annotated, List, TypedDict


class AgentState(TypedDict):
    # messages accumulates the full conversation turn by turn.
    # operator.add means each node appends to the list rather than replacing it.
    messages: Annotated[List[dict], operator.add]

    # current_query holds either the Planner's refined search term
    # or the sentinel string "CONVERSATIONAL" to skip retrieval.
    current_query: str

    # documents contains the top-k reranked text chunks from Qdrant.
    documents: List[str]

    # plan is the running log of reasoning steps shown to the user.
    plan: List[str]

    # status is a human-readable string describing the pipeline's current stage.
    status: str

    # final_answer holds the LLM's synthesized response written by the Responder.
    final_answer: str
