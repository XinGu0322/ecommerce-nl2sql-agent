
"""
NL2SQL 分组维度契约。

将自然语言分组表达映射为允许查询的数据库字段组合。
"""

GROUP_FIELDS = {
    "月份": (
        "dim_date.year",
        "dim_date.month",
    ),
    "品牌": (
        "dim_product.brand",
    ),
    "地区": (
        "dim_region.region_name",
    ),
}

