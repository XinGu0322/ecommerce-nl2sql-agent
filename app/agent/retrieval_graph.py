
from langgraph.graph import END, START, StateGraph

from app.agent.state import QueryState
from app.clients.value_search_client import ValueSearchClient
from app.services.meta_vector_service import MetaVectorService


def build_retrieval_graph(
    vector_service: MetaVectorService,
    value_client: ValueSearchClient,
):
    def validate_input(state: QueryState) -> dict:
        question = state.get("question", "").strip()

        if not question or len(question) > 500:
            raise ValueError("问题不能为空，且不能超过500个字符")

        terms = state.get("search_terms", [])

        if not isinstance(terms, list) or len(terms) > 10:
            raise ValueError("检索词格式不正确或数量超过限制")

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

    async def retrieve_metadata(state: QueryState) -> dict:
        points = await vector_service.search(
            state["question"],
            limit=8,
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
        hits = []

        for term in state.get("search_terms", []):
            matches = await value_client.search(term, limit=5)

            for match in matches:
                source = match["_source"]

                hits.append({
                    "term": term,
                    "field_id": source["field_id"],
                    "value": source["value"],
                    "score": float(match["_score"]),
                })

        return {"value_hits": hits}

    def merge_evidence(state: QueryState) -> dict:
        metadata = state.get("metadata_hits", [])
        values = state.get("value_hits", [])

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

        # 只把与检索词完全一致的真实取值视为已确认。
        # 模糊命中仍保留在 value_hits 中供下一步分析。
        confirmed = []
        seen = set()

        for item in values:
            if item["term"] != item["value"]:
                continue

            key = (item["field_id"], item["value"])
            if key not in seen:
                seen.add(key)
                confirmed.append({
                    "field_id": item["field_id"],
                    "value": item["value"],
                })

        unresolved = [
            term for term in state.get("search_terms", [])
            if not any(
                item["value"] == term for item in confirmed
            )
        ]

        ambiguous = [
            term for term in state.get("search_terms", [])
            if sum(
                item["value"] == term for item in confirmed
            ) > 1
        ]

        return {
            "evidence": {
                "metrics": metrics,
                "columns": columns,
                "tables": tables,
                "confirmed_filters": confirmed,
                "unresolved_terms": unresolved,
                "ambiguous_terms": ambiguous,
                "requires_review": (
                    not metrics
                    or bool(unresolved)
                    or bool(ambiguous)
                ),
            }
        }

    builder = StateGraph(QueryState)

    builder.add_node("validate_input", validate_input)
    builder.add_node("retrieve_metadata", retrieve_metadata)
    builder.add_node("retrieve_values", retrieve_values)
    builder.add_node("merge_evidence", merge_evidence)

    builder.add_edge(START, "validate_input")

    builder.add_edge("validate_input", "retrieve_metadata")
    builder.add_edge("validate_input", "retrieve_values")

    builder.add_edge(
        ["retrieve_metadata", "retrieve_values"],
        "merge_evidence",
    )

    builder.add_edge("merge_evidence", END)

    return builder.compile()
