
import asyncio

import httpx

from app.config import load_config


class EmbeddingClient:
    def __init__(self):
        config = load_config()["embedding"]

        self.dimensions = int(config["dimensions"])
        self.client = httpx.AsyncClient(
            base_url=config["base_url"],
            timeout=httpx.Timeout(120.0, connect=5.0),
            trust_env=False,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("Embedding 输入不能包含空文本")

        response = await self.client.post(
            "/embed",
            json={"inputs": texts},
        )
        response.raise_for_status()

        vectors = response.json()

        if (
            not isinstance(vectors, list)
            or len(vectors) != len(texts)
            or any(
                not isinstance(vector, list)
                or len(vector) != self.dimensions
                for vector in vectors
            )
        ):
            raise ValueError("Embedding 返回数量或向量维度不符合预期")

        return vectors

    async def close(self) -> None:
        await self.client.aclose()


async def main():
    client = EmbeddingClient()

    try:
        texts = ["销售总额", "订单数量"]
        vectors = await client.embed(texts)

        print("文本数量：", len(vectors))
        print("向量维度：", len(vectors[0]))

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
