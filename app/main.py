import asyncio
import logging

from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request

from app.api.routes import router
from app.api.streaming import router as stream_router

from app.api.security import (
    SlidingWindowRateLimiter,
    load_service_api_key,
)
from app.observability import (
    configure_logging,
    reset_request_id,
    set_request_id,
)
from app.runtime import create_query_runtime


MAX_CONCURRENT_QUERIES = 4

RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60


def create_app(
    runtime_factory=create_query_runtime,
) -> FastAPI:
    """
    FastAPI 应用工厂。

    统一管理：
    - 查询运行时生命周期
    - HTTP 请求追踪
    - 结构化访问日志
    - 并发查询控制
    """

    # 1. 初始化应用日志
    configure_logging()

    logger = logging.getLogger("nl2sql.http")

    # 2. 应用生命周期

    # 2. 应用生命周期
    @asynccontextmanager
    async def lifespan(app: FastAPI):

        # 服务启动时先检查鉴权配置
        # 缺少有效 API Key 时直接启动失败
        app.state.api_key = load_service_api_key()

        # 整个服务进程共享一个限流器
        app.state.query_rate_limiter = (
            SlidingWindowRateLimiter(
                limit=RATE_LIMIT_REQUESTS,
                window_seconds=RATE_LIMIT_WINDOW_SECONDS,
            )
        )

        try:
            # 原有的 LangGraph Runtime 保持不变
            async with runtime_factory() as query_graph:

                app.state.query_graph = query_graph

                app.state.query_semaphore = (
                    asyncio.Semaphore(
                        MAX_CONCURRENT_QUERIES
                    )
                )

                try:
                    yield

                finally:
                    app.state.query_graph = None
                    app.state.query_semaphore = None

        finally:
            # 服务退出时清理鉴权与限流状态
            app.state.api_key = None
            app.state.query_rate_limiter = None

    # 3. 创建 FastAPI 应用
    app = FastAPI(
        title="Ecommerce NL2SQL Agent API",
        description="基于 LangGraph 的电商自然语言问数服务",
        version="0.1.0",
        lifespan=lifespan,
    )


    # 3. 创建 FastAPI 应用
    app = FastAPI(
        title="Ecommerce NL2SQL Agent API",
        description="基于 LangGraph 的电商自然语言问数服务",
        version="0.1.0",
        lifespan=lifespan,
    )

    # 4. HTTP 请求追踪中间件
    @app.middleware("http")
    async def request_tracking(
        request: Request,
        call_next,
    ):
        # 每次请求均由服务端生成独立编号
        request_id = uuid4().hex

        request.state.request_id = request_id
        token = set_request_id(request_id)

        started = perf_counter()
        status_code = 500

        try:
            response = await call_next(request)

            status_code = response.status_code

            # 将追踪编号返回给 API 调用方
            response.headers["X-Request-ID"] = request_id

            return response

        except Exception:
            logger.exception(
                "http_unhandled_exception",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            raise

        finally:
            elapsed_ms = round(
                (perf_counter() - started) * 1000,
                2,
            )

            logger.info(
                "http_request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": elapsed_ms,
                },
            )

            # 防止请求上下文残留
            reset_request_id(token)

    # 5. 注册 API 路由
    app.include_router(router)
    app.include_router(stream_router)

    # 6. 存活探针

    # 6. 存活探针
    @app.get(
        "/health/live",
        tags=["Health"],
        summary="服务存活探针",
    )
    async def liveness():
        return {"status": "ok"}

    return app


app = create_app()
