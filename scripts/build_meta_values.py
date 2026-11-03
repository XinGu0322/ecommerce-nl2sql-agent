
import asyncio

from elasticsearch import AsyncElasticsearch

from app.config import load_config
from app.clients.mysql_client import create_mysql_engine
from app.clients.value_search_client import ValueSearchClient
from app.repositories.meta_repository import MetaRepository
from app.services.meta_value_service import MetaValueService


async def main() -> None:
    config = load_config()["elasticsearch"]

    es = AsyncElasticsearch(
        f"http://{config['host']}:{config['port']}",
        request_timeout=30,
    )

    dw_engine = create_mysql_engine("dw")
    meta_engine = create_mysql_engine("meta")

    try:
        repository = MetaRepository(dw_engine, meta_engine)
        search_client = ValueSearchClient(es)
        service = MetaValueService(repository, search_client)

        result = await service.build()
        print("字段取值索引构建完成：", result)

        for query in ["华北", "苹果", "黄金"]:
            print(f"\n检索：{query}")

            hits = await search_client.search(query)

            for hit in hits:
                source = hit["_source"]
                print(
                    f"score={hit['_score']:.4f} | "
                    f"{source['field_id']} = {source['value']}"
                )

    finally:
        await es.close()
        await dw_engine.dispose()
        await meta_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
