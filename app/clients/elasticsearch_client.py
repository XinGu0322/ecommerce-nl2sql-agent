import asyncio

from elasticsearch import AsyncElasticsearch

from app.config import load_config

INDEX_NAME = "ds_products_demo"

async def ensure_product_index(client: AsyncElasticsearch) -> None:
    if await client.indices.exists(index=INDEX_NAME):
        print("索引已存在：", INDEX_NAME)
        return

    await client.indices.create(
        index=INDEX_NAME,
        settings={
            "number_of_shards": 1,
            "number_of_replicas": 0,
        },
        mappings={
            "properties": {
                "product_id": {"type": "keyword"},
                "product_name": {
                    "type": "text",
                    "analyzer": "ik_max_word",
                    "search_analyzer": "ik_smart",
                },
                "category": {"type": "keyword"},
            }
        },
    )
    print("索引创建成功：", INDEX_NAME)

async def write_demo_products(client: AsyncElasticsearch) -> None:
    products = [
        {
            "product_id": "P001",
            "product_name": "华为智能手机",
            "category": "手机",
        },
        {
            "product_id": "P002",
            "product_name": "小米智能手机",
            "category": "手机",
        },
        {
            "product_id": "P003",
            "product_name": "华为笔记本电脑",
            "category": "电脑",
        },
    ]

    for product in products:
        await client.index(
            index=INDEX_NAME,
            id=product["product_id"],
            document=product,
            refresh="wait_for"
        )
    print("商品文档写入完成：", len(products))

async def search_demo_products(client: AsyncElasticsearch, keyword: str) -> None:
    result = await client.search(
        index=INDEX_NAME,
        query={
            "match": {
                "product_name":{
                    "query": keyword,
                    "operator": "and",
                }
            }
        },
        size=5,
    )

    for hit in result["hits"]["hits"]:
        print(
            f"ID={hit['_id']}, "
            f"Score={hit['_score']:.4f}, "
            f"Name={hit['_source']['product_name']}"
        )

async def search_by_category(client: AsyncElasticsearch, category: str) -> None:
    result = await client.search(
        index=INDEX_NAME,
        query={
            "term":{
                "category": category
            }
        },
        size=10,
    )
    hits = result["hits"]["hits"]
    print(f"类别={category}，命中数量={len(hits)}")

    for hit in hits:
        print(
            f"ID={hit['_id']}, "
            f"Name={hit['_source']['product_name']}"
        )

async def check_elasticsearch() -> None:
    config = load_config()["elasticsearch"]

    client = AsyncElasticsearch(
        f"http://{config['host']}:{config['port']}",
        request_timeout = 10,
    )

    try:
        info = await client.info()
        print("Elasticsearch版本：", info["version"]["number"])

        for analyzer in ("ik_smart","ik_max_word"):
            result = await client.indices.analyze(
                analyzer=analyzer,
                text="华为智能手机"
            )

            tokens = [item["token"] for item in result["tokens"]]
            print(f"{analyzer}分词结果：", tokens)

        await ensure_product_index(client)
        await write_demo_products(client)
        await search_demo_products(client, "华为手机")

        print("\n--- 全文检索 ---")
        await search_demo_products(client, "华为")
        await search_demo_products(client, "笔记本")

        print("\n--- 精确匹配 ---")
        await search_by_category(client, "手机")
        await search_by_category(client, "电脑")

    finally:
        await client.close()

if __name__ == '__main__':
    asyncio.run(check_elasticsearch())
