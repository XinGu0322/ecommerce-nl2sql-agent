
import os

from dotenv import load_dotenv
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    create_async_engine,
)

from app.config import PROJECT_ROOT, load_config


load_dotenv(PROJECT_ROOT / ".env")


def create_readonly_dw_engine() -> AsyncEngine:
    """创建仅用于执行业务 SELECT 的 MySQL Engine。"""

    config = load_config()["mysql"]

    username = os.getenv("DW_READONLY_USER")
    password = os.getenv("DW_READONLY_PASSWORD")

    if not username or not password:
        raise RuntimeError(
            "缺少 DW_READONLY_USER 或 DW_READONLY_PASSWORD"
        )

    url = URL.create(
        drivername="mysql+asyncmy",
        username=username,
        password=password,
        host=config["host"],
        port=int(config["port"]),
        database="dw",
        query={"charset": "utf8mb4"},
    )

    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=2,
        max_overflow=0,
    )
