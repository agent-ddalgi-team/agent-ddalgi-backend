"""ERD DB migration entry point. Never imports main or starts application jobs.

An existing, untracked v10 database is adopted only if its complete schema
matches the frozen baseline. The legacy v9 database is never converted here.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager, closing
from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection

ROOT = Path(__file__).resolve().parent.parent
BASELINE_REVISION = "20260928_01"
HEAD_REVISION = "20260929_01"
# Preserve quoted text exactly; ignore whitespace only between SQL tokens.
SQL_TOKEN = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`[^`]*`|\[[^\]]*\]|\w+|[^\s]", re.UNICODE)
SCHEMA_QUERY = (
    "SELECT type, name, tbl_name, sql FROM sqlite_master "
    "WHERE name NOT LIKE 'sqlite_%' AND name != 'alembic_version' "
    "AND tbl_name != 'alembic_version' ORDER BY type, name"
)


def make_config(db_path: Path | None = None) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    if db_path is not None:
        # Attributes avoid ConfigParser interpolation of '%' in filesystem paths.
        config.attributes["db_path"] = Path(db_path).resolve()
    return config


def _schema(rows) -> dict:
    return {(row[0], row[1], row[2]): tuple(SQL_TOKEN.findall(row[3] or "")) for row in rows}


@lru_cache(maxsize=1)
def _baseline_schema() -> dict:
    baseline = ScriptDirectory.from_config(make_config()).get_revision(BASELINE_REVISION).module
    with closing(sqlite3.connect(":memory:")) as reference:
        for statement in baseline.DDL:
            reference.execute(statement)
        return _schema(reference.execute(SCHEMA_QUERY))


def validate_baseline(connection: Connection | sqlite3.Connection) -> None:
    execute = connection.exec_driver_sql if isinstance(connection, Connection) else connection.execute
    expected, actual = _baseline_schema(), _schema(execute(SCHEMA_QUERY))
    if actual != expected:
        changed = sorted({key[1] for key in actual.keys() | expected.keys()
                          if actual.get(key) != expected.get(key)})
        raise ValueError("Incomplete ORM database: baseline schema mismatch in " + ", ".join(changed))
    if list(execute("PRAGMA foreign_key_check")):
        raise ValueError("ORM database has foreign key violations; baseline was not adopted")


def _preflight(connection: Connection | sqlite3.Connection) -> None:
    execute = connection.exec_driver_sql if isinstance(connection, Connection) else connection.execute
    version = execute("PRAGMA user_version").fetchone()[0]
    objects = list(execute(SCHEMA_QUERY))
    tracked = execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'").fetchone()
    has_revision = tracked and execute("SELECT 1 FROM alembic_version LIMIT 1").fetchone()
    if version == 0 and not objects:
        if has_revision:
            raise ValueError("Cannot record an Alembic revision without the ERD schema; use upgrade head")
        return
    if version not in (10, 11):
        raise ValueError("Alembic requires an empty database or ERD v10/v11; preserve the legacy DB at its separate path")
    if version == 10 and not has_revision:
        validate_baseline(connection)
    if version == 10 and has_revision and execute(
        "SELECT 1 FROM alembic_version WHERE version_num!=?", (BASELINE_REVISION,)
    ).fetchone():
        raise ValueError("ERD v10 requires its baseline revision; run upgrade head instead of stamping a later revision")
    if version == 11 and not has_revision:
        raise ValueError("ERD v11 requires its Alembic migration history; do not stamp an untracked database")
    if version == 11 and execute(
        "SELECT 1 FROM alembic_version WHERE version_num=?", (BASELINE_REVISION,)
    ).fetchone():
        raise ValueError("ERD v11 schema and Alembic baseline revision disagree; preserve the database for inspection")


def _database_path(config: Config, arguments: dict[str, str]) -> Path:
    explicit = config.attributes.get("db_path") or arguments.get("db_path")
    if explicit:
        return Path(explicit).resolve()
    from app.config import load_settings

    return load_settings().db_path.resolve()


@contextmanager
def migration_connection(config: Config, arguments: dict[str, str], *, writable: bool = True):
    from app.db import get_engine

    supplied = config.attributes.get("connection")
    path = None if supplied is not None else _database_path(config, arguments)
    if path is not None:
        if path.exists():
            # Reject incompatible databases before any writable connection or WAL change.
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as readonly:
                _preflight(readonly)
        if not writable and not path.exists():
            raise ValueError("Database does not exist; run alembic upgrade head first")
        if writable:
            path.parent.mkdir(parents=True, exist_ok=True)
    connection = supplied if supplied is not None else get_engine(path).connect()
    if connection.in_transaction():
        # A caller-owned transaction must never be rolled back by rejection.
        raise ValueError("Migration connection must not have an active transaction")
    if not writable:
        query_only = connection.exec_driver_sql("PRAGMA query_only").scalar_one()
        try:
            connection.exec_driver_sql("PRAGMA query_only=ON")
            _preflight(connection)
            yield connection
        finally:
            connection.rollback()
            connection.exec_driver_sql(f"PRAGMA query_only={query_only}")
            connection.commit()
            if supplied is None:
                connection.close()
        return
    foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
    try:
        _preflight(connection)
        connection.commit()
        # SQLite batch replacement needs FK enforcement disabled before BEGIN.
        # Other application connections keep foreign_keys=ON.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.commit()
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        _preflight(connection)  # Recheck after acquiring the write lock.
        yield connection
        _preflight(connection)
        if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
            raise ValueError("Migration introduced foreign key violations; rolling back")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.exec_driver_sql(f"PRAGMA foreign_keys={foreign_keys}")
        connection.commit()
        if supplied is None:
            connection.close()


def upgrade_database(db_path: Path, revision: str = "head") -> None:
    command.upgrade(make_config(db_path), revision)
