
import json

from app.repositories.meta_repository import MetaRepository
from app.services.grouping_contract import GROUP_FIELDS


class SchemaLinkError(ValueError):
    """业务元数据不足或存在歧义，无法形成可靠查询计划。"""


def parse_aliases(value) -> list[str]:
    """解析数据库中保存的指标别名。"""

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
        """
        根据 LLM 意图和检索证据生成可信的 Schema Plan。

        负责：
        1. 检查歧义和业务能力边界
        2. 确定唯一业务指标
        3. 验证筛选实体
        4. 确定分组、排序及 Top N
        5. 基于真实外键规划 JOIN

        不在此阶段生成或执行 SQL。
        """

        # ==================================================
        # 1. 检查查询条件是否存在缺失或歧义
        # ==================================================

        if (
            intent.get("needs_clarification")
            or evidence.get("unresolved_terms")
            or evidence.get("ambiguous_terms")
        ):
            raise SchemaLinkError(
                "查询条件存在缺失或歧义"
            )

        # 当前阶段暂不支持独立的时间范围过滤
        if intent.get("time_expression"):
            raise SchemaLinkError(
                "当前阶段暂不支持时间范围过滤"
            )

        # ==================================================
        # 2. 解析分组字段
        # ==================================================

        requested_groups = intent.get("group_by") or []

        if requested_groups:
            if (
                not isinstance(requested_groups, list)
                or len(requested_groups) != 1
                or requested_groups[0] not in GROUP_FIELDS
            ):
                raise SchemaLinkError(
                    "当前仅支持按月份分组或品牌分组："
                    f"{requested_groups}"
                )

            group_by = list(
                GROUP_FIELDS[requested_groups[0]]
            )
        else:
            group_by = []

        # ==================================================
        # 3. 校验排序和 Top N
        # ==================================================

        sort = intent.get("sort")
        limit = intent.get("limit")

        if sort not in (None, "asc", "desc"):
            raise SchemaLinkError(
                f"不支持的排序方向：{sort}"
            )

        if limit is not None:
            if (
                type(limit) is not int
                or not 1 <= limit <= 100
            ):
                raise SchemaLinkError(
                    "Top N 数量必须为1到100之间的整数"
                )

        # 暂时只支持对分组聚合结果进行排序
        if (
            (sort is not None or limit is not None)
            and not group_by
        ):
            raise SchemaLinkError(
                "排序和 Top N 目前只支持分组聚合"
            )

        # 有 Top N 时，必须知道按什么方向排名
        if limit is not None and sort is None:
            raise SchemaLinkError(
                "Top N 查询必须明确排序方向"
            )

        # ==================================================
        # 4. 加载真实业务元数据
        # ==================================================

        sources = await self.repository.get_index_sources()

        tables = {
            item["name"]: item
            for item in sources["table"]
        }

        columns = {
            item["id"]: item
            for item in sources["column"]
        }

        # ==================================================
        # 5. 根据指标名称和别名确定唯一指标
        # ==================================================

        metric_name = (
            intent.get("metric") or ""
        ).strip()

        if not metric_name:
            raise SchemaLinkError(
                "用户问题没有明确的指标"
            )

        metric_matches = [
            item
            for item in sources["metric"]
            if metric_name in (
                [item["name"]]
                + parse_aliases(item["aliases"])
            )
        ]

        if len(metric_matches) != 1:
            raise SchemaLinkError(
                f"无法唯一确定业务指标：{metric_name}"
            )

        metric = metric_matches[0]

        # ==================================================
        # 6. 根据指标依赖确定事实表
        # ==================================================

        dependencies = await (
            self.repository.get_metric_dependencies(
                metric["id"]
            )
        )

        if not dependencies:
            raise SchemaLinkError(
                "业务指标没有字段依赖"
            )

        if any(
            column_id not in columns
            for column_id in dependencies
        ):
            raise SchemaLinkError(
                "业务指标依赖了不存在的字段"
            )

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

        # ==================================================
        # 7. 验证并整理用户筛选实体
        # ==================================================

        filters = []
        seen = set()

        resolutions = evidence.get(
            "filter_resolutions", []
        )

        for mention in intent.get("filters", []):
            matches = {
                (
                    item["field_id"],
                    item["value"],
                )
                for item in resolutions
                if (
                    item["term"] == mention["text"]
                    and item["category"]
                    == mention["category"]
                )
            }

            if len(matches) != 1:
                raise SchemaLinkError(
                    "筛选实体无法唯一对齐："
                    f"{mention['text']}"
                )

            field_id, value = matches.pop()

            if field_id not in columns:
                raise SchemaLinkError(
                    f"筛选字段不存在：{field_id}"
                )

            key = (field_id, value)

            if key not in seen:
                seen.add(key)

                filters.append({
                    "field_id": field_id,
                    "value": value,
                })

        # ==================================================
        # 8. 根据筛选字段确定需要连接的表
        # ==================================================

        target_tables = {
            columns[item["field_id"]]["table_id"]
            for item in filters
        } - {fact_table}

        # ==================================================
        # 9. 分组字段同样参与 JOIN 规划
        # ==================================================

        for field_id in group_by:
            if field_id not in columns:
                raise SchemaLinkError(
                    f"分组字段不存在：{field_id}"
                )

            table_id = columns[field_id]["table_id"]

            if table_id != fact_table:
                target_tables.add(table_id)

        # ==================================================
        # 10. 根据真实外键关系规划 JOIN
        # ==================================================

        joins = []

        for target_table in sorted(target_tables):
            candidates = [
                column
                for column in columns.values()
                if (
                    column["table_id"] == fact_table
                    and column.get("referenced_table")
                    == target_table
                    and column.get("referenced_column")
                )
            ]

            if len(candidates) != 1:
                raise SchemaLinkError(
                    f"无法唯一确定 {fact_table} 到 "
                    f"{target_table} 的直接外键关系"
                )

            fk = candidates[0]

            referenced_id = (
                f"{target_table}."
                f"{fk['referenced_column']}"
            )

            if referenced_id not in columns:
                raise SchemaLinkError(
                    "外键目标字段不存在："
                    f"{referenced_id}"
                )

            joins.append({
                "left_table": fact_table,
                "left_column": fk["name"],
                "right_table": target_table,
                "right_column": fk["referenced_column"],
            })

        # ==================================================
        # 11. 输出结构化 Schema Plan
        # ==================================================

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
            "group_by": group_by,
            "sort": sort,
            "limit": limit,
        }
