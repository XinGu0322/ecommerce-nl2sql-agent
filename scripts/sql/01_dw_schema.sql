
USE dw;

-- 1. 地区维度表
CREATE TABLE IF NOT EXISTS dim_region (
    region_id VARCHAR(20) PRIMARY KEY,
    province VARCHAR(50) NOT NULL,
    region_name VARCHAR(50) NOT NULL,
    country VARCHAR(50) NOT NULL DEFAULT '中国',
    INDEX idx_region_name (region_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2. 客户维度表
CREATE TABLE IF NOT EXISTS dim_customer (
    customer_id VARCHAR(20) PRIMARY KEY,
    customer_name VARCHAR(50) NOT NULL,
    gender VARCHAR(10),
    member_level VARCHAR(20) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3. 商品维度表
CREATE TABLE IF NOT EXISTS dim_product (
    product_id VARCHAR(20) PRIMARY KEY,
    product_name VARCHAR(200) NOT NULL,
    category VARCHAR(50) NOT NULL,
    brand VARCHAR(50) NOT NULL,
    INDEX idx_product_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4. 日期维度表
CREATE TABLE IF NOT EXISTS dim_date (
    date_id INT PRIMARY KEY,
    `year` INT NOT NULL,
    `quarter` VARCHAR(2) NOT NULL,
    `month` INT NOT NULL,
    `day` INT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 5. 订单事实表
CREATE TABLE IF NOT EXISTS fact_order (
    order_id VARCHAR(30) PRIMARY KEY,
    customer_id VARCHAR(20) NOT NULL,
    product_id VARCHAR(20) NOT NULL,
    date_id INT NOT NULL,
    region_id VARCHAR(20) NOT NULL,
    order_quantity INT NOT NULL,
    order_amount DECIMAL(12, 2) NOT NULL,

    CONSTRAINT fk_order_customer
        FOREIGN KEY (customer_id)
        REFERENCES dim_customer(customer_id),

    CONSTRAINT fk_order_product
        FOREIGN KEY (product_id)
        REFERENCES dim_product(product_id),

    CONSTRAINT fk_order_date
        FOREIGN KEY (date_id)
        REFERENCES dim_date(date_id),

    CONSTRAINT fk_order_region
        FOREIGN KEY (region_id)
        REFERENCES dim_region(region_id),

    CONSTRAINT chk_order_quantity
        CHECK (order_quantity > 0),

    CONSTRAINT chk_order_amount
        CHECK (order_amount >= 0),

    INDEX idx_order_region_date (region_id, date_id),
    INDEX idx_order_product_date (product_id, date_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
