
import argparse
import asyncio
from contextlib import AsyncExitStack

from elasticsearch import AsyncElasticsearch
from qdrant_client import AsyncQdrantClient

from app.agent.query_graph import build_query_graph
from app.agent.retrieval_graph import build_retrieval_graph
from app.clients.dw_readonly_client import (
    create_readonly_dw_engine,
)
from app.clients.embedding_client import EmbeddingClient
from app.clients.llm_client import LLMClient
from app.clients.mysql_client import create_mysql_engine
from app.clients.value_search_client import ValueSearchClient
from app.config import load_config
from app.repositories.meta_repository import MetaRepository
from app.services.meta_vector_service import MetaVectorService
from app.services.query_executor import QueryExecutor
from app.services.schema_link_service import (
    SchemaLinkService,
)


def parse_args() -> argparse.Namespace:
    """读取命令行中的自然语言问题。"""

    parser = argparse.ArgumentParser(
        description="电商 NL2SQL 智能问数 Agent"
    )

    parser.add_argument(
        "question",
        nargs="?",
        help="需要查询的自然语言问题",
    )

    return parser.parse_args()


def display_grouped_rows(
    execution: dict,
    schema_plan: dict,
) -> None:
    """
    根据分组维度展示结果。

    支持：
    - 年月分组
    - 品牌分组
    - 后续扩展的通用维度分组
    """

    rows = execution.get("rows")

    if rows is None:
        return

    metric_id = execution["metric_id"]

    group_fields = (
        schema_plan.get("group_by") or []
    )

    dimension_names = [
        field_id.split(".")[1]
        for field_id in group_fields
    ]

    print("\n分组查询结果：")

    if not rows:
        print("没有符合条件的数据")
        return

    for row in rows:

        # ==============================================
        # 1. 年月分组采用友好的日期格式
        # ==============================================

        if group_fields == [
            "dim_date.year",
            "dim_date.month",
        ]:
            group_label = (
                f"{row['year']}-"
                f"{row['month']:02d}"
            )

        # ==============================================
        # 2. 其他维度使用通用展示格式
        # ==============================================

        else:
            group_label = " | ".join(
                f"{name}={row[name]}"
                for name in dimension_names
            )

        print(
            f"{group_label} | "
            f"{metric_id} = {row[metric_id]}"
        )


async def main(question: str) -> int:
    """初始化依赖并执行完整 LangGraph 问数流程。"""

    config = load_config()

    async with AsyncExitStack() as stack:

        # ==================================================
        # 1. MySQL 元数据连接
        # ==================================================

        dw_engine = create_mysql_engine("dw")

        stack.push_async_callback(
            dw_engine.dispose
        )

        meta_engine = create_mysql_engine("meta")

        stack.push_async_callback(
            meta_engine.dispose
        )

        # ==================================================
        # 2. 专用 MySQL 只读查询连接
        # ==================================================

        readonly_engine = (
            create_readonly_dw_engine()
        )

        stack.push_async_callback(
            readonly_engine.dispose
        )

        # ==================================================
        # 3. Qdrant 向量数据库
        # ==================================================

        qdrant_config = config["qdrant"]

        qdrant = AsyncQdrantClient(
            host=qdrant_config["host"],
            port=int(qdrant_config["port"]),
        )

        stack.push_async_callback(
            qdrant.close
        )

        # ==================================================
        # 4. Elasticsearch 实体检索
        # ==================================================

        es_config = config["elasticsearch"]

        es = AsyncElasticsearch(
            f"http://"
            f"{es_config['host']}:"
            f"{es_config['port']}",
            request_timeout=30,
        )

        stack.push_async_callback(
            es.close
        )

        # ==================================================
        # 5. Embedding 和 LLM
        # ==================================================

        embedding = EmbeddingClient()

        stack.push_async_callback(
            embedding.close
        )

        llm = LLMClient()

        stack.push_async_callback(
            llm.close
        )

        # ==================================================
        # 6. Repository
        # ==================================================

        repository = MetaRepository(
            dw_engine,
            meta_engine,
        )

        # ==================================================
        # 7. 检索服务
        # ==================================================

        vector_service = MetaVectorService(
            repository,
            embedding,
            qdrant,
        )

        value_client = ValueSearchClient(es)

        # ==================================================
        # 8. 构建检索 LangGraph 子图
        # ==================================================

        retrieval_graph = build_retrieval_graph(
            vector_service,
            value_client,
            llm_client=llm,
        )

        # ==================================================
        # 9. 构建完整查询 LangGraph
        # ==================================================

        query_graph = build_query_graph(
            retrieval_graph=retrieval_graph,
            schema_service=SchemaLinkService(
                repository
            ),
            executor=QueryExecutor(
                readonly_engine
            ),
        )

        # ==================================================
        # 10. 执行自然语言问题
        # ==================================================

        result = await query_graph.ainvoke({
            "question": question,
        })

        status = result["status"]

        print(
            "\n========== 智能问数结果 =========="
        )

        print("用户问题：", question)
        print("执行状态：", status)

        # ==================================================
        # 11. 成功结果展示
        # ==================================================

        if status == "success":

            execution = result["execution"]
            intent = result["intent"]
            schema_plan = result["schema_plan"]

            print(
                "\n识别指标：",
                intent.get("metric"),
            )

            print(
                "确定指标：",
                schema_plan["metric"]["id"],
            )

            # ----------------------------------------------
            # 11.1 分组查询
            # ----------------------------------------------

            if execution.get("rows") is not None:
                display_grouped_rows(
                    execution,
                    schema_plan,
                )

            # ----------------------------------------------
            # 11.2 单指标聚合
            # ----------------------------------------------

            else:
                print(
                    "查询结果：",
                    execution["value"],
                )

            # ----------------------------------------------
            # 11.3 输出实际执行的 SQL
            # ----------------------------------------------

            print("\n执行 SQL：")
            print(execution["sql"])

            print("\n绑定参数：")
            print(execution["params"])

            return 0

        # ==================================================
        # 12. 未成功执行的业务分支
        # ==================================================

        print("\n未完成查询：")

        print(
            result.get("error") or "未知原因"
        )

        if status in {
            "needs_review",
            "rejected",
        }:
            return 2

        return 1


if __name__ == "__main__":

    args = parse_args()

    if args.question is not None:
        question = args.question

    else:
        question = input(
            "请输入自然语言查询问题："
        )

    raise SystemExit(
        asyncio.run(main(question))
    )
