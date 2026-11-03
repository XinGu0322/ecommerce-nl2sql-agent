
import asyncio

from elasticsearch import AsyncElasticsearch
from qdrant_client import AsyncQdrantClient

from app.agent.retrieval_graph import build_retrieval_graph
from app.clients.embedding_client import EmbeddingClient
from app.clients.mysql_client import create_mysql_engine
from app.clients.value_search_client import ValueSearchClient
from app.config import load_config
from app.repositories.meta_repository import MetaRepository
from app.services.meta_vector_service import MetaVectorService


async def main() -> None:
    config = load_config()

    dw_engine = create_mysql_engine("dw")
    meta_engine = create_mysql_engine("meta")

    qdrant_config = config["qdrant"]
    qdrant = AsyncQdrantClient(
        host=qdrant_config["host"],
        port=int(qdrant_config["port"]),
    )

    es_config = config["elasticsearch"]
    es = AsyncElasticsearch(
        f"http://{es_config['host']}:{es_config['port']}",
        request_timeout=30,
    )

    embedding = EmbeddingClient()

    try:
        repository = MetaRepository(dw_engine, meta_engine)
        vector_service = MetaVectorService(
            repository, embedding, qdrant
        )
        value_client = ValueSearchClient(es)

        graph = build_retrieval_graph(
            vector_service, value_client
        )

        result = await graph.ainvoke({
            "question": "统计华北地区苹果品牌的销售总额",
            "search_terms": ["华北", "苹果"],
        })

        evidence = result["evidence"]

        print("候选指标：")
        for item in evidence["metrics"]:
            print(item["source_id"], round(item["score"], 4))

        print("\n已确认的筛选条件：")
        for item in evidence["confirmed_filters"]:
            print(f"{item['field_id']} = {item['value']}")

        print("\n未识别的检索词：", evidence["unresolved_terms"])
        print("存在歧义的检索词：", evidence["ambiguous_terms"])
        print("需要进一步判断：", evidence["requires_review"])

        assert any(
            item["source_id"] == "total_sales"
            for item in evidence["metrics"]
        ), "没有召回销售总额指标"

        filters = {
            (item["field_id"], item["value"])
            for item in evidence["confirmed_filters"]
        }

        assert ("dim_region.region_name", "华北") in filters
        assert ("dim_product.brand", "苹果") in filters

        print("\n检索证据验收通过")

    finally:
        await embedding.close()
        await qdrant.close()
        await es.close()
        await dw_engine.dispose()
        await meta_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
