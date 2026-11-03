
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from decimal import Decimal

import pytest

from app.services.query_executor import (
    QueryExecutionError,
    QueryExecutor,
)
from app.services.sql_compiler import SQLCompilationError


@pytest.fixture
def schema_plan():
    return {
        "metric": {
            "id": "total_sales",
            "name": "销售总额",
            "expression": "SUM(fact_order.order_amount)",
            "dependencies": ["fact_order.order_amount"],
        },
        "fact_table": "fact_order",
        "required_tables": [
            "fact_order",
            "dim_product",
            "dim_region",
        ],
        "joins": [
            {
                "left_table": "fact_order",
                "left_column": "product_id",
                "right_table": "dim_product",
                "right_column": "product_id",
            },
            {
                "left_table": "fact_order",
                "left_column": "region_id",
                "right_table": "dim_region",
                "right_column": "region_id",
            },
        ],
        "filters": [
            {
                "field_id": "dim_region.region_name",
                "value": "华北",
            },
            {
                "field_id": "dim_product.brand",
                "value": "苹果",
            },
        ],
    }


class FakeResult:
    def __init__(self, value, metric_id="total_sales"):
        self.value = value
        self.metric_id = metric_id

    def keys(self):
        return [self.metric_id]

    def mappings(self):
        return self

    def fetchmany(self, size):
        return [{self.metric_id: self.value}]


class FakeConnection:
    def __init__(self, result):
        self.result = result
        self.commands = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.commands.append((sql, params))

        if sql.startswith("SET SESSION"):
            return None

        return self.result


class FakeEngine:
    def __init__(self, result):
        self.connection = FakeConnection(result)
        self.connect_count = 0

    @asynccontextmanager
    async def connect(self):
        self.connect_count += 1
        yield self.connection


def test_execute_success(schema_plan):
    engine = FakeEngine(
        FakeResult(Decimal("8999.00"))
    )
    executor = QueryExecutor(engine)

    result = asyncio.run(
        executor.execute_plan(schema_plan)
    )

    assert result.metric_id == "total_sales"
    assert result.value == "8999.00"

    # 先设置超时，再执行 SELECT
    assert len(engine.connection.commands) == 2
    assert engine.connection.commands[0][1] == {
        "timeout_ms": 5000
    }

    sql, params = engine.connection.commands[1]

    assert sql.startswith("SELECT")
    assert params == {
        "filter_0": "华北",
        "filter_1": "苹果",
    }

    # 真实取值没有被直接拼入 SQL
    assert "华北" not in sql
    assert "苹果" not in sql


def test_no_matching_sales_returns_null(schema_plan):
    engine = FakeEngine(FakeResult(None))
    executor = QueryExecutor(engine)

    result = asyncio.run(
        executor.execute_plan(schema_plan)
    )

    # SUM 没有匹配数据时可能返回 NULL
    assert result.value is None


def test_invalid_plan_never_connects(schema_plan):
    plan = deepcopy(schema_plan)
    plan["metric"]["expression"] = (
        "AVG(fact_order.order_amount)"
    )

    engine = FakeEngine(FakeResult(Decimal("100.00")))
    executor = QueryExecutor(engine)

    with pytest.raises(SQLCompilationError):
        asyncio.run(executor.execute_plan(plan))

    assert engine.connect_count == 0


def test_unexpected_result_column_rejected(schema_plan):
    engine = FakeEngine(
        FakeResult(
            Decimal("8999.00"),
            metric_id="unexpected_metric",
        )
    )

    executor = QueryExecutor(engine)

    with pytest.raises(QueryExecutionError):
        asyncio.run(
            executor.execute_plan(schema_plan)
        )


class FakeGroupedResult:
    def keys(self):
        return ["year", "month", "total_sales"]

    def mappings(self):
        return self

    def fetchmany(self, size):
        return [
            {
                "year": 2025,
                "month": 1,
                "total_sales": Decimal("1000.00"),
            },
            {
                "year": 2025,
                "month": 2,
                "total_sales": Decimal("2000.00"),
            },
        ]


def test_grouped_query_returns_rows(schema_plan):
    plan = deepcopy(schema_plan)

    plan["group_by"] = [
        "dim_date.year",
        "dim_date.month",
    ]

    plan["required_tables"].append("dim_date")

    plan["joins"].append({
        "left_table": "fact_order",
        "left_column": "date_id",
        "right_table": "dim_date",
        "right_column": "date_id",
    })

    engine = FakeEngine(FakeGroupedResult())
    executor = QueryExecutor(engine)

    result = asyncio.run(
        executor.execute_plan(plan)
    )

    assert result.value is None

    assert result.rows == [
        {
            "year": 2025,
            "month": 1,
            "total_sales": "1000.00",
        },
        {
            "year": 2025,
            "month": 2,
            "total_sales": "2000.00",
        },
    ]
