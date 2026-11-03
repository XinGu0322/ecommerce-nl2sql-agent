
import json

from app.repositories.meta_repository import MetaRepository


class SchemaLinkError(ValueError):
    """业务元数据不足或存在歧义，无法形成可靠查询计划。"""


def parse_aliases(value) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        value = json.loads(value)

    if not isinstance(value, list):
        raise SchemaLinkError("指标别名格式不正确")

    return value


class SchemaLinkService:
    def __init__(self, repository: MetaRepository):
        self.repository = repository

    async def link(self, intent: dict, evidence: dict) -> dict:
        # 1. 先检查当前阶段不能处理的情况
        if (
            intent.get("needs_clarification")
            or evidence.get("unresolved_terms")
            or evidence.get("ambiguous_terms")
        ):
            raise SchemaLinkError("查询条件存在缺失或歧义")

        unsupported = [
            key for key in (
                "time_expression", "group_by", "sort", "limit"
            )
            if intent.get(key)
        ]

        if unsupported:
            raise SchemaLinkError(
                f"当前阶段暂不支持：{unsupported}"
            )

        sources = await self.repository.get_index_sources()

        tables = {
            item["name"]: item
            for item in sources["table"]
        }

        columns = {
            item["id"]: item
            for item in sources["column"]
        }

        # 2. 根据指标名称和别名确定唯一指标
        metric_name = (intent.get("metric") or "").strip()

        if not metric_name:
            raise SchemaLinkError("用户问题没有明确的指标")

        metric_matches = [
            item for item in sources["metric"]
            if metric_name in (
                [item["name"]] + parse_aliases(item["aliases"])
            )
        ]

        if len(metric_matches) != 1:
            raise SchemaLinkError(
                f"无法唯一确定业务指标：{metric_name}"
            )

        metric = metric_matches[0]

        # 3. 从指标依赖确定事实表
        dependencies = await (
            self.repository.get_metric_dependencies(metric["id"])
        )

        if not dependencies:
            raise SchemaLinkError("业务指标没有字段依赖")

        if any(
            column_id not in columns
            for column_id in dependencies
        ):
            raise SchemaLinkError("业务指标依赖了不存在的字段")

        root_tables = {
            columns[column_id]["table_id"]
            for column_id in dependencies
        }

        if len(root_tables) != 1:
            raise SchemaLinkError(
                "当前阶段不支持跨事实表指标"
            )

        fact_table = root_tables.pop()

        if (
            fact_table not in tables
            or tables[fact_table]["table_role"] != "fact"
        ):
            raise SchemaLinkError(
                "指标依赖无法确定有效事实表"
            )

        # 4. 逐个验证用户筛选实体
        filters = []
        seen = set()

        resolutions = evidence.get("filter_resolutions", [])

        for mention in intent.get("filters", []):
            matches = {
                (item["field_id"], item["value"])
                for item in resolutions
                if item["term"] == mention["text"]
                and item["category"] == mention["category"]
            }

            if len(matches) != 1:
                raise SchemaLinkError(
                    f"筛选实体无法唯一对齐：{mention['text']}"
                )

            field_id, value = matches.pop()

            if field_id not in columns:
                raise SchemaLinkError(
                    f"筛选字段不存在：{field_id}"
                )

            if (field_id, value) not in seen:
                seen.add((field_id, value))
                filters.append({
                    "field_id": field_id,
                    "value": value,
                })

        # 5. 根据真实外键关系规划 JOIN
        target_tables = {
            columns[item["field_id"]]["table_id"]
            for item in filters
        } - {fact_table}

        joins = []

        for target_table in sorted(target_tables):
            candidates = [
                column for column in columns.values()
                if column["table_id"] == fact_table
                and column.get("referenced_table")
                == target_table
                and column.get("referenced_column")
            ]

            if len(candidates) != 1:
                raise SchemaLinkError(
                    f"无法唯一确定 {fact_table} 到 "
                    f"{target_table} 的直接外键关系"
                )

            fk = candidates[0]

            referenced_id = (
                f"{target_table}.{fk['referenced_column']}"
            )

            if referenced_id not in columns:
                raise SchemaLinkError(
                    f"外键目标字段不存在：{referenced_id}"
                )

            joins.append({
                "left_table": fact_table,
                "left_column": fk["name"],
                "right_table": target_table,
                "right_column": fk["referenced_column"],
            })

        # 6. 输出结构化计划，不执行 SQL
        return {
            "metric": {
                "id": metric["id"],
                "name": metric["name"],
                "expression": metric["expression"],
                "dependencies": dependencies,
            },
            "fact_table": fact_table,
            "required_tables": [
                fact_table,
                *sorted(target_tables),
            ],
            "joins": joins,
            "filters": filters,
        }
