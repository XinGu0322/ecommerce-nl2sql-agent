
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
    params: dict[str, str]


def quote_identifier(name: str) -> str:
    """安全引用 MySQL 标识符。"""

    if (
        not isinstance(name, str)
        or not IDENTIFIER_PATTERN.fullmatch(name)
    ):
        raise SQLCompilationError(
            f"不合法的 SQL 标识符：{name}"
        )

    return f"`{name}`"


def quote_field(field_id: str) -> str:
    """
    将 table.column 转换为：

    `table`.`column`
    """

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
    将受控 Schema Plan 编译为参数化 MySQL SELECT。

    当前支持：
    1. SUM / COUNT 单字段指标
    2. 事实表与维度表直接 JOIN
    3. 字符串等值筛选
    4. 年月分组
    5. 品牌分组
    6. 聚合指标排序
    7. Top N
    """

    # ==================================================
    # 1. 读取基础业务计划
    # ==================================================

    fact_table = plan["fact_table"]
    metric = plan["metric"]

    fact_sql = quote_identifier(fact_table)
    metric_id = quote_identifier(metric["id"])

    # ==================================================
    # 2. 严格限定支持的指标表达式
    # ==================================================

    expression = re.sub(
        r"\s+",
        "",
        metric["expression"],
    )

    match = METRIC_PATTERN.fullmatch(expression)

    if not match:
        raise SQLCompilationError(
            "当前仅支持 SUM/COUNT 单字段聚合"
        )

    (
        function,
        metric_table,
        metric_column,
    ) = match.groups()

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

    # ==================================================
    # 3. 构建基础 SELECT 和 FROM
    # ==================================================

    sql_parts = [
        f"SELECT {metric_sql} AS {metric_id}",
        f"FROM {fact_sql}",
    ]

    # ==================================================
    # 4. 根据 Schema Plan 构建 JOIN
    # ==================================================

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
            f"{left_table}."
            f"{join['left_column']}"
        )

        right_field = (
            f"{right_table}."
            f"{join['right_column']}"
        )

        sql_parts.append(
            f"INNER JOIN "
            f"{quote_identifier(right_table)} "
            f"ON {quote_field(left_field)} "
            f"= {quote_field(right_field)}"
        )

        joined_tables.add(right_table)

    # ==================================================
    # 5. 检查 JOIN 是否与业务计划一致
    # ==================================================

    required_tables = plan["required_tables"]

    if (
        len(required_tables) != len(set(required_tables))
        or joined_tables != set(required_tables)
    ):
        raise SQLCompilationError(
            "JOIN 表与查询计划中的所需表不一致"
        )

    # ==================================================
    # 6. 构建参数化 WHERE 条件
    # ==================================================

    conditions = []
    params = {}

    for index, item in enumerate(plan["filters"]):
        field_id = item["field_id"]
        field_sql = quote_field(field_id)

        table_name = field_id.split(".")[0]

        if table_name not in joined_tables:
            raise SQLCompilationError(
                "筛选字段所属表未参与查询："
                f"{field_id}"
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

    # ==================================================
    # 7. 读取分组、排序和 Top N 计划
    # ==================================================

    group_by = plan.get("group_by") or []
    sort = plan.get("sort")
    limit = plan.get("limit")

    if not isinstance(group_by, list):
        raise SQLCompilationError(
            "分组字段必须为列表"
        )

    # ==================================================
    # 8. 分组聚合查询
    # ==================================================

    if group_by:
        # 只能使用预先定义的合法分组字段组合
        allowed_groups = set(
            GROUP_FIELDS.values()
        )

        if tuple(group_by) not in allowed_groups:
            raise SQLCompilationError(
                "不支持的分组字段组合"
            )

        # ----------------------------------------------
        # 8.1 校验分组字段是否参与 JOIN
        # ----------------------------------------------

        for field_id in group_by:
            table_name = field_id.split(".")[0]

            if table_name not in joined_tables:
                raise SQLCompilationError(
                    "分组字段所属表未参与 JOIN："
                    f"{field_id}"
                )

        # ----------------------------------------------
        # 8.2 构建通用 SELECT 维度字段
        # ----------------------------------------------

        dimension_selects = []
        dimension_names = []

        for field_id in group_by:
            column_name = field_id.split(".")[1]

            dimension_names.append(column_name)

            dimension_selects.append(
                f"{quote_field(field_id)} "
                f"AS {quote_identifier(column_name)}"
            )

        # 防止分组字段名和指标别名冲突
        result_columns = (
            dimension_names + [metric["id"]]
        )

        if len(result_columns) != len(set(result_columns)):
            raise SQLCompilationError(
                "分组结果存在重复的输出字段名"
            )

        sql_parts[0] = (
            "SELECT "
            + ", ".join(dimension_selects)
            + f", {metric_sql} AS {metric_id}"
        )

        # ----------------------------------------------
        # 8.3 GROUP BY
        # ----------------------------------------------

        group_fields_sql = [
            quote_field(field_id)
            for field_id in group_by
        ]

        sql_parts.append(
            "GROUP BY "
            + ", ".join(group_fields_sql)
        )

        # ----------------------------------------------
        # 8.4 ORDER BY
        # ----------------------------------------------

        dimension_order = ", ".join(
            f"{quote_field(field_id)} ASC"
            for field_id in group_by
        )

        if sort is None:
            # 未指定排名时，按分组维度自然升序
            order_sql = dimension_order

        elif sort in ("asc", "desc"):
            # 按聚合指标排序；
            # 使用维度字段作为同分时的稳定排序依据
            order_sql = (
                f"{metric_id} {sort.upper()}, "
                + dimension_order
            )

        else:
            raise SQLCompilationError(
                "不合法的排序方向"
            )

        sql_parts.append(
            f"ORDER BY {order_sql}"
        )

        # ----------------------------------------------
        # 8.5 LIMIT
        # ----------------------------------------------

        if limit is None:
            # 普通分组查询最多允许返回100行
            # 多取1行用于判断是否超出结果限制
            effective_limit = MAX_GROUP_ROWS + 1

        else:
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

            effective_limit = limit

        sql_parts.append(
            f"LIMIT {effective_limit}"
        )

    # ==================================================
    # 9. 非分组查询不得携带排序或 Top N
    # ==================================================

    elif sort is not None or limit is not None:
        raise SQLCompilationError(
            "非分组查询不允许排序或 Top N"
        )

    # ==================================================
    # 10. 返回绑定参数与 SQL
    # ==================================================

    return BoundQuery(
        sql="\n".join(sql_parts),
        params=params,
    )
