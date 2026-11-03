
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class MetaRepository:
    def __init__(
        self,
        dw_engine: AsyncEngine,
        meta_engine: AsyncEngine,
    ):
        self.dw_engine = dw_engine
        self.meta_engine = meta_engine

    async def get_dw_columns(self) -> list[dict]:
        """从 MySQL 系统表读取 dw 的真实字段及外键。"""

        sql = text("""
            SELECT
                c.TABLE_NAME AS table_name,
                c.COLUMN_NAME AS column_name,
                c.COLUMN_TYPE AS data_type,
                c.COLUMN_KEY AS column_key,
                k.REFERENCED_TABLE_NAME AS referenced_table,
                k.REFERENCED_COLUMN_NAME AS referenced_column
            FROM INFORMATION_SCHEMA.COLUMNS AS c
            LEFT JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE AS k
                ON c.TABLE_SCHEMA = k.TABLE_SCHEMA
               AND c.TABLE_NAME = k.TABLE_NAME
               AND c.COLUMN_NAME = k.COLUMN_NAME
               AND k.REFERENCED_TABLE_NAME IS NOT NULL
            WHERE c.TABLE_SCHEMA = :schema_name
            ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION
        """)

        async with self.dw_engine.connect() as connection:
            result = await connection.execute(
                sql, {"schema_name": "dw"}
            )
            return [dict(row) for row in result.mappings()]


    async def save(
        self,
        tables: list[dict],
        columns: list[dict],
        metrics: list[dict],
        dependencies: list[dict],
    ) -> None:
        async with self.meta_engine.begin() as connection:

            # 1. 保存表元数据
            table_sql = text("""
                INSERT INTO table_info
                    (id, name, table_role, description, aliases)
                VALUES
                    (:id, :name, :table_role, :description, :aliases)
                ON DUPLICATE KEY UPDATE
                    table_role = :table_role,
                    description = :description,
                    aliases = :aliases
            """)

            for table in tables:
                await connection.execute(table_sql, table)

            # 2. 保存字段元数据
            column_sql = text("""
                INSERT INTO column_info
                    (id, table_id, name, data_type, column_role,
                     description, aliases, examples,
                     referenced_table, referenced_column)
                VALUES
                    (:id, :table_id, :name, :data_type, :column_role,
                     :description, :aliases, :examples,
                     :referenced_table, :referenced_column)
                ON DUPLICATE KEY UPDATE
                    data_type = :data_type,
                    column_role = :column_role,
                    description = :description,
                    aliases = :aliases,
                    examples = :examples,
                    referenced_table = :referenced_table,
                    referenced_column = :referenced_column
            """)

            for column in columns:
                await connection.execute(column_sql, column)

            # 3. 保存业务指标
            metric_sql = text("""
                INSERT INTO metric_info
                    (id, name, description, expression, aliases)
                VALUES
                    (:id, :name, :description, :expression, :aliases)
                ON DUPLICATE KEY UPDATE
                    name = :name,
                    description = :description,
                    expression = :expression,
                    aliases = :aliases
            """)

            for metric in metrics:
                await connection.execute(metric_sql, metric)

            # 4. 删除本次同步指标的旧依赖关系
            delete_sql = text("""
                DELETE FROM column_metric
                WHERE metric_id = :metric_id
            """)

            for metric in metrics:
                await connection.execute(
                    delete_sql,
                    {"metric_id": metric["id"]},
                )

            # 5. 写入当前指标依赖关系
            dependency_sql = text("""
                INSERT INTO column_metric
                    (metric_id, column_id)
                VALUES
                    (:metric_id, :column_id)
            """)

            for dependency in dependencies:
                await connection.execute(
                    dependency_sql,
                    dependency,
                )


    async def get_index_sources(self) -> dict[str, list[dict]]:
        """读取需要建立向量索引的元数据。"""

        queries = {
            "table": """
                SELECT id, name, description, aliases, table_role
                FROM table_info
                ORDER BY id
            """,
            "column": """
                SELECT id, name, table_id, data_type,
                       column_role, description, aliases,
                       referenced_table, referenced_column
                FROM column_info
                ORDER BY id
            """,
            "metric": """
                SELECT id, name, description, expression, aliases
                FROM metric_info
                ORDER BY id
            """,
        }

        sources = {}

        async with self.meta_engine.connect() as connection:
            for kind, sql in queries.items():
                result = await connection.execute(text(sql))
                sources[kind] = [
                    dict(row) for row in result.mappings()
                ]

        return sources


    async def get_dw_distinct_values(self) -> dict[str, list[str]]:
        """读取允许建立检索索引的维度字段取值。"""

        queries = {
            "dim_region.region_name": """
                SELECT DISTINCT region_name AS value
                FROM dim_region
                WHERE region_name IS NOT NULL
            """,
            "dim_region.province": """
                SELECT DISTINCT province AS value
                FROM dim_region
                WHERE province IS NOT NULL
            """,
            "dim_product.product_name": """
                SELECT DISTINCT product_name AS value
                FROM dim_product
                WHERE product_name IS NOT NULL
            """,
            "dim_product.category": """
                SELECT DISTINCT category AS value
                FROM dim_product
                WHERE category IS NOT NULL
            """,
            "dim_product.brand": """
                SELECT DISTINCT brand AS value
                FROM dim_product
                WHERE brand IS NOT NULL
            """,
            "dim_customer.member_level": """
                SELECT DISTINCT member_level AS value
                FROM dim_customer
                WHERE member_level IS NOT NULL
            """,
        }

        sources = {}

        async with self.dw_engine.connect() as connection:
            for field_id, sql in queries.items():
                result = await connection.execute(text(sql))

                sources[field_id] = sorted({
                    str(row[0]).strip()
                    for row in result
                    if row[0] is not None
                    and str(row[0]).strip()
                })

        return sources



