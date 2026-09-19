import asyncio
import hashlib
import os
from pathlib import Path

import psycopg


async def migrate() -> None:
    dsn = os.environ.get("DATAEXPLORER_DATABASE_DSN")
    if not dsn:
        raise RuntimeError("DATAEXPLORER_DATABASE_DSN is required")
    migration_root = Path(__file__).resolve().parents[2] / "migrations"
    async with await psycopg.AsyncConnection.connect(dsn) as connection:
        await apply_migrations(connection, migration_root)


async def apply_migrations(connection, migration_root: Path) -> None:
    """Serialize deployments and commit schema changes with their version ledger."""
    async with connection.transaction():
        await connection.execute("SELECT pg_advisory_xact_lock(724619280)")
        await connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            name text PRIMARY KEY, checksum text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now())""")
        for migration in sorted(migration_root.glob("*.sql")):
            sql = migration.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            cursor = await connection.execute(
                "SELECT checksum FROM schema_migrations WHERE name = %s", (migration.name,))
            previous = await cursor.fetchone()
            if previous:
                if previous[0] != checksum:
                    raise RuntimeError(f"Applied migration changed: {migration.name}; add a new migration instead")
                continue
            await connection.execute(sql)
            await connection.execute(
                "INSERT INTO schema_migrations (name, checksum) VALUES (%s, %s)",
                (migration.name, checksum))
            print(f"applied {migration.name}")


async def main() -> None:
    task = os.environ.get("DATAEXPLORER_WORKER_TASK", "migrate")
    if task == "migrate":
        await migrate()
        return
    raise RuntimeError(f"unsupported worker task: {task}")


if __name__ == "__main__":
    asyncio.run(main())
