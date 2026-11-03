
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from decimal import Decimal

import pytest

from app.services.query_executor import QueryExecutor
from app.services.sql_compiler import (
    SQLCompilationError,
    compile_schema_plan,
)
from app.services.sql_validator import validate_sql


@pytest.fixture
def topn_plan():
    """销售额最高的三个品牌对应的业务计划。"""

    return {
        "metric": {
            "id": "total_sales",
            "name": "销售总额",
            "expression": "SUM(fact_order.order_amount)",
            "dependencies": [
                "fact_order.order_amount"
            ],
        },
        "fact_table": "fact_order",
        "required_tables": [
            "fact_order",
            "dim_product",
        ],
        "joins": [
            {
                "left_table": "fact_order",
                "left_column": "product_id",
                "right_table": "dim_product",
                "right_column": "product_id",
            }
        ],
        "filters": [],
        "group_by": ["dim_product.brand"],
        "sort": "desc",
        "limit": 3,
    }


def test_topn_sql_compilation(topn_plan):
    """验证品牌分组、聚合排序和 Top N SQL。"""

    query = compile_schema_plan(topn_plan)

    assert "INNER JOIN `dim_product`" in query.sql
    assert "GROUP BY `dim_product`.`brand`" in query.sql
    assert "ORDER BY `total_sales` DESC" in query.sql
    assert "`dim_product`.`brand` ASC" in query.sql
    assert "LIMIT 3" in query.sql

    assert query.params == {}

    # 生成的 SQL 仍必须通过现有 AST 校验
    validate_sql(query.sql, topn_plan)


@pytest.mark.parametrize(
    "invalid_limit",
    [0, 101, True],
)
def test_invalid_topn_limit_rejected(
    topn_plan,
    invalid_limit,
):
    """非法 Top N 数量不得编译。"""

    plan = deepcopy(topn_plan)
    plan["limit"] = invalid_limit

    with pytest.raises(SQLCompilationError):
        compile_schema_plan(plan)


def test_topn_without_sort_rejected(topn_plan):
    """Top N 必须具有明确的排序方向。"""

    plan = deepcopy(topn_plan)
    plan["sort"] = None

    with pytest.raises(SQLCompilationError):
        compile_schema_plan(plan)


def test_unapproved_group_rejected(topn_plan):
    """未经批准的分组字段不得进入 SQL。"""

    plan = deepcopy(topn_plan)
    plan["group_by"] = ["dim_product.product_name"]

    with pytest.raises(SQLCompilationError):
        compile_schema_plan(plan)


class FakeResult:
    def keys(self):
        return ["brand", "total_sales"]

    def mappings(self):
        return self

    def fetchmany(self, size):
        return [
            {
                "brand": "品牌A",
                "total_sales": Decimal("12000.00"),
            },
            {
                "brand": "品牌B",
                "total_sales": Decimal("9000.00"),
            },
            {
                "brand": "品牌C",
                "total_sales": Decimal("7500.00"),
            },
        ]


class FakeConnection:
    async def execute(self, statement, params=None):
        if str(statement).startswith("SET SESSION"):
            return None

        return FakeResult()


class FakeEngine:
    @asynccontextmanager
    async def connect(self):
        yield FakeConnection()


def test_topn_execution_returns_generic_rows(topn_plan):
    """验证执行器能够返回品牌分组的结构化结果。"""

    executor = QueryExecutor(FakeEngine())

    result = asyncio.run(
        executor.execute_plan(topn_plan)
    )

    assert result.metric_id == "total_sales"
    assert result.value is None
    assert len(result.rows) == 3

    assert result.rows[0] == {
        "brand": "品牌A",
        "total_sales": "12000.00",
    }

    assert result.rows[1]["brand"] == "品牌B"
    assert result.rows[2]["brand"] == "品牌C"
