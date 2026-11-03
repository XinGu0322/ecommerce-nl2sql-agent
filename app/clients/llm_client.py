
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

任务：
从用户的自然语言问题中抽取查询意图。
你只负责意图理解，不负责生成 SQL。

仅输出一个合法 JSON 对象，包含以下字段：

- metric：用户提出的指标表达，未提及则为 null
- filters：筛选实体列表，每项包含 text 和 category
- time_expression：原始时间范围表达，未提及则为 null
- group_by：分组表达列表
- sort：asc、desc 或 null
- limit：正整数或 null
- needs_clarification：用户意图是否存在明显歧义

category 只能选择：
region、province、brand、product、category、
member_level、customer、unknown。

基本规则：

1. 筛选实体的 text 必须直接取自用户问题。
2. 不要擅自补充筛选条件或时间范围。
3. 不要生成 SQL、数据库表名或字段名。
4. 无法判断的实体类型使用 unknown。
5. 不要输出 Markdown 或任何 JSON 以外的文字。

指标理解规则：

6. 保留用户提出的指标表达。
7. “销售额”和“销售总额”均属于销售金额类指标，
   但 metric 应尽量保留用户原始表达。
8. “订单数量”“订单数”属于订单计数类指标。
9. 不要擅自将一个业务指标替换为其他指标。

分组理解规则：

10. “按月份统计”表示 group_by=["月份"]。
11. “按品牌统计”表示 group_by=["品牌"]。
12. “各品牌的销售额”表示按品牌分组。
13. “每个月的销售额”表示按月份分组。
14. 用户没有提出分组要求时，group_by=[]。

排序规则：

15. “最高”“最多”“最大”“排名前”表示降序，
    sort="desc"。
16. “最低”“最少”“最小”表示升序，
    sort="asc"。
17. 没有明确排序要求时，sort=null。
18. 排序方向必须根据用户问题判断，不要自行添加。

Top N 规则：

19. “前三个”“前3名”“排名前三”表示 limit=3。
20. “前五个”“前5名”表示 limit=5。
21. “最高的三个品牌”表示按品牌分组，
    sort="desc"，limit=3。
22. 没有明确数量限制时，limit=null。
23. 不要把年份、月份等时间数字错误识别为 limit。

筛选与分组区分规则：

24. “苹果品牌的销售额”表示：
    filters 中包含明确的苹果品牌筛选。

25. “各品牌的销售额”表示：
    group_by=["品牌"]，不添加品牌筛选实体。

26. “销售额最高的三个品牌”表示：
    group_by=["品牌"]，
    sort="desc"，
    limit=3，
    filters=[]。

27. 不要把泛指的“品牌”“地区”“月份”
    当作具体筛选实体。

28. 只有用户明确指定具体品牌、地区或会员等级，
    才提取对应的筛选实体。

时间表达规则：

29. “按月份统计”是分组要求，
    不是时间范围筛选，
    time_expression=null。

30. “2025年”“最近三个月”“去年”等
    明确的时间范围才填写 time_expression。

31. 不要根据分组要求擅自推断查询时间范围。

示例一：

用户问题：
查询黄金会员的订单数量

输出：
{
  "metric": "订单数量",
  "filters": [
    {
      "text": "黄金会员",
      "category": "member_level"
    }
  ],
  "time_expression": null,
  "group_by": [],
  "sort": null,
  "limit": null,
  "needs_clarification": false
}

示例二：

用户问题：
按月份统计华南地区的销售额

输出：
{
  "metric": "销售额",
  "filters": [
    {
      "text": "华南地区",
      "category": "region"
    }
  ],
  "time_expression": null,
  "group_by": ["月份"],
  "sort": null,
  "limit": null,
  "needs_clarification": false
}

示例三：

用户问题：
查询销售额最高的三个品牌

输出：
{
  "metric": "销售额",
  "filters": [],
  "time_expression": null,
  "group_by": ["品牌"],
  "sort": "desc",
  "limit": 3,
  "needs_clarification": false
}


地区对比规则：

32. “按地区统计销售额”表示 group_by=["地区"]。
33. “对比华北和华南地区的销售额”表示按地区分组。
34. 一个问题中出现多个具体地区时，应分别提取为多个 filters。
35. 不要将“华北和华南地区”作为一个筛选实体。
36. “华北和华南地区”可以分别提取“华北”和“华南地区”，
    两个 text 都必须是原始问题中的连续子串。
37. 不要因为问题中存在“对比”二字就添加排序或 Top N。
38. 地区对比的两个值不应合并为一个新的地区名称。

示例四：

用户问题：
对比华北和华南地区的销售额

输出：
{
  "metric": "销售额",
  "filters": [
    {
      "text": "华北",
      "category": "region"
    },
    {
      "text": "华南地区",
      "category": "region"
    }
  ],
  "time_expression": null,
  "group_by": ["地区"],
  "sort": null,
  "limit": null,
  "needs_clarification": false
}

严格根据用户问题抽取意图。
只输出合法 JSON 对象。
"""


class LLMClient:
    def __init__(self):

        # ==================================================
        # 1. 加载模型配置
        # ==================================================

        base_url = os.getenv("LLM_BASE_URL")
        model = os.getenv("LLM_MODEL")
        api_key = os.getenv("DASHSCOPE_API_KEY")

        if not base_url:
            raise RuntimeError(
                "缺少 LLM_BASE_URL 配置"
            )

        if not model:
            raise RuntimeError(
                "缺少 LLM_MODEL 配置"
            )

        if not api_key:
            raise RuntimeError(
                "缺少 DASHSCOPE_API_KEY 配置"
            )

        self.model = model

        # ==================================================
        # 2. 创建 HTTP 客户端
        # ==================================================

        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={
                "Authorization": f"Bearer {api_key}"
            },
            timeout=httpx.Timeout(
                60.0,
                connect=10.0,
            ),
        )

    async def analyze(
        self,
        question: str,
    ) -> QueryIntent:
        """
        使用 Qwen 提取结构化查询意图。

        返回经过 Pydantic 校验的 QueryIntent。
        """

        # ==================================================
        # 1. 基础输入校验
        # ==================================================

        question = question.strip()

        if (
            not question
            or len(question) > 500
        ):
            raise ValueError(
                "问题不能为空且不能超过500字符"
            )

        # ==================================================
        # 2. 请求 LLM
        # ==================================================

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

        # ==================================================
        # 3. 提取模型输出
        # ==================================================

        content = (
            response.json()["choices"][0]
            ["message"]["content"]
        )

        if not isinstance(content, str):
            raise ValueError(
                "LLM 未返回文本内容"
            )

        # ==================================================
        # 4. 结构化意图校验
        # ==================================================

        intent = QueryIntent.model_validate_json(
            content
        )

        # 验证检索词与原始用户输入的关系
        extract_search_terms(
            question,
            intent,
        )

        return intent

    async def close(self):
        """关闭 HTTP 连接池。"""

        await self.client.aclose()
