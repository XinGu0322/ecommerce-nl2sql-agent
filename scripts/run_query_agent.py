
import argparse
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="电商 NL2SQL 智能问数 Agent"
    )

    parser.add_argument(
        "question",
        nargs="?",
        help="需要查询的自然语言问题",
    )

    return parser.parse_args()


async def main(question: str) -> int:
    config = load_config()

    async with AsyncExitStack() as stack:

        # 1. MySQL 元数据库连接
        dw_engine = create_mysql_engine("dw")
        stack.push_async_callback(dw_engine.dispose)

        meta_engine = create_mysql_engine("meta")
        stack.push_async_callback(meta_engine.dispose)

        # 2. MySQL 只读查询连接
        readonly_engine = create_readonly_dw_engine()
        stack.push_async_callback(readonly_engine.dispose)

        # 3. Qdrant
        qdrant_config = config["qdrant"]

        qdrant = AsyncQdrantClient(
            host=qdrant_config["host"],
            port=int(qdrant_config["port"]),
        )
        stack.push_async_callback(qdrant.close)

        # 4. Elasticsearch
        es_config = config["elasticsearch"]

        es = AsyncElasticsearch(
            f"http://{es_config['host']}:{es_config['port']}",
            request_timeout=30,
        )
        stack.push_async_callback(es.close)

        # 5. Embedding 与 LLM
        embedding = EmbeddingClient()
        stack.push_async_callback(embedding.close)

        llm = LLMClient()
        stack.push_async_callback(llm.close)

        # 6. Repository 与 Service
        repository = MetaRepository(
            dw_engine,
            meta_engine,
        )

        vector_service = MetaVectorService(
            repository,
            embedding,
            qdrant,
        )

        value_client = ValueSearchClient(es)

        # 7. 构建检索子图
        retrieval_graph = build_retrieval_graph(
            vector_service,
            value_client,
            llm_client=llm,
        )

        # 8. 构建完整查询图
        query_graph = build_query_graph(
            retrieval_graph=retrieval_graph,
            schema_service=SchemaLinkService(repository),
            executor=QueryExecutor(readonly_engine),
        )

        # 9. 执行用户问题
        result = await query_graph.ainvoke({
            "question": question,
        })

        status = result["status"]

        print("\n========== 智能问数结果 ==========")
        print("用户问题：", question)
        print("执行状态：", status)

        # 10. 查询成功
        if status == "success":
            execution = result["execution"]
            intent = result["intent"]
            schema_plan = result["schema_plan"]

            print("\n识别指标：", intent.get("metric"))
            print("确定指标：", schema_plan["metric"]["id"])

            if execution.get("rows") is not None:
                print("\n分组查询结果：")

                for row in execution["rows"]:
                    print(
                        f"{row['year']}-"
                        f"{row['month']:02d} | "
                        f"{execution['metric_id']} = "
                        f"{row[execution['metric_id']]}"
                    )

                if not execution["rows"]:
                    print("没有符合条件的数据")
            else:
                print("查询结果：", execution["value"])


            print("\n执行 SQL：")
            print(execution["sql"])

            print("\n绑定参数：")
            print(execution["params"])

            return 0

        # 11. 业务拒绝、需要澄清或执行失败
        print("\n未完成查询：")
        print(result.get("error") or "未知原因")

        if status in {"needs_review", "rejected"}:
            return 2

        return 1


if __name__ == "__main__":
    args = parse_args()

    if args.question is not None:
        question = args.question
    else:
        question = input("请输入自然语言查询问题：")

    raise SystemExit(asyncio.run(main(question)))
