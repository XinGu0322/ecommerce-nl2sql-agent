
import asyncio

from app.clients.llm_client import LLMClient
from app.agent.intent_schema import extract_search_terms


QUESTIONS = [
    "统计华北地区苹果品牌的销售总额",
    "查询黄金会员的订单数量",
    "按月份统计华南地区的销售额",
]


async def main():
    client = LLMClient()

    try:
        for question in QUESTIONS:
            print(f"\n用户问题：{question}")

            intent = await client.analyze(question)

            print("结构化意图：")
            print(intent.model_dump_json(indent=2))

            terms = extract_search_terms(question, intent)
            print("检索关键词：", terms)

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
