
import json

from app.repositories.meta_repository import MetaRepository


def as_json(values: list) -> str:
    return json.dumps(values, ensure_ascii=False)


class MetaSyncService:
    def __init__(self, repository: MetaRepository, config: dict):
        self.repository = repository
        self.config = config

    async def sync(self) -> dict:
        schema_columns = await self.repository.get_dw_columns()

        expected_tables = set(self.config["tables"])
        actual_tables = {
            row["table_name"] for row in schema_columns
        }

        missing_tables = expected_tables - actual_tables
        if missing_tables:
            raise ValueError(
                f"数仓缺少业务表：{sorted(missing_tables)}"
            )

        tables = [
            {
                "id": name,
                "name": name,
                "table_role": (
                    "fact" if name.startswith("fact_")
                    else "dimension"
                ),
                "description": description,
                "aliases": as_json([]),
            }
            for name, description in self.config["tables"].items()
        ]

        columns = []

        for row in schema_columns:
            table_name = row["table_name"]

            if table_name not in expected_tables:
                continue

            column_name = row["column_name"]
            column_id = f"{table_name}.{column_name}"

            description = self.config["columns"].get(column_id)
            if not description:
                raise ValueError(
                    f"字段缺少业务描述：{column_id}"
                )

            if row["column_key"] == "PRI":
                role = "primary_key"
            elif row["referenced_table"]:
                role = "foreign_key"
            elif column_id in {
                "fact_order.order_amount",
                "fact_order.order_quantity",
            }:
                role = "measure"
            else:
                role = "dimension"

            columns.append({
                "id": column_id,
                "table_id": table_name,
                "name": column_name,
                "data_type": row["data_type"],
                "column_role": role,
                "description": description,
                "aliases": as_json(
                    self.config.get("aliases", {}).get(
                        column_id, []
                    )
                ),
                "examples": as_json([]),
                "referenced_table": row["referenced_table"],
                "referenced_column": row["referenced_column"],
            })

        actual_columns = {
            column["id"] for column in columns
        }
        configured_columns = set(self.config["columns"])

        unknown_columns = configured_columns - actual_columns
        if unknown_columns:
            raise ValueError(
                f"配置包含不存在的字段：{sorted(unknown_columns)}"
            )

        metrics = []
        dependencies = []

        for metric_id, definition in self.config["metrics"].items():
            metrics.append({
                "id": metric_id,
                "name": definition["name"],
                "description": definition["description"],
                "expression": definition["expression"],
                "aliases": as_json(
                    definition.get("aliases", [])
                ),
            })

            for column_id in definition["depends_on"]:
                if column_id not in actual_columns:
                    raise ValueError(
                        f"指标依赖字段不存在：{column_id}"
                    )

                dependencies.append({
                    "metric_id": metric_id,
                    "column_id": column_id,
                })

        await self.repository.save(
            tables=tables,
            columns=columns,
            metrics=metrics,
            dependencies=dependencies,
        )

        return {
            "tables": len(tables),
            "columns": len(columns),
            "metrics": len(metrics),
            "dependencies": len(dependencies),
        }
