
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
    DATA = {
        "华北": ("dim_region.region_name", "华北"),
        "华南": ("dim_region.region_name", "华南"),
        "苹果": ("dim_product.brand", "苹果"),
        "黄金": ("dim_customer.member_level", "黄金"),
    }

    async def search_exact(
        self,
        value: str,
        field_ids: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        # 模拟数据库中不存在该值
        item = self.DATA.get(value)

        if item is None:
            return []

        field_id, actual_value = item

        # 模拟 Elasticsearch 的字段白名单过滤
        if field_ids is not None and field_id not in field_ids:
            return []

        # 模拟真实 Elasticsearch 响应结构
        return [{
            "_source": {
                "field_id": field_id,
                "value": actual_value,
            }
        }]



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
