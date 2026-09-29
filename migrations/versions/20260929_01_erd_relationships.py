"""Enforce the remaining 16 FK labels from the ERD without replacing data.

Revision ID: 20260929_01
Revises: 20260928_01

Use the frozen v10 DDL, never current ORM metadata. SQLite table rebuilds keep
rowids, all original columns, CHECK/UNIQUE constraints and explicit indexes.
The migration environment owns the transaction and disables FK actions during
rebuilds; all references are checked before commit. Existing invalid links are
rejected, not guessed, dropped or silently repaired.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing

from alembic import op
from alembic.script import ScriptDirectory

from app.schema_migrations import SCHEMA_QUERY, _schema, validate_baseline

revision = "20260929_01"
down_revision = "20260928_01"
branch_labels = None
depends_on = None

# table, constraint, child columns, parent table, parent columns, deferred
REFERENCES = (
    ("preflights", "fk_preflights_input_revision", ("session_id", "input_revision"),
     "input_revisions", ("session_id", "revision"), False),
    ("document_revisions", "fk_document_revisions_input_revision", ("session_id", "input_revision"),
     "input_revisions", ("session_id", "revision"), False),
    ("proposals", "fk_proposals_base_revision", ("document_id", "base_document_revision", "session_id"),
     "document_revisions", ("document_id", "revision", "session_id"), False),
    ("validations", "fk_validations_revision", ("document_id", "document_revision", "session_id"),
     "document_revisions", ("document_id", "revision", "session_id"), False),
    ("artifacts", "fk_artifacts_revision", ("document_id", "document_revision", "session_id"),
     "document_revisions", ("document_id", "revision", "session_id"), False),
    ("approvals", "fk_approvals_validation", ("validation_id",), "validations", ("validation_id",), False),
    ("approvals", "fk_approvals_layout", ("layout_check_id",), "layout_checks", ("layout_check_id",), False),
    ("approvals", "fk_approvals_artifact", ("artifact_id",), "artifacts", ("artifact_id",), False),
    ("exports", "fk_exports_approval", ("approval_id",), "approvals", ("approval_id",), False),
    ("exports", "fk_exports_artifact", ("artifact_id",), "artifacts", ("artifact_id",), False),
    ("layout_checks", "fk_layout_checks_revision", ("document_id", "document_revision", "session_id"),
     "document_revisions", ("document_id", "revision", "session_id"), False),
    ("layout_checks", "fk_layout_checks_artifact", ("artifact_id",), "artifacts", ("artifact_id",), False),
    ("layout_previews", "fk_layout_previews_layout", ("layout_check_id",),
     "layout_checks", ("layout_check_id",), True),
    ("layout_previews", "fk_layout_previews_artifact", ("artifact_id",), "artifacts", ("artifact_id",), False),
    ("idempotency_keys", "fk_idempotency_keys_session", ("session_id",), "sessions", ("session_id",), False),
    ("cleanup_queue", "fk_cleanup_queue_session", ("session_id",), "sessions", ("session_id",), False),
)
TABLES = tuple(dict.fromkeys(item[0] for item in REFERENCES))


def _quote(value: str) -> str:
    # All identifiers come from the frozen migration or PRAGMA table_info.
    return '"' + value.replace('"', '""') + '"'


def _names(values) -> str:
    return ", ".join(_quote(value) for value in values)


def _baseline_ddl() -> tuple[str, ...]:
    return ScriptDirectory.from_config(op.get_context().config).get_revision(down_revision).module.DDL


def _strengthen(statement: str) -> str:
    for table in TABLES:
        if not statement.startswith(f"CREATE TABLE {table} ("):
            continue
        clauses = []
        for child, name, columns, parent, parent_columns, deferred in REFERENCES:
            if child != table:
                continue
            clause = (f"CONSTRAINT {_quote(name)} FOREIGN KEY ({_names(columns)}) "
                      f"REFERENCES {_quote(parent)} ({_names(parent_columns)})")
            if deferred:
                clause += " DEFERRABLE INITIALLY DEFERRED"
            clauses.append(clause)
        before, after = statement.rsplit(")", 1)
        return before.rstrip() + ",\n\t" + ",\n\t".join(clauses) + "\n)" + after
    return statement


def _check_existing_references(connection) -> None:
    for table, name, columns, parent, parent_columns, _ in REFERENCES:
        present = " AND ".join(f"child.{_quote(column)} IS NOT NULL" for column in columns)
        match = " AND ".join(f"parent.{_quote(target)} = child.{_quote(column)}"
                             for column, target in zip(columns, parent_columns))
        statement = (f"SELECT 1 FROM {_quote(table)} AS child WHERE {present} "
                     f"AND NOT EXISTS (SELECT 1 FROM {_quote(parent)} AS parent WHERE {match}) LIMIT 1")
        if connection.exec_driver_sql(statement).first() is not None:
            # Do not include IDs, file names or private content in diagnostics.
            raise ValueError(f"Cannot apply ERD relationships: invalid existing reference {name}; no data was changed")


def _rebuild(connection, ddl: tuple[str, ...], *, strengthened: bool) -> None:
    if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 0:
        raise ValueError("ERD relationship migration requires the managed migration transaction")
    for table in TABLES:
        create = next(statement for statement in ddl if statement.startswith(f"CREATE TABLE {table} ("))
        indexes = [row[0] for row in connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL ORDER BY name",
            (table,),
        )]
        columns = [row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({_quote(table)})")]
        copy_table = f"__erd_relationships_{table}"
        connection.exec_driver_sql(
            f"CREATE TEMP TABLE {_quote(copy_table)} AS SELECT rowid AS __saved_rowid__, * FROM {_quote(table)}")
        connection.exec_driver_sql(f"DROP TABLE {_quote(table)}")
        connection.exec_driver_sql(_strengthen(create) if strengthened else create)
        connection.exec_driver_sql(
            f"INSERT INTO {_quote(table)} (rowid, {_names(columns)}) "
            f"SELECT __saved_rowid__, {_names(columns)} FROM temp.{_quote(copy_table)} ORDER BY __saved_rowid__")
        connection.exec_driver_sql(f"DROP TABLE temp.{_quote(copy_table)}")
        for statement in indexes:
            connection.exec_driver_sql(statement)


def upgrade() -> None:
    connection = op.get_bind()
    validate_baseline(connection)
    _check_existing_references(connection)
    _rebuild(connection, _baseline_ddl(), strengthened=True)
    connection.exec_driver_sql("PRAGMA user_version=11")


def downgrade() -> None:
    connection = op.get_bind()
    ddl = _baseline_ddl()
    with closing(sqlite3.connect(":memory:")) as expected:
        for statement in ddl:
            expected.execute(_strengthen(statement))
        if _schema(connection.exec_driver_sql(SCHEMA_QUERY)) != _schema(expected.execute(SCHEMA_QUERY)):
            raise ValueError("Cannot downgrade an altered ERD v11 schema")
    _rebuild(connection, ddl, strengthened=False)
    connection.exec_driver_sql("PRAGMA user_version=10")
    validate_baseline(connection)
