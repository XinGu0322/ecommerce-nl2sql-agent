
from elasticsearch import AsyncElasticsearch


class ValueSearchClient:
    INDEX = "ds_dimension_values"

    def __init__(self, client: AsyncElasticsearch):
        self.client = client

    async def ensure_index(self) -> None:
        exists = await self.client.indices.exists(
            index=self.INDEX
        )

        if exists:
            return

        await self.client.indices.create(
            index=self.INDEX,
            settings={
                "number_of_shards": 1,
                "number_of_replicas": 0,
            },
            mappings={
                "properties": {
                    "field_id": {"type": "keyword"},
                    "table_name": {"type": "keyword"},
                    "value": {"type": "keyword"},
                    "search_text": {
                        "type": "text",
                        "analyzer": "ik_max_word",
                        "search_analyzer": "ik_smart",
                    },
                }
            },
        )

    async def write(self, documents: list[dict]) -> int:
        await self.ensure_index()

        for document in documents:
            await self.client.index(
                index=self.INDEX,
                id=document["id"],
                document={
                    "field_id": document["field_id"],
                    "table_name": document["table_name"],
                    "value": document["value"],
                    "search_text": document["search_text"],
                },
            )

        await self.client.indices.refresh(index=self.INDEX)

        response = await self.client.count(index=self.INDEX)
        return response["count"]

    async def search(self, query: str, limit: int = 5) -> list:
        response = await self.client.search(
            index=self.INDEX,
            size=limit,
            query={
                "bool": {
                    "should": [
                        {
                            "term": {
                                "value": {
                                    "value": query,
                                    "boost": 5,
                                }
                            }
                        },
                        {
                            "match": {
                                "search_text": {
                                    "query": query,
                                    "operator": "and",
                                }
                            }
                        },
                    ],
                    "minimum_should_match": 1,
                }
            },
        )

        return response["hits"]["hits"]
