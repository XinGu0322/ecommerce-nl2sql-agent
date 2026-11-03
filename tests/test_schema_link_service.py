
import asyncio

import pytest

from app.services.schema_link_service import (
    SchemaLinkError,
    SchemaLinkService,
)


class FakeMetaRepository:
    def __init__(self, remove_region_fk=False):
        self.columns = [
            {
                "id": "fact_order.order_amount",
                "table_id": "fact_order",
                "name": "order_amount",
                "referenced_table": None,
                "referenced_column": None,
            },
            {
                "id": "fact_order.region_id",
                "table_id": "fact_order",
                "name": "region_id",
                "referenced_table": "dim_region",
                "referenced_column": "region_id",
            },
            {
                "id": "fact_order.product_id",
                "table_id": "fact_order",
                "name": "product_id",
                "referenced_table": "dim_product",
                "referenced_column": "product_id",
            },
            {
                "id": "dim_region.region_id",
                "table_id": "dim_region",
                "name": "region_id",
            },
            {
                "id": "dim_region.region_name",
                "table_id": "dim_region",
                "name": "region_name",
            },
            {
                "id": "dim_product.product_id",
                "table_id": "dim_product",
                "name": "product_id",
            },
            {
                "id": "dim_product.brand",
                "table_id": "dim_product",
                "name": "brand",
            },
        ]

        if remove_region_fk:
            self.columns = [
                column for column in self.columns
                if column["id"] != "fact_order.region_id"
            ]

    async def get_index_sources(self):
        return {
            "table": [
                {
                    "name": "fact_order",
                    "table_role": "fact",
                },
                {
                    "name": "dim_region",
                    "table_role": "dimension",
                },
                {
                    "name": "dim_product",
                    "table_role": "dimension",
                },
            ],
            "column": self.columns,
            "metric": [
                {
                    "id": "total_sales",
                    "name": "销售总额",
                    "expression": "SUM(fact_order.order_amount)",
                    "aliases": '["总销售额", "销售额"]',
                },
            ],
        }

    async def get_metric_dependencies(self, metric_id):
        if metric_id == "total_sales":
            return ["fact_order.order_amount"]
        return []


def make_intent():
    return {
        "metric": "销售总额",
        "filters": [
            {"text": "华北地区", "category": "region"},
            {"text": "苹果", "category": "brand"},
        ],
        "group_by": [],
        "time_expression": None,
        "sort": None,
        "limit": None,
        "needs_clarification": False,
    }


def make_evidence():
    return {
        "unresolved_terms": [],
        "ambiguous_terms": [],
        "filter_resolutions": [
            {
                "term": "华北地区",
                "category": "region",
                "field_id": "dim_region.region_name",
                "value": "华北",
            },
            {
                "term": "苹果",
                "category": "brand",
                "field_id": "dim_product.brand",
                "value": "苹果",
            },
        ],
    }


def test_schema_link_success():
    service = SchemaLinkService(FakeMetaRepository())

    plan = asyncio.run(
        service.link(make_intent(), make_evidence())
    )

    assert plan["metric"]["id"] == "total_sales"
    assert plan["fact_table"] == "fact_order"
    assert len(plan["joins"]) == 2

    joins = {
        (
            item["left_table"],
            item["left_column"],
            item["right_table"],
            item["right_column"],
        )
        for item in plan["joins"]
    }

    assert (
        "fact_order", "region_id",
        "dim_region", "region_id",
    ) in joins

    assert (
        "fact_order", "product_id",
        "dim_product", "product_id",
    ) in joins


def test_unknown_metric_rejected():
    intent = make_intent()
    intent["metric"] = "利润总额"

    service = SchemaLinkService(FakeMetaRepository())

    with pytest.raises(SchemaLinkError, match="无法唯一确定"):
        asyncio.run(service.link(intent, make_evidence()))


def test_missing_join_rejected():
    repository = FakeMetaRepository(remove_region_fk=True)
    service = SchemaLinkService(repository)

    with pytest.raises(SchemaLinkError, match="外键关系"):
        asyncio.run(
            service.link(make_intent(), make_evidence())
        )


def test_unsupported_group_by_rejected():
    intent = make_intent()
    intent["group_by"] = ["月份"]

    service = SchemaLinkService(FakeMetaRepository())

    with pytest.raises(SchemaLinkError, match="暂不支持"):
        asyncio.run(service.link(intent, make_evidence()))


def test_unresolved_filter_rejected():
    evidence = make_evidence()
    evidence["unresolved_terms"] = ["华北地区"]

    service = SchemaLinkService(FakeMetaRepository())

    with pytest.raises(SchemaLinkError, match="缺失或歧义"):
        asyncio.run(
            service.link(make_intent(), evidence)
        )
