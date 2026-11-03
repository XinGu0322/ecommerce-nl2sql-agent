
from contextlib import (
    AsyncExitStack,
    asynccontextmanager,
)
from typing import AsyncIterator, Any

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


@asynccontextmanager
async def create_query_runtime() -> AsyncIterator[Any]:
    """
    创建 NL2SQL 查询运行时。

    统一管理：
    - MySQL 元数据连接池
    - MySQL 只读查询连接池
    - Qdrant 客户端
    - Elasticsearch 客户端
    - Embedding 客户端
    - LLM 客户端
    - LangGraph 查询工作流

    退出上下文时自动释放相关资源。
    """

    config = load_config()

    async with AsyncExitStack() as stack:

        # ==========================================
        # 1. MySQL 连接池
        # ==========================================

        dw_engine = create_mysql_engine("dw")
        stack.push_async_callback(
            dw_engine.dispose
        )

        meta_engine = create_mysql_engine("meta")
        stack.push_async_callback(
            meta_engine.dispose
        )

        readonly_engine = create_readonly_dw_engine()
        stack.push_async_callback(
            readonly_engine.dispose
        )

        # ==========================================
        # 2. Qdrant
        # ==========================================

        qdrant_config = config["qdrant"]

        qdrant = AsyncQdrantClient(
            host=qdrant_config["host"],
            port=int(qdrant_config["port"]),
        )

        stack.push_async_callback(
            qdrant.close
        )

        # ==========================================
        # 3. Elasticsearch
        # ==========================================

        es_config = config["elasticsearch"]

        es = AsyncElasticsearch(
            f"http://{es_config['host']}:{es_config['port']}",
            request_timeout=30,
        )

        stack.push_async_callback(
            es.close
        )

        # ==========================================
        # 4. Embedding 与 LLM
        # ==========================================

        embedding = EmbeddingClient()

        stack.push_async_callback(
            embedding.close
        )

        llm = LLMClient()

        stack.push_async_callback(
            llm.close
        )

        # ==========================================
        # 5. Repository 与 Service
        # ==========================================

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

        # ==========================================
        # 6. 构建检索子图
        # ==========================================

        retrieval_graph = build_retrieval_graph(
            vector_service,
            value_client,
            llm_client=llm,
        )

        # ==========================================
        # 7. 构建完整查询工作流
        # ==========================================

        query_graph = build_query_graph(
            retrieval_graph=retrieval_graph,
            schema_service=SchemaLinkService(repository),
            executor=QueryExecutor(readonly_engine),
        )

        # 所有资源在此上下文期间保持可用
        yield query_graph

    # AsyncExitStack 自动按逆序释放资源
