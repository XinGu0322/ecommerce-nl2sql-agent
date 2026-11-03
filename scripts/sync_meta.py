
import asyncio

import yaml

from app.config import PROJECT_ROOT
from app.clients.mysql_client import create_mysql_engine
from app.repositories.meta_repository import MetaRepository
from app.services.meta_sync_service import MetaSyncService


async def main() -> None:
    config_path = PROJECT_ROOT / "conf" / "meta_config.yaml"

    with config_path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    dw_engine = create_mysql_engine("dw")
    meta_engine = create_mysql_engine("meta")

    try:
        repository = MetaRepository(dw_engine, meta_engine)
        service = MetaSyncService(repository, config)

        result = await service.sync()
        print("元数据同步成功：", result)

    finally:
        await dw_engine.dispose()
        await meta_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
