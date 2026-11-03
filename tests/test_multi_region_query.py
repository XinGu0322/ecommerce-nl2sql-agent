
import asyncio
from copy import deepcopy

import pytest

from app.services.schema_link_service import (
    SchemaLinkError,
    SchemaLinkService,
)
from app.services.sql_compiler import (
    SQLCompilationError,
    compile_schema_plan,
)
from app.services.sql_validator import validate_sql


class FakeMetaRepository:
    """只提供多地区对比测试需要的元数据。"""

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
            ],
            "column": [
                {
                    "id": "fact_order.order_amount",
                    "table_id": "fact_order",
                    "name": "order_amount",
                },
                {
                    "id": "fact_order.region_id",
                    "table_id": "fact_order",
                    "name": "region_id",
                    "referenced_table": "dim_region",
                    "referenced_column": "region_id",
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
            ],
            "metric": [
                {
                    "id": "total_sales",
                    "name": "销售总额",
                    "expression": "SUM(fact_order.order_amount)",
                    "aliases": '["销售额", "总销售额"]',
                },
            ],
        }

    async def get_metric_dependencies(self, metric_id):
        if metric_id == "total_sales":
            return ["fact_order.order_amount"]
        return []


def make_intent():
    return {
        "metric": "销售额",
        "filters": [
            {"text": "华北", "category": "region"},
            {"text": "华南地区", "category": "region"},
        ],
        "time_expression": None,
        "group_by": ["地区"],
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
                "term": "华北",
                "category": "region",
                "field_id": "dim_region.region_name",
                "value": "华北",
            },
            {
                "term": "华南地区",
                "category": "region",
                "field_id": "dim_region.region_name",
                "value": "华南",
            },
        ],
    }


@pytest.fixture
def comparison_plan():
    service = SchemaLinkService(FakeMetaRepository())

    return asyncio.run(
        service.link(
            make_intent(),
            make_evidence(),
        )
    )


def test_multi_region_schema_link(comparison_plan):
    """两个地区应归并为同一字段的多值筛选。"""

    assert comparison_plan["metric"]["id"] == "total_sales"

    assert comparison_plan["group_by"] == [
        "dim_region.region_name"
    ]

    assert comparison_plan["filters"] == [
        {
            "field_id": "dim_region.region_name",
            "values": ["华北", "华南"],
        }
    ]

    assert comparison_plan["required_tables"] == [
        "fact_order",
        "dim_region",
    ]

    assert len(comparison_plan["joins"]) == 1


def test_multi_region_sql_and_ast(comparison_plan):
    """SQL 应使用参数化 IN，并按地区分组。"""

    query = compile_schema_plan(comparison_plan)

    assert "INNER JOIN `dim_region`" in query.sql

    assert (
        "IN (:filter_0, :filter_1)"
        in query.sql
    )

    assert (
        "GROUP BY `dim_region`.`region_name`"
        in query.sql
    )

    assert "LIMIT 101" in query.sql

    assert query.params == {
        "filter_0": "华北",
        "filter_1": "华南",
    }

    validate_sql(query.sql, comparison_plan)


def test_single_value_remains_compatible():
    """一个地区仍使用原来的等值筛选。"""

    intent = make_intent()
    intent["filters"] = [
        {"text": "华北", "category": "region"}
    ]

    service = SchemaLinkService(FakeMetaRepository())

    plan = asyncio.run(
        service.link(intent, make_evidence())
    )

    assert plan["filters"] == [
        {
            "field_id": "dim_region.region_name",
            "value": "华北",
        }
    ]

    query = compile_schema_plan(plan)

    assert (
        "`dim_region`.`region_name` = :filter_0"
        in query.sql
    )


def test_multi_value_without_group_rejected():
    """对比不能悄悄退化为合计查询。"""

    intent = make_intent()
    intent["group_by"] = []

    service = SchemaLinkService(FakeMetaRepository())

    with pytest.raises(
        SchemaLinkError,
        match="多值对比必须",
    ):
        asyncio.run(
            service.link(intent, make_evidence())
        )


@pytest.mark.parametrize(
    "invalid_values",
    [
        [],
        ["华北"],
        ["华北", "华北"],
        ["华北", 42],
        ["华北"] * 11,
    ],
)
def test_invalid_multi_values_rejected(
    comparison_plan,
    invalid_values,
):
    """不合法的 IN 参数列表必须被拒绝。"""

    plan = deepcopy(comparison_plan)

    plan["filters"][0]["values"] = invalid_values

    with pytest.raises(SQLCompilationError):
        compile_schema_plan(plan)


def test_multi_value_sql_injection_is_bound(comparison_plan):
    """筛选取值必须通过参数绑定，不进入 SQL 文本。"""

    plan = deepcopy(comparison_plan)

    dangerous_value = "华南' OR 1=1 --"

    plan["filters"][0]["values"] = [
        "华北",
        dangerous_value,
    ]

    query = compile_schema_plan(plan)

    assert dangerous_value not in query.sql
    assert query.params["filter_1"] == dangerous_value

    validate_sql(query.sql, plan)
