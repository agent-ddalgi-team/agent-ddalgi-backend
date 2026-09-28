"""ERD v10의 새 DB 생성·기존 DB 보호·실제 ORM와 버전 연결을 확인한다.

모든 DB는 pytest 임시 폴더를 사용하며 실제 회사 자료/DB를 열지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import event, inspect, select
from sqlalchemy.exc import IntegrityError

from app.db import ORM_SCHEMA_VERSION, connect, get_engine, init_db, init_orm_db, orm_session
from app.orm_models import (
    Base, ExtractionRun, InputRevision, Segment, SessionSourceSelection, Source,
    SourceVersion, WorkSession,
)


NOW = "2026-09-28T00:00:00+00:00"


@pytest.fixture
def orm_db(tmp_path):
    path = tmp_path / "runs" / "orm.sqlite3"
    init_orm_db(path, path.parent)
    return path


def _session(session_id="session_a"):
    return WorkSession(
        session_id=session_id, owner_id="owner_a", status="active", input_revision=1,
        brief_json='{"purpose":"새 자료 시험"}', selected_source_ids="[]",
        created_at=NOW, last_activity_at=NOW, expires_at="2099-01-01T00:00:00+00:00",
    )


def _seed_versions(path):
    with orm_session(path) as session:
        session.add(_session())
        session.flush()
        session.add(InputRevision(session_id="session_a", revision=1, brief_json="{}", created_at=NOW))
        for source_id in ("source_a", "source_b"):
            session.add(Source(
                source_id=source_id, source_version=1, scope="registered", name="fixture.txt",
                mime_type="text/plain", size_bytes=1, kind="other", parse_status="complete",
                text_available=1, image_available=0, stored_path=f"registered/{source_id}.txt",
                content_hash="fixture-hash", created_at=NOW,
            ))
        session.flush()
        for source_id, version in (("source_a", 1), ("source_a", 2), ("source_b", 1)):
            session.add(SourceVersion(
                source_id=source_id, version=version, original_name="fixture.txt",
                mime_type="text/plain", size_bytes=1, stored_path=f"registered/{source_id}.txt",
                content_hash=f"fixture-{version}", created_at=NOW, provenance="test_fixture",
            ))
        session.flush()
        for run_id, source_id, version in (
            ("run_a1", "source_a", 1), ("run_a2", "source_a", 2), ("run_b1", "source_b", 1),
        ):
            session.add(ExtractionRun(
                run_id=run_id, source_id=source_id, source_version=version,
                method="plain_text", status="complete", created_at=NOW, completed_at=NOW,
            ))


def test_orm_schema_has_25_tables_indexes_and_foreign_keys(orm_db):
    inspector = inspect(get_engine(orm_db))
    assert len(Base.metadata.tables) == 25
    assert set(inspector.get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
    assert {"source_versions", "extraction_runs", "input_revisions", "session_source_selections",
            "impact_reviews", "confirmations"} <= set(inspector.get_table_names())
    assert "current_run_id" in {column["name"] for column in inspector.get_columns("sources")}
    assert "confirmation_id" in {column["name"] for column in inspector.get_columns("approvals")}
    assert any(index["name"] == "ux_exports_active" and index["unique"]
               for index in inspector.get_indexes("exports"))
    with connect(orm_db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == ORM_SCHEMA_VERSION == 10
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_real_orm_persists_reads_and_rolls_back_a_partial_write(orm_db):
    with orm_session(orm_db) as session:
        original = _session()
        session.add(original)
    assert original.demo == 0
    with pytest.raises(RuntimeError, match="cancel transaction"):
        with orm_session(orm_db) as session:
            existing = session.get(WorkSession, "session_a")
            assert isinstance(existing, WorkSession)
            existing.brief_json = '{"purpose":"취소되어야 함"}'
            session.add(_session("session_rollback"))
            session.flush()
            raise RuntimeError("cancel transaction")
    with orm_session(orm_db) as session:
        rows = session.scalars(select(WorkSession)).all()
        assert len(rows) == 1
        assert rows[0].brief_json == '{"purpose":"새 자료 시험"}'


@pytest.mark.parametrize("source_id,version,run_id", [
    ("source_a", 1, "run_b1"), ("source_a", 1, "run_a2"), ("source_a", 2, "run_a1"),
])
def test_segment_cannot_point_at_another_sources_or_versions_run(orm_db, source_id, version, run_id):
    _seed_versions(orm_db)
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        with orm_session(orm_db) as session:
            session.add(Segment(
                segment_id="bad_segment", source_id=source_id, source_version=version,
                run_id=run_id, ordinal=1, locator_json="{}", text="evidence", created_at=NOW,
            ))
    with orm_session(orm_db) as session:
        assert session.get(Segment, "bad_segment") is None


def test_source_current_run_must_match_its_source_and_version(orm_db):
    _seed_versions(orm_db)
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        with orm_session(orm_db) as session:
            session.get(Source, "source_a").current_run_id = "run_a2"
    with orm_session(orm_db) as session:
        session.get(Source, "source_a").current_run_id = "run_a1"
    with orm_session(orm_db) as session:
        assert session.get(Source, "source_a").current_run_id == "run_a1"


@pytest.mark.parametrize("input_revision,source_version,run_id", [
    (99, 1, "run_a1"), (1, 99, None), (1, 1, "run_b1"), (1, 1, "run_a2"),
])
def test_input_selection_requires_existing_input_and_matching_source_run(
    orm_db, input_revision, source_version, run_id,
):
    _seed_versions(orm_db)
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        with orm_session(orm_db) as session:
            session.add(SessionSourceSelection(
                session_id="session_a", input_revision=input_revision, source_id="source_a",
                source_version=source_version, run_id=run_id,
            ))
    with orm_session(orm_db) as session:
        session.add(SessionSourceSelection(
            session_id="session_a", input_revision=1, source_id="source_a", source_version=1, run_id="run_a1",
        ))
    with orm_session(orm_db) as session:
        row = session.get(SessionSourceSelection, ("session_a", 1, "source_a"))
        assert row.run_id == "run_a1"


def test_v9_database_is_refused_without_changing_existing_data(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    init_db(path, tmp_path)
    with orm_session(path) as session:
        session.add(_session())
    before_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="empty database"):
        init_orm_db(path, tmp_path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before_hash
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
        assert conn.execute("SELECT brief_json FROM sessions").fetchone()[0] == '{"purpose":"새 자료 시험"}'
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 19


def test_v10_initialization_is_idempotent_and_does_not_downgrade(orm_db):
    _seed_versions(orm_db)
    with closing(sqlite3.connect(orm_db)) as conn:
        before = tuple(conn.iterdump())
    init_db(orm_db, orm_db.parent)
    init_orm_db(orm_db, orm_db.parent)
    init_db(orm_db, orm_db.parent)
    with closing(sqlite3.connect(orm_db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
        assert tuple(conn.iterdump()) == before
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("initializer", [init_db, init_orm_db])
def test_unknown_future_version_is_refused_without_downgrading(tmp_path, initializer):
    path = tmp_path / "future.sqlite3"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE preserved (value TEXT)")
        conn.execute("INSERT INTO preserved VALUES ('keep')")
        conn.execute("PRAGMA user_version=11")
        conn.commit()
    before = path.read_bytes()
    with pytest.raises(ValueError):
        initializer(path, tmp_path)
    assert path.read_bytes() == before


def test_unversioned_nonempty_database_is_not_overwritten(tmp_path):
    path = tmp_path / "unversioned.sqlite3"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE preserved (value TEXT)")
        conn.commit()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="empty database"):
        init_orm_db(path, tmp_path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("break_schema", [
    "DROP TABLE impact_reviews", "ALTER TABLE input_revisions DROP COLUMN brief_json",
])
def test_incomplete_v10_schema_is_detected_not_silently_recreated(orm_db, break_schema):
    with connect(orm_db) as conn:
        conn.execute(break_schema)
    for initializer in (init_db, init_orm_db):
        with pytest.raises(ValueError, match="Incomplete ORM database"):
            initializer(orm_db, orm_db.parent)
    with connect(orm_db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10


def test_failed_orm_schema_creation_rolls_back_every_table(tmp_path):
    path = tmp_path / "atomic.sqlite3"

    def fail_after_tables_created(connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.strip() == "PRAGMA user_version=10":
            assert len(inspect(connection).get_table_names()) == 26
            raise RuntimeError("simulated DDL failure")

    engine = get_engine(path)
    event.listen(engine, "before_cursor_execute", fail_after_tables_created)
    try:
        with pytest.raises(RuntimeError, match="simulated DDL failure"):
            init_orm_db(path, tmp_path)
    finally:
        event.remove(engine, "before_cursor_execute", fail_after_tables_created)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    init_orm_db(path, tmp_path)
    assert len(inspect(get_engine(path)).get_table_names()) == 26


def test_rebuild_refuses_existing_target_without_modifying_either_folder(tmp_path):
    from scripts.rebuild_database import RebuildError, rebuild

    legacy, target = tmp_path / "legacy", tmp_path / "existing"
    legacy.mkdir()
    target.mkdir()
    old_file, target_file = legacy / "keep.txt", target / "keep.txt"
    old_file.write_bytes(b"old data")
    target_file.write_bytes(b"existing target")
    with pytest.raises(RebuildError, match="이미 있습니다"):
        rebuild(target, legacy)
    assert old_file.read_bytes() == b"old data"
    assert target_file.read_bytes() == b"existing target"
    assert sorted(path.name for path in target.iterdir()) == ["keep.txt"]


def test_rebuild_refuses_old_root_ancestors_and_source_storage(tmp_path):
    from scripts.rebuild_database import RebuildError, rebuild

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    for target in (legacy, tmp_path, legacy / "registered" / "new",
                   legacy / "registered_src" / "new"):
        with pytest.raises(RebuildError):
            rebuild(target, legacy)
    assert list(legacy.iterdir()) == []


def test_rebuild_rejects_source_metadata_path_escape_before_copy(tmp_path):
    from scripts.rebuild_database import RebuildError, _prepare_sources

    legacy, bundle, package = tmp_path / "legacy", tmp_path / "bundle", tmp_path / "package"
    legacy.mkdir()
    (bundle / "06_개발전달").mkdir(parents=True)
    package.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"must stay outside")
    manifest = [{"source_id": "ESCAPE", "status": "ready", "path": "../outside.txt"}]
    (bundle / "06_개발전달" / "sources.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RebuildError, match="폴더 밖"):
        _prepare_sources(legacy, bundle, package, "real")
    assert outside.read_bytes() == b"must stay outside"
    assert list(package.iterdir()) == []


def test_rebuild_rejects_unexpected_source_hash_before_copy(tmp_path):
    from scripts.rebuild_database import RebuildError, _prepare_sources

    legacy, bundle, package = tmp_path / "legacy", tmp_path / "bundle", tmp_path / "package"
    legacy.mkdir()
    (bundle / "06_개발전달").mkdir(parents=True)
    package.mkdir()
    original = bundle / "source.txt"
    original.write_bytes(b"retained fixture")
    manifest = [{"source_id": "SRC", "status": "ready", "path": "source.txt", "sha256": "0" * 64}]
    (bundle / "06_개발전달" / "sources.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RebuildError, match="해시"):
        _prepare_sources(legacy, bundle, package, "real")
    assert original.read_bytes() == b"retained fixture"
    assert list(package.iterdir()) == []


def test_existing_standalone_photo_run_refuses_new_photo_but_allows_permission_update(orm_db, tmp_path):
    from app.config import Settings
    from app.services import registered
    from test_registered_import import _team_bundle

    settings = Settings(private_runs_dir=orm_db.parent, db_path=orm_db)
    bundle, ingest, _, _ = _team_bundle(tmp_path)
    candidates_path = ingest / "photo_candidates.json"
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    candidates[0].pop("source_id")
    candidates_path.write_text(json.dumps(candidates), encoding="utf-8")
    assert registered.run(settings, bundle, ingest).added_assets == 1
    assert registered.run(settings, bundle, ingest).added_assets == 0
    candidates[0]["approved_for_external_use"] = True
    candidates_path.write_text(json.dumps(candidates), encoding="utf-8")
    assert registered.run(settings, bundle, ingest, update_publication=True).publication_updated == 1
    with closing(sqlite3.connect(orm_db)) as conn:
        before = tuple(conn.iterdump())
        original_asset = conn.execute("SELECT asset_id, run_id, approved_for_external_use FROM assets").fetchone()
        assert original_asset[1] and original_asset[2] == 1
    before_files = {path.relative_to(orm_db.parent).as_posix(): path.read_bytes()
                    for path in (orm_db.parent / "registered").rglob("*") if path.is_file()}
    candidates[0]["approved_for_external_use"] = False  # 거부 시 기존 사진의 허가도 유지되어야 한다.
    candidates.append({**candidates[0], "photo_id": "PHOTO02"})
    candidates_path.write_text(json.dumps(candidates), encoding="utf-8")
    for dry_run in (True, False):
        with pytest.raises(registered.ImportError_) as error:
            registered.run(settings, bundle, ingest, dry_run=dry_run, update_publication=True)
        assert error.value.code == "SOURCE_HISTORY_CONFLICT"
    with closing(sqlite3.connect(orm_db)) as conn:
        assert tuple(conn.iterdump()) == before
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    after_files = {path.relative_to(orm_db.parent).as_posix(): path.read_bytes()
                   for path in (orm_db.parent / "registered").rglob("*") if path.is_file()}
    assert after_files == before_files
