
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.sql_compiler import compile_schema_plan
from app.services.sql_validator import validate_sql


class QueryExecutionError(RuntimeError):
    """数据库查询失败或返回结果不符合当前查询契约。"""


@dataclass(frozen=True)
class QueryExecutionResult:
    metric_id: str
    value: str | int | None
    sql: str
    params: dict[str, str]


class QueryExecutor:
    def __init__(
        self,
        engine: AsyncEngine,
        timeout_ms: int = 5000,
    ):
        if not 100 <= timeout_ms <= 60000:
            raise ValueError("查询超时必须在100到60000毫秒之间")

        self.engine = engine
        self.timeout_ms = timeout_ms

    async def execute_plan(
        self,
        schema_plan: dict,
    ) -> QueryExecutionResult:
        """
        执行经过 Schema Linking 的单指标聚合查询。

        不接收用户或 LLM 直接传入的原始 SQL。
        """

        # 1. 根据业务计划编译 SQL
        compiled = compile_schema_plan(schema_plan)

        # 2. 重新进行 AST 与业务计划一致性校验
        validated = validate_sql(
            compiled.sql,
            schema_plan,
        )

        metric_id = schema_plan["metric"]["id"]

        # 3. 使用独立只读连接执行
        try:
            async with self.engine.connect() as connection:

                # 设置当前连接的 SELECT 执行时间限制
                await connection.execute(
                    text(
                        "SET SESSION max_execution_time = "
                        ":timeout_ms"
                    ),
                    {"timeout_ms": self.timeout_ms},
                )

                result = await connection.execute(
                    text(validated.sql),
                    validated.params,
                )

                # 当前业务仅支持单指标、无 GROUP BY
                if list(result.keys()) != [metric_id]:
                    raise QueryExecutionError(
                        "查询返回的指标列与业务计划不一致"
                    )

                # 最多读取两行，用于检查当前结果契约
                rows = result.mappings().fetchmany(2)

                if len(rows) != 1:
                    raise QueryExecutionError(
                        "单指标聚合查询必须返回一行"
                    )

                raw_value = rows[0][metric_id]

                if isinstance(raw_value, Decimal):
                    # 金额不能随意转成 float
                    value = str(raw_value)
                elif isinstance(raw_value, int):
                    value = raw_value
                elif raw_value is None:
                    # SUM 在没有匹配记录时可能返回 NULL
                    value = None
                else:
                    raise QueryExecutionError(
                        "查询结果不是预期的数值类型"
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
