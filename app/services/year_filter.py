
import re


YEAR_PATTERN = re.compile(r"(?:19|20)\d{2}年")


def parse_explicit_year(expression: str) -> int:
    """
    将明确年份转换为数据库可使用的整数。

    支持：
        2025年 -> 2025

    暂不支持：
        去年
        最近三个月
        2025年1月
        2025年至2026年
    """

    if not isinstance(expression, str):
        raise ValueError("时间表达必须是字符串")

    expression = expression.strip()

    if not YEAR_PATTERN.fullmatch(expression):
        raise ValueError(
            f"当前仅支持明确年份，例如2025年：{expression}"
        )

    return int(expression[:-1])
