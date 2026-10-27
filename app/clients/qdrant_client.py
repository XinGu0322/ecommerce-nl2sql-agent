import asyncio

from qdrant_client import AsyncQdrantClient, models

from app.config import load_config

async def ensure_demo_collection(client: AsyncQdrantClient) -> None:
    collection_name = "ds_vector_demo"

    if await client.collection_exists(collection_name):
        print(f"集合已存在：{collection_name}")
        return

    await client.create_collection(
        collection_name = collection_name,
        vectors_config=models.VectorParams(
            size=3,
            distance=models.Distance.COSINE,
        ),
    )
    print(f"集合创建成功：{collection_name}")

async def upsert_demo_points(client: AsyncQdrantClient) -> None:
    points = [
        models.PointStruct(
            id=1,
            vector=[1.0, 0.0, 0.0],
            payload={"name" : "A:完全相同"}
        ),
        models.PointStruct(
            id=2,
            vector=[0.8, 0.6, 0.0],
            payload={"name" : "B:方向接近"}
        ),
        models.PointStruct(
            id=3,
            vector=[0.0, 1.0, 0.0],
            payload={"name" : "C:方向正交"}
        ),
    ]

    result = await client.upsert(
        collection_name="ds_vector_demo",
        points=points,
        wait=True,
    )
    print("写入状态：", result.status)

async def query_demo_points(client: AsyncQdrantClient) -> None:
    result = await client.query_points(
        collection_name="ds_vector_demo",
        query=[1.0, 0.0, 0.0],
        limit=3,
        with_payload=True,
    )

    for point in result.points:
        print(
            f"ID={point.id}, "
            f"Score={point.score:.4f}, "
            f"Name={point.payload['name']}"
        )

async def check_qdrant() -> None:
    config = load_config()["qdrant"]

    client = AsyncQdrantClient(
        host=config["host"],
        port=config["port"],
        timeout=10,
    )

    try:
        await ensure_demo_collection(client)
        await upsert_demo_points(client)
        await query_demo_points(client)

        result = await client.get_collections()
        collections = [item.name for item in result.collections]
        print("向量集合：", collections)
    finally:
        await client.close()

if __name__ == '__main__':
    asyncio.run(check_qdrant())