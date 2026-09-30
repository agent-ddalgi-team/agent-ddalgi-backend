"""시연 수용 기반: 가짜 자료만 사용하며 실제 Agent 해석·실제 자료 적재를 검증한 것으로 보지 않는다.

규칙 테스트는 브라우저 없이 실행한다. 승인/Export 테스트의 배치 행과 PDF 바이트는
격리 DB에 명시적으로 심은 테스트 데이터이며 실제 배치 검사 성공을 뜻하지 않는다.
"""
from __future__ import annotations

import hashlib
import io
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.config import Settings
from app.db import connect, init_db
from app.models import Block, Document, Page
from app.services import ai_jobs, export_render, exports, idempotency, layout_check_jobs, layout_checks, preflights, reading, refs, sessions
from app.timeutil import now, to_iso
from test_be09 import Flow as BaseFlow

BRIEF = {"purpose": "가상 자료 테스트", "emphasis": [], "direction": "balanced", "target_pages": 4,
         "photo_preference": "balanced"}
TEXT = "회사명: 예시 회사\n회사 개요: 예시 회사는 가상 부품 표면처리와 검사를 하는 테스트 기업입니다.\n사업 분야: 가상 부품 표면처리\n".encode()
INSTRUCTION = "인증 중심 소개서, 납기 설명 제외\n인증: 가상 인증 보유\n".encode()
PAST = "2000-01-01T00:00:00Z"


@pytest.fixture
def settings(tmp_path):
    return Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "test.sqlite3",
                    demo_mode=True, cleanup_sweep_interval_s=0,
                    export_browser_path=str(tmp_path / "no-browser" / "chrome.exe"))


@pytest.fixture
def app(settings):
    return create_app(settings)


def _row(settings, sql, *args):
    with connect(settings.db_path) as conn:
        return conn.execute(sql, args).fetchone()


def _count(settings, table, sid):
    return _row(settings, f"SELECT COUNT(*) FROM {table} WHERE session_id=?", sid)[0]


def _create(client, *, demo=True, key=None):
    r = client.post("/api/v1/sessions", json={"brief": BRIEF, "demo": demo},
                    headers={"Idempotency-Key": key} if key else {})
    assert r.status_code == 201, r.text
    assert r.json()["demo"] is demo
    return r.json()["session_id"]


def _upload(client, sid, *, role=None, key=None, data=TEXT):
    return client.post(f"/api/v1/sessions/{sid}/sources",
                       files=[("files", ("example.txt", io.BytesIO(data), "text/plain"))],
                       data={"role": role} if role is not None else {},
                       headers={"Idempotency-Key": key} if key else {})


def _off_client(settings, client):
    return TestClient(create_app(replace(settings, demo_mode=False)), cookies=dict(client.cookies))


def _assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code, response.text


def test_demo_creation_requires_mode_but_ordinary_session_does_not(settings):
    c = TestClient(create_app(replace(settings, demo_mode=False)))
    _assert_error(c.post("/api/v1/sessions", json={"brief": BRIEF, "demo": True}), 403, "DEMO_MODE_DISABLED")
    sid = _create(c, demo=False)
    assert _row(settings, "SELECT demo FROM sessions WHERE session_id=?", sid)[0] == 0
    assert _row(settings, "SELECT COUNT(*) FROM sessions")[0] == 1


def test_demo_off_blocks_without_deleting_or_touching_lifetime(app, settings):
    c = TestClient(app)
    sid = _create(c)
    upload = _upload(c, sid)
    assert upload.status_code == 202, upload.text
    before = dict(_row(settings, "SELECT * FROM sessions WHERE session_id=?", sid))
    off = _off_client(settings, c)
    for response in (off.get(f"/api/v1/sessions/{sid}"), off.get(f"/api/v1/sessions/{sid}/sources"),
                     off.patch(f"/api/v1/sessions/{sid}/inputs", json={"expected_input_revision": 1, "brief": BRIEF}),
                     _upload(off, sid)):
        _assert_error(response, 403, "DEMO_MODE_DISABLED")
    assert dict(_row(settings, "SELECT * FROM sessions WHERE session_id=?", sid)) == before
    assert _count(settings, "segments", sid) > 0
    assert (settings.private_runs_dir / sid).is_dir()
    assert _count(settings, "cleanup_queue", sid) == 0
    assert c.get(f"/api/v1/sessions/{sid}").status_code == 200


def test_owner_can_delete_disabled_demo_but_other_owner_cannot(app, settings):
    c = TestClient(app)
    sid = _create(c)
    assert _upload(c, sid).status_code == 202
    off = _off_client(settings, c)
    stranger = TestClient(off.app)
    _create(stranger, demo=False)
    _assert_error(stranger.get(f"/api/v1/sessions/{sid}"), 404, "RESOURCE_NOT_FOUND")
    _assert_error(stranger.delete(f"/api/v1/sessions/{sid}"), 404, "RESOURCE_NOT_FOUND")
    result = off.delete(f"/api/v1/sessions/{sid}")
    assert result.status_code == 200 and result.json()["cleanup"] == "done", result.text
    assert not (settings.private_runs_dir / sid).exists()
    row = _row(settings, "SELECT status,purged_at FROM sessions WHERE session_id=?", sid)
    assert row["status"] == "closed" and row["purged_at"]
    _assert_error(off.get(f"/api/v1/sessions/{sid}"), 410, "SESSION_EXPIRED")


@pytest.mark.parametrize("terminal", ["expired", "closed"])
def test_terminal_demo_has_410_priority_and_committed_purge_without_sweep(app, settings, terminal):
    c = TestClient(app)
    sid = _create(c, key="create-demo")
    assert _upload(c, sid).status_code == 202
    off = _off_client(settings, c)
    with connect(settings.db_path) as conn:
        if terminal == "expired":
            conn.execute("UPDATE sessions SET expires_at=? WHERE session_id=?", (PAST, sid))
        else:
            conn.execute("UPDATE sessions SET status='closed' WHERE session_id=?", (sid,))
    response = off.get(f"/api/v1/sessions/{sid}")
    _assert_error(response, 410, "SESSION_EXPIRED")
    assert response.json()["error"]["details"]["status"] == terminal
    row = _row(settings, "SELECT * FROM sessions WHERE session_id=?", sid)
    assert row["status"] == terminal and row["purged_at"] and row["brief_json"] == "{}"
    assert _count(settings, "segments", sid) == 0
    _assert_error(off.post("/api/v1/sessions", json={"brief": BRIEF, "demo": True},
                           headers={"Idempotency-Key": "create-demo"}), 410, "SESSION_EXPIRED")


def test_demo_off_blocks_create_upload_and_patch_cached_success_without_sweep(app, settings):
    c = TestClient(app)
    sid = _create(c, key="create")
    up = _upload(c, sid, key="upload")
    assert up.status_code == 202
    body = {"expected_input_revision": 1, "selected_source_ids": [up.json()["items"][0]["source_id"]]}
    patch = c.patch(f"/api/v1/sessions/{sid}/inputs", json=body, headers={"Idempotency-Key": "patch"})
    assert patch.status_code == 200, patch.text
    before = (_count(settings, "sources", sid), _count(settings, "jobs", sid),
              dict(_row(settings, "SELECT * FROM sessions WHERE session_id=?", sid)))
    off = _off_client(settings, c)
    responses = [off.post("/api/v1/sessions", json={"brief": BRIEF, "demo": True}, headers={"Idempotency-Key": "create"}),
                 _upload(off, sid, key="upload"),
                 off.patch(f"/api/v1/sessions/{sid}/inputs", json=body, headers={"Idempotency-Key": "patch"})]
    for response in responses:
        _assert_error(response, 403, "DEMO_MODE_DISABLED")
    assert before == (_count(settings, "sources", sid), _count(settings, "jobs", sid),
                      dict(_row(settings, "SELECT * FROM sessions WHERE session_id=?", sid)))


def test_session_demo_is_immutable_through_input_patch(app, settings):
    c = TestClient(app)
    sid = _create(c)
    r = c.patch(f"/api/v1/sessions/{sid}/inputs", json={"expected_input_revision": 1, "demo": False})
    _assert_error(r, 400, "INVALID_REQUEST")
    assert _row(settings, "SELECT demo,input_revision FROM sessions WHERE session_id=?", sid)[:] == (1, 1)


@pytest.mark.parametrize("first,second", [(None, "evidence"), ("evidence", None), ("instruction", "instruction")])
def test_upload_role_replay_preserves_first_result(app, settings, first, second):
    c = TestClient(app)
    sid = _create(c)
    initial = _upload(c, sid, role=first, key="upload")
    repeat = _upload(c, sid, role=second, key="upload")
    assert initial.status_code == repeat.status_code == 202, (initial.text, repeat.text)
    assert initial.json() == repeat.json()
    assert _count(settings, "sources", sid) == _count(settings, "jobs", sid) == 1
    assert _row(settings, "SELECT role FROM sources WHERE session_id=?", sid)[0] == (first or "evidence")


@pytest.mark.parametrize("first,second", [("evidence", "instruction"), ("instruction", "evidence")])
def test_upload_role_conflict_adds_no_source_or_job(app, settings, first, second):
    c = TestClient(app)
    sid = _create(c)
    assert _upload(c, sid, role=first, key="upload").status_code == 202
    _assert_error(_upload(c, sid, role=second, key="upload"), 409, "IDEMPOTENCY_KEY_CONFLICT")
    assert _count(settings, "sources", sid) == _count(settings, "jobs", sid) == 1


def test_upload_evidence_replays_legacy_digest(app, settings):
    c = TestClient(app)
    sid = _create(c)
    path = f"/api/v1/sessions/{sid}/sources"
    digest = hashlib.sha256(json.dumps([("example.txt", hashlib.sha256(TEXT).hexdigest())]).encode()).hexdigest()
    uploaded = _upload(c, sid)
    assert uploaded.status_code == 202, uploaded.text
    original = uploaded.json()
    # 실제 업로드 성공에는 파일 항목이 있다. 역할 필드 도입 전의 응답만 재현한다.
    for item in original["items"]:
        item.pop("role", None)
    with connect(settings.db_path) as conn:
        owner = conn.execute("SELECT owner_id FROM sessions WHERE session_id=?", (sid,)).fetchone()[0]
        idempotency.remember(conn, "legacy", owner, path, digest, 202, original, session_id=sid)
    for role in (None, "evidence"):
        r = _upload(c, sid, role=role, key="legacy")
        assert r.status_code == 202 and r.json() == original, r.text
    assert _count(settings, "sources", sid) == _count(settings, "jobs", sid) == 1


def test_instruction_is_stored_but_never_company_evidence(app, settings):
    c = TestClient(app)
    sid = _create(c)
    upload = _upload(c, sid, role="instruction", data=INSTRUCTION)
    assert upload.status_code == 202, upload.text
    source_id = upload.json()["items"][0]["source_id"]
    assert _row(settings, "SELECT role,parse_status FROM sources WHERE source_id=?", source_id)[:] == ("instruction", "complete")
    assert _count(settings, "segments", sid) > 0  # 읽기만 수행, 자연어 조건 해석은 범위 밖
    select = c.patch(f"/api/v1/sessions/{sid}/inputs",
                     json={"expected_input_revision": 1, "selected_source_ids": [source_id]})
    _assert_error(select, 422, "SOURCE_ROLE_NOT_EVIDENCE")
    with connect(settings.db_path) as conn:
        assert preflights.build_sources(conn, sid, [source_id]) == []
        allowed = refs.load(conn, sid)
        assert source_id not in allowed.source_versions and allowed.segment_ids == set() and allowed.asset_ids == set()
    assert _count(settings, "preflights", sid) == 0


def test_invalid_role_is_rejected_without_writes(app, settings):
    c = TestClient(app)
    sid = _create(c)
    r = _upload(c, sid, role="company_fact")
    assert r.status_code == 400, r.text
    assert _count(settings, "sources", sid) == _count(settings, "jobs", sid) == 0


def test_read_result_after_demo_disable_is_discarded_without_session_purge(app, settings, monkeypatch):
    c = TestClient(app)
    sid = _create(c)
    real_parse = reading.parse
    enabled = {"value": True}
    monkeypatch.setattr(sessions, "demo_allowed", lambda _settings: enabled["value"])

    def parse_then_disable(*args, **kwargs):
        result = real_parse(*args, **kwargs)
        enabled["value"] = False
        return result

    monkeypatch.setattr(reading, "parse", parse_then_disable)
    upload = _upload(c, sid)
    assert upload.status_code == 202, upload.text
    job = _row(settings, "SELECT * FROM jobs WHERE job_id=?", upload.json()["job_id"])
    assert job["status"] == "failed" and job["result_ref_json"] is None
    assert json.loads(job["error_json"])["code"] == "DEMO_MODE_DISABLED"
    assert _count(settings, "segments", sid) == 0 and _count(settings, "assets", sid) == 0
    session = _row(settings, "SELECT status,purged_at FROM sessions WHERE session_id=?", sid)
    assert session[:] == ("active", None)
    enabled["value"] = True
    assert c.get(f"/api/v1/sessions/{sid}").status_code == 200
    assert _row(settings, "SELECT status FROM jobs WHERE job_id=?", job["job_id"])[0] == "failed"
    listed = c.get(f"/api/v1/sessions/{sid}/sources").json()["items"]
    assert listed and all(source["parse_status"] == "failed" for source in listed)
    assert _count(settings, "segments", sid) == 0 and _count(settings, "assets", sid) == 0


def _seed_registered(settings, source_id="DEMO01", *, origin="demo", image=False):
    """등록 자산 접근/근거 규칙의 독립 fixture. 적재기 검증은 test_registered_import.py에서 한다."""
    stamp = to_iso(now())
    data = TEXT
    if image:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (8, 6), (20, 60, 90)).save(buf, format="PNG")
        data = buf.getvalue()
    name = source_id + (".png" if image else ".txt")
    path = settings.private_runs_dir / "registered" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    with connect(settings.db_path) as conn:
        conn.execute("INSERT INTO sources (source_id,source_version,scope,session_id,name,mime_type,size_bytes,kind,"
                     "parse_status,text_available,image_available,stored_path,content_hash,created_at,origin_kind,is_mock,role) "
                     "VALUES (?,1,'registered',NULL,?,?,?,?, 'complete',?,?,?,?,?,?,?,'evidence')",
                     (source_id, name, "image/png" if image else "text/plain", len(data), "photo" if image else "company",
                      int(not image), int(image), path.relative_to(settings.private_runs_dir).as_posix(),
                      hashlib.sha256(data).hexdigest(), stamp, origin, int(origin == "mock")))
        if image:
            conn.execute("INSERT INTO assets (asset_id,source_id,source_version,scope,session_id,origin,mime_type,width,height,"
                         "content_hash,status,stored_path,created_at,approved_for_external_use) "
                         "VALUES (?,?,1,'registered',NULL,'source_image','image/png',8,6,?,'ready',?,?,1)",
                         (f"asset_{source_id}", source_id, hashlib.sha256(data).hexdigest(),
                          path.relative_to(settings.private_runs_dir).as_posix(), stamp))
        else:
            for i, line in enumerate(data.decode().splitlines(), start=1):
                conn.execute("INSERT INTO segments (segment_id,source_id,source_version,session_id,ordinal,locator_json,text,created_at) "
                             "VALUES (?,?,1,NULL,?,?,?,?)", (f"seg_{source_id}_{i}", source_id, i,
                             json.dumps({"line_start": i, "line_end": i}), ("[MOCK] " if origin == "mock" else "") + line, stamp))
    return source_id, data


def test_brochure_photo_descriptions_require_current_publication_and_non_mock_origin(app, settings):
    c = TestClient(app)
    sid = _create(c)
    ids = [_seed_registered(settings, name, origin=origin, image=True)[0]
           for name, origin in (("YES", "demo"), ("NO", "real"), ("UNKNOWN", "real"), ("MOCKPIC", "mock"))]
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE assets SET caption_candidate='가상 공정 사진'")
        conn.execute("UPDATE assets SET approved_for_external_use=0 WHERE source_id='NO'")
        conn.execute("UPDATE assets SET approved_for_external_use=NULL WHERE source_id='UNKNOWN'")
    response = c.patch(f"/api/v1/sessions/{sid}/inputs", json={"expected_input_revision": 1, "selected_source_ids": ids})
    assert response.status_code == 200, response.text
    with connect(settings.db_path) as conn:
        selected = preflights.build_sources(conn, sid, ids)
        descriptions = {aid for source in selected for aid in source.asset_descriptions}
        assert descriptions == {"asset_YES"}
        conn.execute("UPDATE assets SET approved_for_external_use=0 WHERE source_id='YES'")
        assert not any(s.asset_descriptions for s in preflights.build_sources(conn, sid, ids))


class DemoFlow(BaseFlow):
    def __init__(self, app, settings, *, origin="demo"):
        self.settings = settings
        self.c = TestClient(app)
        self.sid = _create(self.c)
        source_id, _ = _seed_registered(settings, "MOCKDEMO" if origin == "mock" else "DEMO01", origin=origin)
        self.source_ids = [source_id]
        r = self.c.patch(f"/api/v1/sessions/{self.sid}/inputs",
                         json={"expected_input_revision": 1, "selected_source_ids": self.source_ids})
        assert r.status_code == 200, r.text
        self.rev_in = r.json()["input_revision"]
        self.did = None

    def validate(self, *, acknowledge=True):
        response = self.c.post(f"/api/v1/sessions/{self.sid}/documents/{self.did}/validate",
                               json={"expected_revision": self.rev(), "input_revision": self.rev_in})
        assert response.status_code == 202, response.text
        self.job(response.json()["job_id"])
        result = self.get()["validation"]
        assert result["status"] in {"needs_review", "passed"}, result
        if acknowledge:
            items = self.c.get(f"/api/v1/sessions/{self.sid}/documents/{self.did}/issues").json()["issues"]
            for issue in items:
                if issue["status"] == "open" and issue["severity"] == "warning":
                    response = self.c.post(f"/api/v1/sessions/{self.sid}/issues/{issue['issue_id']}/resolve", json={
                        "expected_revision": self.rev(), "input_revision": self.rev_in, "validation_id": result["validation_id"],
                        "resolution": {"action": "acknowledged", "reason": "가상 자료를 사용한 시연임을 확인함"}})
                    assert response.status_code == 200, response.text
            assert self.get()["validation"]["status"] == "passed"
        return self.get()["validation"]

    def fabricate(self):
        result = super().fabricate()
        with connect(self.settings.db_path) as conn:
            conn.execute("UPDATE artifacts SET demo=1 WHERE artifact_id=?", (result["artifact_id"],))
            conn.execute("UPDATE layout_checks SET demo=1 WHERE layout_check_id=?", (result["layout_check_id"],))
        return result


def test_demo_sources_hidden_by_default_and_rejected_as_normal_evidence(app, settings):
    _seed_registered(settings)
    _seed_registered(settings, "REAL01", origin="real")
    c = TestClient(app)
    sid = _create(c, demo=False)
    assert {s["source_id"] for s in c.get("/api/v1/sources").json()["items"]} == {"REAL01"}
    shown = c.get("/api/v1/sources", params={"include_demo": "true"})
    assert shown.status_code == 200, shown.text
    assert {s["source_id"] for s in shown.json()["items"]} == {"REAL01", "DEMO01"}
    response = c.patch(f"/api/v1/sessions/{sid}/inputs", json={"expected_input_revision": 1, "selected_source_ids": ["DEMO01"]})
    _assert_error(response, 422, "DEMO_SOURCE_NOT_ALLOWED")
    with connect(settings.db_path) as conn:
        assert preflights.build_sources(conn, sid, ["DEMO01"]) == []
        assert "DEMO01" not in refs.load(conn, sid).source_versions
    off = _off_client(settings, c)
    _assert_error(off.get("/api/v1/sources", params={"include_demo": "true"}), 403, "DEMO_MODE_DISABLED")
    assert {s["source_id"] for s in off.get("/api/v1/sources").json()["items"]} == {"REAL01"}


def test_demo_registered_asset_access_and_snapshot_are_session_scoped(app, settings):
    _, data = _seed_registered(settings, "DEMOIMG", image=True)
    c = TestClient(app)
    normal = _create(c, demo=False)
    demo = _create(c)
    url = lambda sid: f"/api/v1/sessions/{sid}/assets/asset_DEMOIMG"
    _assert_error(c.get(url(normal)), 404, "RESOURCE_NOT_FOUND")
    assert c.get(url(demo)).content == data
    doc = Document(document_id="doc_fake", session_id=normal, document_revision=1, input_revision=1,
                   title="예시 회사", target_pages=1, status="draft", pages=[Page(page_id="p1", title="사진", layout_key="default",
                   blocks=[Block(block_id="b1", type="image", content={"asset_id": "asset_DEMOIMG", "caption": "예시 사진"})])])
    with connect(settings.db_path) as conn:
        snapshot = export_render.build_snapshot(conn, settings, normal, doc)
    assert not snapshot.assets["asset_DEMOIMG"].ok
    assert snapshot.assets["asset_DEMOIMG"].reason == "not_accessible"
    off = _off_client(settings, c)
    _assert_error(off.get(url(demo)), 403, "DEMO_MODE_DISABLED")
    assert off.delete(f"/api/v1/sessions/{demo}").status_code == 200
    assert _row(settings, "SELECT COUNT(*) FROM assets WHERE asset_id='asset_DEMOIMG'")[0] == 1
    assert (settings.private_runs_dir / "registered" / "DEMOIMG.png").read_bytes() == data
    _assert_error(c.get(url(normal)), 404, "RESOURCE_NOT_FOUND")


@pytest.mark.parametrize("kind", ["preflight", "draft", "propose", "validate"])
@pytest.mark.parametrize("agent_raises", [False, True])
def test_late_agent_result_cannot_store_after_demo_disabled(app, settings, monkeypatch, kind, agent_raises):
    flow = DemoFlow(app, settings)
    if kind in {"propose", "validate"}:
        flow.draft()
    if kind == "draft":
        r = flow.c.post(f"/api/v1/sessions/{flow.sid}/preflights", json={"expected_input_revision": flow.rev_in})
        pf = flow.job(r.json()["job_id"])["result_ref"]["preflight_id"]
    original = ai_jobs._run
    enabled = {"value": True}
    monkeypatch.setattr(sessions, "demo_allowed", lambda _settings: enabled["value"])

    def run_then_disable(*args, **kwargs):
        result = original(*args, **kwargs)
        enabled["value"] = False
        if agent_raises:
            raise RuntimeError("FAKE_AGENT_ERROR_AFTER_MODE_OFF")
        return result

    monkeypatch.setattr(ai_jobs, "_run", run_then_disable)
    prefix = f"/api/v1/sessions/{flow.sid}"
    if kind == "preflight":
        response = flow.c.post(prefix + "/preflights", json={"expected_input_revision": flow.rev_in})
        table = "preflights"
    elif kind == "draft":
        response = flow.c.post(prefix + "/drafts", json={"preflight_id": pf, "input_revision": flow.rev_in, "confirmed": True})
        table = "documents"
    elif kind == "propose":
        bid = next(b["block_id"] for p in flow.doc()["pages"] for b in p["blocks"] if b["type"] == "paragraph")
        response = flow.c.post(prefix + f"/documents/{flow.did}/proposals", json={"expected_revision": flow.rev(),
                               "input_revision": flow.rev_in, "target_block_ids": [bid], "instruction": "문장 정리", "kind": "text"})
        table = "proposals"
    else:
        response = flow.c.post(prefix + f"/documents/{flow.did}/validate",
                               json={"expected_revision": flow.rev(), "input_revision": flow.rev_in})
        table = "validations"
    assert response.status_code == 202, response.text
    row = _row(settings, "SELECT * FROM jobs WHERE job_id=?", response.json()["job_id"])
    assert row["status"] == "failed" and row["result_ref_json"] is None
    assert json.loads(row["error_json"])["code"] == "DEMO_MODE_DISABLED"
    assert "FAKE_AGENT_ERROR_AFTER_MODE_OFF" not in row["error_json"]
    assert _count(settings, table, flow.sid) == 0
    assert _row(settings, "SELECT status,purged_at FROM sessions WHERE session_id=?", flow.sid)[:] == ("active", None)


def test_demo_warning_requires_acknowledgement_and_reuses_artifact_bytes(app, settings):
    flow = DemoFlow(app, settings).draft()
    unchecked = flow.validate(acknowledge=False)
    layout = flow.fabricate()
    denied = flow.c.post(f"/api/v1/sessions/{flow.sid}/documents/{flow.did}/approvals", json={
        "expected_revision": flow.rev(), "input_revision": flow.rev_in, "format": "pdf",
        "validation_id": unchecked["validation_id"], "layout_check_id": layout["layout_check_id"], "confirmed": True})
    assert denied.status_code == 422 and denied.json()["error"]["code"] == "WARNING_ACKNOWLEDGEMENT_REQUIRED"
    approval = flow.approved()
    assert flow.get()["demo"] is True
    assert approval["demo"] is True
    with connect(settings.db_path) as conn:
        warnings = conn.execute("SELECT status,severity FROM issues WHERE document_id=? AND code='DEMO_VALUE'", (flow.did,)).fetchall()
    assert warnings and all(w["status"] == "acknowledged" and w["severity"] == "warning" for w in warnings)
    export = flow.export_ready(approval["approval_id"])
    assert export["demo"] is True
    response = flow.download(export["export_id"])
    assert response.status_code == 200, response.text
    artifact = _row(settings, "SELECT * FROM artifacts WHERE artifact_id=?", approval["artifact_id"])
    assert artifact["demo"] == 1 and artifact["sha256"] == hashlib.sha256(response.content).hexdigest()
    assert response.content == (settings.private_runs_dir / artifact["stored_path"]).read_bytes()


def test_demo_does_not_exempt_mock_blocker(app, settings):
    flow = DemoFlow(app, settings, origin="mock").draft()
    response = flow.c.post(f"/api/v1/sessions/{flow.sid}/documents/{flow.did}/validate",
                           json={"expected_revision": flow.rev(), "input_revision": flow.rev_in})
    flow.job(response.json()["job_id"])
    assert flow.get()["validation"]["status"] == "failed"
    with connect(settings.db_path) as conn:
        blockers = conn.execute("SELECT status,severity FROM issues WHERE document_id=? AND code='MOCK_VALUE'", (flow.did,)).fetchall()
    assert blockers and all(b["status"] == "open" and b["severity"] == "blocker" for b in blockers)


@pytest.mark.parametrize("table", ["artifacts", "layout_checks", "approvals", "exports"])
def test_demo_identity_cannot_be_crossed_at_download(app, settings, table):
    flow = DemoFlow(app, settings).draft()
    approval = flow.approved()
    export = flow.export_ready(approval["approval_id"])
    assert flow.download(export["export_id"]).status_code == 200
    with connect(settings.db_path) as conn:
        conn.execute(f"UPDATE {table} SET demo=0 WHERE session_id=?", (flow.sid,))
    rejected = flow.download(export["export_id"])
    _assert_error(rejected, 422, "RENDER_IDENTITY_MISMATCH")
    assert not rejected.content.startswith(b"%PDF")


def test_mode_off_blocks_approved_export_replays_and_download_but_preserves_approval(app, settings):
    flow = DemoFlow(app, settings).draft()
    approval = flow.approved()
    path = f"/api/v1/sessions/{flow.sid}/exports"
    body = {"approval_id": approval["approval_id"], "format": "pdf"}
    initial = flow.c.post(path, json=body, headers={"Idempotency-Key": "export"})
    assert initial.status_code in (200, 202), initial.text
    export = flow.export_ready(approval["approval_id"])
    off = _off_client(settings, flow.c)
    for response in (off.post(path, json=body, headers={"Idempotency-Key": "export"}),
                     off.get(path + f"/{export['export_id']}/download")):
        _assert_error(response, 403, "DEMO_MODE_DISABLED")
    assert _row(settings, "SELECT status FROM approvals WHERE approval_id=?", approval["approval_id"])[0] == "active"
    assert flow.download(export["export_id"]).status_code == 200


def test_v9_migration_preserves_rows_backfills_origins_and_is_idempotent(app, settings):
    _seed_registered(settings, "REAL01", origin="real")
    _seed_registered(settings, "MOCK01", origin="mock")
    c = TestClient(app)
    sid = _create(c, demo=False)
    with connect(settings.db_path) as conn:
        for table in ("sessions", "layout_checks", "artifacts", "approvals", "exports"):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN demo")
        conn.execute("ALTER TABLE sources DROP COLUMN role")
        conn.execute("ALTER TABLE sources DROP COLUMN origin_kind")
        conn.execute("PRAGMA user_version=8")
    init_db(settings.db_path, settings.private_runs_dir)
    init_db(settings.db_path, settings.private_runs_dir)
    with connect(settings.db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
        assert conn.execute("SELECT demo FROM sessions WHERE session_id=?", (sid,)).fetchone()[0] == 0
        rows = {r["source_id"]: r for r in conn.execute("SELECT * FROM sources")}
        assert rows["REAL01"]["origin_kind"] == "real" and rows["MOCK01"]["origin_kind"] == "mock"
        assert all(r["role"] == "evidence" for r in rows.values())
        assert conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 6


@pytest.mark.parametrize("failed_render", [False, True])
def test_layout_result_after_mode_disable_is_not_published(app, settings, monkeypatch, failed_render):
    flow = DemoFlow(app, settings).draft()
    enabled = {"value": True}
    monkeypatch.setattr(sessions, "demo_allowed", lambda _settings: enabled["value"])

    def fake_render(snapshot, fmt, out_dir, _settings):
        enabled["value"] = False
        if failed_render:
            raise export_render.RenderError("render_failed", "가짜 렌더 실패")
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "fake.pdf"
        path.write_bytes(b"%PDF fake test bytes")
        return export_render.RenderResult(fmt, path, 1,
            [export_render.LayoutCheckRecord(key, True, "ok") for key in export_render.CHECK_KEYS], [], True,
            layout_checks.TEMPLATE_VERSION, layout_checks.RENDER_OPTIONS_HASH, snapshot.asset_manifest_hash,
            "fake/1", 0, demo=snapshot.demo)

    monkeypatch.setattr(export_render, "render", fake_render)
    monkeypatch.setattr(layout_check_jobs, "_render_previews", lambda *args: [])
    response = flow.c.post(f"/api/v1/sessions/{flow.sid}/documents/{flow.did}/layout-checks",
                           json={"expected_revision": flow.rev(), "format": "pdf"})
    assert response.status_code == 202, response.text
    job = _row(settings, "SELECT * FROM jobs WHERE job_id=?", response.json()["job_id"])
    assert job["status"] == "failed" and json.loads(job["error_json"])["code"] == "DEMO_MODE_DISABLED"
    for table in ("layout_checks", "artifacts", "layout_previews"):
        assert _count(settings, table, flow.sid) == 0
    assert not list((settings.private_runs_dir / flow.sid).rglob("fake.pdf"))
    assert _row(settings, "SELECT status,purged_at FROM sessions WHERE session_id=?", flow.sid)[:] == ("active", None)


@pytest.mark.parametrize("restart", [False, True])
def test_queued_export_mode_disable_fails_and_reenable_does_not_restart(app, settings, monkeypatch, restart):
    flow = DemoFlow(app, settings).draft()
    approval = flow.approved()
    real_run = exports.run_export_job
    monkeypatch.setattr(exports, "run_export_job", lambda *args: None)
    response = flow.c.post(f"/api/v1/sessions/{flow.sid}/exports", json={"approval_id": approval["approval_id"], "format": "pdf"},
                           headers={"Idempotency-Key": "queued"})
    assert response.status_code == 202, response.text
    eid, jid = response.json()["export"]["export_id"], response.json()["job_id"]
    off_settings = replace(settings, demo_mode=False)
    if restart:
        with connect(settings.db_path) as conn:
            conn.execute("UPDATE exports SET status='generating' WHERE export_id=?", (eid,))
            conn.execute("UPDATE jobs SET status='running' WHERE job_id=?", (jid,))
        create_app(off_settings)
    else:
        real_run(off_settings, flow.sid, jid, eid)
    export = _row(settings, "SELECT * FROM exports WHERE export_id=?", eid)
    job = _row(settings, "SELECT * FROM jobs WHERE job_id=?", jid)
    assert export["status"] == "failed" and export["finalized_reason"] == "demo_disabled"
    assert json.loads(export["error_json"])["code"] == "DEMO_MODE_DISABLED"
    assert job["status"] == "failed" and json.loads(job["error_json"])["code"] == "DEMO_MODE_DISABLED"
    create_app(settings)
    assert _row(settings, "SELECT status FROM exports WHERE export_id=?", eid)[0] == "failed"
    assert _row(settings, "SELECT status FROM jobs WHERE job_id=?", jid)[0] == "failed"
    assert _row(settings, "SELECT status FROM approvals WHERE approval_id=?", approval["approval_id"])[0] == "active"


def test_preview_mode_guard_keeps_bytes_and_checks_owner(app, settings):
    flow = DemoFlow(app, settings).draft()
    fabricated = flow.fabricate()
    path = settings.private_runs_dir / flow.sid / "artifacts" / "previews" / "fake.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"fake preview bytes"
    path.write_bytes(data)
    with connect(settings.db_path) as conn:
        conn.execute("INSERT INTO layout_previews (asset_id,session_id,layout_check_id,artifact_id,page_no,stored_path,sha256,"
                     "size_bytes,width,height,mime_type,status,created_at) VALUES ('prv_fake',?,?,?,1,?,?,?,8,6,'image/png','ready',?)",
                     (flow.sid, fabricated["layout_check_id"], fabricated["artifact_id"],
                      path.relative_to(settings.private_runs_dir).as_posix(), hashlib.sha256(data).hexdigest(), len(data), to_iso(now())))
    url = f"/api/v1/sessions/{flow.sid}/assets/prv_fake"
    assert flow.c.get(url).content == data
    off = _off_client(settings, flow.c)
    _assert_error(off.get(url), 403, "DEMO_MODE_DISABLED")
    assert path.read_bytes() == data
    normal_sid = _create(off, demo=False)
    _assert_error(off.get(f"/api/v1/sessions/{normal_sid}/assets/prv_fake"), 404, "RESOURCE_NOT_FOUND")
    assert flow.c.get(url).content == data


@pytest.mark.parametrize("permission", [None, 0])
def test_demo_approval_does_not_bypass_photo_permission(app, settings, permission):
    flow = DemoFlow(app, settings).draft()
    _seed_registered(settings, "DEMOIMG", image=True)
    first_page = flow.doc()["pages"][0]
    flow.patch([{"op": "insert_block", "page_id": first_page["page_id"], "after_block_id": first_page["blocks"][-1]["block_id"],
                 "block": {"block_id": "b_photo", "type": "image", "content": {"asset_id": "asset_DEMOIMG", "caption": "예시 사진", "alt": "예시 사진", "fit": "contain"},
                           "fact_ids": [], "evidence_refs": []}}])
    validation = flow.validate()
    fake = flow.fabricate()
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE assets SET approved_for_external_use=? WHERE asset_id='asset_DEMOIMG'", (permission,))
    response = flow.c.post(f"/api/v1/sessions/{flow.sid}/documents/{flow.did}/approvals",
                           json={"expected_revision": flow.rev(), "input_revision": flow.rev_in, "format": "pdf", "confirmed": True,
                                 "validation_id": validation["validation_id"], "layout_check_id": fake["layout_check_id"]})
    _assert_error(response, 422, "LAYOUT_NOT_READY")
    assert response.json()["error"]["details"]["reason"] == "publication_policy"
    assert response.json()["error"]["details"]["blocked"][0]["reason"] == ("not_decided" if permission is None else "denied")
    assert _count(settings, "approvals", flow.sid) == 0


def test_normal_artifact_and_layout_cannot_be_approved_for_demo_session(app, settings):
    flow = DemoFlow(app, settings).draft()
    validation = flow.validate()
    fake = flow.fabricate()
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE layout_checks SET demo=0 WHERE layout_check_id=?", (fake["layout_check_id"],))
        conn.execute("UPDATE artifacts SET demo=0 WHERE artifact_id=?", (fake["artifact_id"],))
    response = flow.c.post(f"/api/v1/sessions/{flow.sid}/documents/{flow.did}/approvals",
                           json={"expected_revision": flow.rev(), "input_revision": flow.rev_in, "format": "pdf", "confirmed": True,
                                 "validation_id": validation["validation_id"], "layout_check_id": fake["layout_check_id"]})
    _assert_error(response, 422, "LAYOUT_NOT_READY")
    assert response.json()["error"]["details"]["reason"] == "demo_mismatch"
    assert _count(settings, "approvals", flow.sid) == 0
