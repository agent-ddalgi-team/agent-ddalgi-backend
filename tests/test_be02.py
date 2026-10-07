"""BE-02 확인: 세션 생성·조회·만료·삭제, 업로드 제한, 두 소유자 격리(QA-02), Idempotency-Key.

실행: uv run pytest
임시 폴더에 DB와 파일을 만들므로 private_runs/를 건드리지 않는다. 실제 LLM 호출은 없다.
"""
from __future__ import annotations

import io
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.config import Settings
from app.models import Brief
from app.services import sessions as sessions_service
from app.timeutil import from_iso, to_iso

BRIEF = {"purpose": "신규 거래처 제안용", "emphasis": ["공정"], "direction": "quality_process",
         "target_pages": 4, "photo_preference": "balanced"}


@pytest.fixture
def settings(tmp_path):
    return Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "test.sqlite3",
                    max_file_bytes=1024, max_files_per_session=3)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    return TestClient(app)


def _create(client: TestClient, key: str | None = None) -> dict:
    headers = {"Idempotency-Key": key} if key else {}
    r = client.post("/api/v1/sessions", json={"brief": BRIEF}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _txt(name: str, size: int = 10):
    return (name, io.BytesIO(b"a" * size), "text/plain")


# ---------- 세션 ----------

def test_create_and_get_session(client):
    s = _create(client)
    assert s["status"] == "active" and s["input_revision"] == 1 and s["selected_source_ids"] == []
    assert s["brief"] == Brief.model_validate(BRIEF).model_dump()
    assert client.cookies.get("ddalgi_owner")
    r = client.get(f"/api/v1/sessions/{s['session_id']}")
    assert r.status_code == 200 and r.json()["session_id"] == s["session_id"]


def test_editorial_brief_defaults_preserve_legacy_idempotency(client, settings):
    from app.db import connect
    from app.services.idempotency import body_hash

    headers = {"Idempotency-Key": "editorial-create"}
    created = client.post("/api/v1/sessions", json={"brief": BRIEF}, headers=headers)
    assert created.status_code == 201
    with connect(settings.db_path) as conn:
        stored = conn.execute("SELECT body_hash FROM idempotency_keys WHERE idem_key='editorial-create'").fetchone()[0]
    assert stored == body_hash({"brief": BRIEF})
    expanded = Brief.model_validate(BRIEF).model_dump()
    assert client.post("/api/v1/sessions", json={"brief": expanded}, headers=headers).json()["session_id"] == created.json()["session_id"]
    changed = {**expanded, "audience": "기술 검토자"}
    assert client.post("/api/v1/sessions", json={"brief": changed}, headers=headers).status_code == 409

    url = f"/api/v1/sessions/{created.json()['session_id']}/inputs"
    patch = {"expected_input_revision": 1, "brief": BRIEF}
    headers = {"Idempotency-Key": "editorial-patch"}
    assert client.patch(url, json=patch, headers=headers).status_code == 200
    with connect(settings.db_path) as conn:
        stored = conn.execute("SELECT body_hash FROM idempotency_keys WHERE idem_key='editorial-patch'").fetchone()[0]
    assert stored == body_hash({**patch, "selected_source_ids": None})
    assert client.patch(url, json={**patch, "brief": expanded}, headers=headers).status_code == 200
    assert client.patch(url, json={**patch, "brief": changed}, headers=headers).status_code == 409


def test_invalid_brief_returns_common_error(client):
    r = client.post("/api/v1/sessions", json={"brief": {**BRIEF, "target_pages": 5}})
    assert r.status_code == 400
    body = r.json()["error"]
    assert body["code"] == "INVALID_REQUEST" and body["request_id"].startswith("req_")
    assert body["details"]["fields"] == ["brief.target_pages"]


def test_no_cookie_is_unauthorized(app):
    anonymous = TestClient(app)
    r = anonymous.get("/api/v1/sessions/sess_whatever")
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"


def test_other_owner_cannot_see_session(app):
    """QA-02: 다른 소유자는 세션·자료·작업 어느 것도 못 본다. 존재 여부도 드러나지 않는다(404)."""
    a, b = TestClient(app), TestClient(app)
    sa = _create(a)
    _create(b)  # b도 자기 쿠키를 받는다
    up = a.post(f"/api/v1/sessions/{sa['session_id']}/sources", files=[("files", _txt("y.txt"))]).json()
    for path in (
        f"/api/v1/sessions/{sa['session_id']}",
        f"/api/v1/sessions/{sa['session_id']}/sources",
        f"/api/v1/sessions/{sa['session_id']}/jobs/{up['job_id']}",
    ):
        r = b.get(path)
        assert r.status_code == 404 and r.json()["error"]["code"] == "RESOURCE_NOT_FOUND", path
    r = b.delete(f"/api/v1/sessions/{sa['session_id']}")
    assert r.status_code == 404
    # a는 여전히 정상
    assert a.get(f"/api/v1/sessions/{sa['session_id']}").status_code == 200


def test_delete_session_blocks_access_and_removes_files(client, settings):
    s = _create(client)
    sid = s["session_id"]
    client.post(f"/api/v1/sessions/{sid}/sources", files=[("files", _txt("a.txt"))])
    assert any((settings.private_runs_dir / sid).iterdir())
    r = client.delete(f"/api/v1/sessions/{sid}")
    assert r.status_code == 200 and r.json() == {"session_id": sid, "status": "closed", "cleanup": "done"}
    assert not (settings.private_runs_dir / sid).exists()
    r = client.get(f"/api/v1/sessions/{sid}")
    assert r.status_code == 410 and r.json()["error"]["code"] == "SESSION_EXPIRED"
    # 멱등: 다시 지워도 같은 답
    assert client.delete(f"/api/v1/sessions/{sid}").status_code == 200


def test_expired_session_is_gone(client, settings):
    s = _create(client)
    sid = s["session_id"]
    from app.db import connect
    with connect(settings.db_path) as conn:
        past = to_iso(from_iso(s["expires_at"]) - timedelta(hours=48))
        conn.execute("UPDATE sessions SET expires_at=? WHERE session_id=?", (past, sid))
    r = client.get(f"/api/v1/sessions/{sid}")
    assert r.status_code == 410 and r.json()["error"]["details"]["status"] == "expired"


def test_get_does_not_extend_expiry_but_patch_does(client, settings):
    s = _create(client)
    sid = s["session_id"]
    before = client.get(f"/api/v1/sessions/{sid}").json()["expires_at"]
    from app.db import connect
    with connect(settings.db_path) as conn:
        older = to_iso(from_iso(s["last_activity_at"]) - timedelta(minutes=30))
        conn.execute("UPDATE sessions SET last_activity_at=?, expires_at=? WHERE session_id=?",
                     (older, to_iso(from_iso(before) - timedelta(minutes=30)), sid))
    after_get = client.get(f"/api/v1/sessions/{sid}").json()["expires_at"]
    assert from_iso(after_get) < from_iso(before)  # GET은 연장하지 않음
    client.patch(f"/api/v1/sessions/{sid}/inputs", json={"expected_input_revision": 1, "brief": BRIEF})
    after_patch = client.get(f"/api/v1/sessions/{sid}").json()["expires_at"]
    assert from_iso(after_patch) >= from_iso(before)  # 상태 변경은 연장


# ---------- 입력 버전 ----------

def test_patch_inputs_bumps_revision_and_conflicts(client):
    s = _create(client)
    sid = s["session_id"]
    up = client.post(f"/api/v1/sessions/{sid}/sources", files=[("files", _txt("a.txt"))]).json()
    src = up["items"][0]["source_id"]
    r = client.patch(f"/api/v1/sessions/{sid}/inputs",
                     json={"expected_input_revision": 1, "selected_source_ids": [src]})
    assert r.status_code == 200
    assert r.json() == {"session_id": sid, "input_revision": 2, "selected_source_ids": [src],
                        "preflight_invalidated": True}
    r = client.patch(f"/api/v1/sessions/{sid}/inputs", json={"expected_input_revision": 1, "brief": BRIEF})
    assert r.status_code == 409
    assert r.json()["error"]["details"] == {"expected_input_revision": 1, "current_input_revision": 2}
    r = client.patch(f"/api/v1/sessions/{sid}/inputs",
                     json={"expected_input_revision": 2, "selected_source_ids": ["src_nope"]})
    assert r.status_code == 404 and r.json()["error"]["details"]["missing_source_ids"] == ["src_nope"]


# ---------- 업로드 ----------

def test_upload_stores_and_lists_and_creates_job(client, settings):
    s = _create(client)
    sid = s["session_id"]
    r = client.post(f"/api/v1/sessions/{sid}/sources", data={"kind": "interview"},
                    files=[("files", _txt("메모.txt", 20)), ("files", _txt("b.md", 5))])
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_id"].startswith("job_") and len(body["items"]) == 2
    item = body["items"][0]
    assert item["scope"] == "session" and item["session_id"] == sid and item["kind"] == "interview"
    assert item["parse_status"] == "queued" and item["text_available"] is False  # 202 시점
    assert item["name"] == "메모.txt" and item["size_bytes"] == 20 and item["expires_at"] == s["expires_at"]
    assert "stored_path" not in item
    files_on_disk = sorted(p.name for p in (settings.private_runs_dir / sid).iterdir())
    assert len(files_on_disk) == 2 and all(n.startswith("src_") for n in files_on_disk)
    listing = client.get(f"/api/v1/sessions/{sid}/sources").json()["items"]
    assert [i["source_id"] for i in listing] == [i["source_id"] for i in body["items"]]
    # BE-03부터 TestClient는 응답 뒤 백그라운드 읽기까지 마치고 돌아오므로 Job은 이미 끝나 있다.
    job = client.get(f"/api/v1/sessions/{sid}/jobs/{body['job_id']}").json()
    assert job["kind"] == "read" and job["status"] == "succeeded"


def test_upload_rejects_unsupported_type_without_storing(client, settings):
    s = _create(client)
    sid = s["session_id"]
    r = client.post(f"/api/v1/sessions/{sid}/sources",
                    files=[("files", _txt("ok.txt")), ("files", ("bad.hwp", io.BytesIO(b"x"), "application/octet-stream"))])
    assert r.status_code == 415 and r.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
    assert r.json()["error"]["details"]["file_name"] == "bad.hwp"
    assert not (settings.private_runs_dir / sid).exists()
    assert client.get(f"/api/v1/sessions/{sid}/sources").json()["items"] == []


def test_upload_rejects_oversize_by_actual_bytes(client, settings):
    s = _create(client)
    sid = s["session_id"]
    r = client.post(f"/api/v1/sessions/{sid}/sources", files=[("files", _txt("big.txt", settings.max_file_bytes + 1))])
    assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert client.get(f"/api/v1/sessions/{sid}/sources").json()["items"] == []
    r = client.post(f"/api/v1/sessions/{sid}/sources", files=[("files", _txt("edge.txt", settings.max_file_bytes))])
    assert r.status_code == 202


def test_upload_rejects_too_many_files_per_session(client, settings):
    s = _create(client)
    sid = s["session_id"]
    client.post(f"/api/v1/sessions/{sid}/sources", files=[("files", _txt("1.txt")), ("files", _txt("2.txt"))])
    r = client.post(f"/api/v1/sessions/{sid}/sources", files=[("files", _txt("3.txt")), ("files", _txt("4.txt"))])
    assert r.status_code == 413
    assert r.json()["error"]["details"] == {"max_files_per_session": 3, "current": 2, "requested": 2}
    assert len(client.get(f"/api/v1/sessions/{sid}/sources").json()["items"]) == 2


def test_delete_selected_source_bumps_revision(client, settings):
    s = _create(client)
    sid = s["session_id"]
    up = client.post(f"/api/v1/sessions/{sid}/sources", files=[("files", _txt("a.txt"))]).json()
    src = up["items"][0]["source_id"]
    client.patch(f"/api/v1/sessions/{sid}/inputs", json={"expected_input_revision": 1, "selected_source_ids": [src]})
    r = client.delete(f"/api/v1/sessions/{sid}/sources/{src}", params={"expected_input_revision": 1})
    assert r.status_code == 409
    r = client.delete(f"/api/v1/sessions/{sid}/sources/{src}", params={"expected_input_revision": 2})
    assert r.status_code == 200 and r.json() == {"source_id": src, "deleted": True, "input_revision": 3}
    assert client.get(f"/api/v1/sessions/{sid}/sources").json()["items"] == []
    assert client.get(f"/api/v1/sessions/{sid}").json()["selected_source_ids"] == []
    assert not list((settings.private_runs_dir / sid).iterdir())
    r = client.delete(f"/api/v1/sessions/{sid}/sources/{src}", params={"expected_input_revision": 3})
    assert r.status_code == 404


# ---------- Idempotency ----------

def test_idempotent_create_returns_same_session(client):
    first = _create(client, key="k1")
    second = _create(client, key="k1")
    assert first == second
    r = client.post("/api/v1/sessions", json={"brief": {**BRIEF, "purpose": "다른 목적"}},
                    headers={"Idempotency-Key": "k1"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_idempotent_upload_does_not_store_twice(client, settings):
    s = _create(client)
    sid = s["session_id"]
    r1 = client.post(f"/api/v1/sessions/{sid}/sources", files=[("files", _txt("a.txt"))],
                     headers={"Idempotency-Key": "up1"})
    r2 = client.post(f"/api/v1/sessions/{sid}/sources", files=[("files", _txt("a.txt"))],
                     headers={"Idempotency-Key": "up1"})
    assert r1.status_code == r2.status_code == 202 and r1.json() == r2.json()
    assert len(client.get(f"/api/v1/sessions/{sid}/sources").json()["items"]) == 1
    assert len(list((settings.private_runs_dir / sid).iterdir())) == 1


def test_idempotent_patch_does_not_bump_twice(client):
    s = _create(client)
    sid = s["session_id"]
    body = {"expected_input_revision": 1, "brief": BRIEF}
    r1 = client.patch(f"/api/v1/sessions/{sid}/inputs", json=body, headers={"Idempotency-Key": "p1"})
    r2 = client.patch(f"/api/v1/sessions/{sid}/inputs", json=body, headers={"Idempotency-Key": "p1"})
    assert r1.status_code == r2.status_code == 200 and r1.json()["input_revision"] == r2.json()["input_revision"] == 2
    assert client.get(f"/api/v1/sessions/{sid}").json()["input_revision"] == 2


# ---------- 기타 ----------

def test_request_id_header_and_root(client):
    r = client.get("/")
    assert r.status_code == 200 and r.headers["X-Request-Id"].startswith("req_")
    assert client.get("/docs").status_code == 200


# ---------- SQLAlchemy 도입: 기존 SQLite 데이터·트랜잭션 계약 ----------

@pytest.fixture
def sqlalchemy_db(tmp_path):
    """새 임시 DB만 사용한다. 직접 sqlite3로 만든 테이블도 새 연결에서 읽을 수 있어야 한다."""
    import sqlite3
    from contextlib import closing

    from app.db import init_db

    db_path = tmp_path / "runs" / "adapter.sqlite3"
    init_db(db_path, db_path.parent)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("CREATE TABLE db_adapter_probe (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        conn.commit()
    return db_path


def test_sqlalchemy_core_connection_and_bound_statements(sqlalchemy_db):
    from sqlalchemy import Column, Integer, MetaData, Table, Text, bindparam, event, select
    from sqlalchemy.engine import Connection, Engine

    from app.db import connect, get_engine

    assert isinstance(get_engine(sqlalchemy_db), Engine)
    probe = Table("db_adapter_probe", MetaData(), Column("id", Integer, primary_key=True), Column("value", Text))
    value = "한글 ' OR 1=1; --"
    statements = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    with connect(sqlalchemy_db) as conn:
        assert isinstance(conn.sqlalchemy_connection, Connection)
        event.listen(conn.sqlalchemy_connection, "before_cursor_execute", record)
        try:
            conn.execute(probe.insert(), {"id": 1, "value": value})
            row = conn.execute(select(probe.c.id, probe.c.value).where(probe.c.value == bindparam("wanted")),
                               {"wanted": value}).fetchone()
            assert row["id"] == 1 and row["value"] == value
            assert conn.execute("SELECT value FROM db_adapter_probe WHERE id=:id", {"id": 1}).fetchone()[0] == value
        finally:
            event.remove(conn.sqlalchemy_connection, "before_cursor_execute", record)
    assert len(statements) == 3  # SQLAlchemy 실행 이벤트까지 도달한 실제 insert/select이다.
    assert all(value not in statement for statement in statements)


@pytest.mark.parametrize("immediate", [False, True])
def test_sqlalchemy_transaction_commits_success_and_rolls_back_failure(sqlalchemy_db, immediate):
    from app.db import connect

    with connect(sqlalchemy_db, immediate=immediate) as conn:
        conn.execute("INSERT INTO db_adapter_probe VALUES (?, ?)", (1, "보존"))
    with pytest.raises(RuntimeError, match="저장 중 실패"):
        with connect(sqlalchemy_db, immediate=immediate) as conn:
            conn.execute("UPDATE db_adapter_probe SET value=? WHERE id=?", ("취소", 1))
            conn.execute("INSERT INTO db_adapter_probe VALUES (?, ?)", (2, "부분 저장 금지"))
            raise RuntimeError("저장 중 실패")
    with connect(sqlalchemy_db, immediate=True) as conn:
        rows = conn.execute("SELECT id, value FROM db_adapter_probe ORDER BY id").fetchall()
        assert [tuple(row) for row in rows] == [(1, "보존")]
        conn.execute("UPDATE db_adapter_probe SET value='다음 요청 성공' WHERE id=1")


@pytest.mark.parametrize("immediate", [False, True])
def test_sqlalchemy_explicit_commit_survives_later_error(sqlalchemy_db, immediate):
    """만료 확정·승인 무효화 등을 커밋하고 API 오류를 내는 기존 서비스 흐름."""
    from app.db import connect

    with pytest.raises(RuntimeError, match="응답 오류"):
        with connect(sqlalchemy_db, immediate=immediate) as conn:
            conn.execute("INSERT INTO db_adapter_probe VALUES (?, ?)", (1, "확정"))
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO db_adapter_probe VALUES (?, ?)", (2, "취소"))
            raise RuntimeError("응답 오류")
    with connect(sqlalchemy_db) as conn:
        assert [tuple(row) for row in conn.execute("SELECT id, value FROM db_adapter_probe")] == [(1, "확정")]


def test_sqlalchemy_select_does_not_claim_a_sqlite_write_transaction(sqlalchemy_db):
    from app.db import connect

    with connect(sqlalchemy_db) as conn:
        assert not conn.in_transaction
        conn.execute("SELECT COUNT(*) FROM db_adapter_probe").fetchone()
        assert not conn.in_transaction  # Agent 확인 그래프의 잠금 가드에서 사용하는 실제 SQLite 상태.
        conn.execute("BEGIN IMMEDIATE")
        assert conn.in_transaction
        conn.execute("INSERT INTO db_adapter_probe VALUES (?, ?)", (1, "취소"))
        conn.rollback()
        assert not conn.in_transaction
        assert conn.execute("SELECT COUNT(*) FROM db_adapter_probe").fetchone()[0] == 0
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO db_adapter_probe VALUES (?, ?)", (2, "새 트랜잭션"))
        conn.commit()
        assert not conn.in_transaction
    with connect(sqlalchemy_db) as conn:
        assert conn.execute("SELECT id FROM db_adapter_probe").fetchone()[0] == 2


def test_sqlalchemy_immediate_lock_blocks_other_writers_and_is_released(sqlalchemy_db):
    import sqlite3
    from contextlib import closing

    from app.db import connect

    with closing(sqlite3.connect(sqlalchemy_db, timeout=0.05, isolation_level=None)) as contender:
        with connect(sqlalchemy_db, immediate=True) as conn:
            assert conn.in_transaction
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                contender.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO db_adapter_probe VALUES (?, ?)", (1, "첫 요청"))
        contender.execute("BEGIN IMMEDIATE")
        contender.execute("INSERT INTO db_adapter_probe VALUES (?, ?)", (2, "잠금 해제 후 요청"))
        contender.commit()
    with connect(sqlalchemy_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM db_adapter_probe").fetchone()[0] == 2


def test_sqlalchemy_enforces_foreign_keys_on_each_new_connection(sqlalchemy_db):
    from sqlalchemy.exc import IntegrityError

    from app.db import connect

    with connect(sqlalchemy_db) as conn:
        conn.execute("CREATE TABLE db_adapter_child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES db_adapter_probe(id))")
        conn.execute("INSERT INTO db_adapter_probe VALUES (?, ?)", (1, "부모"))
    for index, immediate in enumerate((False, True, False, True), start=1):
        with pytest.raises(IntegrityError):
            with connect(sqlalchemy_db, immediate=immediate) as conn:
                assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
                conn.execute("INSERT INTO db_adapter_child VALUES (?, ?)", (index, 999))
    with connect(sqlalchemy_db) as conn:
        conn.execute("INSERT INTO db_adapter_child VALUES (?, ?)", (1, 1))
        assert conn.execute("SELECT COUNT(*) FROM db_adapter_child").fetchone()[0] == 1


def test_sqlalchemy_rows_rowcounts_and_executemany_preserve_service_contract(sqlalchemy_db):
    from app.db import connect

    with connect(sqlalchemy_db) as conn:
        assert conn.executemany("INSERT INTO db_adapter_probe VALUES (?, ?)", []).rowcount == 0
        assert conn.execute("SELECT COUNT(*) FROM db_adapter_probe").fetchone()[0] == 0
        inserted = conn.executemany("INSERT INTO db_adapter_probe VALUES (?, ?)", [(1, "하나"), (2, "둘")])
        assert inserted.rowcount == 2
        result = conn.execute("SELECT id, value FROM db_adapter_probe ORDER BY id")
        row = result.fetchone()
        assert row["value"] == row[1] == row[-1] == "하나"
        assert row[:2] == (1, "하나") and tuple(row) == (1, "하나")
        assert list(row.keys()) == ["id", "value"] and dict(row) == {"id": 1, "value": "하나"}
        assert [tuple(item) for item in result.fetchall()] == [(2, "둘")]
        assert result.fetchone() is None
        assert [row["id"] for row in conn.execute("SELECT id FROM db_adapter_probe ORDER BY id")] == [1, 2]
        assert conn.execute("UPDATE db_adapter_probe SET value=? WHERE id=?", ("수정", 1)).rowcount == 1
        assert conn.execute("UPDATE db_adapter_probe SET value=? WHERE id=?", ("없음", 999)).rowcount == 0
        assert conn.executemany("UPDATE db_adapter_probe SET value=? WHERE id=?", []).rowcount == 0
        assert conn.execute("SELECT value FROM db_adapter_probe WHERE id=1").fetchone()[0] == "수정"


def test_sqlalchemy_handles_windows_unicode_and_url_characters_in_paths(tmp_path):
    import sqlite3
    from contextlib import closing

    from app.db import connect, init_db

    db_path = tmp_path / "한글 공백 #100%@+" / "앱 #100%@+.sqlite3"
    init_db(db_path, db_path.parent)
    with connect(db_path) as conn:
        conn.execute("CREATE TABLE path_probe (value TEXT NOT NULL)")
        conn.execute("INSERT INTO path_probe VALUES (?)", ("경로 보존",))
    assert db_path.is_file()
    assert list(db_path.parent.glob("*.sqlite3")) == [db_path]
    # Windows에서도 요청 종료 뒤 파일을 옮길 수 있어야 한다(연결 풀의 열린 핸들 없음).
    moved = db_path.with_name("옮긴 데이터.sqlite3")
    db_path.rename(moved)
    with closing(sqlite3.connect(moved)) as conn:
        assert conn.execute("SELECT value FROM path_probe").fetchone()[0] == "경로 보존"


def test_sqlalchemy_keeps_existing_v9_schema_and_data_on_reinitialization(sqlalchemy_db):
    import sqlite3
    from contextlib import closing

    from app.db import SCHEMA_VERSION, connect, init_db

    with closing(sqlite3.connect(sqlalchemy_db)) as legacy:
        legacy.execute("INSERT INTO sessions (session_id, owner_id, status, input_revision, brief_json, selected_source_ids, "
                       "created_at, last_activity_at, expires_at, demo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       ("sess_kept", "owner_kept", "active", 7, '{"purpose":"기존 작업"}', '[]', "t", "t", "2099", 1))
        legacy.execute("INSERT INTO sources (source_id, session_id, source_version, scope, name, mime_type, size_bytes, kind, "
                       "parse_status, text_available, image_available, stored_path, content_hash, created_at, origin_kind, role) "
                       "VALUES (?, NULL, 1, 'registered', ?, 'text/plain', 1, 'other', 'complete', 1, 0, ?, 'h', 't', 'demo', 'instruction')",
                       ("src_kept", "기존 자료", "registered/kept.txt"))
        legacy.commit()
        before = tuple(legacy.iterdump())
        version = legacy.execute("PRAGMA user_version").fetchone()[0]
    with connect(sqlalchemy_db) as conn:
        row = conn.execute("SELECT input_revision, brief_json, demo FROM sessions WHERE session_id=?", ("sess_kept",)).fetchone()
        assert tuple(row) == (7, '{"purpose":"기존 작업"}', 1)
        assert conn.execute("SELECT origin_kind, role FROM sources WHERE source_id=?", ("src_kept",)).fetchone()[:] == ("demo", "instruction")
    init_db(sqlalchemy_db, sqlalchemy_db.parent)
    init_db(sqlalchemy_db, sqlalchemy_db.parent)
    with closing(sqlite3.connect(sqlalchemy_db)) as legacy:
        assert legacy.execute("PRAGMA user_version").fetchone()[0] == version == SCHEMA_VERSION
        assert legacy.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert tuple(legacy.iterdump()) == before
        assert legacy.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert legacy.execute("PRAGMA foreign_key_check").fetchall() == []


def test_company_change_persists_clears_selection_and_replays(client, settings):
    s = _create(client)
    root = f"/api/v1/sessions/{s['session_id']}"
    src = client.post(root + "/sources", files=[("files", _txt("company.txt"))]).json()["items"][0]["source_id"]
    assert client.patch(root + "/inputs", json={"expected_input_revision": 1, "selected_source_ids": [src]}).status_code == 200
    change = {"expected_input_revision": 2, "brief": {**BRIEF, "target_company": " 새 회사 "}, "selected_source_ids": []}
    headers = {"Idempotency-Key": "company-change"}
    result = client.patch(root + "/inputs", json=change, headers=headers)
    assert result.status_code == 200
    assert client.patch(root + "/inputs", json=change, headers=headers).json() == result.json()
    current = client.get(root).json()
    assert current["brief"]["target_company"] == "새 회사"
    assert current["selected_source_ids"] == []
    assert current["input_revision"] == 3
    assert client.get(root + "/sources").json()["items"][0]["source_id"] == src
    with TestClient(create_app(settings)) as restored:
        restored.cookies.update(client.cookies)
        assert restored.get(root).json()["brief"]["target_company"] == "새 회사"


@pytest.mark.parametrize("name", ["  ", chr(10), "회사" + chr(9) + "명"])
def test_company_name_rejects_empty_or_controls(client, name):
    response = client.post("/api/v1/sessions", json={"brief": {**BRIEF, "target_company": name}})
    assert response.status_code == 400


def test_public_data_not_configured_is_protected_and_does_not_mutate(client, app, settings):
    from app.db import connect
    s = _create(client)
    root = f"/api/v1/sessions/{s['session_id']}"
    assert client.get(root + "/public-data").json()["status"] == "not_configured"
    assert client.post(root + "/public-data/import", json={"expected_input_revision": 1}).status_code == 422
    assert client.patch(root + "/inputs", json={"expected_input_revision": 1, "brief": {**BRIEF, "target_company": "테스트 회사"}}).status_code == 200
    before = client.get(root).json()
    for _ in range(2):
        response = client.post(root + "/public-data/import", json={"expected_input_revision": 2})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "PUBLIC_DATA_NOT_CONFIGURED"
        assert response.json()["error"]["retryable"] is False
    assert client.get(root).json() == before
    assert client.post(root + "/public-data/import", json={"expected_input_revision": 1}).status_code == 409
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM sources WHERE session_id=?", (s["session_id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM jobs WHERE session_id=?", (s["session_id"],)).fetchone()[0] == 0
    with TestClient(app) as other:
        assert other.get(root + "/public-data").status_code == 401
        _create(other)
        assert other.get(root + "/public-data").status_code == 404
        assert other.post(root + "/public-data/import", json={"expected_input_revision": 2}).status_code == 404
    client.delete(root)
    assert client.get(root + "/public-data").status_code == 410


@pytest.fixture(params=["legacy", "orm"])
def dart_client(settings, monkeypatch, request):
    import json
    import zipfile
    from dataclasses import replace
    from app.services import sources
    settings = replace(settings, dart_api_key="test-dart-key")
    if request.param == "orm":
        from app.db import init_orm_db
        init_orm_db(settings.db_path, settings.private_runs_dir)
    monkeypatch.setattr(sources, "_DART_COMPANY_CACHE", None)
    calls = []
    def zipped(name, content):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(name, content)
        return buffer.getvalue()
    def download(current, endpoint, params, limit=10 * 1024 * 1024):
        calls.append(endpoint)
        if endpoint == "corpCode.xml":
            return zipped("CORPCODE.xml", "<result><list><corp_code>00123456</corp_code><corp_name>테스트회사(주)</corp_name></list></result>")
        if endpoint == "company.json":
            return json.dumps({"status":"000", "corp_code":"00123456", "corp_name":"테스트회사(주)", "ceo_nm":"홍길동", "est_dt":"20000101"}, ensure_ascii=False).encode()
        if endpoint == "list.json":
            return json.dumps({"status":"000", "list":[{"corp_code":"00123456", "rcept_no":"20261007000001", "rcept_dt":"20261007", "report_nm":"감사보고서"}]}).encode()
        return zipped("filing.xml", "<DOCUMENT><TITLE>감사보고서</TITLE><P>제조업 부품을 생산합니다.</P></DOCUMENT>")
    monkeypatch.setattr(sources, "_dart_download", download)
    with TestClient(create_app(settings)) as client:
        session = client.post("/api/v1/sessions", json={"brief":{**BRIEF, "target_company":"테스트회사"}}).json()
        yield client, settings, session, calls, download


def test_dart_import_real_source_history_replay_and_dedup(dart_client):
    from app.db import connect
    from app.services.preflights import build_sources
    client, settings, session, calls, _ = dart_client
    root = f"/api/v1/sessions/{session['session_id']}"
    status = client.get(root + "/public-data").json()
    assert status["status"] == "ready" and status["configured_providers"] == ["dart"]
    request = {"expected_input_revision":1}
    response = client.post(root + "/public-data/import", json=request, headers={"Idempotency-Key":"dart-one"})
    assert response.status_code == 202
    job = client.get(root + "/jobs/" + response.json()["job_id"]).json()
    assert job["status"] == "succeeded" and job["result_ref"]["type"] == "sources"
    items = client.get(root + "/sources").json()["items"]
    assert len(items) == 2 and all(item["scope"] == "session" and item["origin_kind"] == "real" for item in items)
    assert all(item["text_available"] and item["warnings"][0]["code"] == "PUBLIC_OPEN_DATA" for item in items)
    assert all("test-dart-key" not in str(item) for item in items)
    assert client.get(root).json()["input_revision"] == 1
    assert client.get(root).json()["selected_source_ids"] == []
    assert client.post(root + "/public-data/import", json=request, headers={"Idempotency-Key":"dart-one"}).json() == response.json()
    assert len(calls) == 4
    assert client.post(root + "/public-data/import", json=request, headers={"Idempotency-Key":"dart-two"}).status_code == 202
    assert len(client.get(root + "/sources").json()["items"]) == 2
    source_ids = [item["source_id"] for item in items]
    assert client.patch(root + "/inputs", json={"expected_input_revision":1, "selected_source_ids":source_ids}).status_code == 200
    with connect(settings.db_path) as conn:
        selected = build_sources(conn, session["session_id"], source_ids)
        assert len(selected) == 2 and all(source.segments for source in selected)
        from app.services import db_history
        if db_history.enabled(conn):
            assert conn.execute("SELECT count(*) FROM extraction_runs WHERE session_id=?", (session["session_id"],)).fetchone()[0] == 2
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert len(list(settings.private_runs_dir.glob(session["session_id"] + "/*.dart.*"))) == 2
    assert client.delete(root).status_code == 200
    assert not (settings.private_runs_dir / session["session_id"]).exists()


@pytest.mark.parametrize("mode", ["close", "revision", "capacity", "auth", "empty"])
def test_dart_import_rejection_leaves_no_sources_or_files(dart_client, monkeypatch, mode):
    from app.db import connect
    from app.errors import ApiError
    from app.services import sources
    client, settings, session, calls, _ = dart_client
    root = f"/api/v1/sessions/{session['session_id']}"
    original = sources._dart_documents
    def documents(current, target, corp_code=None):
        if mode == "close":
            client.delete(root)
        elif mode == "revision":
            client.patch(root + "/inputs", json={"expected_input_revision":1,"brief":{**BRIEF,"target_company":"다른회사"}})
        elif mode == "auth":
            raise ApiError(503,"DART_AUTH_ERROR","인증 실패")
        elif mode == "empty":
            raise ApiError(404,"DART_COMPANY_NOT_FOUND","회사 없음")
        return original(current, target, corp_code)
    monkeypatch.setattr(sources, "_dart_documents", documents)
    if mode == "capacity":
        object.__setattr__(settings, "max_files_per_session", 1)
    response = client.post(root + "/public-data/import", json={"expected_input_revision":1})
    assert response.status_code == 202
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM sources WHERE session_id=?", (session["session_id"],)).fetchone()[0] == 0
        job = conn.execute("SELECT status,error_json FROM jobs WHERE job_id=?",(response.json()["job_id"],)).fetchone()
        assert job["status"] == ("cancelled" if mode == "close" else "failed")
    assert not list(settings.private_runs_dir.glob(session["session_id"] + "/*"))


def test_dart_concurrent_request_keys_join_same_job(dart_client, monkeypatch):
    from app.services import sources
    client, settings, session, calls, _ = dart_client
    monkeypatch.setattr(sources, "run_dart_import", lambda *args: None)
    root = f"/api/v1/sessions/{session['session_id']}"
    response = client.post(root + "/public-data/import", json={"expected_input_revision":1}, headers={"Idempotency-Key":"key-1"})
    joined = client.post(root + "/public-data/import", json={"expected_input_revision":1}, headers={"Idempotency-Key":"key-2"})
    assert response.json()["job_id"] == joined.json()["job_id"]
    assert client.post(root + "/public-data/import", json={"expected_input_revision":1}, headers={"Idempotency-Key":"key-2"}).json() == joined.json()
    assert not calls


@pytest.mark.parametrize("status,code", [("010","DART_AUTH_ERROR"),("012","DART_AUTH_ERROR"),("013","DART_DATA_NOT_FOUND"),("020","DART_RATE_LIMIT"),("800","DART_SERVICE_ERROR")])
def test_dart_provider_error_mapping_hides_key(settings, monkeypatch, status, code):
    from app.services.sources import _dart_download
    from app.errors import ApiError
    import json
    import urllib.request
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self, size):
            data = getattr(self, "data", json.dumps({"status":status,"message":"do not expose raw message"}).encode())
            self.data = b""
            return data
    class Opener:
        def open(self,*args,**kwargs): return Response()
    monkeypatch.setattr(urllib.request,"build_opener",lambda *args:Opener())
    with pytest.raises(ApiError) as exc:
        _dart_download(settings,"company.json",{"corp_code":"00123456"})
    assert exc.value.code == code and "raw message" not in exc.value.message


def test_dart_company_ambiguity_and_empty_never_choose_another_company(dart_client, monkeypatch):
    from app.services import sources
    from app.errors import ApiError
    import hashlib, time
    _, settings, _, _, _ = dart_client
    with pytest.raises(ApiError) as exc:
        sources._dart_company_code(settings,"없는회사")
    assert exc.value.code == "DART_COMPANY_NOT_FOUND"
    monkeypatch.setattr(sources,"_DART_COMPANY_CACHE",(hashlib.sha256(settings.dart_api_key.encode()).hexdigest(),time.monotonic()+100,[("00123456","테스트회사"),("00987654","테스트회사(주)")]))
    with pytest.raises(ApiError) as exc:
        sources._dart_company_code(settings,"테스트회사")
    assert exc.value.code == "DART_COMPANY_AMBIGUOUS"


def test_dart_xml_disables_entities_and_zip_never_extracts_paths():
    from app.services.sources import _dart_xml, _dart_zip_entries
    from app.errors import ApiError
    import zipfile
    with pytest.raises(ApiError):
        _dart_xml(b'<!DOCTYPE x [<!ENTITY a SYSTEM "file:///secret">]><x>&a;</x>')
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,"w") as archive:
        archive.writestr("../escape.xml","<x>safe</x>")
    assert _dart_zip_entries(buffer.getvalue()) == [("../escape.xml", b"<x>safe</x>")]


def test_dart_handles_html_in_xml_filename_without_script_or_external_loads():
    from app.services.sources import _dart_document_blocks
    data = b'<html><head><meta name="encoding"><style>secret style</style></head><body><p>Company A</p><script>do not execute or cite</script><table><tr><td>Revenue</td><td>100</td></tr></table></body></html>'
    assert _dart_document_blocks(data) == ["Company A", "Revenue", "100"]
    assert _dart_document_blocks(b'<!DOCTYPE DOCUMENT SYSTEM "http://127.0.0.1/private.dtd"><DOCUMENT><P>text</P></DOCUMENT>') == ["text"]


def test_dart_company_search_exact_first_partial_and_cache(dart_client, monkeypatch):
    import hashlib, time
    from app.services import sources
    client, settings, _, calls, _ = dart_client
    assert client.get("/api/v1/companies", params={"query":"테스트"}).json()["items"] == [{"corp_code":"00123456", "corp_name":"테스트회사(주)"}]
    assert client.get("/api/v1/companies", params={"query":"없는회사"}).json() == {"items":[]}
    assert calls == ["corpCode.xml"]
    rows = [("00123456","주식회사 테스트회사"),("00987654","큰테스트회사"),("00000002","테스트회사서비스"),("00000003","테스트회사")]
    monkeypatch.setattr(sources, "_DART_COMPANY_CACHE", (hashlib.sha256(settings.dart_api_key.encode()).hexdigest(),time.monotonic()+100,rows))
    items = client.get("/api/v1/companies", params={"query":" 테스트 회사 "}).json()["items"]
    assert [i["corp_code"] for i in items] == ["00123456","00000003","00000002","00987654"]
    assert client.get("/api/v1/companies", params={"query":"테"}).status_code == 400
    assert client.get("/api/v1/companies", params={"query":"x"*51}).status_code == 400
    assert "test-dart-key" not in str(items)


def test_dart_selected_code_disambiguates_but_cannot_select_wrong_company(dart_client, monkeypatch):
    import hashlib, time
    from app.services import sources
    from app.errors import ApiError
    client, settings, session, _, _ = dart_client
    rows=[("00123456","테스트회사(주)"),("00987654","테스트회사"),("00000001","다른회사")]
    monkeypatch.setattr(sources, "_DART_COMPANY_CACHE", (hashlib.sha256(settings.dart_api_key.encode()).hexdigest(),time.monotonic()+100,rows))
    assert sources._dart_company_code(settings,"테스트회사","00123456") == "00123456"
    with pytest.raises(ApiError) as error:
        sources._dart_company_code(settings,"테스트회사","00000001")
    assert error.value.code == "DART_COMPANY_MISMATCH"
    root=f"/api/v1/sessions/{session['session_id']}"
    change={"expected_input_revision":1,"brief":{**BRIEF,"target_company":"테스트회사","dart_corp_code":"00123456"},"selected_source_ids":[]}
    assert client.patch(root+"/inputs",json=change).status_code == 200
    assert client.get(root).json()["brief"]["dart_corp_code"] == "00123456"
    accepted=client.post(root+"/public-data/import",json={"expected_input_revision":2}).json()
    assert client.get(root+"/jobs/"+accepted["job_id"]).json()["status"] == "succeeded"
    change["expected_input_revision"]=2
    change["brief"]["dart_corp_code"]="00000001"
    assert client.patch(root+"/inputs",json=change).status_code == 200
    accepted=client.post(root+"/public-data/import",json={"expected_input_revision":3}).json()
    assert client.get(root+"/jobs/"+accepted["job_id"]).json()["error"]["code"] == "DART_COMPANY_MISMATCH"
    assert len(client.get(root+"/sources").json()["items"]) == 2


def test_dart_company_search_without_key_does_not_claim_results(client):
    response=client.get("/api/v1/companies",params={"query":"네이버"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PUBLIC_DATA_NOT_CONFIGURED"


def test_dart_naver_search_alias_preserves_canonical_identity_and_limit(dart_client, monkeypatch):
    import hashlib,time
    from app.services import sources
    client,settings,_,_,_=dart_client
    rows=[("00266961","NAVER"),("01246715","네이버랩스")]+[(f"{i:08d}",f"네이버서비스{i}") for i in range(100,130)]
    monkeypatch.setattr(sources,"_DART_COMPANY_CACHE",(hashlib.sha256(settings.dart_api_key.encode()).hexdigest(),time.monotonic()+100,rows))
    items=client.get("/api/v1/companies",params={"query":"네이버"}).json()["items"]
    assert items[0] == {"corp_code":"00266961","corp_name":"NAVER","display_name":"NAVER (네이버)"}
    assert len(items) == 20
    assert client.get("/api/v1/companies",params={"query":"naver"}).json()["items"][0]["corp_name"] == "NAVER"


@pytest.mark.parametrize("mode", ["legal_stock_names", "naver_names", "wrong_code", "wrong_names"])
def test_dart_master_stock_name_and_legal_name_use_verified_identity(dart_client, monkeypatch, mode):
    import json, hashlib, time
    from app.services import sources
    client, settings, session, calls, original = dart_client
    target = "NAVER" if mode == "naver_names" else "공시등록명"
    master_code = "00266961" if mode == "naver_names" else "00123456"
    legal = "네이버(주)" if mode == "naver_names" else "정식법인(주)"
    monkeypatch.setattr(sources, "_DART_COMPANY_CACHE", (hashlib.sha256(settings.dart_api_key.encode()).hexdigest(),time.monotonic()+100,[(master_code,target)]))
    def download(current, endpoint, params, limit=10*1024*1024):
        if endpoint == "company.json":
            return json.dumps({"status":"000", "corp_code":"00987654" if mode == "wrong_code" else master_code,
                "corp_name":legal, "stock_name":"다른공시등록명" if mode == "wrong_names" else target},ensure_ascii=False).encode()
        if endpoint == "list.json":
            from app.errors import ApiError
            raise ApiError(404,"DART_DATA_NOT_FOUND","공시 없음")
        return original(current, endpoint, params, limit)
    monkeypatch.setattr(sources, "_dart_download", download)
    root=f"/api/v1/sessions/{session['session_id']}"
    body={"expected_input_revision":1,"brief":{**BRIEF,"target_company":target,"dart_corp_code":master_code},"selected_source_ids":[]}
    assert client.patch(root+"/inputs",json=body).status_code == 200
    accepted=client.post(root+"/public-data/import",json={"expected_input_revision":2}).json()
    job=client.get(root+"/jobs/"+accepted["job_id"]).json()
    items=client.get(root+"/sources").json()["items"]
    if mode in {"wrong_code","wrong_names"}:
        assert job["status"] == "failed" and job["error"]["code"] == "DART_COMPANY_MISMATCH"
        assert items == [] and not list(settings.private_runs_dir.glob(session["session_id"]+"/*"))
    else:
        assert job["status"] == "succeeded" and len(items) == 1
        from app.db import connect
        with connect(settings.db_path) as conn:
            text="\n".join(row[0] for row in conn.execute("SELECT text FROM segments WHERE source_id=? ORDER BY ordinal",(items[0]["source_id"],)))
        assert f"회사명: {legal}" in text and f"공시 등록명: {target}" in text
