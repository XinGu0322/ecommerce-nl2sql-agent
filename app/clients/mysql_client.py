
import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import PROJECT_ROOT, load_config


load_dotenv(PROJECT_ROOT / ".env")


def create_mysql_engine(database: str) -> AsyncEngine:
    config = load_config()["mysql"]

    username = os.getenv("MYSQL_USER")
    password = os.getenv("MYSQL_PASSWORD")

    if not username or password is None:
        raise RuntimeError("MySQL 用户名或密码未配置")

    url = URL.create(
        drivername="mysql+asyncmy",
        username=username,
        password=password,
        host=config["host"],
        port=int(config["port"]),
        database=database,
        query={"charset": "utf8mb4"},
    )

    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


async def check_mysql() -> None:
    for database in ("dw", "meta"):
        engine = create_mysql_engine(database)

        try:
            async with engine.connect() as connection:
                result = await connection.execute(
                    text("SELECT DATABASE()")
                )
                current_database = result.scalar_one()

                result = await connection.execute(
                    text("SHOW TABLES")
                )
                tables = result.scalars().all()

                print(
                    f"数据库：{current_database}，"
                    f"包含 {len(tables)} 张表：{tables}"
                )

        finally:
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(check_mysql())
