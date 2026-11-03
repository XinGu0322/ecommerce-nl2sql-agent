
import json

from app.repositories.meta_repository import MetaRepository
from app.services.grouping_contract import GROUP_FIELDS


class SchemaLinkError(ValueError):
    """业务元数据不足或存在歧义，无法形成可靠查询计划。"""


def parse_aliases(value) -> list[str]:
    """解析指标别名。"""

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

    async def link(
        self,
        intent: dict,
        evidence: dict,
    ) -> dict:
        """
        根据用户意图和检索证据生成 Schema Plan。

        支持：
        - 指标名称和别名匹配
        - 单值筛选
        - 同字段多值筛选
        - 月份、品牌、地区分组
        - 聚合排序及 Top N
        - 基于真实外键的 JOIN 规划
        """

        # 1. 检查查询条件
        if (
            intent.get("needs_clarification")
            or evidence.get("unresolved_terms")
            or evidence.get("ambiguous_terms")
        ):
            raise SchemaLinkError(
                "查询条件存在缺失或歧义"
            )

        if intent.get("time_expression"):
            raise SchemaLinkError(
                "当前阶段暂不支持时间范围过滤"
            )

        # 2. 解析分组要求
        requested_groups = intent.get("group_by") or []

        if not isinstance(requested_groups, list):
            raise SchemaLinkError(
                "分组表达格式不正确"
            )

        if requested_groups:
            if (
                len(requested_groups) != 1
                or requested_groups[0] not in GROUP_FIELDS
            ):
                raise SchemaLinkError(
                    "当前仅支持按月份分组、品牌分组"
                    f"或地区分组：{requested_groups}"
                )

            group_by = list(
                GROUP_FIELDS[requested_groups[0]]
            )
        else:
            group_by = []

        # 3. 排序与 Top N 校验
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

        if (
            (sort is not None or limit is not None)
            and not group_by
        ):
            raise SchemaLinkError(
                "排序和 Top N 目前只支持分组聚合"
            )

        if limit is not None and sort is None:
            raise SchemaLinkError(
                "Top N 查询必须明确排序方向"
            )

        # 4. 加载数据库元数据
        sources = await self.repository.get_index_sources()

        tables = {
            item["name"]: item
            for item in sources["table"]
        }

        columns = {
            item["id"]: item
            for item in sources["column"]
        }

        # 5. 匹配唯一业务指标
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

        # 6. 从指标依赖确定事实表
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

        # 7. 逐个验证筛选实体，并按字段归并
        resolutions = evidence.get(
            "filter_resolutions", []
        )

        field_values: dict[str, list[str]] = {}

        for mention in intent.get("filters", []):
            matches = {
                (item["field_id"], item["value"])
                for item in resolutions
                if (
                    item["term"] == mention["text"]
                    and item["category"] == mention["category"]
                )
            }

            # 一个原始实体必须唯一映射到一个字段和值
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

            if not isinstance(value, str) or not value:
                raise SchemaLinkError(
                    "筛选取值格式不正确"
                )

            values = field_values.setdefault(
                field_id, []
            )

            # 同字段同取值去重
            if value not in values:
                values.append(value)

        # 8. 生成统一筛选计划
        filters = []

        for field_id, values in field_values.items():

            if len(values) > 10:
                raise SchemaLinkError(
                    "同一筛选字段最多允许10个取值"
                )

            if len(values) == 1:
                # 保留原来的单值结构
                filters.append({
                    "field_id": field_id,
                    "value": values[0],
                })

            else:
                # 当前多值场景要求按该字段分别统计
                if field_id not in group_by:
                    raise SchemaLinkError(
                        "多值对比必须按对应字段分组："
                        f"{field_id}"
                    )

                filters.append({
                    "field_id": field_id,
                    "values": values,
                })

        # 9. 根据筛选字段确定所需维度表
        target_tables = {
            columns[item["field_id"]]["table_id"]
            for item in filters
        } - {fact_table}

        # 分组字段也必须加入所需表
        for field_id in group_by:
            if field_id not in columns:
                raise SchemaLinkError(
                    f"分组字段不存在：{field_id}"
                )

            table_id = columns[field_id]["table_id"]

            if table_id != fact_table:
                target_tables.add(table_id)

        # 10. 基于元数据中的外键规划 JOIN
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
                    f"外键目标字段不存在：{referenced_id}"
                )

            joins.append({
                "left_table": fact_table,
                "left_column": fk["name"],
                "right_table": target_table,
                "right_column": fk["referenced_column"],
            })

        # 11. 输出完整 Schema Plan
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
