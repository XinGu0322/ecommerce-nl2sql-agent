
# LLM 实体类型允许对应的数据库字段
CATEGORY_FIELDS = {
    "region": ["dim_region.region_name"],
    "province": ["dim_region.province"],
    "brand": ["dim_product.brand"],
    "product": ["dim_product.product_name"],
    "category": ["dim_product.category"],
    "member_level": ["dim_customer.member_level"],
    "customer": ["dim_customer.customer_name"],
}

# 只移除与当前实体类型匹配的通用后缀
CATEGORY_SUFFIXES = {
    "region": ("地区", "区域", "大区"),
    "province": ("省份", "省"),
    "brand": ("品牌",),
    "product": ("商品", "产品"),
    "category": ("商品类别", "品类", "类别"),
    "member_level": ("会员等级", "会员级别", "会员"),
}


def build_lookup_candidates(
    text: str,
    category: str,
) -> list[str]:
    original = text.strip()
    candidates = [original]

    for suffix in CATEGORY_SUFFIXES.get(category, ()):
        if original.endswith(suffix):
            candidate = original[:-len(suffix)].strip()

            if candidate and candidate not in candidates:
                candidates.append(candidate)

    return candidates
