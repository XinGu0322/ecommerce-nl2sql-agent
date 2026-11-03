
from dataclasses import asdict

from langgraph.graph import START, END, StateGraph

from app.agent.state import QueryState
from app.services.schema_link_service import (
    SchemaLinkError,
    SchemaLinkService,
)
from app.services.query_executor import (
    QueryExecutionError,
    QueryExecutor,
)
from app.services.sql_compiler import SQLCompilationError
from app.services.sql_validator import SQLValidationError


def build_query_graph(
    retrieval_graph,
    schema_service: SchemaLinkService,
    executor: QueryExecutor,
):
    """构建包含检索、业务规划及安全执行的完整查询图。"""

    def route_after_retrieval(state: QueryState) -> str:
        evidence = state.get("evidence", {})
        intent = state.get("intent", {})

        if (
            evidence.get("requires_review", True)
            or intent.get("needs_clarification", False)
        ):
            return "needs_review"

        return "schema_link"

    def needs_review(state: QueryState) -> dict:
        return {
            "status": "needs_review",
            "error": "查询条件尚未完全确认，需要进一步澄清",
        }

    async def schema_link(state: QueryState) -> dict:
        try:
            plan = await schema_service.link(
                state["intent"],
                state["evidence"],
            )
        except SchemaLinkError as exc:
            return {
                "status": "rejected",
                "error": str(exc),
            }

        return {"schema_plan": plan}

    def route_after_schema(state: QueryState) -> str:
        if state.get("status") == "rejected":
            return "stop"

        return "execute_query"

    async def execute_query(state: QueryState) -> dict:
        try:
            result = await executor.execute_plan(
                state["schema_plan"]
            )

        except (
            SQLCompilationError,
            SQLValidationError,
            QueryExecutionError,
        ) as exc:
            return {
                "status": "execution_failed",
                "error": str(exc),
            }

        return {
            "status": "success",
            "execution": asdict(result),
            "error": None,
        }

    builder = StateGraph(QueryState)

    # 复用已有的完整检索工作流
    builder.add_node("retrieval", retrieval_graph)

    builder.add_node("needs_review", needs_review)
    builder.add_node("schema_link", schema_link)
    builder.add_node("execute_query", execute_query)

    builder.add_edge(START, "retrieval")

    builder.add_conditional_edges(
        "retrieval",
        route_after_retrieval,
        {
            "needs_review": "needs_review",
            "schema_link": "schema_link",
        },
    )

    builder.add_edge("needs_review", END)

    builder.add_conditional_edges(
        "schema_link",
        route_after_schema,
        {
            "stop": END,
            "execute_query": "execute_query",
        },
    )

    builder.add_edge("execute_query", END)

    return builder.compile()
