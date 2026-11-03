
from typing import Any, TypedDict


class QueryState(TypedDict, total=False):
    question: str
    intent: dict[str, Any]
    search_terms: list[str]

    metadata_hits: list[dict[str, Any]]
    value_hits: list[dict[str, Any]]
    evidence: dict[str, Any]

    schema_plan: dict[str, Any]
    execution: dict[str, Any]

    status: str
    error: str | None
