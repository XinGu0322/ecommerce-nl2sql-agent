
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.sql_compiler import (
    MAX_GROUP_ROWS,
    compile_schema_plan,
)
from app.services.sql_validator import validate_sql


class QueryExecutionError(RuntimeError):
    """查询执行或结果契约不符合要求。"""


@dataclass(frozen=True)
class QueryExecutionResult:
    """
    统一的查询执行结果。

    单指标查询：
        value 有值
        rows 为 None

    分组查询：
        value 为 None
        rows 为结构化结果列表
    """

    metric_id: str
    value: str | int | None
    sql: str
    params: dict[str, str | int]
    rows: list[dict] | None = None


def normalize_numeric(value):
    """保留 Decimal 精度，规范化聚合指标结果。"""

    if isinstance(value, Decimal):
        return str(value)

    if type(value) is int:
        return value

    if value is None:
        return None

    raise QueryExecutionError(
        "不支持的查询结果类型："
        f"{type(value).__name__}"
    )


class QueryExecutor:
    def __init__(
        self,
        engine: AsyncEngine,
        timeout_ms: int = 5000,
    ):
        if not 100 <= timeout_ms <= 60000:
            raise ValueError(
                "查询超时必须在100到60000毫秒之间"
            )

        self.engine = engine
        self.timeout_ms = timeout_ms

    async def execute_plan(
        self,
        schema_plan: dict,
    ) -> QueryExecutionResult:
        """
        执行受控的 Schema Plan。

        支持：
        1. 单指标聚合
        2. 年月分组
        3. 品牌分组
        4. 聚合排序和 Top N

        不允许直接执行未经业务计划校验的 SQL。
        """

        # ==================================================
        # 1. 编译并校验 SQL
        # ==================================================

        # 必须先通过业务计划编译
        compiled = compile_schema_plan(
            schema_plan
        )

        # 再验证 SQL 与 Schema Plan 一致
        validated = validate_sql(
            compiled.sql,
            schema_plan,
        )

        metric_id = schema_plan["metric"]["id"]

        group_fields = (
            schema_plan.get("group_by") or []
        )

        grouped = bool(group_fields)

        # ==================================================
        # 2. 执行只读 SQL
        # ==================================================

        try:
            async with self.engine.connect() as connection:

                # MySQL SELECT 执行时间限制
                await connection.execute(
                    text(
                        "SET SESSION max_execution_time = "
                        ":timeout_ms"
                    ),
                    {
                        "timeout_ms": self.timeout_ms
                    },
                )

                # 使用绑定参数执行已验证 SQL
                result = await connection.execute(
                    text(validated.sql),
                    validated.params,
                )

                # ==========================================
                # 3. 分组查询结果解析
                # ==========================================

                if grouped:

                    # --------------------------------------
                    # 3.1 根据业务计划确定维度列名
                    # --------------------------------------

                    dimension_names = []

                    for field_id in group_fields:
                        if (
                            not isinstance(field_id, str)
                            or field_id.count(".") != 1
                        ):
                            raise QueryExecutionError(
                                "分组字段格式不合法"
                            )

                        column_name = (
                            field_id.split(".")[1]
                        )

                        dimension_names.append(
                            column_name
                        )

                    expected_columns = (
                        dimension_names + [metric_id]
                    )

                    # 避免维度字段与指标字段重名
                    if (
                        len(expected_columns)
                        != len(set(expected_columns))
                    ):
                        raise QueryExecutionError(
                            "查询结果存在重复字段名"
                        )

                    # --------------------------------------
                    # 3.2 校验数据库实际返回的列
                    # --------------------------------------

                    if (
                        list(result.keys())
                        != expected_columns
                    ):
                        raise QueryExecutionError(
                            "分组查询结果列与业务计划不一致"
                        )

                    # --------------------------------------
                    # 3.3 限制返回结果行数
                    # --------------------------------------

                    raw_rows = (
                        result.mappings().fetchmany(
                            MAX_GROUP_ROWS + 1
                        )
                    )

                    if len(raw_rows) > MAX_GROUP_ROWS:
                        raise QueryExecutionError(
                            "分组结果超过允许的最大行数"
                        )

                    # --------------------------------------
                    # 3.4 通用维度结果解析
                    # --------------------------------------

                    rows = []

                    for raw_row in raw_rows:
                        row = {}

                        for name in dimension_names:
                            value = raw_row[name]

                            # 当前支持字符串、整数和空维度值
                            if (
                                type(value) not in (
                                    str,
                                    int,
                                )
                                and value is not None
                            ):
                                raise QueryExecutionError(
                                    "分组字段类型不支持："
                                    f"{name}"
                                )

                            row[name] = value

                        # ----------------------------------
                        # 3.5 保留年月的业务合法性检查
                        # ----------------------------------

                        if group_fields == [
                            "dim_date.year",
                            "dim_date.month",
                        ]:
                            year = row["year"]
                            month = row["month"]

                            if (
                                type(year) is not int
                                or type(month) is not int
                                or not 1 <= month <= 12
                            ):
                                raise QueryExecutionError(
                                    "年月分组结果不合法"
                                )

                        # ----------------------------------
                        # 3.6 聚合指标数值规范化
                        # ----------------------------------

                        row[metric_id] = (
                            normalize_numeric(
                                raw_row[metric_id]
                            )
                        )

                        rows.append(row)

                    # --------------------------------------
                    # 3.7 返回通用分组结果
                    # --------------------------------------

                    return QueryExecutionResult(
                        metric_id=metric_id,
                        value=None,
                        sql=validated.sql,
                        params=dict(
                            validated.params
                        ),
                        rows=rows,
                    )

                # ==========================================
                # 4. 单指标聚合查询
                # ==========================================

                # 非分组查询必须只返回指标列
                if list(result.keys()) != [metric_id]:
                    raise QueryExecutionError(
                        "查询返回的指标列与业务计划不一致"
                    )

                raw_rows = (
                    result.mappings().fetchmany(2)
                )

                if len(raw_rows) != 1:
                    raise QueryExecutionError(
                        "单指标聚合查询必须返回一行"
                    )

                value = normalize_numeric(
                    raw_rows[0][metric_id]
                )

                return QueryExecutionResult(
                    metric_id=metric_id,
                    value=value,
                    sql=validated.sql,
                    params=dict(
                        validated.params
                    ),
                    rows=None,
                )

        # ==================================================
        # 5. 数据库异常统一处理
        # ==================================================

        except SQLAlchemyError as exc:
            raise QueryExecutionError(
                "MySQL 查询执行失败"
            ) from exc
