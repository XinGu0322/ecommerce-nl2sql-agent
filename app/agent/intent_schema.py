
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


class FilterMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    category: Literal[
        "region",
        "province",
        "brand",
        "product",
        "category",
        "member_level",
        "customer",
        "unknown",
    ]

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("筛选词不能为空")
        return value


class QueryIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str | None = None
    filters: list[FilterMention] = Field(default_factory=list)

    time_expression: str | None = None
    group_by: list[str] = Field(default_factory=list)

    sort: Literal["asc", "desc"] | None = None
    limit: int | None = Field(default=None, ge=1, le=100)

    needs_clarification: bool = False



def extract_search_terms(
    question: str,
    intent: QueryIntent,
) -> list[str]:
    """验证模型抽取的实体与时间确实来自用户问题。"""

    terms = []

    for item in intent.filters:
        if item.text not in question:
            raise ValueError(
                f"模型输出了原问题中不存在的筛选词：{item.text}"
            )

        if item.text not in terms:
            terms.append(item.text)

    # 时间表达必须来自用户原文，不能由模型凭空添加
    if (
        intent.time_expression
        and intent.time_expression not in question
    ):
        raise ValueError(
            "模型输出了原问题中不存在的时间表达："
            f"{intent.time_expression}"
        )

    return terms

