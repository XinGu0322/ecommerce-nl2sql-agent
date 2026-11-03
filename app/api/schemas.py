
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


QueryStatus = Literal[
    "success",
    "needs_review",
    "rejected",
    "execution_failed",
]


class QueryRequest(BaseModel):
    """自然语言问数请求。"""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        min_length=1,
        max_length=500,
        description="用户自然语言查询问题",
        examples=[
            "查询销售额最高的三个品牌"
        ],
    )

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("查询问题不能为空")

        return value


class QueryData(BaseModel):
    """查询成功后返回的结构化业务数据。"""

    metric_id: str

    # 单指标聚合返回 value
    value: str | int | None = None

    # 分组查询返回 rows
    rows: list[
        dict[str, str | int | None]
    ] | None = None


class QueryResponse(BaseModel):
    """统一 API 响应模型。"""

    status: QueryStatus

    data: QueryData | None = None

    error: str | None = None
