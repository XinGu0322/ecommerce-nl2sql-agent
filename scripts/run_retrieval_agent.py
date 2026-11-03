
import asyncio

from elasticsearch import AsyncElasticsearch
from qdrant_client import AsyncQdrantClient

from app.agent.retrieval_graph import build_retrieval_graph
from app.clients.embedding_client import EmbeddingClient
from app.clients.llm_client import LLMClient
from app.clients.mysql_client import create_mysql_engine
from app.clients.value_search_client import ValueSearchClient
from app.config import load_config
from app.repositories.meta_repository import MetaRepository
from app.services.meta_vector_service import MetaVectorService
from app.services.schema_link_service import SchemaLinkService

async def main() -> None:
    config = load_config()

    # 1. 初始化 MySQL 数据库连接
    dw_engine = create_mysql_engine("dw")
    meta_engine = create_mysql_engine("meta")

    # 2. 初始化 Qdrant 客户端
    qdrant_config = config["qdrant"]
    qdrant = AsyncQdrantClient(
        host=qdrant_config["host"],
        port=int(qdrant_config["port"]),
    )

    # 3. 初始化 Elasticsearch 客户端
    es_config = config["elasticsearch"]
    es = AsyncElasticsearch(
        f"http://{es_config['host']}:{es_config['port']}",
        request_timeout=30,
    )

    # 4. 初始化 Embedding 和 LLM 客户端
    embedding = EmbeddingClient()
    llm = LLMClient()

    try:
        # 5. 初始化 Repository 和 Service
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

        # 6. 构建 LangGraph 检索工作流
        graph = build_retrieval_graph(
            vector_service,
            value_client,
            llm_client=llm,
        )

        # 7. 输入自然语言问题
        question = "统计华北地区苹果品牌的销售总额"

        print(f"\n用户问题：{question}")

        # search_terms 由 LLM 自动生成，无须手动传入
        result = await graph.ainvoke({
            "question": question,
        })

        # 8. 输出 LLM 结构化意图
        intent = result["intent"]

        print("\n========== LLM 意图理解 ==========")
        print("目标指标：", intent.get("metric"))
        print("筛选实体：", intent.get("filters"))
        print("时间表达：", intent.get("time_expression"))
        print("分组要求：", intent.get("group_by"))
        print("排序方向：", intent.get("sort"))
        print("数量限制：", intent.get("limit"))
        print("需要澄清：", intent.get("needs_clarification"))

        print("\nLLM 提取的检索词：")
        print(result.get("search_terms", []))

        # 9. 获取检索证据
        evidence = result["evidence"]


        # Schema Linking：根据真实元数据确定指标与 JOIN
        schema_service = SchemaLinkService(repository)

        schema_plan = await schema_service.link(
            result["intent"],
            evidence,
        )

        print("\n========== Schema Linking ==========")

        print("确定指标：", schema_plan["metric"]["id"])
        print(
            "计算表达式：",
            schema_plan["metric"]["expression"],
        )
        print("事实表：", schema_plan["fact_table"])
        print("所需表：", schema_plan["required_tables"])

        print("\nJOIN 规划：")
        for join in schema_plan["joins"]:
            print(
                f"{join['left_table']}."
                f"{join['left_column']} = "
                f"{join['right_table']}."
                f"{join['right_column']}"
            )

        print("\n筛选条件：")
        for item in schema_plan["filters"]:
            print(
                f"{item['field_id']} = {item['value']}"
            )

        assert schema_plan["metric"]["id"] == "total_sales"
        assert schema_plan["fact_table"] == "fact_order"
        assert len(schema_plan["joins"]) == 2

        print("\nSchema Linking 验收通过")


        print("\n========== Qdrant 指标召回 ==========")
        for item in evidence["metrics"]:
            print(
                f"{item['source_id']} | "
                f"score={item['score']:.4f}"
            )

        print("\n========== Qdrant 字段召回 ==========")
        for item in evidence["columns"]:
            print(
                f"{item['source_id']} | "
                f"score={item['score']:.4f}"
            )

        # 10. 输出实体对齐结果
        print("\n========== Elasticsearch 实体对齐 ==========")

        for item in evidence["filter_resolutions"]:
            print(
                f"原始表达：{item['term']} | "
                f"检索候选：{item['lookup']} | "
                f"数据库字段：{item['field_id']} | "
                f"实际取值：{item['value']}"
            )

        print("\n========== 已确认的筛选条件 ==========")
        for item in evidence["confirmed_filters"]:
            print(
                f"{item['field_id']} = {item['value']}"
            )

        # 11. 输出业务检查结果
        print("\n========== 业务检查 ==========")
        print(
            "未识别的检索词：",
            evidence["unresolved_terms"],
        )
        print(
            "存在歧义的检索词：",
            evidence["ambiguous_terms"],
        )
        print(
            "需要进一步判断：",
            evidence["requires_review"],
        )

        # 12. 业务验收断言
        assert intent["metric"] == "销售总额", (
            "LLM 未正确识别销售总额指标"
        )

        assert any(
            item["source_id"] == "total_sales"
            for item in evidence["metrics"]
        ), "没有召回 total_sales 指标"

        filters = {
            (item["field_id"], item["value"])
            for item in evidence["confirmed_filters"]
        }

        assert (
            "dim_region.region_name",
            "华北",
        ) in filters, "没有正确识别华北地区"

        assert (
            "dim_product.brand",
            "苹果",
        ) in filters, "没有正确识别苹果品牌"

        assert not evidence["unresolved_terms"], (
            "存在未识别的筛选条件"
        )

        assert not evidence["ambiguous_terms"], (
            "存在有歧义的筛选条件"
        )

        assert not evidence["requires_review"], (
            "检索证据仍需进一步判断"
        )

        print("\n检索证据验收通过")

    finally:
        # 13. 统一释放外部资源
        await llm.close()
        await embedding.close()
        await qdrant.close()
        await es.close()
        await dw_engine.dispose()
        await meta_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
