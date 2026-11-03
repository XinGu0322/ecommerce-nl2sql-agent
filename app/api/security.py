
import asyncio
import hashlib
import math
import os
import secrets

from collections import deque
from dotenv import load_dotenv
from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from time import monotonic

from app.config import PROJECT_ROOT


# Swagger 会自动识别此 API Key 安全方案
api_key_header = APIKeyHeader(
    name="X-API-Key",
    scheme_name="NL2SQLApiKey",
    description="调用 NL2SQL 查询接口所需的 API Key",
    auto_error=False,
)


def load_service_api_key() -> str:
    """
    加载客户端访问本服务所使用的 API Key。

    在服务启动时校验，防止未配置鉴权凭据
    的服务意外对外提供查询接口。
    """
    load_dotenv(PROJECT_ROOT / ".env")

    api_key = os.getenv("NL2SQL_API_KEY")

    if (
        not api_key
        or len(api_key) < 32
        or api_key != api_key.strip()
    ):
        raise RuntimeError(
            "NL2SQL_API_KEY 未配置或长度不足32字符"
        )

    return api_key


class SlidingWindowRateLimiter:
    """
    基于内存的滑动窗口限流器。

    例如：
        limit=30
        window_seconds=60

    表示同一个客户端标识在过去60秒内
    最多通过30次请求。

    本实现仅适用于单进程服务。
    """

    def __init__(
        self,
        limit: int = 30,
        window_seconds: float = 60,
    ):
        if type(limit) is not int or limit < 1:
            raise ValueError(
                "限流次数必须是正整数"
            )

        if window_seconds <= 0:
            raise ValueError(
                "限流窗口必须大于0"
            )

        self.limit = limit
        self.window_seconds = window_seconds

        # 每个客户端保存其窗口内的请求时间
        self._requests: dict[
            str, deque[float]
        ] = {}

        # 防止并发请求同时修改计数
        self._lock = asyncio.Lock()

    async def consume(
        self,
        client_id: str,
    ) -> int | None:
        """
        消耗一次请求配额。

        返回：
            None：允许请求
            整数：被限流，应等待的秒数
        """

        async with self._lock:
            now = monotonic()

            history = self._requests.setdefault(
                client_id,
                deque(),
            )

            # 删除窗口之外的请求记录
            while (
                history
                and now - history[0]
                >= self.window_seconds
            ):
                history.popleft()

            # 已达到配额上限
            if len(history) >= self.limit:
                retry_after = (
                    self.window_seconds
                    - (now - history[0])
                )

                return max(
                    1,
                    math.ceil(retry_after),
                )

            history.append(now)

            return None


async def require_query_access(
    request: Request,
    supplied_key: str | None = Security(
        api_key_header
    ),
) -> None:
    """
    API Key 鉴权与限流依赖。

    先鉴权，后限流：
    1. 验证请求头中的 API Key
    2. 为已授权请求检查限流配额
    3. 拒绝非法请求进入 LangGraph
    """

    expected_key = getattr(
        request.app.state,
        "api_key",
        None,
    )

    if expected_key is None:
        raise HTTPException(
            status_code=503,
            detail="鉴权服务尚未就绪",
        )

    # 使用常量时间比较，避免普通字符串比较
    if (
        not supplied_key
        or not secrets.compare_digest(
            supplied_key,
            expected_key,
        )
    ):
        raise HTTPException(
            status_code=401,
            detail="API Key 缺失或无效",
        )

    limiter = getattr(
        request.app.state,
        "query_rate_limiter",
        None,
    )

    if limiter is None:
        raise HTTPException(
            status_code=503,
            detail="限流服务尚未就绪",
        )

    # 不将 API Key 原文保存到限流器中
    client_id = hashlib.sha256(
        supplied_key.encode("utf-8")
    ).hexdigest()

    retry_after = await limiter.consume(
        client_id
    )

    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="请求过于频繁，请稍后重试",
            headers={
                "Retry-After": str(retry_after)
            },
        )
