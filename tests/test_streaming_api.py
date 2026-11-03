import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


TEST_KEY = "local-stream-test-api-key-1234567890abcdef"


class FakeStreamGraph:
    def __init__(self):
        self.calls = 0

    async def astream(self, state, stream_mode):
        self.calls += 1

        yield {
            "retrieval": {
                "evidence": {"requires_review": False}
            }
        }

        yield {
            "schema_link": {
                "schema_plan": {}
            }
        }

        yield {
            "execute_query": {
                "status": "success",
                "execution": {
                    "metric_id": "total_sales",
                    "value": None,
                    "rows": [
                        {
                            "brand": "苹果",
                            "total_sales": "17998.00",
                        }
                    ],
                },
            }
        }


@pytest.fixture
def app_and_graph(monkeypatch):
    monkeypatch.setenv("NL2SQL_API_KEY", TEST_KEY)

    graph = FakeStreamGraph()

    @asynccontextmanager
    async def fake_runtime():
        yield graph

    app = create_app(runtime_factory=fake_runtime)

    return app, graph


def test_sse_success(app_and_graph):
    app, graph = app_and_graph

    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/v1/query/stream",
            headers={"X-API-Key": TEST_KEY},
            json={"question": "查询销售额最高的三个品牌"},
        ) as response:
            body = "".join(response.iter_text())

            assert response.status_code == 200
            assert "text/event-stream" in response.headers[
                "content-type"
            ]

    assert "event: started" in body
    assert "event: progress" in body
    assert "event: result" in body
    assert "event: done" in body


    result_event = next(
        block
        for block in body.split("\n\n")
        if block.startswith("event: result\n")
    )

    result_data = json.loads(
        result_event.split("data: ", 1)[1]
    )

    assert result_data["status"] == "success"
    assert (
            result_data["data"]["rows"][0]["total_sales"]
            == "17998.00"
    )
    assert graph.calls == 1


def test_sse_requires_api_key(app_and_graph):
    app, graph = app_and_graph

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/query/stream",
            json={"question": "查询销售额"},
        )

    assert response.status_code == 401
    assert graph.calls == 0
