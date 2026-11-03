
import re
from dataclasses import dataclass


IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*$"
)

METRIC_PATTERN = re.compile(
    r"^(SUM|COUNT)\("
    r"([A-Za-z_][A-Za-z0-9_]*)\."
    r"([A-Za-z_][A-Za-z0-9_]*)\)$",
    re.IGNORECASE,
)


class SQLCompilationError(ValueError):
    pass


@dataclass(frozen=True)
class BoundQuery:
    sql: str
    params: dict[str, str]


def quote_identifier(name: str) -> str:
    """只允许简单 SQL 标识符。"""

    if (
        not isinstance(name, str)
        or not IDENTIFIER_PATTERN.fullmatch(name)
    ):
        raise SQLCompilationError(
            f"不合法的 SQL 标识符：{name}"
        )

    return f"`{name}`"


def quote_field(field_id: str) -> str:
    """将 table.column 转换为安全的限定字段名。"""

    if (
        not isinstance(field_id, str)
        or field_id.count(".") != 1
    ):
        raise SQLCompilationError(
            f"不合法的字段标识：{field_id}"
        )

    table, column = field_id.split(".")

    return (
        f"{quote_identifier(table)}."
        f"{quote_identifier(column)}"
    )


def compile_schema_plan(plan: dict) -> BoundQuery:
    """将已验证的 Schema Plan 编译为参数化 SELECT。"""

    fact_table = plan["fact_table"]
    metric = plan["metric"]

    fact_sql = quote_identifier(fact_table)
    metric_id = quote_identifier(metric["id"])

    # 1. 严格限定支持的指标表达式
    expression = re.sub(
        r"\s+", "", metric["expression"]
    )

    match = METRIC_PATTERN.fullmatch(expression)

    if not match:
        raise SQLCompilationError(
            "当前仅支持 SUM/COUNT 单字段聚合"
        )

    function, metric_table, metric_column = match.groups()

    metric_field = f"{metric_table}.{metric_column}"

    if metric_table != fact_table:
        raise SQLCompilationError(
            "指标字段不属于当前事实表"
        )

    if metric["dependencies"] != [metric_field]:
        raise SQLCompilationError(
            "指标表达式与字段依赖不一致"
        )

    metric_sql = (
        f"{function.upper()}("
        f"{quote_field(metric_field)})"
    )

    sql_parts = [
        f"SELECT {metric_sql} AS {metric_id}",
        f"FROM {fact_sql}",
    ]

    # 2. 根据 Schema Plan 构建 JOIN
    joins = plan["joins"]
    joined_tables = {fact_table}

    for join in joins:
        left_table = join["left_table"]
        right_table = join["right_table"]

        if left_table != fact_table:
            raise SQLCompilationError(
                "当前仅支持事实表直接连接维度表"
            )

        if right_table in joined_tables:
            raise SQLCompilationError(
                f"重复 JOIN：{right_table}"
            )

        left_field = (
            f"{left_table}.{join['left_column']}"
        )
        right_field = (
            f"{right_table}.{join['right_column']}"
        )

        sql_parts.append(
            f"INNER JOIN {quote_identifier(right_table)} "
            f"ON {quote_field(left_field)} "
            f"= {quote_field(right_field)}"
        )

        joined_tables.add(right_table)

    required_tables = plan["required_tables"]

    if (
        len(required_tables) != len(set(required_tables))
        or joined_tables != set(required_tables)
    ):
        raise SQLCompilationError(
            "JOIN 表与查询计划中的所需表不一致"
        )

    # 3. 构建参数化 WHERE 条件
    conditions = []
    params = {}

    for index, item in enumerate(plan["filters"]):
        field_id = item["field_id"]
        field_sql = quote_field(field_id)

        table_name = field_id.split(".")[0]

        if table_name not in joined_tables:
            raise SQLCompilationError(
                f"筛选字段所属表未参与查询：{field_id}"
            )

        value = item["value"]

        if not isinstance(value, str):
            raise SQLCompilationError(
                "当前阶段只支持字符串等值筛选"
            )

        parameter_name = f"filter_{index}"

        conditions.append(
            f"{field_sql} = :{parameter_name}"
        )

        params[parameter_name] = value

    if conditions:
        sql_parts.append(
            "WHERE " + " AND ".join(conditions)
        )

    return BoundQuery(
        sql="\n".join(sql_parts),
        params=params,
    )
