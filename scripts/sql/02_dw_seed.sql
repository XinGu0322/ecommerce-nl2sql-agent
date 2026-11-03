
USE dw;

-- 地区数据
INSERT INTO dim_region
(region_id, province, region_name, country)
VALUES
('R001', '广东省', '华南', '中国'),
('R002', '浙江省', '华东', '中国'),
('R003', '四川省', '西南', '中国'),
('R004', '北京市', '华北', '中国'),
('R005', '上海市', '华东', '中国'),
('R006', '湖北省', '华中', '中国')
AS incoming
ON DUPLICATE KEY UPDATE
    province = incoming.province,
    region_name = incoming.region_name,
    country = incoming.country;

-- 客户数据
INSERT INTO dim_customer
(customer_id, customer_name, gender, member_level)
VALUES
('C001', '李伟', '男', '黄金'),
('C002', '王芳', '女', '白银'),
('C003', '张敏', '女', '黄金'),
('C004', '刘洋', '男', '青铜'),
('C005', '陈静', '女', '铂金'),
('C006', '赵磊', '男', '白银')
AS incoming
ON DUPLICATE KEY UPDATE
    customer_name = incoming.customer_name,
    gender = incoming.gender,
    member_level = incoming.member_level;

-- 商品数据
INSERT INTO dim_product
(product_id, product_name, category, brand)
VALUES
('P001', 'iPhone 15 Pro', '手机数码', '苹果'),
('P002', 'Mate 60 Pro', '手机数码', '华为'),
('P003', '戴森 V15 吸尘器', '家用电器', '戴森'),
('P004', '耐克 Air Max 270 运动鞋', '鞋靴', '耐克'),
('P005', '雀巢金牌速溶咖啡', '食品饮料', '雀巢'),
('P006', '蒙牛纯牛奶', '食品饮料', '蒙牛')
AS incoming
ON DUPLICATE KEY UPDATE
    product_name = incoming.product_name,
    category = incoming.category,
    brand = incoming.brand;

-- 日期数据
INSERT INTO dim_date
(date_id, `year`, `quarter`, `month`, `day`)
VALUES
(20250105, 2025, 'Q1', 1, 5),
(20250115, 2025, 'Q1', 1, 15),
(20250205, 2025, 'Q1', 2, 5),
(20250215, 2025, 'Q1', 2, 15),
(20250305, 2025, 'Q1', 3, 5),
(20250315, 2025, 'Q1', 3, 15)
AS incoming
ON DUPLICATE KEY UPDATE
    `year` = incoming.`year`,
    `quarter` = incoming.`quarter`,
    `month` = incoming.`month`,
    `day` = incoming.`day`;

-- 订单数据：order_amount 表示该订单记录的总金额
INSERT INTO fact_order
(order_id, customer_id, product_id, date_id,
 region_id, order_quantity, order_amount)
VALUES
('ORD001', 'C001', 'P001', 20250105, 'R004', 1, 8999.00),
('ORD002', 'C002', 'P002', 20250115, 'R004', 1, 6999.00),
('ORD003', 'C003', 'P005', 20250115, 'R002', 5, 125.00),
('ORD004', 'C004', 'P003', 20250205, 'R001', 1, 5499.00),
('ORD005', 'C005', 'P004', 20250205, 'R004', 1, 899.00),
('ORD006', 'C006', 'P006', 20250215, 'R005', 10, 50.00),
('ORD007', 'C001', 'P002', 20250215, 'R002', 1, 6999.00),
('ORD008', 'C002', 'P005', 20250305, 'R004', 4, 100.00),
('ORD009', 'C003', 'P003', 20250305, 'R006', 1, 5499.00),
('ORD010', 'C004', 'P006', 20250315, 'R003', 12, 60.00),
('ORD011', 'C005', 'P001', 20250315, 'R005', 1, 8999.00),
('ORD012', 'C006', 'P004', 20250105, 'R001', 1, 899.00)
AS incoming
ON DUPLICATE KEY UPDATE
    customer_id = incoming.customer_id,
    product_id = incoming.product_id,
    date_id = incoming.date_id,
    region_id = incoming.region_id,
    order_quantity = incoming.order_quantity,
    order_amount = incoming.order_amount;
