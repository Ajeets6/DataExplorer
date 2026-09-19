from contextlib import asynccontextmanager

import pytest

from dataexplorer.worker import apply_migrations


class Database:
    def __init__(self):
        self.ledger = {}
        self.executed = []
        self.row = None

    @asynccontextmanager
    async def transaction(self):
        ledger, executed = self.ledger.copy(), self.executed.copy()
        try:
            yield
        except Exception:
            self.ledger, self.executed = ledger, executed
            raise

    async def execute(self, sql, params=None):
        if sql.startswith("SELECT checksum"):
            value = self.ledger.get(params[0])
            self.row = (value,) if value else None
        elif sql.startswith("INSERT INTO schema_migrations"):
            self.ledger[params[0]] = params[1]
        elif sql == "INVALID SQL":
            raise RuntimeError("syntax error")
        elif not sql.startswith(("SELECT pg_advisory", "CREATE TABLE IF NOT EXISTS schema_migrations")):
            self.executed.append(sql)
        return self

    async def fetchone(self):
        return self.row


async def test_migration_replay_and_changed_file_are_safe(tmp_path):
    first = tmp_path / "001.sql"
    first.write_text("CREATE TABLE example (id int)")
    db = Database()
    await apply_migrations(db, tmp_path)
    await apply_migrations(db, tmp_path)
    assert len(db.executed) == 1
    first.write_text("DROP TABLE example")
    with pytest.raises(RuntimeError, match="Applied migration changed"):
        await apply_migrations(db, tmp_path)
    assert len(db.executed) == 1


async def test_failed_batch_does_not_record_partial_migrations(tmp_path):
    (tmp_path / "001.sql").write_text("CREATE TABLE example (id int)")
    (tmp_path / "002.sql").write_text("INVALID SQL")
    db = Database()
    with pytest.raises(RuntimeError, match="syntax error"):
        await apply_migrations(db, tmp_path)
    assert db.ledger == {}
    assert db.executed == []
