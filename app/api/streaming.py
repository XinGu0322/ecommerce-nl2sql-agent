
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.schemas import QueryData, QueryRequest
from app.api.security import require_query_access
from app.observability import (
    set_request_id,
    reset_request_id,
)


logger = logging.getLogger("nl2sql.stream")

router = APIRouter(
    prefix="/api/v1",
    tags=["NL2SQL Streaming"],
)


def sse_event(event: str, data: dict) -> str:
    """将业务事件编码为 SSE 格式。"""

    payload = json.dumps(
        data,
        ensure_ascii=False,
    )

    return f"event: {event}\ndata: {payload}\n\n"


@router.post(
    "/query/stream",
    dependencies=[Depends(require_query_access)],
    summary="流式执行自然语言问数",
)
async def stream_query(
    payload: QueryRequest,
    request: Request,
):
    """返回 LangGraph 各阶段进度和最终结果。"""

    graph = getattr(
        request.app.state,
        "query_graph",
        None,
    )

    semaphore = getattr(
        request.app.state,
        "query_semaphore",
        None,
    )

    if graph is None or semaphore is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail="查询服务尚未就绪",
        )

    request_id = request.state.request_id

    async def generate():
        # StreamingResponse 在中间件返回后才执行生成器，
        # 因此需要重新绑定当前异步任务的 Request ID。
        token = set_request_id(request_id)

        completed = False

        try:
            yield sse_event(
                "started",
                {
                    "request_id": request_id,
                    "message": "查询开始",
                },
            )

            async with asyncio.timeout(90):
                async with semaphore:

                    async for update in graph.astream(
                        {"question": payload.question},
                        stream_mode="updates",
                    ):
                        for stage, value in update.items():

                            if stage == "retrieval":
                                yield sse_event(
                                    "progress",
                                    {
                                        "stage": "retrieval",
                                        "message": "意图理解和检索完成",
                                    },
                                )

                            elif stage == "schema_link":
                                if value.get("status") == "rejected":
                                    yield sse_event(
                                        "error",
                                        {
                                            "status": "rejected",
                                            "message": "查询条件暂不支持",
                                        },
                                    )
                                    completed = True
                                else:
                                    yield sse_event(
                                        "progress",
                                        {
                                            "stage": "schema_link",
                                            "message": "业务查询计划已生成",
                                        },
                                    )

                            elif stage == "needs_review":
                                yield sse_event(
                                    "error",
                                    {
                                        "status": "needs_review",
                                        "message": "查询条件需要澄清",
                                    },
                                )
                                completed = True

                            elif stage == "execute_query":
                                if value.get("status") == "success":
                                    execution = value["execution"]

                                    data = QueryData(
                                        metric_id=execution["metric_id"],
                                        value=execution.get("value"),
                                        rows=execution.get("rows"),
                                    )

                                    yield sse_event(
                                        "result",
                                        {
                                            "status": "success",
                                            "data": data.model_dump(
                                                mode="json"
                                            ),
                                        },
                                    )
                                else:
                                    yield sse_event(
                                        "error",
                                        {
                                            "status": "execution_failed",
                                            "message": "查询执行失败",
                                        },
                                    )

                                completed = True

            if not completed:
                yield sse_event(
                    "error",
                    {
                        "status": "execution_failed",
                        "message": "查询未返回最终结果",
                    },
                )

        except asyncio.CancelledError:
            # 客户端断开连接时及时取消查询任务
            raise

        except TimeoutError:
            yield sse_event(
                "error",
                {
                    "status": "timeout",
                    "message": "查询处理超时",
                },
            )

        except Exception:
            logger.exception("sse_query_failed")

            yield sse_event(
                "error",
                {
                    "status": "execution_failed",
                    "message": "查询服务内部错误",
                },
            )

        finally:
            reset_request_id(token)

        yield sse_event(
            "done",
            {"request_id": request_id},
        )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
