
import asyncio
import json
import logging

from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.agent.query_graph import build_query_graph
from app.main import create_app
from app.observability import JsonLogFormatter
from app.services.query_executor import QueryExecutionResult


class JsonCaptureHandler(logging.Handler):
    """在测试期间收集结构化日志。"""

    def __init__(self):
        super().__init__()
        self.lines = []
        self.setFormatter(JsonLogFormatter())

    def emit(self, record):
        self.lines.append(
            json.loads(self.format(record))
        )


class FakeGraph:
    async def ainvoke(self, state):
        return {
            "status": "success",
            "execution": {
                "metric_id": "order_count",
                "value": 4,
                "rows": None,
            },
        }


def make_api_app():
    @asynccontextmanager
    async def fake_runtime():
        yield FakeGraph()

    return create_app(
        runtime_factory=fake_runtime
    )


def test_request_id_and_http_log(monkeypatch):
    test_api_key = (
        "local-test-api-key-1234567890-abcdefghij"
    )

    monkeypatch.setenv(
        "NL2SQL_API_KEY",
        test_api_key,
    )

    app = make_api_app()

    logger = logging.getLogger("nl2sql.http")
    collector = JsonCaptureHandler()

    logger.addHandler(collector)

    try:
        with TestClient(
                app,
                headers={"X-API-Key": test_api_key},
        ) as client:
            first = client.post(
                "/api/v1/query",
                json={
                    "question": "查询订单数量"
                },
            )

            second = client.post(
                "/api/v1/query",
                json={
                    "question": "查询销售额"
                },
            )
    finally:
        logger.removeHandler(collector)

    assert first.status_code == 200
    assert second.status_code == 200

    first_id = first.headers["X-Request-ID"]
    second_id = second.headers["X-Request-ID"]

    assert len(first_id) == 32
    assert len(second_id) == 32
    assert first_id != second_id

    access_logs = [
        item
        for item in collector.lines
        if item["event"] == "http_request"
    ]

    assert len(access_logs) == 2

    logged_ids = {
        item["request_id"]
        for item in access_logs
    }

    assert logged_ids == {
        first_id,
        second_id,
    }

    assert all(
        item["status_code"] == 200
        and item["duration_ms"] >= 0
        for item in access_logs
    )


class FakeSchemaService:
    async def link(self, intent, evidence):
        return {
            "metric": {
                "id": "total_sales"
            }
        }


class FakeExecutor:
    async def execute_plan(self, plan):
        return QueryExecutionResult(
            metric_id="total_sales",
            value="8999.00",
            sql="SELECT ...",
            params={},
        )


async def fake_retrieval(state):
    return {
        "intent": {
            "metric": "销售额",
            "needs_clarification": False,
        },
        "evidence": {
            "requires_review": False,
        },
    }


def test_graph_stage_timing():
    logger = logging.getLogger("nl2sql.agent")
    collector = JsonCaptureHandler()

    logger.addHandler(collector)

    try:
        graph = build_query_graph(
            retrieval_graph=fake_retrieval,
            schema_service=FakeSchemaService(),
            executor=FakeExecutor(),
        )

        result = asyncio.run(
            graph.ainvoke({
                "question": "查询销售额"
            })
        )
    finally:
        logger.removeHandler(collector)

    assert result["status"] == "success"

    stage_logs = [
        item
        for item in collector.lines
        if item["event"] == "agent_stage"
    ]

    assert [
        item["stage"]
        for item in stage_logs
    ] == [
        "retrieval",
        "schema_link",
        "execute_query",
    ]

    assert all(
        item["outcome"] == "success"
        and item["duration_ms"] >= 0
        for item in stage_logs
    )
