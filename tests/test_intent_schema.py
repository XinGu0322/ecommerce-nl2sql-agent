
import pytest

from app.agent.intent_schema import (
    QueryIntent,
    extract_search_terms,
)


def test_valid_intent():
    question = "统计华北地区苹果品牌的销售总额"

    intent = QueryIntent.model_validate({
        "metric": "销售总额",
        "filters": [
            {"text": "华北", "category": "region"},
            {"text": "苹果", "category": "brand"},
        ],
    })

    assert intent.metric == "销售总额"
    assert extract_search_terms(question, intent) == [
        "华北", "苹果"
    ]


def test_hallucinated_filter_is_rejected():
    intent = QueryIntent.model_validate({
        "filters": [
            {"text": "华南", "category": "region"}
        ]
    })

    with pytest.raises(ValueError, match="不存在"):
        extract_search_terms(
            "查询华北地区的销售额",
            intent,
        )


def test_invalid_category_is_rejected():
    with pytest.raises(ValueError):
        QueryIntent.model_validate({
            "filters": [
                {"text": "苹果", "category": "sql_column"}
            ]
        })


def test_duplicate_terms_are_removed():
    intent = QueryIntent.model_validate({
        "filters": [
            {"text": "华北", "category": "region"},
            {"text": "华北", "category": "region"},
        ]
    })

    assert extract_search_terms(
        "查询华北销售额",
        intent,
    ) == ["华北"]
