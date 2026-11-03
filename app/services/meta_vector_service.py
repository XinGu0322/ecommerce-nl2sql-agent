
import json
import uuid

from qdrant_client import AsyncQdrantClient, models

from app.clients.embedding_client import EmbeddingClient
from app.repositories.meta_repository import MetaRepository


class MetaVectorService:
    COLLECTION = "ds_meta_bge_large_zh_v1_5"
    BATCH_SIZE = 2

    def __init__(
        self,
        repository: MetaRepository,
        embedding: EmbeddingClient,
        qdrant: AsyncQdrantClient,
    ):
        self.repository = repository
        self.embedding = embedding
        self.qdrant = qdrant

    @staticmethod
    def parse_aliases(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return json.loads(value)
        return value

    def make_documents(self, sources: dict) -> list[dict]:
        documents = []

        for kind, records in sources.items():
            for record in records:
                aliases = self.parse_aliases(
                    record.get("aliases")
                )
                alias_text = "、".join(aliases) or "无"

                if kind == "table":
                    content = (
                        f"数据库表：{record['name']}。"
                        f"表类型：{record['table_role']}。"
                        f"业务含义：{record['description']}。"
                        f"别名：{alias_text}。"
                    )

                elif kind == "column":
                    reference = ""
                    if record["referenced_table"]:
                        reference = (
                            "外键关联："
                            f"{record['referenced_table']}."
                            f"{record['referenced_column']}。"
                        )

                    content = (
                        f"数据库字段：{record['id']}。"
                        f"所属表：{record['table_id']}。"
                        f"字段类型：{record['data_type']}。"
                        f"字段角色：{record['column_role']}。"
                        f"业务含义：{record['description']}。"
                        f"别名：{alias_text}。"
                        f"{reference}"
                    )

                else:
                    content = (
                        f"业务指标：{record['name']}。"
                        f"指标含义：{record['description']}。"
                        f"计算口径：{record['expression']}。"
                        f"别名：{alias_text}。"
                    )

                documents.append({
                    "kind": kind,
                    "source_id": record["id"],
                    "text": content,
                })

        return documents

    async def ensure_collection(self) -> None:
        if await self.qdrant.collection_exists(self.COLLECTION):
            info = await self.qdrant.get_collection(
                collection_name=self.COLLECTION
            )
            vectors = info.config.params.vectors

            if (
                not isinstance(vectors, models.VectorParams)
                or vectors.size != self.embedding.dimensions
            ):
                raise ValueError(
                    "Qdrant Collection 向量配置不匹配，"
                    "不能直接写入"
                )
            return

        await self.qdrant.create_collection(
            collection_name=self.COLLECTION,
            vectors_config=models.VectorParams(
                size=self.embedding.dimensions,
                distance=models.Distance.COSINE,
            ),
        )

    async def build(self) -> dict:
        sources = await self.repository.get_index_sources()
        documents = self.make_documents(sources)

        if not documents:
            raise ValueError("元数据库没有可索引的数据")

        await self.ensure_collection()

        for start in range(0, len(documents), self.BATCH_SIZE):
            batch = documents[start:start + self.BATCH_SIZE]

            vectors = await self.embedding.embed(
                [item["text"] for item in batch]
            )

            points = []

            for item, vector in zip(batch, vectors):
                point_id = str(uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"ecommerce-meta:{item['kind']}:{item['source_id']}",
                ))

                points.append(models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=item,
                ))

            await self.qdrant.upsert(
                collection_name=self.COLLECTION,
                points=points,
                wait=True,
            )

        count = await self.qdrant.count(
            collection_name=self.COLLECTION,
            exact=True,
        )

        return {
            "indexed_documents": len(documents),
            "collection_points": count.count,
        }

    async def search(self, query: str, limit: int = 5):
        vector = (await self.embedding.embed([query]))[0]

        result = await self.qdrant.query_points(
            collection_name=self.COLLECTION,
            query=vector,
            limit=limit,
            with_payload=True,
        )

        return result.points
