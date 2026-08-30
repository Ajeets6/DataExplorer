import asyncio
import os
from pathlib import Path

import psycopg


async def migrate() -> None:
    dsn = os.environ.get("DATAEXPLORER_DATABASE_DSN")
    if not dsn:
        raise RuntimeError("DATAEXPLORER_DATABASE_DSN is required")
    migration_root = Path(__file__).resolve().parents[2] / "migrations"
    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        for migration in sorted(migration_root.glob("*.sql")):
            await connection.execute(migration.read_text(encoding="utf-8"))
            print(f"applied {migration.name}")


async def main() -> None:
    task = os.environ.get("DATAEXPLORER_WORKER_TASK", "migrate")
    if task == "migrate":
        await migrate()
        return
    raise RuntimeError(f"unsupported worker task: {task}")


if __name__ == "__main__":
    asyncio.run(main())
