
import re
from dataclasses import dataclass

from app.services.grouping_contract import GROUP_FIELDS


MAX_GROUP_ROWS = 100

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
    """Schema Plan 无法安全编译为 SQL。"""


@dataclass(frozen=True)
class BoundQuery:
    sql: str
    params: dict[str, str | int]


def quote_identifier(name: str) -> str:
    """限制 SQL 标识符格式并添加反引号。"""

    if (
        not isinstance(name, str)
        or not IDENTIFIER_PATTERN.fullmatch(name)
    ):
        raise SQLCompilationError(
            f"不合法的 SQL 标识符：{name}"
        )

    return f"`{name}`"


def quote_field(field_id: str) -> str:
    """将 table.column 转换为安全限定字段名。"""

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
    """
    将已确认的 Schema Plan 编译为参数化 SELECT。

    支持：
    - SUM / COUNT 单指标聚合
    - 事实表到维度表的直接 JOIN
    - 单值等值筛选
    - 同字段多值 IN 筛选
    - 年月、品牌、地区分组
    - 聚合指标排序与 Top N
    """

    # 1. 确定事实表和聚合指标
    fact_table = plan["fact_table"]
    metric = plan["metric"]

    fact_sql = quote_identifier(fact_table)
    metric_id = quote_identifier(metric["id"])

    expression = re.sub(
        r"\s+", "", metric["expression"]
    )

    match = METRIC_PATTERN.fullmatch(expression)

    if not match:
        raise SQLCompilationError(
            "当前仅支持 SUM/COUNT 单字段聚合"
        )

    function, metric_table, metric_column = match.groups()

    metric_field = (
        f"{metric_table}.{metric_column}"
    )

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

    # 2. 构建基础 SELECT / FROM
    sql_parts = [
        f"SELECT {metric_sql} AS {metric_id}",
        f"FROM {fact_sql}",
    ]

    # 3. 构建 JOIN
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

    # 4. 验证 JOIN 覆盖了所需表
    required_tables = plan["required_tables"]

    if (
        len(required_tables) != len(set(required_tables))
        or joined_tables != set(required_tables)
    ):
        raise SQLCompilationError(
            "JOIN 表与查询计划中的所需表不一致"
        )

    # 5. 解析并校验分组、排序和数量限制
    group_by = plan.get("group_by") or []
    sort = plan.get("sort")
    limit = plan.get("limit")

    if (
        not isinstance(group_by, list)
        or any(
            not isinstance(field_id, str)
            for field_id in group_by
        )
    ):
        raise SQLCompilationError(
            "分组字段格式不正确"
        )

    allowed_groups = set(
        GROUP_FIELDS.values()
    )

    if group_by and tuple(group_by) not in allowed_groups:
        raise SQLCompilationError(
            "不支持的分组字段组合"
        )

    if sort not in (None, "asc", "desc"):
        raise SQLCompilationError(
            "不合法的排序方向"
        )

    if limit is not None:
        if (
            type(limit) is not int
            or not 1 <= limit <= MAX_GROUP_ROWS
        ):
            raise SQLCompilationError(
                "Top N 数量超出允许范围"
            )

        if sort is None:
            raise SQLCompilationError(
                "Top N 缺少明确排序方向"
            )

    if (
        (sort is not None or limit is not None)
        and not group_by
    ):
        raise SQLCompilationError(
            "非分组查询不允许排序或 Top N"
        )

    # 6. 构建参数化 WHERE 条件
    conditions = []
    params: dict[str, str | int] = {}
    seen_filter_fields = set()

    for item in plan["filters"]:
        field_id = item["field_id"]
        field_sql = quote_field(field_id)

        table_name = field_id.split(".")[0]

        if table_name not in joined_tables:
            raise SQLCompilationError(
                f"筛选字段所属表未参与查询：{field_id}"
            )

        if field_id in seen_filter_fields:
            raise SQLCompilationError(
                f"重复的筛选字段：{field_id}"
            )

        seen_filter_fields.add(field_id)

        has_value = "value" in item
        has_values = "values" in item

        if has_value == has_values:
            raise SQLCompilationError(
                "筛选条件必须且只能包含 value 或 values"
            )


        # 6.1 单值等值筛选
        if has_value:
            value = item["value"]

            if field_id == "dim_date.year":
                # 年份只能是整数，不能是字符串或布尔值
                if (
                    type(value) is not int
                    or not 1900 <= value <= 2099
                ):
                    raise SQLCompilationError(
                        "年份筛选必须是1900至2099之间的整数"
                    )

            elif not isinstance(value, str):
                raise SQLCompilationError(
                    "普通单值筛选只支持字符串"
                )

            param_name = f"filter_{len(params)}"

            conditions.append(
                f"{field_sql} = :{param_name}"
            )

            params[param_name] = value

        # 6.2 同字段多值 IN 筛选
        else:
            values = item["values"]

            # 此处继续保留你现有的多值筛选代码


            if (
                not isinstance(values, list)
                or not 2 <= len(values) <= 10
                or any(
                    not isinstance(value, str)
                    or not value
                    for value in values
                )
                or len(set(values)) != len(values)
            ):
                raise SQLCompilationError(
                    "多值筛选必须包含2到10个"
                    "不重复的字符串"
                )

            # 对比场景必须同时按该字段分组
            if field_id not in group_by:
                raise SQLCompilationError(
                    "多值筛选缺少对应分组字段"
                )

            placeholders = []

            for value in values:
                param_name = f"filter_{len(params)}"

                placeholders.append(
                    f":{param_name}"
                )

                params[param_name] = value

            conditions.append(
                f"{field_sql} IN "
                f"({', '.join(placeholders)})"
            )

    if conditions:
        sql_parts.append(
            "WHERE " + " AND ".join(conditions)
        )

    # 7. 构建分组与排序
    if group_by:
        dimension_selects = []
        dimension_names = []

        for field_id in group_by:
            table_name, column_name = field_id.split(".")

            if table_name not in joined_tables:
                raise SQLCompilationError(
                    f"分组字段所属表未参与 JOIN：{field_id}"
                )

            dimension_names.append(column_name)

            dimension_selects.append(
                f"{quote_field(field_id)} "
                f"AS {quote_identifier(column_name)}"
            )

        output_names = dimension_names + [metric["id"]]

        if len(output_names) != len(set(output_names)):
            raise SQLCompilationError(
                "分组结果存在重复的输出字段名"
            )

        # 7.1 SELECT 返回维度列和聚合指标
        sql_parts[0] = (
            "SELECT "
            + ", ".join(dimension_selects)
            + f", {metric_sql} AS {metric_id}"
        )

        # 7.2 GROUP BY
        group_fields_sql = [
            quote_field(field_id)
            for field_id in group_by
        ]

        sql_parts.append(
            "GROUP BY " + ", ".join(group_fields_sql)
        )

        # 7.3 ORDER BY
        dimension_order = ", ".join(
            f"{quote_field(field_id)} ASC"
            for field_id in group_by
        )

        if sort is None:
            order_sql = dimension_order
        else:
            order_sql = (
                f"{metric_id} {sort.upper()}, "
                + dimension_order
            )

        sql_parts.append(
            f"ORDER BY {order_sql}"
        )

        # 7.4 LIMIT
        if limit is None:
            # 多读取一行，用于检测是否超过100行
            effective_limit = MAX_GROUP_ROWS + 1
        else:
            effective_limit = limit

        sql_parts.append(
            f"LIMIT {effective_limit}"
        )

    # 8. 返回 SQL 和绑定参数
    return BoundQuery(
        sql="\n".join(sql_parts),
        params=params,
    )
