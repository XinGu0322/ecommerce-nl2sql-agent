
import os

import httpx
from dotenv import load_dotenv

from app.config import PROJECT_ROOT
from app.agent.intent_schema import (
    QueryIntent,
    extract_search_terms,
)

load_dotenv(PROJECT_ROOT / ".env")


SYSTEM_PROMPT = """
你是电商智能问数系统的问题理解模块。

任务：从用户的自然语言问题中抽取查询意图。

仅输出一个合法 JSON 对象，包含以下字段：
- metric：用户提出的指标表达，未提及则为 null
- filters：筛选实体列表，每项包含 text 和 category
- time_expression：原始时间表达，未提及则为 null
- group_by：分组表达列表
- sort：asc、desc 或 null
- limit：正整数或 null
- needs_clarification：用户意图是否存在明显歧义

category 只能选择：
region、province、brand、product、category、
member_level、customer、unknown。

规则：
1. 筛选实体的 text 必须直接取自用户问题。
2. 不要擅自补充筛选条件或时间范围。
3. 不要生成 SQL、数据库表名或字段名。
4. 无法判断的实体类型使用 unknown。
5. 不要输出 Markdown 或任何 JSON 以外的文字。
"""


class LLMClient:
    def __init__(self):
        base_url = os.getenv("LLM_BASE_URL")
        model = os.getenv("LLM_MODEL")
        api_key = os.getenv("DASHSCOPE_API_KEY")

        self.model = model

        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

        self.model = model
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

    async def analyze(self, question: str) -> QueryIntent:
        question = question.strip()

        if not question or len(question) > 500:
            raise ValueError("问题不能为空且不能超过500字符")

        response = await self.client.post(
            "chat/completions",
            json={
                "model": self.model,
                "temperature": 0,
                "stream": False,
                "enable_thinking": False,
                "response_format": {
                    "type": "json_object"
                },
                "messages": [
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": question,
                    },
                ],
            },
        )

        response.raise_for_status()

        content = response.json()["choices"][0]["message"]["content"]

        if not isinstance(content, str):
            raise ValueError("LLM 未返回文本内容")

        intent = QueryIntent.model_validate_json(content)

        extract_search_terms(question, intent)

        return intent

    async def close(self):
        await self.client.aclose()
