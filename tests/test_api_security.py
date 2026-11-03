
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.api.security import SlidingWindowRateLimiter
from app.main import create_app


TEST_API_KEY = (
    "local-test-api-key-1234567890-abcdefghij"
)


class FakeGraph:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, state):
        self.calls += 1

        return {
            "status": "success",
            "execution": {
                "metric_id": "order_count",
                "value": 4,
                "rows": None,
            },
        }


@pytest.fixture
def test_application(monkeypatch):
    """创建不依赖外部服务的测试应用。"""

    monkeypatch.setenv(
        "NL2SQL_API_KEY",
        TEST_API_KEY,
    )

    graph = FakeGraph()

    @asynccontextmanager
    async def fake_runtime():
        yield graph

    app = create_app(
        runtime_factory=fake_runtime
    )

    return app, graph


def make_request(client, headers=None):
    return client.post(
        "/api/v1/query",
        json={
            "question": "查询黄金会员的订单数量"
        },
        headers=headers,
    )


def test_missing_api_key_rejected(test_application):
    """缺失 API Key 时不得执行 Agent。"""

    app, graph = test_application

    with TestClient(app) as client:
        response = make_request(client)

    assert response.status_code == 401
    assert graph.calls == 0


def test_invalid_api_key_rejected(test_application):
    """错误 API Key 不得进入 LangGraph。"""

    app, graph = test_application

    with TestClient(app) as client:
        response = make_request(
            client,
            headers={"X-API-Key": "invalid-key"},
        )

    assert response.status_code == 401
    assert graph.calls == 0


def test_valid_api_key_success(test_application):
    """合法 API Key 可以正常查询。"""

    app, graph = test_application

    with TestClient(app) as client:
        response = make_request(
            client,
            headers={"X-API-Key": TEST_API_KEY},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["data"]["value"] == 4
    assert graph.calls == 1


def test_rate_limit_returns_429(test_application):
    """超过滑动窗口配额后拒绝执行 Agent。"""

    app, graph = test_application

    with TestClient(app) as client:

        # 测试使用2次配额，不影响真实服务配置
        app.state.query_rate_limiter = (
            SlidingWindowRateLimiter(
                limit=2,
                window_seconds=60,
            )
        )

        headers = {
            "X-API-Key": TEST_API_KEY
        }

        first = make_request(client, headers)
        second = make_request(client, headers)
        third = make_request(client, headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429

    # 超限后提供重试等待秒数
    assert int(
        third.headers["Retry-After"]
    ) >= 1

    # 第三次请求不能进入 LangGraph
    assert graph.calls == 2


def test_health_endpoint_is_public(test_application):
    """服务存活探针不要求 API Key。"""

    app, _ = test_application

    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_missing_server_key_blocks_startup(
    test_application,
    monkeypatch,
):
    """服务端没有有效 Key 时应拒绝启动。"""

    app, _ = test_application

    monkeypatch.setenv(
        "NL2SQL_API_KEY",
        "",
    )

    with pytest.raises(
        RuntimeError,
        match="NL2SQL_API_KEY",
    ):
        with TestClient(app):
            pass
