
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

TEST_API_KEY = (
    "local-test-api-key-1234567890-abcdefghij"
)


@pytest.fixture(autouse=True)
def configure_test_api_key(monkeypatch):
    monkeypatch.setenv(
        "NL2SQL_API_KEY",
        TEST_API_KEY,
    )


class FakeGraph:
    """用于 API 测试的查询工作流。"""

    def __init__(self, result, should_fail=False):
        self.result = result
        self.should_fail = should_fail
        self.questions = []

    async def ainvoke(self, state):
        self.questions.append(state["question"])

        if self.should_fail:
            raise RuntimeError("模拟内部服务故障")

        return self.result


def make_test_app(result, should_fail=False):
    """注入 FakeGraph，避免依赖外部服务。"""

    graph = FakeGraph(
        result=result,
        should_fail=should_fail,
    )

    @asynccontextmanager
    async def fake_runtime():
        yield graph

    app = create_app(
        runtime_factory=fake_runtime
    )

    return app, graph


def test_health_live():
    app, _ = make_test_app({})

    with TestClient(
            app,
            headers={"X-API-Key": TEST_API_KEY},
    ) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_single_metric_query():
    app, graph = make_test_app({
        "status": "success",
        "execution": {
            "metric_id": "order_count",
            "value": 4,
            "rows": None,
            "sql": "SELECT ...",
            "params": {"filter_0": "黄金"},
        },
    })

    with TestClient(
            app,
            headers={"X-API-Key": TEST_API_KEY},
    ) as client:
        response = client.post(
            "/api/v1/query",
            json={
                "question": "查询黄金会员的订单数量"
            },
        )

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "success"
    assert body["data"]["metric_id"] == "order_count"
    assert body["data"]["value"] == 4
    assert body["data"]["rows"] is None

    assert graph.questions == [
        "查询黄金会员的订单数量"
    ]

    # 内部 SQL 和参数不暴露给 API 调用方
    assert "sql" not in body
    assert "params" not in body


def test_grouped_query():
    app, _ = make_test_app({
        "status": "success",
        "execution": {
            "metric_id": "total_sales",
            "value": None,
            "rows": [
                {
                    "region_name": "华北",
                    "total_sales": "16997.00",
                },
                {
                    "region_name": "华南",
                    "total_sales": "6398.00",
                },
            ],
            "sql": "SELECT ...",
            "params": {},
        },
    })

    with TestClient(
            app,
            headers={"X-API-Key": TEST_API_KEY},
    ) as client:
        response = client.post(
            "/api/v1/query",
            json={
                "question": "对比华北和华南地区的销售额"
            },
        )

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == "success"
    assert body["data"]["value"] is None
    assert len(body["data"]["rows"]) == 2

    assert body["data"]["rows"][0]["region_name"] == "华北"
    assert body["data"]["rows"][1]["total_sales"] == "6398.00"


@pytest.mark.parametrize(
    "status",
    ["needs_review", "rejected"],
)
def test_business_rejection(status):
    app, _ = make_test_app({
        "status": status,
        "error": "当前查询条件无法处理",
    })

    with TestClient(
            app,
            headers={"X-API-Key": TEST_API_KEY},
    ) as client:
        response = client.post(
            "/api/v1/query",
            json={"question": "一个无法处理的查询"},
        )

    assert response.status_code == 422

    body = response.json()

    assert body["status"] == status
    assert body["data"] is None
    assert body["error"] == "当前查询条件无法处理"


def test_execution_failure():
    app, _ = make_test_app({
        "status": "execution_failed",
        "error": "内部数据库异常详情",
    })

    with TestClient(
            app,
            headers={"X-API-Key": TEST_API_KEY},
    ) as client:
        response = client.post(
            "/api/v1/query",
            json={"question": "查询销售额"},
        )

    assert response.status_code == 502

    body = response.json()

    assert body["status"] == "execution_failed"
    assert body["error"] == "查询执行失败"

    # 不向调用方泄露内部数据库异常信息
    assert "内部数据库异常详情" not in str(body)


def test_empty_question_rejected():
    app, graph = make_test_app({})

    with TestClient(
            app,
            headers={"X-API-Key": TEST_API_KEY},
    ) as client:
        response = client.post(
            "/api/v1/query",
            json={"question": "   "},
        )

    assert response.status_code == 422

    # 输入验证失败，不能调用 LangGraph
    assert graph.questions == []


def test_unexpected_error_hidden():
    app, _ = make_test_app(
        result={},
        should_fail=True,
    )

    with TestClient(
            app,
            headers={"X-API-Key": TEST_API_KEY},
    ) as client:
        response = client.post(
            "/api/v1/query",
            json={"question": "查询销售额"},
        )

    assert response.status_code == 500
    assert response.json()["detail"] == "查询服务内部错误"
