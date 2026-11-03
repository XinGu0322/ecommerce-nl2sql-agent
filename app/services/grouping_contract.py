
"""
NL2SQL 分组维度契约。

将自然语言分组表达映射为允许查询的数据库字段组合。
"""

GROUP_FIELDS = {
    # 按月份统计时必须同时包含年份
    "月份": (
        "dim_date.year",
        "dim_date.month",
    ),

    # 按品牌进行聚合或排名
    "品牌": (
        "dim_product.brand",
    ),
}
