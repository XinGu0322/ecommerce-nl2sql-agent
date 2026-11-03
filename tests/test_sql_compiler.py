
from copy import deepcopy

import pytest

from app.services.sql_compiler import (
    SQLCompilationError,
    compile_schema_plan,
)
from app.services.sql_validator import (
    SQLValidationError,
    validate_sql,
)


@pytest.fixture
def schema_plan():
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


def test_compile_query(schema_plan):
    query = compile_schema_plan(schema_plan)

    assert "SUM(" in query.sql
    assert "INNER JOIN" in query.sql

    assert query.params == {
        "filter_0": "华北",
        "filter_1": "苹果",
    }

    validate_sql(query.sql, schema_plan)


def test_filter_values_are_parameterized(schema_plan):
    plan = deepcopy(schema_plan)

    dangerous_value = "苹果' OR 1=1 --"
    plan["filters"][1]["value"] = dangerous_value

    query = compile_schema_plan(plan)

    assert dangerous_value not in query.sql
    assert query.params["filter_1"] == dangerous_value


def test_injected_metric_expression_rejected(schema_plan):
    plan = deepcopy(schema_plan)

    plan["metric"]["expression"] = (
        "SUM(fact_order.order_amount); DROP TABLE fact_order"
    )

    with pytest.raises(SQLCompilationError):
        compile_schema_plan(plan)


def test_unplanned_table_rejected(schema_plan):
    plan = deepcopy(schema_plan)

    plan["joins"][0]["right_table"] = "mysql"

    with pytest.raises(SQLCompilationError):
        compile_schema_plan(plan)


def test_delete_statement_rejected(schema_plan):
    with pytest.raises(SQLValidationError):
        validate_sql(
            "DELETE FROM fact_order",
            schema_plan,
        )


def test_extra_filter_rejected(schema_plan):
    query = compile_schema_plan(schema_plan)

    altered_sql = (
        query.sql
        + " AND `fact_order`.`order_amount` > 100"
    )

    with pytest.raises(SQLValidationError):
        validate_sql(
            altered_sql,
            schema_plan,
        )


def test_multiple_statements_rejected(schema_plan):
    query = compile_schema_plan(schema_plan)

    with pytest.raises(SQLValidationError):
        validate_sql(
            query.sql + "; SELECT 1",
            schema_plan,
        )
