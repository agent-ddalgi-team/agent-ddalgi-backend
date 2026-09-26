"""BE-08 서버 규칙 테스트 — 브라우저 없이 실행된다(렌더 통합 테스트는 tests/test_be08.py).

출력 식별값 일관성·멱등 재전송·만료/철회 캐시 차단·공개 허가 값 형식·배치 Issue 해결 범위·마이그레이션·경로 비노출.
배치 검사 행·artifact는 테스트가 직접 만들거나(가짜 PDF 바이트) render()를 가짜 결과로 바꿔 넣는다. 실제 회사 자료·AI 호출 없음.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.config import Settings
from app.db import SCHEMA_VERSION, connect, init_db
from app.models import Block, Document, Page
from app.services import artifacts, export_render, exports, layout_check_jobs, layout_checks, registered
from app.services import publication as publication_service
from app.services.documents import get_current
from app.services.validation import IssueDraft

ROOT = Path(__file__).resolve().parent.parent
BRIEF = {"purpose": "테스트", "emphasis": [], "direction": "balanced", "target_pages": 4, "photo_preference": "balanced"}
CLEAN_TXT = "회사명: 예시 회사\n회사 개요: 예시 회사는 가상 부품 표면처리와 검사를 하는 테스트 기업입니다.\n사업 분야: 가상 부품 표면처리\n".encode()
BANNED = ("거산", "케미칼", "Geosan")


def _png(width: int = 8, height: int = 6, color=(10, 20, 30)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def settings(tmp_path):
    return Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "t.sqlite3",
                    export_browser_path=str(tmp_path / "no-browser" / "chrome.exe"))   # 브라우저를 찾지 못하게 고정


@pytest.fixture
def app(settings):
    return create_app(settings)


class Flow:
    """세션 + clean TXT/PNG + mock 초안 + 검증 passed. 배치 검사 행·artifact는 fabricate()로 직접 만든다(브라우저 불필요)."""

    def __init__(self, app, settings, *, registered_ids=None, upload_png=True):
        self.settings = settings
        self.c = TestClient(app)
        self.sid = self.c.post("/api/v1/sessions", json={"brief": BRIEF}).json()["session_id"]
        files = [("files", ("a.txt", io.BytesIO(CLEAN_TXT)))]
        if upload_png:
            files.append(("files", ("p.png", io.BytesIO(_png()))))
        up = self.c.post(f"/api/v1/sessions/{self.sid}/sources", files=files).json()
        selected = (registered_ids or []) + [i["source_id"] for i in up["items"]]
        r = self.c.patch(f"/api/v1/sessions/{self.sid}/inputs", json={"expected_input_revision": 1, "selected_source_ids": selected})
        self.rev_in = r.json()["input_revision"]
        job = self.c.post(f"/api/v1/sessions/{self.sid}/preflights", json={"expected_input_revision": self.rev_in}).json()
        pf = self.job(job["job_id"])["result_ref"]["preflight_id"]
        job = self.c.post(f"/api/v1/sessions/{self.sid}/drafts", json={"preflight_id": pf, "input_revision": self.rev_in, "confirmed": True}).json()
        self.did = self.job(job["job_id"])["result_ref"]["document_id"]
        ops = [{"op": "delete_block", "block_id": b["block_id"]} for p in self.doc()["pages"] for b in p["blocks"]
               if (b["type"] == "paragraph" and b["content"]["text"] == "추가 확인 필요") or b["type"] == "image_placeholder"]
        if ops:
            self.patch(ops)

    def job(self, jid, *, expect="succeeded"):
        for _ in range(600):
            job = self.c.get(f"/api/v1/sessions/{self.sid}/jobs/{jid}").json()
            if job["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.02)
        if expect is not None:
            assert job["status"] == expect, job
        return job

    def get(self):
        return self.c.get(f"/api/v1/sessions/{self.sid}/documents/{self.did}").json()

    def doc(self):
        return self.get()["document"]

    def rev(self):
        return self.doc()["document_revision"]

    def patch(self, ops):
        r = self.c.patch(f"/api/v1/sessions/{self.sid}/documents/{self.did}", json={"expected_revision": self.rev(), "operations": ops})
        assert r.status_code == 200, r.text
        return r.json()

    def validate(self):
        r = self.c.post(f"/api/v1/sessions/{self.sid}/documents/{self.did}/validate", json={"expected_revision": self.rev(), "input_revision": self.rev_in})
        assert r.status_code == 202, r.text
        self.job(r.json()["job_id"])
        v = self.get()["validation"]
        assert v["status"] == "passed", v
        return v

    def fabricate(self, *, fmt="pdf", status="passed", renderer="chrome/153.0.0.0") -> dict:
        """실제 렌더 없이 배치 검사 행 + 불변 artifact(가짜 PDF 바이트)를 만든다. 식별값은 layout_checks의 현재 값."""
        adir = artifacts.artifacts_dir(self.settings, self.sid)
        adir.mkdir(parents=True, exist_ok=True)
        art_id, lc_id = f"art_{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:16]}", f"lc_{hashlib.sha256(str(time.time_ns() + 1).encode()).hexdigest()[:16]}"
        path = adir / f"{art_id}.{fmt}"
        data = b"%PDF-1.4 fake artifact " + art_id.encode() + b"\n"
        path.write_bytes(data)
        with connect(self.settings.db_path) as conn:
            doc = get_current(conn, self.sid, self.did)
            manifest = layout_checks.asset_manifest_hash(conn, doc)
            conn.execute("INSERT INTO artifacts (artifact_id, session_id, document_id, document_revision, input_revision, format, stored_path, sha256, "
                         "size_bytes, template_version, render_options_hash, asset_manifest_hash, renderer, actual_pages, layout_check_id, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 't')",
                         (art_id, self.sid, self.did, doc.document_revision, self.rev_in, fmt, path.relative_to(self.settings.private_runs_dir).as_posix(),
                          hashlib.sha256(data).hexdigest(), len(data), layout_checks.TEMPLATE_VERSION, layout_checks.RENDER_OPTIONS_HASH, manifest, renderer, lc_id))
            conn.execute("INSERT INTO layout_checks (layout_check_id, session_id, document_id, document_revision, input_revision, format, template_version, "
                         "render_options_hash, asset_manifest_hash, status, actual_pages, issue_ids_json, created_at, layout_ok, publication_policy_ok, checks_json, "
                         "findings_json, fail_reasons_json, renderer, artifact_id, preview_basis, preview_ids_json, warnings_json, publication_blocks_json) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, '[]', 't', ?, 1, '[]', '[]', '[]', ?, ?, 'pdf', '[]', '[]', '[]')",
                         (lc_id, self.sid, self.did, doc.document_revision, self.rev_in, fmt, layout_checks.TEMPLATE_VERSION, layout_checks.RENDER_OPTIONS_HASH,
                          manifest, status, int(status == "passed"), renderer, art_id))
        return {"layout_check_id": lc_id, "artifact_id": art_id, "path": path, "bytes": data}

    def approve(self, vid, lcid, *, fmt="pdf"):
        return self.c.post(f"/api/v1/sessions/{self.sid}/documents/{self.did}/approvals",
                           json={"expected_revision": self.rev(), "input_revision": self.rev_in, "format": fmt,
                                 "validation_id": vid, "layout_check_id": lcid, "confirmed": True})

    def approved(self) -> tuple[dict, dict]:
        v = self.validate()
        fab = self.fabricate()
        r = self.approve(v["validation_id"], fab["layout_check_id"])
        assert r.status_code == 201, r.text
        return r.json(), fab

    def export(self, approval_id, *, key=None, fmt="pdf"):
        return self.c.post(f"/api/v1/sessions/{self.sid}/exports", json={"approval_id": approval_id, "format": fmt},
                           headers={"Idempotency-Key": key} if key else {})

    def export_ready(self, approval_id, *, key=None) -> dict:
        r = self.export(approval_id, key=key)
        assert r.status_code in (200, 202), r.text
        if r.json()["job_id"] and r.json()["export"]["status"] != "ready":
            self.job(r.json()["job_id"])
        r2 = self.export(approval_id)           # 키 없이 현재 상태 확인(같은 키는 최초 응답을 그대로 돌려준다)
        assert r2.status_code == 200 and r2.json()["export"]["status"] == "ready", r2.text
        return r2.json()["export"]

    def download(self, eid):
        return self.c.get(f"/api/v1/sessions/{self.sid}/exports/{eid}/download")


def _row(settings, sql, *params):
    with connect(settings.db_path) as conn:
        return conn.execute(sql, params).fetchone()


# ================= 1. 출력 식별값 일관성 =================

def test_identity_values_are_checked_everywhere(app, settings, monkeypatch):
    flow = Flow(app, settings)
    a, fab = flow.approved()
    exp = flow.export_ready(a["approval_id"], key="K-ok")
    assert flow.download(exp["export_id"]).status_code == 200
    # 템플릿 버전 변경 → 생성·발행·다운로드·멱등 성공 응답·복구 모두 거부
    monkeypatch.setattr(layout_checks, "TEMPLATE_VERSION", "template_v1")
    r = flow.export(a["approval_id"], key="K-new")
    assert r.status_code == 422 and r.json()["error"]["code"] == "RENDER_IDENTITY_MISMATCH" and r.json()["error"]["details"]["reason"] == "template_version_changed"
    r = flow.export(a["approval_id"], key="K-ok")                 # 같은 키 캐시 성공 응답도 차단
    assert r.status_code == 422 and r.json()["error"]["code"] == "RENDER_IDENTITY_MISMATCH"
    d = flow.download(exp["export_id"])
    assert d.status_code == 422 and d.json()["error"]["details"]["recheck_required"]
    assert _row(settings, "SELECT status, finalized_reason FROM exports WHERE export_id=?", exp["export_id"])["finalized_reason"] == "identity_mismatch"
    assert _row(settings, "SELECT status FROM approvals WHERE approval_id=?", a["approval_id"])["status"] == "active"   # 승인 자체는 유지(재검사 안내)
    # 재시작 복구도 거부(실제 사유)
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE exports SET status='generating' WHERE export_id=?", (exp["export_id"],))
    create_app(settings)
    e = _row(settings, "SELECT status, error_json FROM exports WHERE export_id=?", exp["export_id"])
    assert e["status"] == "failed" and json.loads(e["error_json"])["code"] == "RENDER_IDENTITY_MISMATCH"
    monkeypatch.undo()
    # 렌더 옵션 변경
    monkeypatch.setattr(layout_checks, "RENDER_OPTIONS_HASH", "0123456789abcdef")
    r = flow.export(a["approval_id"], key="K-opt")
    assert r.status_code == 422 and r.json()["error"]["details"]["reason"] == "render_options_changed"
    monkeypatch.undo()
    # 이미지 목록(asset_manifest) 변경
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE assets SET content_hash='changed' WHERE session_id=?", (flow.sid,))
    r = flow.export(a["approval_id"], key="K-asset")
    assert r.status_code == 422 and r.json()["error"]["details"]["reason"] == "asset_manifest_changed"
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE assets SET content_hash=(SELECT content_hash FROM sources WHERE sources.source_id=assets.source_id) WHERE session_id=?", (flow.sid,))
    assert flow.export(a["approval_id"], key="K-back").status_code in (200, 202)   # 원상복구되면 다시 가능


@pytest.mark.parametrize("mutate, reason", [
    ("UPDATE artifacts SET layout_check_id='lc_other' WHERE artifact_id=?", "artifact_link_mismatch"),
    ("UPDATE artifacts SET template_version='template_vX' WHERE artifact_id=?", "artifact_identity_mismatch"),
    ("UPDATE layout_checks SET asset_manifest_hash='deadbeef' WHERE artifact_id=?", "layout_check_mismatch"),
])
def test_artifact_and_layout_check_link_mismatch_is_rejected(app, settings, mutate, reason):
    flow = Flow(app, settings)
    a, fab = flow.approved()
    with connect(settings.db_path) as conn:
        conn.execute(mutate, (fab["artifact_id"],))
    r = flow.export(a["approval_id"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "RENDER_IDENTITY_MISMATCH" and r.json()["error"]["details"]["reason"] == reason
    assert _row(settings, "SELECT COUNT(*) AS n FROM exports")["n"] == 0


def test_missing_artifact_row_is_artifact_invalid_and_invalidates_approval(app, settings):
    """승인 → Export ready → artifact 행 삭제 → 재요청: ARTIFACT_INVALID + Approval invalidated + Export failed. 오류 응답에도 커밋된다."""
    flow = Flow(app, settings)
    a, fab = flow.approved()
    exp = flow.export_ready(a["approval_id"], key="K-row")
    with connect(settings.db_path) as conn:
        conn.execute("DELETE FROM artifacts WHERE artifact_id=?", (fab["artifact_id"],))
    r = flow.export(a["approval_id"], key="K-row2")
    assert r.status_code == 422 and r.json()["error"]["code"] == "ARTIFACT_INVALID"
    assert r.json()["error"]["details"]["reason"] == "not_found" and r.json()["error"]["details"]["recheck_required"]
    ap = _row(settings, "SELECT status, invalidated_reason FROM approvals WHERE approval_id=?", a["approval_id"])
    ex = _row(settings, "SELECT status, finalized_reason, error_json FROM exports WHERE export_id=?", exp["export_id"])
    assert (ap["status"], ap["invalidated_reason"]) == ("invalidated", "artifact_invalid")
    assert (ex["status"], ex["finalized_reason"]) == ("failed", "artifact_invalid") and json.loads(ex["error_json"])["code"] == "ARTIFACT_INVALID"
    assert flow.get()["approval"] is None
    assert _row(settings, "SELECT COUNT(*) AS n FROM exports WHERE approval_id=?", a["approval_id"])["n"] == 1   # 새 Export 없음
    # 이후 요청은 무효 승인으로 차단(같은 키 캐시도)
    assert flow.export(a["approval_id"], key="K-row").json()["error"]["code"] == "APPROVAL_NOT_ACTIVE"
    assert flow.download(exp["export_id"]).status_code == 409
    # 다운로드 경로에서 발견되는 경우도 같은 결과(다른 승인본으로 확인)
    flow2 = Flow(app, settings)
    a2, fab2 = flow2.approved()
    exp2 = flow2.export_ready(a2["approval_id"])
    with connect(settings.db_path) as conn:
        conn.execute("DELETE FROM artifacts WHERE artifact_id=?", (fab2["artifact_id"],))
    d = flow2.download(exp2["export_id"])
    assert d.status_code == 422 and d.json()["error"]["code"] == "ARTIFACT_INVALID"
    assert _row(settings, "SELECT status FROM approvals WHERE approval_id=?", a2["approval_id"])["status"] == "invalidated"
    assert _row(settings, "SELECT status, finalized_reason FROM exports WHERE export_id=?", exp2["export_id"])["finalized_reason"] == "artifact_invalid"


def test_browser_version_change_alone_keeps_using_the_same_bytes(app, settings):
    flow = Flow(app, settings)
    a, fab = flow.approved()
    with connect(settings.db_path) as conn:   # 브라우저가 바뀐 상황: 승인·artifact의 renderer 기록만 다르다
        conn.execute("UPDATE approvals SET renderer='chrome/999.0.0.0' WHERE approval_id=?", (a["approval_id"],))
    exp = flow.export_ready(a["approval_id"])
    d = flow.download(exp["export_id"])
    assert d.status_code == 200 and d.content == fab["bytes"]
    assert exp["artifact_id"] == fab["artifact_id"]


# ================= 3. 멱등 =================

def test_idempotent_replay_returns_first_response_after_validity(app, settings, monkeypatch):
    flow = Flow(app, settings)
    a, fab = flow.approved()
    first = flow.export(a["approval_id"], key="K1")
    assert first.status_code == 202 and first.json()["export"]["status"] == "queued"
    flow.job(first.json()["job_id"])
    assert flow.export(a["approval_id"]).json()["export"]["status"] == "ready"
    replay = flow.export(a["approval_id"], key="K1")
    assert replay.status_code == 202 and replay.json() == first.json()          # ready가 된 뒤에도 최초 응답 그대로
    assert flow.export(a["approval_id"], key="K2").status_code == 200           # 새 키: ready 재사용
    # 같은 키·다른 본문은 조건과 무관하게 409(존재하지 않는 승인 ID여도)
    r = flow.c.post(f"/api/v1/sessions/{flow.sid}/exports", json={"approval_id": "apr_nope", "format": "pdf"}, headers={"Idempotency-Key": "K1"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    # 실패 후 재시도: 같은 키는 최초 응답, 새 키는 attempt+1. 그 뒤에도 원래 키 응답은 동일
    real_publish = exports.publish
    state = {"fail": True}

    def flaky(conn, s, row):
        if state["fail"]:
            state["fail"] = False
            raise RuntimeError("glitch")
        return real_publish(conn, s, row)
    monkeypatch.setattr(exports, "publish", flaky)
    with connect(settings.db_path) as conn:   # 기존 ready를 만료로 확정해 새 Export가 생기게 한다
        conn.execute("UPDATE exports SET expires_at='2000-01-01T00:00:00Z'")
    r3 = flow.export(a["approval_id"], key="K3")
    assert r3.status_code == 202 and r3.json()["export"]["attempt"] == 1
    flow.job(r3.json()["job_id"], expect="failed")
    assert flow.export(a["approval_id"], key="K3").json() == r3.json()
    r4 = flow.export(a["approval_id"], key="K4")
    assert r4.status_code == 202 and r4.json()["export"]["export_id"] == r3.json()["export"]["export_id"] and r4.json()["export"]["attempt"] == 2
    flow.job(r4.json()["job_id"])
    assert flow.export(a["approval_id"], key="K3").json() == r3.json() and flow.export(a["approval_id"], key="K4").json() == r4.json()
    assert flow.download(r4.json()["export"]["export_id"]).status_code == 200


def test_expired_or_revoked_blocks_cached_success_response(app, settings):
    flow = Flow(app, settings)
    a, fab = flow.approved()
    exp = flow.export_ready(a["approval_id"], key="K-exp")
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE exports SET expires_at='2000-01-01T00:00:00Z' WHERE export_id=?", (exp["export_id"],))
    r = flow.export(a["approval_id"], key="K-exp")
    assert r.status_code == 410 and r.json()["error"]["code"] == "ARTIFACT_EXPIRED"
    e = _row(settings, "SELECT status, finalized_reason, expires_at, export_id FROM exports WHERE export_id=?", exp["export_id"])
    assert (e["status"], e["finalized_reason"], e["expires_at"]) == ("failed", "expired", "2000-01-01T00:00:00Z")
    new = flow.export(a["approval_id"], key="K-exp2")                          # 새 키로만 새 Export(UNIQUE 충돌 없음)
    assert new.status_code == 202 and new.json()["export"]["export_id"] != exp["export_id"]
    flow.job(new.json()["job_id"])
    # 승인 철회(무효화) 뒤 캐시 성공 응답 차단
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE approvals SET status='invalidated', invalidated_reason='document_changed' WHERE approval_id=?", (a["approval_id"],))
    r = flow.export(a["approval_id"], key="K-exp2")
    assert r.status_code == 409 and r.json()["error"]["code"] == "APPROVAL_NOT_ACTIVE"
    assert flow.download(new.json()["export"]["export_id"]).status_code == 409


# ================= 2. 공개 허가 값 형식 =================

def _fake_bundle(root: Path, *, approved, raw: str | None = None) -> Path:
    """비mock 등록 자료 1건 + 사진 1장(FAKE_IMG01). raw를 주면 approved_for_external_use 자리에 그 JSON 조각을 그대로 넣는다."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "orig.txt").write_text("회사명: 예시 회사\n회사 개요: 예시 회사는 가상 부품 표면처리와 검사를 하는 테스트 기업입니다.\n", encoding="utf-8")
    img_dir = root / "05_이미지" / "전체_추출이미지" / "01_제품"
    img_dir.mkdir(parents=True, exist_ok=True)
    (img_dir / "FAKE_IMG01.png").write_bytes(_png(24, 16, (30, 120, 40)))
    sha_txt = hashlib.sha256((root / "orig.txt").read_bytes()).hexdigest()
    sha_png = hashlib.sha256((img_dir / "FAKE_IMG01.png").read_bytes()).hexdigest()
    (root / "sources.json").write_text(json.dumps([{"source_id": "FAKE01", "name": "가상 등록 자료", "status": "ready", "path": "orig.txt",
                                                    "sha256": sha_txt, "available_in_package": True}], ensure_ascii=False), encoding="utf-8")
    (root / "company_chunks.jsonl").write_text(json.dumps({"chunk_id": "FAKE01_C001", "source_id": "FAKE01", "text": "회사명: 예시 회사",
                                                           "locator": "TXT 1행", "evidence_status": "자료에 기재됨"}, ensure_ascii=False) + "\n", encoding="utf-8")
    with (root / "00_이미지목록.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["폴더", "파일명", "PPT페이지", "원본이미지", "처리", "가로px", "세로px"])
        w.writerow(["01_제품", "FAKE_IMG01.png", "1", "image1.png", "원본 그대로 추출", "24", "16"])
    value = raw if raw is not None else json.dumps(approved)
    (root / "photo_candidates.json").write_text(
        '[{"photo_id": "FAKE_IMG01", "path": "05_이미지/전체_추출이미지/01_제품/FAKE_IMG01.png", "source_id": "FAKE01", "sha256": "%s", '
        '"selected_as_candidate": true, "approved_for_external_use": %s, "caption_candidate": "공장 사진"}]' % (sha_png, value), encoding="utf-8")
    return root


def _counts(settings):
    with connect(settings.db_path) as conn:
        return (conn.execute("SELECT COUNT(*) FROM sources WHERE source_id='FAKE01'").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM assets WHERE photo_id='FAKE_IMG01'").fetchone()[0],
                (lambda r: r[0] if r else None)(conn.execute("SELECT approved_for_external_use FROM assets WHERE photo_id='FAKE_IMG01'").fetchone()))


@pytest.mark.parametrize("raw", ['"false"', '"true"', '"null"', "1", "0", "[]", "{}"])
def test_publication_value_rejects_non_boolean_on_new_import(app, settings, tmp_path, raw):
    with pytest.raises(registered.ImportError_) as e:
        registered.run(settings, _fake_bundle(tmp_path / "b", approved=None, raw=raw))
    assert e.value.code == "INVALID_INPUT" and "approved_for_external_use" in e.value.message
    assert _counts(settings) == (0, 0, None)          # 전체 롤백: 자료·사진 없음


@pytest.mark.parametrize("raw, expected", [("true", 1), ("false", 0), ("null", None)])
def test_publication_value_accepts_boolean_or_null(app, settings, tmp_path, raw, expected):
    s = registered.run(settings, _fake_bundle(tmp_path / "b", approved=None, raw=raw))
    assert s.added_assets == 1 and _counts(settings)[2] == expected


def test_publication_value_rejects_non_boolean_on_reimport_and_keeps_everything(app, settings, tmp_path):
    registered.run(settings, _fake_bundle(tmp_path / "b1", approved=True))
    with connect(settings.db_path) as conn:
        asset = conn.execute("SELECT asset_id FROM assets WHERE photo_id='FAKE_IMG01'").fetchone()["asset_id"]
    flow = Flow(app, settings, upload_png=False, registered_ids=["FAKE01"])
    page = flow.doc()["pages"][0]["page_id"]
    flow.patch([{"op": "insert_block", "page_id": page, "after_block_id": None,
                 "block": {"block_id": "b_reg", "type": "image", "content": {"asset_id": asset, "alt": "공장 사진", "caption": "공장 사진", "fit": "contain"}}}])
    a, fab = flow.approved()
    exp = flow.export_ready(a["approval_id"])
    for raw in ('"false"', "0", '"null"'):
        with pytest.raises(registered.ImportError_) as e:
            registered.run(settings, _fake_bundle(tmp_path / f"b_{abs(hash(raw))}", approved=None, raw=raw), update_publication=True)
        assert e.value.code == "INVALID_INPUT"
        assert _counts(settings) == (1, 1, 1)                                       # 허가 값 그대로
        assert _row(settings, "SELECT status FROM approvals WHERE approval_id=?", a["approval_id"])["status"] == "active"
        assert _row(settings, "SELECT status FROM exports WHERE export_id=?", exp["export_id"])["status"] == "ready"
    # dry-run은 정상 값이라도 바꾸지 않는다
    s = registered.run(settings, _fake_bundle(tmp_path / "b_dry", approved=False), update_publication=True, dry_run=True)
    assert s.publication_updated == 0 and s.publication_pending == 1 and _counts(settings)[2] == 1
    # 정상 boolean 재적재는 갱신·무효화
    s = registered.run(settings, _fake_bundle(tmp_path / "b_false", approved=False), update_publication=True)
    assert s.publication_updated == 1 and s.approvals_invalidated == 1 and s.exports_finalized == 1 and _counts(settings) == (1, 1, 0)
    assert flow.download(exp["export_id"]).status_code == 409


# ================= 4. 배치 Issue 해결 범위 =================

def _seed_document(settings) -> tuple[str, Document]:
    with connect(settings.db_path) as conn:
        conn.execute("INSERT INTO sessions VALUES ('s1','o1','active',1,'{}','[]','t','t','2099-01-01T00:00:00Z',NULL,NULL)")
        conn.execute("INSERT INTO documents VALUES ('d1','s1',1,'t',4,'t','t')")
    doc = Document(document_id="d1", session_id="s1", document_revision=1, input_revision=1, title="t", target_pages=4, status="draft",
                   pages=[Page(page_id="p", title="t", layout_key="text", blocks=[Block(block_id="b1", type="paragraph", content={"text": "x"})])])
    return "s1", doc


def _rec(key, result):
    return export_render.LayoutCheckRecord(key, True, result)


def test_open_layout_issue_is_resolved_only_by_a_rerun_check(app, settings):
    sid, doc = _seed_document(settings)
    overflow = IssueDraft("layout", "LAYOUT_OVERFLOW", "blocker", "넘침", block_ids=["b1"], origin="layout", layout_format="pdf")
    doc_level = IssueDraft("layout", "BROKEN_IMAGE", "blocker", "문서 전체", block_ids=[], origin="layout", layout_format="pdf")
    pub = IssueDraft("layout", "IMAGE_PUBLICATION_UNCONFIRMED", "blocker", "허가", block_ids=["b1"], origin="layout", layout_format=None)
    with connect(settings.db_path) as conn:
        layout_check_jobs.persist_layout_issues(conn, sid, doc, "lc1", "pdf", [overflow, doc_level, pub], 1,
                                                checks=[_rec("overflow", "finding"), _rec("broken_image", "finding"), _rec("placeholder_remaining", "ok")], publication_checked=True)
        statuses = lambda: {r["code"]: r["status"] for r in conn.execute("SELECT code, status FROM issues")}  # noqa: E731
        assert statuses() == {"LAYOUT_OVERFLOW": "open", "BROKEN_IMAGE": "open", "IMAGE_PUBLICATION_UNCONFIRMED": "open"}
        # 측정 실패(overflow not_checked)·broken_image not_checked·허가 미검사 → findings에 없어도 전부 open 유지
        layout_check_jobs.persist_layout_issues(conn, sid, doc, "lc2", "pdf", [], 1,
                                                checks=[_rec("overflow", "not_checked"), _rec("broken_image", "not_checked"), _rec("placeholder_remaining", "ok")], publication_checked=False)
        assert statuses() == {"LAYOUT_OVERFLOW": "open", "BROKEN_IMAGE": "open", "IMAGE_PUBLICATION_UNCONFIRMED": "open"}
        # 다른 형식(docx)의 재검사는 pdf Issue를 닫지 않는다(허가 Issue는 형식 무관이라 닫힐 수 있다)
        layout_check_jobs.persist_layout_issues(conn, sid, doc, "lc3", "docx", [], 1,
                                                checks=[_rec("overflow", "ok"), _rec("broken_image", "ok"), _rec("placeholder_remaining", "ok")], publication_checked=False)
        assert statuses() == {"LAYOUT_OVERFLOW": "open", "BROKEN_IMAGE": "open", "IMAGE_PUBLICATION_UNCONFIRMED": "open"}
        # 실제로 재실행한 검사에서 원인이 없을 때만 resolved(형식별·허가 분리)
        layout_check_jobs.persist_layout_issues(conn, sid, doc, "lc4", "pdf", [], 1,
                                                checks=[_rec("overflow", "ok"), _rec("broken_image", "not_checked"), _rec("placeholder_remaining", "ok")], publication_checked=True)
        assert statuses() == {"LAYOUT_OVERFLOW": "resolved", "BROKEN_IMAGE": "open", "IMAGE_PUBLICATION_UNCONFIRMED": "resolved"}
        res = conn.execute("SELECT resolution_json FROM issues WHERE code='LAYOUT_OVERFLOW'").fetchone()
        assert json.loads(res["resolution_json"])["layout_check_id"] == "lc4"
        layout_check_jobs.persist_layout_issues(conn, sid, doc, "lc5", "pdf", [], 1,
                                                checks=[_rec("overflow", "ok"), _rec("broken_image", "ok"), _rec("placeholder_remaining", "ok")], publication_checked=True)
        assert statuses()["BROKEN_IMAGE"] == "resolved"


def _fake_render_factory(plan: dict):
    """render()를 가짜 결과로 바꾼다. plan[fmt] = (overflow result, findings). 파일은 out_dir에 가짜 PDF를 만든다."""
    def fake_render(snapshot, fmt, out_dir, settings=None):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{snapshot.document_id}_rev{snapshot.document_revision}.{fmt}"
        path.write_bytes(b"%PDF-1.4 fake " + fmt.encode())
        result_kind, findings = plan[fmt]
        checks = [export_render.LayoutCheckRecord("overflow", True, result_kind, block_ids=[f.block_id for f in findings if f.kind == "overflow" and f.block_id]),
                  export_render.LayoutCheckRecord("broken_image", True, "ok"), export_render.LayoutCheckRecord("placeholder_remaining", True, "ok")]
        return export_render.RenderResult(format=fmt, file_path=path, actual_pages=1, checks=checks, findings=list(findings),
                                          layout_ok=all(c.result == "ok" for c in checks), template_version=layout_checks.TEMPLATE_VERSION,
                                          render_options_hash=layout_checks.RENDER_OPTIONS_HASH, asset_manifest_hash=snapshot.asset_manifest_hash,
                                          renderer="fake/1", elapsed_ms=1, details={})
    return fake_render


def test_layout_check_job_keeps_issue_open_when_measure_not_checked(app, settings, monkeypatch):
    flow = Flow(app, settings)
    flow.validate()
    block = flow.doc()["pages"][0]["blocks"][0]["block_id"]
    overflow = export_render.Finding("overflow", flow.doc()["pages"][0]["page_id"], block, "넘침", {"excess_mm": 10.0})
    plan = {"pdf": ("finding", [overflow]), "docx": ("not_checked", [])}
    monkeypatch.setattr(export_render, "render", _fake_render_factory(plan))
    monkeypatch.setattr(layout_check_jobs, "_render_previews", lambda pdf_path, out_dir: [])

    def check():
        r = flow.c.post(f"/api/v1/sessions/{flow.sid}/documents/{flow.did}/layout-checks", json={"expected_revision": flow.rev(), "format": "pdf"})
        assert r.status_code == 202, r.text
        flow.job(r.json()["job_id"])
        return flow.get()["layout_checks"]["pdf"]
    lc1 = check()
    issues = lambda: {(i["code"], i["status"]) for i in flow.c.get(f"/api/v1/sessions/{flow.sid}/documents/{flow.did}/issues").json()["issues"] if i["scope"] == "layout"}  # noqa: E731
    assert lc1["status"] == "failed" and issues() == {("LAYOUT_OVERFLOW", "open")}
    plan["pdf"] = ("not_checked", [])                       # 측정 실패: findings 없음이지만 overflow는 not_checked
    lc2 = check()
    assert lc2["status"] == "failed" and lc2["fail_reasons"] == ["overflow:not_checked"] and issues() == {("LAYOUT_OVERFLOW", "open")}
    plan["pdf"] = ("ok", [])                                # 정상 측정에서 원인이 없을 때만 resolved
    lc3 = check()
    assert lc3["status"] == "passed" and issues() == {("LAYOUT_OVERFLOW", "resolved")}


# ================= 마이그레이션·규칙(브라우저 불필요) =================

def test_layout_issue_keys_survive_db_reinit(settings, app):
    with connect(settings.db_path) as conn:
        conn.execute("INSERT INTO sessions VALUES ('s1','o1','active',1,'{}','[]','t','t','2099-01-01T00:00:00Z',NULL,NULL)")
        conn.execute("INSERT INTO documents VALUES ('d1','s1',1,'t',4,'t','t')")
        conn.execute("INSERT INTO issues (issue_id, session_id, document_id, identity_key, scope, code, severity, status, message, "
                     "source_ids_json, fact_ids_json, block_ids_json, origin, created_at, updated_at, layout_format) "
                     "VALUES ('i1','s1','d1','layout|layout|LAYOUT_OVERFLOW|b1|||pdf','layout','LAYOUT_OVERFLOW','blocker','open','m','[]','[]','[\"b1\"]','layout','t','t','pdf')")
    for _ in range(3):
        init_db(settings.db_path, settings.private_runs_dir)
    with connect(settings.db_path) as conn:
        rows = conn.execute("SELECT identity_key, layout_format FROM issues").fetchall()
        assert [(r["identity_key"], r["layout_format"]) for r in rows] == [("layout|layout|LAYOUT_OVERFLOW|b1|||pdf", "pdf")]
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 7


def test_v6_to_v7_migration_adds_columns_and_tables_keeping_rows(tmp_path):
    db, runs = tmp_path / "v6.sqlite3", tmp_path / "runs"
    init_db(db, runs)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE artifacts"); conn.execute("DROP TABLE exports"); conn.execute("DROP TABLE layout_previews")
        conn.execute("DROP TABLE layout_checks")
        conn.execute("CREATE TABLE layout_checks (layout_check_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, document_id TEXT NOT NULL, "
                     "document_revision INTEGER NOT NULL, input_revision INTEGER NOT NULL, format TEXT NOT NULL, template_version TEXT NOT NULL, "
                     "render_options_hash TEXT NOT NULL, asset_manifest_hash TEXT NOT NULL, status TEXT NOT NULL, actual_pages INTEGER, "
                     "issue_ids_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL)")
        conn.execute("INSERT INTO layout_checks VALUES ('lc1','s1','d1',1,1,'pdf','template_v0','h','m','passed',4,'[]','t')")
        conn.execute("PRAGMA user_version=6")
    init_db(db, runs)
    init_db(db, runs)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        cols = [r[1] for r in conn.execute("PRAGMA table_info(layout_checks)")]
        assert {"layout_ok", "publication_policy_ok", "checks_json", "artifact_id", "preview_ids_json"} <= set(cols)
        row = conn.execute("SELECT * FROM layout_checks WHERE layout_check_id='lc1'").fetchone()
        assert row["status"] == "passed" and row["actual_pages"] == 4 and row["layout_ok"] is None
        assert {"artifacts", "exports", "layout_previews"} <= {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "layout_format" in [r[1] for r in conn.execute("PRAGMA table_info(issues)")]
        assert "renderer" in [r[1] for r in conn.execute("PRAGMA table_info(approvals)")]
    assert layout_check_jobs.to_out(row).layout_ok is False and layout_check_jobs.to_out(row).checks == []


def test_publication_check_semantics(settings, app):
    with connect(settings.db_path) as conn:
        conn.execute("INSERT INTO sessions VALUES ('s1','o1','active',1,'{}','[]','t','t','2099-01-01T00:00:00Z',NULL,NULL)")
        conn.execute("INSERT INTO sources (source_id, session_id, source_version, scope, name, mime_type, size_bytes, kind, parse_status, "
                     "text_available, image_available, stored_path, content_hash, created_at) VALUES ('R1',NULL,1,'registered','r','image/png',1,'photo','complete',0,1,'x','h','t')")
        conn.execute("INSERT INTO sources (source_id, session_id, source_version, scope, name, mime_type, size_bytes, kind, parse_status, "
                     "text_available, image_available, stored_path, content_hash, created_at) VALUES ('S1','s1',1,'session','s','image/png',1,'photo','complete',0,1,'x','h','t')")
        for aid, src, scope, sid, val in (("a_null", "R1", "registered", None, None), ("a_false", "R1", "registered", None, 0),
                                          ("a_true", "R1", "registered", None, 1), ("a_sess", "S1", "session", "s1", None)):
            conn.execute("INSERT INTO assets (asset_id, source_id, source_version, scope, session_id, origin, mime_type, width, height, content_hash, "
                         "status, stored_path, created_at, approved_for_external_use) VALUES (?, ?, 1, ?, ?, 'source_image', 'image/png', 1, 1, 'h', 'ready', 'x', 't', ?)",
                         (aid, src, scope, sid, val))

        def doc(*asset_ids):
            blocks = [Block(block_id=f"b_{a}", type="image", content={"asset_id": a, "alt": "", "caption": "", "fit": "contain"}) for a in asset_ids]
            return Document(document_id="d", session_id="s1", document_revision=1, input_revision=1, title="t", target_pages=1, status="draft",
                            pages=[Page(page_id="p", title="t", layout_key="text", blocks=blocks)])
        assert publication_service.check_document(conn, doc("a_true", "a_sess")).ok
        res = publication_service.check_document(conn, doc("a_null", "a_false", "a_true"))
        assert not res.ok and {(b.asset_id, b.reason) for b in res.blocked} == {("a_null", "not_decided"), ("a_false", "denied")}
        assert res.reason_codes() == ["publication:denied", "publication:not_decided"]
        assert publication_service.on_publication_changed(conn, "a_true", 1, 1) == {"approvals_invalidated": 0, "exports_finalized": 0}


def test_no_real_company_terms_in_be08_code():
    import inspect

    from app.routers import exports as exports_router
    from app.routers import layout_checks as layout_router

    for mod in (artifacts, exports, layout_check_jobs, publication_service, exports_router, layout_router):
        for banned in BANNED:
            assert banned not in inspect.getsource(mod)
