"""Alembic migrations use temporary databases only; no real company data is loaded."""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory

from app.db import get_engine, init_db
from app.orm_models import Base
from app.schema_migrations import (
    BASELINE_REVISION, ROOT, make_config, migration_connection, upgrade_database, validate_baseline,
)


NOW = "2026-09-28T00:00:00+00:00"


def _read(path: Path, sql: str):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(sql).fetchall()


def _dump(path: Path):
    with closing(sqlite3.connect(path)) as conn:
        return tuple(conn.iterdump())


def _seed(path: Path):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO sessions (session_id,owner_id,status,input_revision,brief_json,"
            "selected_source_ids,created_at,last_activity_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("keep_session", "owner", "active", 1, '{"purpose":"보존할 가짜 자료"}', "[]", NOW, NOW, NOW),
        )
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='input_revisions'").fetchone():
            conn.execute(
                "INSERT INTO input_revisions(session_id,revision,brief_json,created_at) VALUES(?,?,?,?)",
                ("keep_session", 1, '{"purpose":"보존할 가짜 자료"}', NOW),
            )
        conn.commit()


def _untracked(path: Path):
    Base.metadata.create_all(get_engine(path))
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA user_version=10")


def _assert_rejected_unchanged(path: Path):
    before = path.read_bytes()
    with pytest.raises(ValueError):
        upgrade_database(path)
    assert path.read_bytes() == before
    assert _read(path, "SELECT name FROM sqlite_master WHERE name='alembic_version'") == []


def _copied_config(tmp_path: Path, path: Path, *, upgrade: str, downgrade: str):
    directory = tmp_path / "migration_copy"
    shutil.copytree(ROOT / "migrations", directory, ignore=shutil.ignore_patterns("__pycache__"))
    (directory / "versions" / "20990101_01_fixture.py").write_text(
        "from alembic import op\nimport sqlalchemy as sa\n"
        "revision = '20990101_01'\n"
        f"down_revision = {BASELINE_REVISION!r}\nbranch_labels = None\ndepends_on = None\n\n"
        f"def upgrade():\n{upgrade}\n\ndef downgrade():\n{downgrade}\n",
        encoding="utf-8",
    )
    config = make_config(path)
    config.set_main_option("script_location", str(directory).replace("%", "%%"))
    return config


def test_fresh_database_has_25_empty_business_tables_and_revision(tmp_path):
    path = tmp_path / "new" / "app.sqlite3"
    upgrade_database(path)
    assert set(sa.inspect(get_engine(path)).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
    assert _read(path, "SELECT version_num FROM alembic_version") == [(BASELINE_REVISION,)]
    assert _read(path, "PRAGMA user_version") == [(10,)]
    assert _read(path, "PRAGMA journal_mode") == [("wal",)]
    assert _read(path, "PRAGMA foreign_key_check") == []
    assert all(_read(path, f'SELECT COUNT(*) FROM "{name}"') == [(0,)] for name in Base.metadata.tables)
    with get_engine(path).connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        validate_baseline(connection)


def test_adopting_untracked_v10_preserves_existing_rows_and_is_repeatable(tmp_path):
    path = tmp_path / "existing.sqlite3"
    _untracked(path)
    _seed(path)
    before = {name: _read(path, f'SELECT * FROM "{name}"') for name in Base.metadata.tables}
    upgrade_database(path)
    assert {name: _read(path, f'SELECT * FROM "{name}"') for name in Base.metadata.tables} == before
    first_dump = _dump(path)
    upgrade_database(path)
    assert _dump(path) == first_dump
    assert _read(path, "SELECT version_num FROM alembic_version") == [(BASELINE_REVISION,)]


def test_legacy_v9_is_not_changed_or_stamped(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    init_db(path, tmp_path)
    _seed(path)
    _assert_rejected_unchanged(path)
    assert _read(path, "PRAGMA user_version") == [(9,)]
    assert _read(path, "SELECT session_id FROM sessions") == [("keep_session",)]


@pytest.mark.parametrize("version", [0, 1, 11, 999])
def test_unknown_or_unversioned_existing_database_is_unchanged(tmp_path, version):
    path = tmp_path / "unknown.sqlite3"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE preserved(value TEXT)")
        conn.execute("INSERT INTO preserved VALUES('keep')")
        conn.execute(f"PRAGMA user_version={version}")
        conn.commit()
    _assert_rejected_unchanged(path)


@pytest.mark.parametrize("prefix,before,after", [
    ("CREATE TABLE sessions", "input_revision INTEGER", "input_revision TEXT"),
    ("CREATE TABLE sessions", "owner_id TEXT NOT NULL", "owner_id TEXT"),
    ("CREATE TABLE sessions", "CHECK (demo IN (0,1))", "CHECK (demo IN (0,1,2))"),
    ("CREATE TABLE sources", "REFERENCES sessions (session_id)", "REFERENCES documents (document_id)"),
    ("CREATE UNIQUE INDEX ux_exports_active", "'queued', 'generating', 'ready'", "'queued', 'ready'"),
])
def test_untracked_v10_with_changed_constraints_types_or_indexes_is_not_stamped(tmp_path, prefix, before, after):
    path = tmp_path / "changed.sqlite3"
    baseline = ScriptDirectory.from_config(make_config()).get_revision(BASELINE_REVISION).module
    changed = False
    with closing(sqlite3.connect(path)) as conn:
        for statement in baseline.DDL:
            if statement.startswith(prefix):
                assert before in statement
                statement = statement.replace(before, after)
                changed = True
            conn.execute(statement)
        conn.execute("PRAGMA user_version=10")
    assert changed
    _assert_rejected_unchanged(path)


@pytest.mark.parametrize("statement", [
    "DROP TABLE impact_reviews",
    "CREATE TABLE unexpected_table(value TEXT)",
    "CREATE TRIGGER unexpected_trigger AFTER INSERT ON sessions BEGIN SELECT 1; END",
])
def test_untracked_v10_with_missing_or_extra_schema_objects_is_not_stamped(tmp_path, statement):
    path = tmp_path / "unexpected.sqlite3"
    _untracked(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(statement)
    _assert_rejected_unchanged(path)


def test_adoption_checks_existing_foreign_key_violations_before_writing(tmp_path):
    path = tmp_path / "broken_links.sqlite3"
    _untracked(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            "INSERT INTO input_revisions(session_id,revision,brief_json,created_at) VALUES('missing',1,'{}',?)",
            (NOW,),
        )
        conn.commit()
    _assert_rejected_unchanged(path)


def test_baseline_is_frozen_even_when_current_model_metadata_changes(tmp_path):
    future = sa.Table("future_model_only", Base.metadata, sa.Column("id", sa.Integer, primary_key=True))
    try:
        path = tmp_path / "frozen.sqlite3"
        upgrade_database(path)
        assert "future_model_only" not in sa.inspect(get_engine(path)).get_table_names()
        with get_engine(path).connect() as connection:
            validate_baseline(connection)
    finally:
        Base.metadata.remove(future)


def test_failed_creation_rolls_back_prior_ddl_and_revision(tmp_path):
    path = tmp_path / "rollback.sqlite3"
    engine = get_engine(path)

    def fail_before_later_table(connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("CREATE TABLE input_revisions"):
            assert connection.exec_driver_sql("SELECT 1 FROM sqlite_master WHERE name='sessions'").first()
            raise RuntimeError("injected migration failure")

    sa.event.listen(engine, "before_cursor_execute", fail_before_later_table)
    try:
        with pytest.raises(RuntimeError, match="injected migration failure"):
            upgrade_database(path)
    finally:
        sa.event.remove(engine, "before_cursor_execute", fail_before_later_table)
    assert _read(path, "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'") == []
    assert _read(path, "PRAGMA user_version") == [(0,)]
    upgrade_database(path)
    assert _read(path, "SELECT version_num FROM alembic_version") == [(BASELINE_REVISION,)]


def test_next_migration_batch_upgrade_and_downgrade_preserve_parent_and_child_rows(tmp_path):
    path = tmp_path / "future.sqlite3"
    upgrade_database(path)
    _seed(path)
    original_sessions = _read(path, "SELECT * FROM sessions")
    original_inputs = _read(path, "SELECT * FROM input_revisions")
    config = _copied_config(
        tmp_path, path,
        upgrade="    with op.batch_alter_table('sessions', recreate='always', "
                "table_args=(sa.CheckConstraint('demo IN (0,1)'),)) as batch:\n"
                "        batch.add_column(sa.Column('migration_note', sa.Text(), nullable=True))",
        downgrade="    with op.batch_alter_table('sessions', recreate='always', "
                  "table_args=(sa.CheckConstraint('demo IN (0,1)'),)) as batch:\n"
                  "        batch.drop_column('migration_note')",
    )
    command.upgrade(config, "head")
    assert _read(path, "SELECT version_num FROM alembic_version") == [("20990101_01",)]
    assert _read(path, "SELECT migration_note FROM sessions") == [(None,)]
    assert _read(path, "SELECT * FROM input_revisions") == original_inputs
    assert _read(path, "PRAGMA foreign_key_check") == []
    with closing(sqlite3.connect(path)) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute("UPDATE sessions SET demo=2")
    command.downgrade(config, BASELINE_REVISION)
    assert _read(path, "SELECT * FROM sessions") == original_sessions
    assert _read(path, "SELECT * FROM input_revisions") == original_inputs
    assert _read(path, "PRAGMA foreign_key_check") == []
    assert _read(path, "SELECT version_num FROM alembic_version") == [(BASELINE_REVISION,)]
    with closing(sqlite3.connect(path)) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute("UPDATE sessions SET demo=2")


def test_migration_introducing_foreign_key_violation_rolls_back_schema_data_and_revision(tmp_path):
    path = tmp_path / "bad_future.sqlite3"
    upgrade_database(path)
    _seed(path)
    before = _dump(path)
    config = _copied_config(
        tmp_path, path,
        upgrade="    op.create_table('transient', sa.Column('value', sa.Text()))\n"
                "    op.execute(\"INSERT INTO input_revisions(session_id,revision,brief_json,created_at) "
                "VALUES('no_parent',1,'{}','fixture')\")",
        downgrade="    op.drop_table('transient')",
    )
    with pytest.raises(ValueError, match="foreign key violations"):
        command.upgrade(config, "head")
    assert _dump(path) == before
    with get_engine(path).connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


def test_baseline_downgrade_refuses_populated_tables_before_any_drop(tmp_path):
    path = tmp_path / "keep.sqlite3"
    upgrade_database(path)
    _seed(path)
    before = _dump(path)
    with pytest.raises(ValueError, match="populated"):
        command.downgrade(make_config(path), "base")
    assert _dump(path) == before


def test_empty_baseline_downgrade_and_reupgrade_handle_circular_foreign_keys(tmp_path):
    path = tmp_path / "empty.sqlite3"
    upgrade_database(path)
    command.downgrade(make_config(path), "base")
    assert _read(path, "PRAGMA user_version") == [(0,)]
    assert sa.inspect(get_engine(path)).get_table_names() == ["alembic_version"]
    assert _read(path, "SELECT version_num FROM alembic_version") == []
    upgrade_database(path)
    assert _read(path, "SELECT version_num FROM alembic_version") == [(BASELINE_REVISION,)]


def test_cli_explicit_korean_percent_space_path_and_schema_check(tmp_path):
    path = tmp_path / "한글 100% 자료" / "app.sqlite3"
    env = {**os.environ, "PYTHON_DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    base = [sys.executable, "-B", "-X", "utf8", "-m", "alembic", "-c", str(ROOT / "alembic.ini"),
            "-x", f"db_path={path}"]
    for arguments in (["upgrade", "head"], ["current"], ["check"]):
        result = subprocess.run(base + arguments, cwd=tmp_path, env=env, capture_output=True,
                                text=True, encoding="utf-8", timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
    assert _read(path, "SELECT version_num FROM alembic_version") == [(BASELINE_REVISION,)]


def test_revision_template_creates_editable_script_in_copied_directory(tmp_path):
    config = _copied_config(tmp_path, tmp_path / "unused.sqlite3", upgrade="    pass", downgrade="    pass")
    script = command.revision(config, message="fixture next structure", rev_id="20990102_01")
    assert script is not None and script.down_revision == "20990101_01"
    source = Path(script.path).read_text(encoding="utf-8")
    assert "def upgrade()" in source and "def downgrade()" in source
    assert not (tmp_path / "unused.sqlite3").exists()


def test_empty_stamp_is_rolled_back_and_does_not_prevent_later_creation(tmp_path):
    path = tmp_path / "no_schema.sqlite3"
    with pytest.raises(ValueError, match="without the ERD schema"):
        command.stamp(make_config(path), "head")
    assert _read(path, "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'") == []
    upgrade_database(path)
    assert _read(path, "SELECT version_num FROM alembic_version") == [(BASELINE_REVISION,)]


def test_inspection_and_autogenerate_do_not_issue_writes_or_take_write_lock(tmp_path):
    path = tmp_path / "inspect.sqlite3"
    upgrade_database(path)
    engine = get_engine(path)
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.strip().upper())

    config = make_config(path)
    copy_dir = tmp_path / "migration_copy"
    shutil.copytree(ROOT / "migrations", copy_dir, ignore=shutil.ignore_patterns("__pycache__"))
    config.set_main_option("script_location", str(copy_dir).replace("%", "%%"))
    before = _dump(path)
    sa.event.listen(engine, "before_cursor_execute", record)
    try:
        command.current(config)
        command.check(config)
        command.revision(config, message="no change fixture", autogenerate=True, rev_id="20990102_02")
    finally:
        sa.event.remove(engine, "before_cursor_execute", record)
    assert _dump(path) == before
    assert not any(statement.startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER",
                                         "BEGIN IMMEDIATE", "PRAGMA JOURNAL_MODE=")) for statement in statements)


def test_current_on_untracked_database_does_not_create_revision_table_or_change_bytes(tmp_path):
    path = tmp_path / "untracked.sqlite3"
    _untracked(path)
    before = path.read_bytes()
    command.current(make_config(path))
    assert path.read_bytes() == before
    assert _read(path, "SELECT name FROM sqlite_master WHERE name='alembic_version'") == []


def test_current_on_missing_database_does_not_create_file(tmp_path):
    path = tmp_path / "missing" / "db.sqlite3"
    with pytest.raises(ValueError, match="does not exist"):
        command.current(make_config(path))
    assert not path.parent.exists()


def test_rejecting_supplied_active_transaction_preserves_callers_changes(tmp_path):
    path = tmp_path / "owned_transaction.sqlite3"
    upgrade_database(path)
    _seed(path)
    with get_engine(path).connect() as connection:
        connection.exec_driver_sql("UPDATE sessions SET owner_id='pending_owner'")
        config = make_config(path)
        config.attributes["connection"] = connection
        with pytest.raises(ValueError, match="active transaction"):
            command.upgrade(config, "head")
        assert connection.in_transaction()
        assert connection.exec_driver_sql("SELECT owner_id FROM sessions").scalar_one() == "pending_owner"
        connection.rollback()
    assert _read(path, "SELECT owner_id FROM sessions") == [("owner",)]


def test_migration_restores_supplied_connection_foreign_key_setting(tmp_path):
    path = tmp_path / "foreign_key_setting.sqlite3"
    upgrade_database(path)
    with get_engine(path).connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        config = make_config(path)
        config.attributes["connection"] = connection
        with migration_connection(config, {}) as supplied:
            assert supplied is connection
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 0
