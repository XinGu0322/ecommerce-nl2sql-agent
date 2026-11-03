
import hashlib

from app.repositories.meta_repository import MetaRepository
from app.clients.value_search_client import ValueSearchClient


class MetaValueService:
    def __init__(
        self,
        repository: MetaRepository,
        search_client: ValueSearchClient,
    ):
        self.repository = repository
        self.search_client = search_client

    async def build(self) -> dict:
        values = await self.repository.get_dw_distinct_values()
        sources = await self.repository.get_index_sources()

        column_descriptions = {
            row["id"]: row["description"]
            for row in sources["column"]
        }

        documents = []

        for field_id, field_values in values.items():
            if field_id not in column_descriptions:
                raise ValueError(
                    f"元数据库中不存在字段：{field_id}"
                )

            table_name, _ = field_id.split(".", 1)

            for value in field_values:
                raw_id = f"{field_id}:{value}"

                document_id = hashlib.sha256(
                    raw_id.encode("utf-8")
                ).hexdigest()

                documents.append({
                    "id": document_id,
                    "field_id": field_id,
                    "table_name": table_name,
                    "value": value,
                    "search_text": (
                        f"数据库字段：{field_id}。"
                        f"业务含义：{column_descriptions[field_id]}。"
                        f"实际取值：{value}。"
                    ),
                })

        count = await self.search_client.write(documents)

        return {
            "source_fields": len(values),
            "indexed_documents": len(documents),
            "es_count": count,
        }
