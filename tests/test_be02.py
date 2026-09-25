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
    assert s["brief"] == BRIEF
    assert client.cookies.get("ddalgi_owner")
    r = client.get(f"/api/v1/sessions/{s['session_id']}")
    assert r.status_code == 200 and r.json()["session_id"] == s["session_id"]


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
    assert item["parse_status"] == "queued" and item["text_available"] is False
    assert item["name"] == "메모.txt" and item["size_bytes"] == 20 and item["expires_at"] == s["expires_at"]
    assert "stored_path" not in item
    files_on_disk = sorted(p.name for p in (settings.private_runs_dir / sid).iterdir())
    assert len(files_on_disk) == 2 and all(n.startswith("src_") for n in files_on_disk)
    listing = client.get(f"/api/v1/sessions/{sid}/sources").json()["items"]
    assert [i["source_id"] for i in listing] == [i["source_id"] for i in body["items"]]
    job = client.get(f"/api/v1/sessions/{sid}/jobs/{body['job_id']}").json()
    assert job["kind"] == "read" and job["status"] == "queued" and job["progress"]["stage"] == "queued"


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
