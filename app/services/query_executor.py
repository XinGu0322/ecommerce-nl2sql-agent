
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
    metric_id: str
    value: str | int | None
    sql: str
    params: dict[str, str]

    # 分组查询使用 rows；
    # 普通聚合查询仍使用 value。
    rows: list[dict] | None = None


def normalize_numeric(value):
    """保持 Decimal 精度，统一数值输出。"""

    if isinstance(value, Decimal):
        return str(value)

    if type(value) is int:
        return value

    if value is None:
        return None

    raise QueryExecutionError(
        f"不支持的查询结果类型：{type(value).__name__}"
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

        # 1. 编译和校验必须在数据库连接之前完成
        compiled = compile_schema_plan(schema_plan)

        validated = validate_sql(
            compiled.sql,
            schema_plan,
        )

        metric_id = schema_plan["metric"]["id"]
        grouped = bool(schema_plan.get("group_by"))

        try:
            async with self.engine.connect() as connection:

                # 2. 限制 SELECT 执行时间
                await connection.execute(
                    text(
                        "SET SESSION max_execution_time = "
                        ":timeout_ms"
                    ),
                    {"timeout_ms": self.timeout_ms},
                )

                # 3. 参数化执行
                result = await connection.execute(
                    text(validated.sql),
                    validated.params,
                )

                # 4. 分组聚合查询
                if grouped:
                    expected_columns = [
                        "year",
                        "month",
                        metric_id,
                    ]

                    if list(result.keys()) != expected_columns:
                        raise QueryExecutionError(
                            "分组查询结果列不符合预期"
                        )

                    raw_rows = result.mappings().fetchmany(
                        MAX_GROUP_ROWS + 1
                    )

                    if len(raw_rows) > MAX_GROUP_ROWS:
                        raise QueryExecutionError(
                            "分组结果超过允许的最大行数"
                        )

                    rows = []

                    for row in raw_rows:
                        year = row["year"]
                        month = row["month"]

                        if (
                            type(year) is not int
                            or type(month) is not int
                            or not 1 <= month <= 12
                        ):
                            raise QueryExecutionError(
                                "日期分组结果不合法"
                            )

                        rows.append({
                            "year": year,
                            "month": month,
                            metric_id: normalize_numeric(
                                row[metric_id]
                            ),
                        })

                    return QueryExecutionResult(
                        metric_id=metric_id,
                        value=None,
                        sql=validated.sql,
                        params=dict(validated.params),
                        rows=rows,
                    )

                # 5. 保留原来的单值聚合行为
                if list(result.keys()) != [metric_id]:
                    raise QueryExecutionError(
                        "查询返回的指标列与业务计划不一致"
                    )

                raw_rows = result.mappings().fetchmany(2)

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
                    params=dict(validated.params),
                )

        except SQLAlchemyError as exc:
            raise QueryExecutionError(
                "MySQL 查询执行失败"
            ) from exc
