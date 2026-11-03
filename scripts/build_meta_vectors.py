
import asyncio

from qdrant_client import AsyncQdrantClient

from app.config import load_config
from app.clients.mysql_client import create_mysql_engine
from app.clients.embedding_client import EmbeddingClient
from app.repositories.meta_repository import MetaRepository
from app.services.meta_vector_service import MetaVectorService


async def main():
    config = load_config()["qdrant"]

    dw_engine = create_mysql_engine("dw")
    meta_engine = create_mysql_engine("meta")

    qdrant = AsyncQdrantClient(
        host=config["host"],
        port=int(config["port"]),
    )
    embedding = EmbeddingClient()

    try:
        repository = MetaRepository(dw_engine, meta_engine)
        service = MetaVectorService(
            repository,
            embedding,
            qdrant,
        )

        result = await service.build()
        print("向量知识库构建完成：", result)

        print("\n检索测试：销售总额")
        matches = await service.search("销售总额", limit=5)

        for point in matches:
            payload = point.payload or {}
            print(
                f"score={point.score:.4f} | "
                f"{payload.get('kind')} | "
                f"{payload.get('source_id')}"
            )

    finally:
        await embedding.close()
        await qdrant.close()
        await dw_engine.dispose()
        await meta_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
