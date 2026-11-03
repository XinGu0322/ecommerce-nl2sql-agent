
import asyncio
import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from fastapi.responses import JSONResponse

from app.api.schemas import (
    QueryData,
    QueryRequest,
    QueryResponse,
)
from app.api.security import require_query_access


logger = logging.getLogger("nl2sql.api")

router = APIRouter(
    prefix="/api/v1",
    tags=["NL2SQL Query"],
)

REQUEST_TIMEOUT_SECONDS = 90


@router.post(
    "/query",
    response_model=QueryResponse,
    dependencies=[
        Depends(require_query_access)
    ],
    summary="执行自然语言问数",
)
async def query(
    payload: QueryRequest,
    request: Request,
):
    """
    接收自然语言问题，调用 LangGraph 查询工作流。

    返回：
    - 200: 查询成功
    - 422: 业务条件需要澄清或被拒绝
    - 502: 查询执行失败
    - 503: 服务尚未就绪
    - 504: 请求处理超时
    - 500: 未预期的服务错误
    """

    # ==========================================
    # 1. 获取应用启动时构建的查询图
    # ==========================================

    graph = getattr(
        request.app.state,
        "query_graph",
        None,
    )

    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="查询服务尚未就绪",
        )

    # ==========================================
    # 2. 执行 LangGraph
    # ==========================================

    try:
        # 包含等待并发名额和查询处理的总时限
        async with asyncio.timeout(
            REQUEST_TIMEOUT_SECONDS
        ):
            async with request.app.state.query_semaphore:
                result = await graph.ainvoke({
                    "question": payload.question,
                })

    except TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="查询处理超时",
        ) from exc

    except Exception as exc:
        logger.exception(
            "NL2SQL 查询处理出现未预期异常"
        )

        raise HTTPException(
            status_code=500,
            detail="查询服务内部错误",
        ) from exc

    # ==========================================
    # 3. 成功查询
    # ==========================================

    status = result.get("status")

    if status == "success":
        execution = result["execution"]

        return QueryResponse(
            status="success",
            data=QueryData(
                metric_id=execution["metric_id"],
                value=execution.get("value"),
                rows=execution.get("rows"),
            ),
            error=None,
        )

    # ==========================================
    # 4. 需要澄清 / 业务拒绝
    # ==========================================

    if status in ("needs_review", "rejected"):
        response = QueryResponse(
            status=status,
            data=None,
            error=result.get("error")
            or "当前查询无法完成",
        )

        return JSONResponse(
            status_code=422,
            content=response.model_dump(
                mode="json"
            ),
        )

    # ==========================================
    # 5. SQL 或数据库执行失败
    # ==========================================

    if status == "execution_failed":
        response = QueryResponse(
            status="execution_failed",
            data=None,
            error="查询执行失败",
        )

        return JSONResponse(
            status_code=502,
            content=response.model_dump(
                mode="json"
            ),
        )

    # ==========================================
    # 6. 未知 Agent 状态
    # ==========================================

    logger.error(
        "NL2SQL 返回未知执行状态：%r",
        status,
    )

    raise HTTPException(
        status_code=500,
        detail="查询工作流返回未知状态",
    )
