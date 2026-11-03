
import asyncio
import logging

from time import perf_counter

from langchain_core.runnables import RunnableConfig
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

from dataclasses import asdict


logger = logging.getLogger("nl2sql.agent")


async def trace_stage(stage: str, operation):
    """
    统计单个 Agent 阶段的执行时间。

    不记录问题原文、SQL、参数及数据库结果。
    """

    started = perf_counter()

    outcome = "success"
    error_type = None

    try:
        return await operation

    except asyncio.CancelledError:
        outcome = "cancelled"
        error_type = "CancelledError"
        raise

    except Exception as exc:
        outcome = "failed"
        error_type = type(exc).__name__
        raise

    finally:
        elapsed_ms = round(
            (perf_counter() - started) * 1000,
            2,
        )

        log_method = (
            logger.info
            if outcome == "success"
            else logger.warning
        )

        log_method(
            "agent_stage",
            extra={
                "stage": stage,
                "outcome": outcome,
                "duration_ms": elapsed_ms,
                "error_type": error_type,
            },
        )


def build_query_graph(
    retrieval_graph,
    schema_service: SchemaLinkService,
    executor: QueryExecutor,
):
    """
    构建完整 NL2SQL 查询工作流。

    retrieval
       |
       +-- needs_review --> END
       |
       v
    schema_link
       |
       +-- rejected --> END
       |
       v
    execute_query --> END
    """

    # ==============================================
    # 1. 执行检索子图
    # ==============================================

    async def retrieval_node(
        state: QueryState,
        config: RunnableConfig,
    ) -> dict:
        # 兼容已编译的 LangGraph 子图
        # 以及现有单元测试使用的异步函数
        if hasattr(retrieval_graph, "ainvoke"):
            operation = retrieval_graph.ainvoke(
                state,
                config=config,
            )
        else:
            operation = retrieval_graph(state)

        return await trace_stage(
            "retrieval",
            operation,
        )

    # ==============================================
    # 2. 检索后的业务条件判断
    # ==============================================

    def route_after_retrieval(
        state: QueryState,
    ) -> str:
        evidence = state.get("evidence", {})
        intent = state.get("intent", {})

        if (
            evidence.get("requires_review", True)
            or intent.get("needs_clarification", False)
        ):
            return "needs_review"

        return "schema_link"

    def needs_review(
        state: QueryState,
    ) -> dict:
        return {
            "status": "needs_review",
            "error": (
                "查询条件尚未完全确认，"
                "需要进一步澄清"
            ),
        }

    # ==============================================
    # 3. Schema Linking
    # ==============================================

    async def schema_link(
        state: QueryState,
    ) -> dict:
        try:
            plan = await trace_stage(
                "schema_link",
                schema_service.link(
                    state["intent"],
                    state["evidence"],
                ),
            )

        except SchemaLinkError as exc:
            return {
                "status": "rejected",
                "error": str(exc),
            }

        return {
            "schema_plan": plan,
        }

    # ==============================================
    # 4. Schema Linking 后的路由
    # ==============================================

    def route_after_schema(
        state: QueryState,
    ) -> str:
        if state.get("status") == "rejected":
            return "stop"

        return "execute_query"

    # ==============================================
    # 5. 安全 SQL 执行
    # ==============================================

    async def execute_query(
        state: QueryState,
    ) -> dict:
        try:
            result = await trace_stage(
                "execute_query",
                executor.execute_plan(
                    state["schema_plan"]
                ),
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

    # ==============================================
    # 6. 构建 StateGraph
    # ==============================================

    builder = StateGraph(QueryState)

    builder.add_node(
        "retrieval",
        retrieval_node,
    )

    builder.add_node(
        "needs_review",
        needs_review,
    )

    builder.add_node(
        "schema_link",
        schema_link,
    )

    builder.add_node(
        "execute_query",
        execute_query,
    )

    # ==============================================
    # 7. 配置节点连接
    # ==============================================

    builder.add_edge(
        START,
        "retrieval",
    )

    builder.add_conditional_edges(
        "retrieval",
        route_after_retrieval,
        {
            "needs_review": "needs_review",
            "schema_link": "schema_link",
        },
    )

    builder.add_edge(
        "needs_review",
        END,
    )

    builder.add_conditional_edges(
        "schema_link",
        route_after_schema,
        {
            "stop": END,
            "execute_query": "execute_query",
        },
    )

    builder.add_edge(
        "execute_query",
        END,
    )

    return builder.compile()
