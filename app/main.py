
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.runtime import create_query_runtime


MAX_CONCURRENT_QUERIES = 4


def create_app(
    runtime_factory=create_query_runtime,
) -> FastAPI:
    """
    FastAPI 应用工厂。

    runtime_factory 可替换，使接口测试能够使用
    FakeGraph，而不依赖真实 MySQL / Qdrant / LLM。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):

        # 服务启动时构建一次查询工作流
        async with runtime_factory() as query_graph:

            app.state.query_graph = query_graph

            # 限制当前服务进程的并发查询数
            app.state.query_semaphore = asyncio.Semaphore(
                MAX_CONCURRENT_QUERIES
            )

            try:
                yield

            finally:
                app.state.query_graph = None
                app.state.query_semaphore = None

        # 退出时由 runtime 自动释放连接池和客户端

    app = FastAPI(
        title="Ecommerce NL2SQL Agent API",
        description="基于 LangGraph 的电商自然语言问数服务",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(router)

    @app.get(
        "/health/live",
        tags=["Health"],
        summary="服务存活探针",
    )
    async def liveness():
        return {"status": "ok"}

    return app


app = create_app()
