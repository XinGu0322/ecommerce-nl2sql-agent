
import json
import logging
import sys

from contextvars import ContextVar
from datetime import datetime, timezone


# 每个异步请求都有自己独立的 ContextVar 上下文
_request_id = ContextVar(
    "request_id",
    default="-",
)


def set_request_id(value: str):
    """为当前请求绑定追踪编号。"""
    return _request_id.set(value)


def reset_request_id(token):
    """请求结束后恢复上下文。"""
    _request_id.reset(token)


class JsonLogFormatter(logging.Formatter):
    """将应用日志格式化为单行 JSON。"""

    EXTRA_FIELDS = (
        "method",
        "path",
        "status_code",
        "duration_ms",
        "stage",
        "outcome",
        "error_type",
    )

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": datetime.now(
                timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": _request_id.get(),
            "event": record.getMessage(),
        }

        for field in self.EXTRA_FIELDS:
            value = getattr(record, field, None)

            if value is not None:
                data[field] = value

        # 默认只记录异常类型，不在结构化日志中
        # 自动输出 SQL 参数、用户数据或完整异常文本
        if record.exc_info:
            data["exception_type"] = (
                record.exc_info[0].__name__
            )

        return json.dumps(
            data,
            ensure_ascii=False,
        )


def configure_logging() -> None:
    """初始化 NL2SQL 专用日志器，避免重复添加 Handler。"""

    logger = logging.getLogger("nl2sql")

    logger.setLevel(logging.INFO)
    logger.propagate = False

    if any(
        getattr(handler, "_nl2sql_json", False)
        for handler in logger.handlers
    ):
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())

    handler._nl2sql_json = True

    logger.addHandler(handler)
