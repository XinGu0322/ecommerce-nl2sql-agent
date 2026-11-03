
import asyncio

from app.agent.query_graph import build_query_graph
from app.services.query_executor import (
    QueryExecutionError,
    QueryExecutionResult,
)
from app.services.schema_link_service import SchemaLinkError


class FakeSchemaService:
    def __init__(self, should_reject=False):
        self.should_reject = should_reject
        self.calls = 0

    async def link(self, intent, evidence):
        self.calls += 1

        if self.should_reject:
            raise SchemaLinkError("指标无法唯一确定")

        return {"fact_table": "fact_order"}


class FakeExecutor:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.calls = 0

    async def execute_plan(self, plan):
        self.calls += 1

        if self.should_fail:
            raise QueryExecutionError("数据库查询失败")

        return QueryExecutionResult(
            metric_id="total_sales",
            value="8999.00",
            sql="SELECT ...",
            params={},
        )


def make_retrieval(requires_review=False):
    async def retrieval(state):
        return {
            "intent": {
                "metric": "销售总额",
                "needs_clarification": False,
            },
            "evidence": {
                "requires_review": requires_review,
            },
        }

    return retrieval


def test_query_success():
    schema = FakeSchemaService()
    executor = FakeExecutor()

    graph = build_query_graph(
        make_retrieval(), schema, executor
    )

    result = asyncio.run(graph.ainvoke({
        "question": "统计销售总额",
    }))

    assert result["status"] == "success"
    assert result["execution"]["value"] == "8999.00"
    assert schema.calls == 1
    assert executor.calls == 1


def test_review_skips_database():
    schema = FakeSchemaService()
    executor = FakeExecutor()

    graph = build_query_graph(
        make_retrieval(requires_review=True),
        schema,
        executor,
    )

    result = asyncio.run(graph.ainvoke({
        "question": "查询不明确的地区销售额",
    }))

    assert result["status"] == "needs_review"
    assert schema.calls == 0
    assert executor.calls == 0


def test_schema_rejection_skips_execution():
    schema = FakeSchemaService(should_reject=True)
    executor = FakeExecutor()

    graph = build_query_graph(
        make_retrieval(), schema, executor
    )

    result = asyncio.run(graph.ainvoke({
        "question": "统计销售总额",
    }))

    assert result["status"] == "rejected"
    assert executor.calls == 0


def test_database_failure_is_reported():
    schema = FakeSchemaService()
    executor = FakeExecutor(should_fail=True)

    graph = build_query_graph(
        make_retrieval(), schema, executor
    )

    result = asyncio.run(graph.ainvoke({
        "question": "统计销售总额",
    }))

    assert result["status"] == "execution_failed"
    assert executor.calls == 1
    assert result["error"] == "数据库查询失败"
