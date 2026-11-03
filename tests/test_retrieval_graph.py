
import asyncio
from types import SimpleNamespace

import pytest

from app.agent.retrieval_graph import build_retrieval_graph


class FakeVectorService:
    async def search(self, query: str, limit: int = 8):
        return [
            SimpleNamespace(
                score=0.85,
                payload={
                    "kind": "metric",
                    "source_id": "total_sales",
                    "text": "销售总额指标",
                },
            )
        ]


class FakeValueClient:
    async def search(self, query: str, limit: int = 5):
        if query == "华北":
            return [{
                "_score": 3.0,
                "_source": {
                    "field_id": "dim_region.region_name",
                    "value": "华北",
                },
            }]
        return []


def test_retrieval_finds_metric_and_region():
    graph = build_retrieval_graph(
        FakeVectorService(),
        FakeValueClient(),
    )

    result = asyncio.run(graph.ainvoke({
        "question": "华北地区销售总额",
        "search_terms": ["华北"],
    }))

    evidence = result["evidence"]

    assert evidence["metrics"][0]["source_id"] == "total_sales"
    assert evidence["confirmed_filters"] == [{
        "field_id": "dim_region.region_name",
        "value": "华北",
    }]
    assert evidence["requires_review"] is False


def test_unknown_value_requires_review():
    graph = build_retrieval_graph(
        FakeVectorService(),
        FakeValueClient(),
    )

    result = asyncio.run(graph.ainvoke({
        "question": "火星地区销售总额",
        "search_terms": ["火星"],
    }))

    evidence = result["evidence"]

    assert evidence["confirmed_filters"] == []
    assert evidence["unresolved_terms"] == ["火星"]
    assert evidence["requires_review"] is True


def test_empty_question_is_rejected():
    graph = build_retrieval_graph(
        FakeVectorService(),
        FakeValueClient(),
    )

    with pytest.raises(ValueError, match="问题不能为空"):
        asyncio.run(graph.ainvoke({
            "question": "   ",
            "search_terms": [],
        }))
