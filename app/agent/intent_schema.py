
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
    """校验抽取词确实来自原问题，并去重。"""

    terms = []

    for item in intent.filters:
        if item.text not in question:
            raise ValueError(
                f"模型输出了原问题中不存在的筛选词：{item.text}"
            )

        if item.text not in terms:
            terms.append(item.text)

    return terms
