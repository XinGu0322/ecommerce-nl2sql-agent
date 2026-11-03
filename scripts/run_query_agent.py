
import asyncio
from contextlib import AsyncExitStack

from elasticsearch import AsyncElasticsearch
from qdrant_client import AsyncQdrantClient

from app.agent.query_graph import build_query_graph
from app.agent.retrieval_graph import build_retrieval_graph
from app.clients.dw_readonly_client import create_readonly_dw_engine
from app.clients.embedding_client import EmbeddingClient
from app.clients.llm_client import LLMClient
from app.clients.mysql_client import create_mysql_engine
from app.clients.value_search_client import ValueSearchClient
from app.config import load_config
from app.repositories.meta_repository import MetaRepository
from app.services.meta_vector_service import MetaVectorService
from app.services.query_executor import QueryExecutor
from app.services.schema_link_service import SchemaLinkService


async def main():
    config = load_config()

    async with AsyncExitStack() as stack:
        dw_engine = create_mysql_engine("dw")
        stack.push_async_callback(dw_engine.dispose)

        meta_engine = create_mysql_engine("meta")
        stack.push_async_callback(meta_engine.dispose)

        readonly_engine = create_readonly_dw_engine()
        stack.push_async_callback(readonly_engine.dispose)

        qc = config["qdrant"]
        qdrant = AsyncQdrantClient(
            host=qc["host"],
            port=int(qc["port"]),
        )
        stack.push_async_callback(qdrant.close)

        ec = config["elasticsearch"]
        es = AsyncElasticsearch(
            f"http://{ec['host']}:{ec['port']}",
            request_timeout=30,
        )
        stack.push_async_callback(es.close)

        embedding = EmbeddingClient()
        stack.push_async_callback(embedding.close)

        llm = LLMClient()
        stack.push_async_callback(llm.close)

        repository = MetaRepository(dw_engine, meta_engine)

        vector_service = MetaVectorService(
            repository, embedding, qdrant
        )
        value_client = ValueSearchClient(es)

        retrieval_graph = build_retrieval_graph(
            vector_service,
            value_client,
            llm_client=llm,
        )

        query_graph = build_query_graph(
            retrieval_graph=retrieval_graph,
            schema_service=SchemaLinkService(repository),
            executor=QueryExecutor(readonly_engine),
        )

        question = "统计华北地区苹果品牌的销售总额"

        result = await query_graph.ainvoke({
            "question": question,
        })

        print("用户问题：", question)
        print("执行状态：", result["status"])

        if result["status"] == "success":
            execution = result["execution"]

            print("\n指标：", execution["metric_id"])
            print("查询结果：", execution["value"])
            print("\n执行 SQL：")
            print(execution["sql"])
            print("\n绑定参数：", execution["params"])
        else:
            print("未执行查询，原因：", result.get("error"))


if __name__ == "__main__":
    asyncio.run(main())
