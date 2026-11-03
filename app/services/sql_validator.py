
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.services.sql_compiler import (
    BoundQuery,
    compile_schema_plan,
)


class SQLValidationError(ValueError):
    pass


def validate_sql(
    candidate_sql: str,
    schema_plan: dict,
) -> BoundQuery:
    """
    当前阶段的严格校验：
    候选 SQL 必须与受控 Schema Plan
    编译出的 SQL 具有相同 AST。
    """

    if not isinstance(candidate_sql, str):
        raise SQLValidationError("SQL 必须是字符串")

    # 当前只接受单条、无注释的 SELECT
    if any(
        token in candidate_sql
        for token in (";", "--", "/*", "*/", "#")
    ):
        raise SQLValidationError(
            "不允许分号、SQL 注释或多语句"
        )

    expected = compile_schema_plan(schema_plan)

    try:
        statements = sqlglot.parse(
            candidate_sql, read="mysql"
        )

        expected_statements = sqlglot.parse(
            expected.sql, read="mysql"
        )

    except ParseError as exc:
        raise SQLValidationError(
            f"SQL 解析失败：{exc}"
        ) from exc

    if (
        len(statements) != 1
        or not isinstance(statements[0], exp.Select)
    ):
        raise SQLValidationError(
            "仅允许单条 SELECT 查询"
        )

    if len(expected_statements) != 1:
        raise SQLValidationError(
            "基准 SQL 解析异常"
        )

    # 比较解析后的结构，而不是字符串格式
    if statements[0] != expected_statements[0]:
        raise SQLValidationError(
            "SQL 结构与已验证的 Schema Plan 不一致"
        )

    # 返回受控 SQL 和绑定参数，不直接信任原始输入
    return expected
