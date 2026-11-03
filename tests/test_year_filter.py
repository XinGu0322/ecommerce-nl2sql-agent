
import asyncio
from copy import deepcopy

import pytest

from app.agent.intent_schema import (
    QueryIntent,
    extract_search_terms,
)
from app.services.year_filter import parse_explicit_year
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
    async def get_index_sources(self):
        return {
            "table": [
                {"name": "fact_order", "table_role": "fact"},
                {"name": "dim_date", "table_role": "dimension"},
                {"name": "dim_region", "table_role": "dimension"},
            ],
            "column": [
                {
                    "id": "fact_order.order_amount",
                    "table_id": "fact_order",
                    "name": "order_amount",
                },
                {
                    "id": "fact_order.date_id",
                    "table_id": "fact_order",
                    "name": "date_id",
                    "referenced_table": "dim_date",
                    "referenced_column": "date_id",
                },
                {
                    "id": "fact_order.region_id",
                    "table_id": "fact_order",
                    "name": "region_id",
                    "referenced_table": "dim_region",
                    "referenced_column": "region_id",
                },
                {
                    "id": "dim_date.date_id",
                    "table_id": "dim_date",
                    "name": "date_id",
                },
                {
                    "id": "dim_date.year",
                    "table_id": "dim_date",
                    "name": "year",
                },
                {
                    "id": "dim_date.month",
                    "table_id": "dim_date",
                    "name": "month",
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
                    "aliases": ["销售额"],
                }
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
            {
                "text": "华南地区",
                "category": "region",
            }
        ],
        "time_expression": "2025年",
        "group_by": ["月份"],
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
                "term": "华南地区",
                "category": "region",
                "field_id": "dim_region.region_name",
                "value": "华南",
            }
        ],
    }


def make_plan(intent=None):
    service = SchemaLinkService(FakeMetaRepository())

    return asyncio.run(
        service.link(
            intent or make_intent(),
            make_evidence(),
        )
    )


def test_explicit_year_parsing():
    assert parse_explicit_year("2025年") == 2025


@pytest.mark.parametrize(
    "expression",
    [
        "去年",
        "最近三个月",
        "2025年1月",
        "2025年至2026年",
        "2025",
    ],
)
def test_unsupported_time_expression(expression):
    with pytest.raises(ValueError):
        parse_explicit_year(expression)


def test_year_filter_schema_link():
    plan = make_plan()

    assert plan["group_by"] == [
        "dim_date.year",
        "dim_date.month",
    ]

    assert {
        "field_id": "dim_date.year",
        "value": 2025,
    } in plan["filters"]

    assert "dim_date" in plan["required_tables"]
    assert "dim_region" in plan["required_tables"]

    assert any(
        join["right_table"] == "dim_date"
        and join["left_column"] == "date_id"
        for join in plan["joins"]
    )


def test_year_filter_sql_compilation():
    plan = make_plan()
    query = compile_schema_plan(plan)

    assert "INNER JOIN `dim_date`" in query.sql
    assert "INNER JOIN `dim_region`" in query.sql

    assert "`dim_date`.`year` = :filter_1" in query.sql
    assert "GROUP BY" in query.sql

    assert query.params == {
        "filter_0": "华南",
        "filter_1": 2025,
    }

    # 生成的 SQL 必须仍然通过 AST 安全校验
    validate_sql(query.sql, plan)


def test_year_filter_without_group():
    intent = make_intent()
    intent["group_by"] = []

    plan = make_plan(intent)
    query = compile_schema_plan(plan)

    assert "dim_date" in plan["required_tables"]
    assert "GROUP BY" not in query.sql
    assert 2025 in query.params.values()


@pytest.mark.parametrize(
    "invalid_year",
    ["2025", True, 2100],
)
def test_invalid_year_binding_rejected(invalid_year):
    plan = make_plan()
    plan = deepcopy(plan)

    for item in plan["filters"]:
        if item["field_id"] == "dim_date.year":
            item["value"] = invalid_year

    with pytest.raises(SQLCompilationError):
        compile_schema_plan(plan)


def test_unsupported_time_rejected_by_schema_link():
    intent = make_intent()
    intent["time_expression"] = "最近三个月"

    with pytest.raises(
        SchemaLinkError,
        match="暂不支持的时间表达",
    ):
        make_plan(intent)


def test_llm_cannot_invent_year():
    intent = QueryIntent(
        metric="销售额",
        time_expression="2025年",
    )

    with pytest.raises(
        ValueError,
        match="不存在的时间表达",
    ):
        extract_search_terms(
            "查询华南地区的销售额",
            intent,
        )
