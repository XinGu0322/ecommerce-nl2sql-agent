
USE meta;

-- 表级元数据
CREATE TABLE IF NOT EXISTS table_info (
    id VARCHAR(100) PRIMARY KEY,
    name VARCHAR(64) NOT NULL UNIQUE,
    table_role VARCHAR(20) NOT NULL,
    description TEXT NOT NULL,
    aliases JSON,
    updated_at TIMESTAMP NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT chk_table_role
        CHECK (table_role IN ('fact', 'dimension'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 字段级元数据
CREATE TABLE IF NOT EXISTS column_info (
    id VARCHAR(150) PRIMARY KEY,
    table_id VARCHAR(100) NOT NULL,
    name VARCHAR(64) NOT NULL,
    data_type VARCHAR(64) NOT NULL,
    column_role VARCHAR(20) NOT NULL,
    description TEXT NOT NULL,
    aliases JSON,
    examples JSON,
    referenced_table VARCHAR(64),
    referenced_column VARCHAR(64),
    updated_at TIMESTAMP NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_column_table
        FOREIGN KEY (table_id)
        REFERENCES table_info(id),

    CONSTRAINT chk_column_role
        CHECK (
            column_role IN (
                'primary_key',
                'foreign_key',
                'dimension',
                'measure'
            )
        ),

    UNIQUE KEY uk_table_column (table_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 业务指标元数据
CREATE TABLE IF NOT EXISTS metric_info (
    id VARCHAR(100) PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT NOT NULL,
    expression TEXT NOT NULL,
    aliases JSON,
    updated_at TIMESTAMP NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 指标与依赖字段之间的关联
CREATE TABLE IF NOT EXISTS column_metric (
    metric_id VARCHAR(100) NOT NULL,
    column_id VARCHAR(150) NOT NULL,

    PRIMARY KEY (metric_id, column_id),

    CONSTRAINT fk_cm_metric
        FOREIGN KEY (metric_id)
        REFERENCES metric_info(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_cm_column
        FOREIGN KEY (column_id)
        REFERENCES column_info(id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
