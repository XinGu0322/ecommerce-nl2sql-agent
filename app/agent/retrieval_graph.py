
from langgraph.graph import END, START, StateGraph

from app.agent.state import QueryState
from app.agent.intent_schema import extract_search_terms
from app.agent.entity_resolution import (
    CATEGORY_FIELDS,
    build_lookup_candidates,
)
from app.clients.llm_client import LLMClient
from app.clients.value_search_client import ValueSearchClient
from app.services.meta_vector_service import MetaVectorService


def build_retrieval_graph(
    vector_service: MetaVectorService,
    value_client: ValueSearchClient,
    llm_client: LLMClient | None = None,
):
    def validate_input(state: QueryState) -> dict:
        question = state.get("question", "").strip()

        if not question or len(question) > 500:
            raise ValueError(
                "问题不能为空，且不能超过500个字符"
            )

        if llm_client is not None:
            return {"question": question}

        terms = state.get("search_terms", [])

        if not isinstance(terms, list) or len(terms) > 10:
            raise ValueError("检索词数量或格式不正确")

        if any(
            not isinstance(term, str) or not term.strip()
            for term in terms
        ):
            raise ValueError("检索词必须是非空字符串")

        return {
            "question": question,
            "search_terms": list(dict.fromkeys(
                term.strip() for term in terms
            )),
        }

    async def analyze_question(state: QueryState) -> dict:
        intent = await llm_client.analyze(
            state["question"]
        )

        terms = extract_search_terms(
            state["question"], intent
        )

        return {
            "intent": intent.model_dump(mode="json"),
            "search_terms": terms,
        }

    async def retrieve_metadata(state: QueryState) -> dict:
        intent = state.get("intent", {})

        # 优先围绕指标检索，降低完整问题的语义干扰
        query = intent.get("metric") or state["question"]

        points = await vector_service.search(
            query, limit=8
        )

        hits = []
        for point in points:
            payload = point.payload or {}

            hits.append({
                "kind": payload.get("kind"),
                "source_id": payload.get("source_id"),
                "text": payload.get("text"),
                "score": float(point.score),
            })

        return {"metadata_hits": hits}

    async def retrieve_values(state: QueryState) -> dict:
        intent = state.get("intent")

        if intent is not None:
            mentions = intent["filters"]
        else:
            # 兼容原有测试输入
            mentions = [
                {"text": term, "category": "unknown"}
                for term in state.get("search_terms", [])
            ]

        results = []

        for mention in mentions:
            original = mention["text"]
            category = mention["category"]

            allowed_fields = CATEGORY_FIELDS.get(category)

            candidates = build_lookup_candidates(
                original, category
            )

            # 按顺序匹配：原始词优先，规范化候选词其次
            for candidate in candidates:
                matches = await value_client.search_exact(
                    value=candidate,
                    field_ids=allowed_fields,
                    limit=20,
                )

                if not matches:
                    continue

                for match in matches:
                    source = match["_source"]

                    results.append({
                        "term": original,
                        "category": category,
                        "lookup": candidate,
                        "field_id": source["field_id"],
                        "value": source["value"],
                    })

                # 找到当前优先级的精确匹配，就不再尝试后缀处理
                break

        return {"value_hits": results}

    def merge_evidence(state: QueryState) -> dict:
        metadata = state.get("metadata_hits", [])
        value_hits = state.get("value_hits", [])
        search_terms = state.get("search_terms", [])

        metrics = [
            item for item in metadata
            if item["kind"] == "metric"
        ]
        columns = [
            item for item in metadata
            if item["kind"] == "column"
        ]
        tables = [
            item for item in metadata
            if item["kind"] == "table"
        ]

        confirmed_filters = []
        resolutions = []
        seen_filters = set()

        for item in value_hits:
            key = (
                item["field_id"],
                item["value"],
            )

            if key not in seen_filters:
                seen_filters.add(key)
                confirmed_filters.append({
                    "field_id": item["field_id"],
                    "value": item["value"],
                })

            resolutions.append(item)

        unresolved = []
        ambiguous = []

        for term in search_terms:
            matches = [
                item for item in resolutions
                if item["term"] == term
            ]

            unique_matches = {
                (item["field_id"], item["value"])
                for item in matches
            }

            if not unique_matches:
                unresolved.append(term)
            elif len(unique_matches) > 1:
                ambiguous.append(term)

        intent = state.get("intent", {})

        return {
            "evidence": {
                "metrics": metrics,
                "columns": columns,
                "tables": tables,
                "confirmed_filters": confirmed_filters,
                "filter_resolutions": resolutions,
                "unresolved_terms": unresolved,
                "ambiguous_terms": ambiguous,
                "requires_review": (
                    not metrics
                    or bool(unresolved)
                    or bool(ambiguous)
                    or intent.get("needs_clarification", False)
                ),
            }
        }

    builder = StateGraph(QueryState)

    builder.add_node("validate_input", validate_input)
    builder.add_node("retrieve_metadata", retrieve_metadata)
    builder.add_node("retrieve_values", retrieve_values)
    builder.add_node("merge_evidence", merge_evidence)

    builder.add_edge(START, "validate_input")

    if llm_client is not None:
        builder.add_node("analyze_question", analyze_question)
        builder.add_edge("validate_input", "analyze_question")
        retrieval_start = "analyze_question"
    else:
        retrieval_start = "validate_input"

    builder.add_edge(retrieval_start, "retrieve_metadata")
    builder.add_edge(retrieval_start, "retrieve_values")

    builder.add_edge(
        ["retrieve_metadata", "retrieve_values"],
        "merge_evidence",
    )

    builder.add_edge("merge_evidence", END)

    return builder.compile()
