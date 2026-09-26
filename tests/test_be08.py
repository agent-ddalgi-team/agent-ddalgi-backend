"""BE-08 확인: 배치 검사 Job·미리보기·승인 ⑥ 실체화·불변 artifact·Export·다운로드·공개 허가·Issue 분리·재시작 복구·만료.

모든 자료는 가상(clean TXT·Pillow PNG·가짜 등록 묶음). 실제 AI 호출 없음(mock Agent). PDF 배치 검사는 시스템 Chromium 계열
브라우저가 있을 때만 실제로 돈다. 없으면 해당 테스트는 skip으로 표시된다(통과가 아니다). DOCX 승인·Export·다운로드는 이 범위에서 차단이 정상.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.agent_mock import MockAgent
from app.config import Settings
from app.db import connect
from app.services import artifacts, export_render, exports, layout_check_jobs, registered

ROOT = Path(__file__).resolve().parent.parent
BRIEF = {"purpose": "테스트", "emphasis": [], "direction": "balanced", "target_pages": 4, "photo_preference": "balanced"}
CLEAN_TXT = "회사명: 예시 회사\n회사 개요: 예시 회사는 가상 부품 표면처리와 검사를 하는 테스트 기업입니다.\n사업 분야: 가상 부품 표면처리\n".encode()
LONG_UNIT = "[MOCK] 긴 문단의 줄바꿈과 배치 경고를 시험하는 가상 문장입니다."
BANNED = ("거산", "케미칼", "Geosan")
BROWSER_PATH_ENV = (os.environ.get("EXPORT_BROWSER_PATH") or "").strip() or None
BROWSER = export_render.find_browser(Settings(private_runs_dir=Path("."), db_path=Path("."), export_browser_path=BROWSER_PATH_ENV))
needs_browser = pytest.mark.skipif(BROWSER is None, reason="Chromium 계열 브라우저 없음 — 배치 검사(PDF) 테스트 미실행")


def _png(width: int = 8, height: int = 6, color=(10, 20, 30)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def settings(tmp_path):
    return Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "t.sqlite3", export_browser_path=BROWSER_PATH_ENV)


@pytest.fixture
def app(settings):
    return create_app(settings)


class Flow:
    """세션 + clean TXT/PNG 업로드 + mock 초안 + 검증(passed). 배치 검사·승인·출력 헬퍼."""

    def __init__(self, app, settings, *, upload_png: bool = True, registered_ids: list[str] | None = None):
        self.app, self.settings = app, settings
        self.c = TestClient(app)
        self.sid = self.c.post("/api/v1/sessions", json={"brief": BRIEF}).json()["session_id"]
        files = [("files", ("a.txt", io.BytesIO(CLEAN_TXT)))]
        if upload_png:
            files.append(("files", ("p.png", io.BytesIO(_png()))))
        up = self.c.post(f"/api/v1/sessions/{self.sid}/sources", files=files).json()
        selected = (registered_ids or []) + [i["source_id"] for i in up["items"]]
        r = self.c.patch(f"/api/v1/sessions/{self.sid}/inputs", json={"expected_input_revision": 1, "selected_source_ids": selected})
        assert r.status_code == 200, r.text
        self.rev_in = r.json()["input_revision"]
        job = self.c.post(f"/api/v1/sessions/{self.sid}/preflights", json={"expected_input_revision": self.rev_in}).json()
        self.pf = self.job(job["job_id"])["result_ref"]["preflight_id"]
        job = self.c.post(f"/api/v1/sessions/{self.sid}/drafts", json={"preflight_id": self.pf, "input_revision": self.rev_in, "confirmed": True}).json()
        self.did = self.job(job["job_id"])["result_ref"]["document_id"]

    # ---- 기본 ----
    def job(self, jid, *, expect="succeeded"):
        for _ in range(600):
            job = self.c.get(f"/api/v1/sessions/{self.sid}/jobs/{jid}").json()
            if job["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.05)
        if expect is not None:
            assert job["status"] == expect, job
        return job

    def get(self) -> dict:
        return self.c.get(f"/api/v1/sessions/{self.sid}/documents/{self.did}").json()

    def doc(self) -> dict:
        return self.get()["document"]

    def rev(self) -> int:
        return self.doc()["document_revision"]

    def patch(self, ops, expected=None):
        r = self.c.patch(f"/api/v1/sessions/{self.sid}/documents/{self.did}",
                         json={"expected_revision": self.rev() if expected is None else expected, "operations": ops})
        assert r.status_code == 200, r.text
        return r.json()

    def clean(self):
        """mock 초안의 안내 문단·사진 자리를 지워 검증이 passed가 되게 한다(BE-06 make_clean_and_validate와 같은 규칙)."""
        ops = []
        for page in self.doc()["pages"]:
            for b in page["blocks"]:
                if (b["type"] == "paragraph" and b["content"]["text"] == "추가 확인 필요") or b["type"] == "image_placeholder":
                    ops.append({"op": "delete_block", "block_id": b["block_id"]})
        if ops:
            self.patch(ops)

    def validate(self) -> dict:
        r = self.c.post(f"/api/v1/sessions/{self.sid}/documents/{self.did}/validate",
                        json={"expected_revision": self.rev(), "input_revision": self.rev_in})
        assert r.status_code == 202, r.text
        self.job(r.json()["job_id"])
        v = self.get()["validation"]
        assert v is not None
        return v

    def issues(self) -> list[dict]:
        return self.c.get(f"/api/v1/sessions/{self.sid}/documents/{self.did}/issues").json()["issues"]

    def open_issues(self, code=None):
        return [i for i in self.issues() if i["status"] == "open" and (code is None or i["code"] == code)]

    # ---- BE-08 ----
    def layout_check(self, fmt="pdf", *, expected=None, headers=None, expect_job="succeeded") -> tuple[dict, dict]:
        r = self.c.post(f"/api/v1/sessions/{self.sid}/documents/{self.did}/layout-checks",
                        json={"expected_revision": self.rev() if expected is None else expected, "format": fmt}, headers=headers or {})
        assert r.status_code == 202, r.text
        job = self.job(r.json()["job_id"], expect=expect_job)
        return r.json(), job

    def ready_pdf(self) -> tuple[dict, dict]:
        """clean → validate(passed) → pdf 배치 검사(passed). (validation, layout_check) 반환."""
        self.clean()
        v = self.validate()
        assert v["status"] == "passed", v
        _, job = self.layout_check("pdf")
        lc = self.get()["layout_checks"]["pdf"]
        assert lc is not None and lc["layout_check_id"] == job["result_ref"]["layout_check_id"]
        assert lc["status"] == "passed", lc
        return v, lc

    def approve(self, vid, lcid, *, fmt="pdf", headers=None, confirmed=True):
        return self.c.post(f"/api/v1/sessions/{self.sid}/documents/{self.did}/approvals",
                           json={"expected_revision": self.rev(), "input_revision": self.rev_in, "format": fmt,
                                 "validation_id": vid, "layout_check_id": lcid, "confirmed": confirmed}, headers=headers or {})

    def export(self, approval_id, *, fmt="pdf", key=None):
        return self.c.post(f"/api/v1/sessions/{self.sid}/exports", json={"approval_id": approval_id, "format": fmt},
                           headers={"Idempotency-Key": key} if key else {})

    def export_ready(self, approval_id, *, key=None) -> dict:
        r = self.export(approval_id, key=key)
        assert r.status_code in (200, 202), r.text
        body = r.json()
        if body["export"]["status"] != "ready":
            self.job(body["job_id"])
        r2 = self.export(approval_id)           # 키 없이 현재 상태 확인(같은 키는 최초 응답을 그대로 돌려준다)
        assert r2.status_code == 200 and r2.json()["export"]["status"] == "ready", r2.text
        return r2.json()["export"]

    def download(self, eid):
        return self.c.get(f"/api/v1/sessions/{self.sid}/exports/{eid}/download")

    def approved_pdf(self) -> tuple[dict, dict]:
        """(approval, export ready)."""
        v, lc = self.ready_pdf()
        r = self.approve(v["validation_id"], lc["layout_check_id"])
        assert r.status_code == 201, r.text
        return r.json(), self.export_ready(r.json()["approval_id"])


def _count(settings, sql, *params) -> int:
    with connect(settings.db_path) as conn:
        return conn.execute(sql, params).fetchone()[0]


def _no_paths(obj) -> None:
    text = json.dumps(obj, ensure_ascii=False)
    for needle in ("private_runs", "stored_path", "browser_path", "file_path", ":\\\\", "C:/"):
        assert needle not in text, needle


# ================= PDF 완주 =================

@needs_browser
def test_pdf_layout_check_approve_export_download(app, settings):
    flow = Flow(app, settings)
    v, lc = flow.ready_pdf()
    # 배치 검사 결과: 3종 required ok, 미리보기, artifact, 렌더러. 서버 경로 없음
    assert lc["layout_ok"] and lc["publication_policy_ok"] and lc["fail_reasons"] == [] and lc["findings"] == []
    assert {c["check_key"]: c["result"] for c in lc["checks"]} == {"overflow": "ok", "broken_image": "ok", "placeholder_remaining": "ok"}
    assert lc["actual_pages"] == len(flow.doc()["pages"]) and lc["renderer"].startswith(("chrome/", "edge/", "chromium/"))
    assert lc["preview_basis"] == "pdf" and len(lc["preview_asset_ids"]) == lc["actual_pages"] and lc["artifact_id"].startswith("art_")
    _no_paths(flow.get())
    # 미리보기는 기존 GET /assets/{asset_id}로
    png = flow.c.get(f"/api/v1/sessions/{flow.sid}/assets/{lc['preview_asset_ids'][0]}")
    assert png.status_code == 200 and png.headers["content-type"].startswith("image/png") and png.content[:8] == b"\x89PNG\r\n\x1a\n"
    # 승인 201(실제 행), renderer·artifact 기록
    r = flow.approve(v["validation_id"], lc["layout_check_id"])
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["status"] == "active" and a["layout_check_id"] == lc["layout_check_id"] and a["artifact_id"] == lc["artifact_id"] and a["renderer"] == lc["renderer"]
    assert flow.doc()["status"] == "approved"
    # Export 202 → ready → 다운로드
    r = flow.export(a["approval_id"])
    assert r.status_code in (200, 202), r.text
    body = r.json()
    if body["export"]["status"] != "ready":
        job = flow.job(body["job_id"])
        assert job["result_ref"]["export_id"] == body["export"]["export_id"]
    exp = flow.export(a["approval_id"]).json()["export"]
    assert exp["status"] == "ready" and exp["artifact_id"] == lc["artifact_id"] and exp["error"] is None
    _no_paths(exp)
    d = flow.download(exp["export_id"])
    assert d.status_code == 200 and d.headers["content-type"] == "application/pdf"
    assert 'filename="company_intro_draft.pdf"' in d.headers["content-disposition"] and "filename*=UTF-8''" in d.headers["content-disposition"]
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(d.content))
    assert len(reader.pages) == lc["actual_pages"] and "예시 회사" in (reader.pages[0].extract_text() or "")
    assert not any(w in (reader.pages[0].extract_text() or "") for w in BANNED)
    with connect(settings.db_path) as conn:
        art = conn.execute("SELECT * FROM artifacts WHERE artifact_id=?", (lc["artifact_id"],)).fetchone()
    assert hashlib.sha256(d.content).hexdigest() == art["sha256"] and len(d.content) == art["size_bytes"]


@needs_browser
def test_stress_document_layout_failed_then_fixed_and_issue_separation(app, settings):
    flow = Flow(app, settings)
    flow.clean()
    first_page = flow.doc()["pages"][0]["page_id"]
    ref = next(b for pg in flow.doc()["pages"] for b in pg["blocks"] if b["type"] == "paragraph" and b["fact_ids"])
    flow.patch([{"op": "insert_block", "page_id": first_page, "after_block_id": None,
                 "block": {"block_id": "b_long", "type": "paragraph", "content": {"text": " ".join(["다음은 회사 소개입니다."] * 400)},
                           "fact_ids": ref["fact_ids"], "evidence_refs": ref["evidence_refs"]}},
                {"op": "insert_block", "page_id": first_page, "after_block_id": None,
                 "block": {"block_id": "b_ph", "type": "image_placeholder", "content": {"description": "사진 자리"}}}])
    v = flow.validate()
    assert v["status"] == "passed", v   # 연결 문장·사진 자리는 내용 blocker가 아니다
    _, job = flow.layout_check("pdf")
    lc = flow.get()["layout_checks"]["pdf"]
    assert lc["status"] == "failed" and "overflow:finding" in lc["fail_reasons"] and "placeholder_remaining:finding" in lc["fail_reasons"]
    open_layout = flow.open_issues()
    codes = {i["code"]: i for i in open_layout if i["scope"] == "layout"}
    assert set(codes) == {"LAYOUT_OVERFLOW", "PLACEHOLDER_REMAINING"}
    assert all(i["origin"] == "layout" and i["layout_format"] == "pdf" and i["severity"] == "blocker" for i in codes.values())
    assert codes["PLACEHOLDER_REMAINING"]["block_ids"] == ["b_ph"] and codes["LAYOUT_OVERFLOW"]["block_ids"] == ["b_long"]
    # 내용 Validation은 배치 Issue를 닫지 않는다(그대로 open), Validation 상태도 배치 Issue에 영향받지 않는다
    v2 = flow.validate()
    assert v2["status"] == "passed" and {i["code"] for i in flow.open_issues() if i["scope"] == "layout"} == {"LAYOUT_OVERFLOW", "PLACEHOLDER_REMAINING"}
    # 직접 해결 시도는 배치 재검사 요구
    iid = codes["LAYOUT_OVERFLOW"]["issue_id"]
    for action, code in (("resolved", "LAYOUT_RECHECK_REQUIRED"), ("acknowledged", "RESOLUTION_NOT_ALLOWED"), ("excluded", "RESOLUTION_NOT_ALLOWED")):
        r = flow.c.post(f"/api/v1/sessions/{flow.sid}/issues/{iid}/resolve",
                        json={"expected_revision": flow.rev(), "resolution": {"action": action, "reason": "테스트"}})
        assert r.status_code == 422 and r.json()["error"]["code"] == code, (action, r.text)
    # 승인은 ⑥에서 차단(status_failed)
    r = flow.approve(v2["validation_id"], lc["layout_check_id"])
    assert r.status_code == 422 and r.json()["error"]["details"]["reason"] == "status_failed"
    # DOCX 검사: 같은 사진 자리 Issue가 docx 형식으로 따로 생긴다(PDF 검사가 닫지 않는다)
    flow.layout_check("docx")
    docx_issue = [i for i in flow.open_issues("PLACEHOLDER_REMAINING") if i["layout_format"] == "docx"]
    assert len(docx_issue) == 1
    # 블록 삭제 후 pdf 재검사 → pdf Issue만 resolved, docx Issue는 그대로 open
    flow.patch([{"op": "delete_block", "block_id": "b_long"}, {"op": "delete_block", "block_id": "b_ph"}])
    v3 = flow.validate()
    _, job = flow.layout_check("pdf")
    lc2 = flow.get()["layout_checks"]["pdf"]
    assert lc2["status"] == "passed" and lc2["layout_check_id"] != lc["layout_check_id"]
    by_fmt = {(i["code"], i["layout_format"]): i["status"] for i in flow.issues() if i["scope"] == "layout"}
    assert by_fmt[("LAYOUT_OVERFLOW", "pdf")] == "resolved" and by_fmt[("PLACEHOLDER_REMAINING", "pdf")] == "resolved"
    assert by_fmt[("PLACEHOLDER_REMAINING", "docx")] == "open"
    resolved = next(i for i in flow.issues() if i["code"] == "LAYOUT_OVERFLOW")
    assert resolved["resolution"]["by"] == "server" and resolved["resolution"]["layout_check_id"] == lc2["layout_check_id"]
    r = flow.approve(v3["validation_id"], lc2["layout_check_id"])
    assert r.status_code == 201, r.text          # docx Issue는 pdf 승인을 막지 않는다(형식별)


@needs_browser
def test_docx_check_creates_file_and_pdf_preview_but_stays_blocked(app, settings):
    flow = Flow(app, settings)
    v, _ = flow.ready_pdf()
    _, job = flow.layout_check("docx")
    lc = flow.get()["layout_checks"]["docx"]
    assert lc["status"] == "failed" and lc["fail_reasons"] == ["overflow:not_checked"] and lc["actual_pages"] is None
    assert lc["layout_ok"] is False and lc["publication_policy_ok"] is True and lc["findings"] == []
    assert lc["preview_basis"] == "pdf" and lc["preview_asset_ids"] and any("PDF 렌더 기준" in w for w in lc["warnings"])
    assert job["result_ref"]["status"] == "failed" and job["result_ref"]["warnings"]
    assert lc["renderer"].startswith("python-docx/")
    with connect(settings.db_path) as conn:
        art = conn.execute("SELECT * FROM artifacts WHERE artifact_id=?", (lc["artifact_id"],)).fetchone()
        assert art["format"] == "docx" and (settings.private_runs_dir / art["stored_path"]).is_file()
    assert flow.open_issues() == []          # not_checked는 Issue가 아니다
    r = flow.approve(v["validation_id"], lc["layout_check_id"], fmt="docx")
    assert r.status_code == 422 and r.json()["error"]["details"]["reason"] == "status_failed"
    a, _ = flow.approved_pdf()
    r = flow.export(a["approval_id"], fmt="docx")
    assert r.status_code == 422 and r.json()["error"]["code"] == "EXPORT_NOT_ALLOWED"


# ================= 승인 후 변경·재사용·동시성 =================

@needs_browser
def test_export_and_download_rejected_after_document_or_input_change(app, settings):
    flow = Flow(app, settings)
    a, exp = flow.approved_pdf()
    r_ok = flow.export(a["approval_id"], key="K-before")
    assert r_ok.status_code == 200
    flow.patch([{"op": "rename_page", "page_id": flow.doc()["pages"][0]["page_id"], "title": "바뀐 제목"}])
    r = flow.export(a["approval_id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "APPROVAL_NOT_ACTIVE"
    r = flow.export(a["approval_id"], key="K-before")            # 멱등 재전송도 현재 승인 상태가 먼저
    assert r.status_code == 409 and r.json()["error"]["code"] == "APPROVAL_NOT_ACTIVE"
    d = flow.download(exp["export_id"])
    assert d.status_code == 409 and d.json()["error"]["code"] == "APPROVAL_NOT_ACTIVE"
    with connect(settings.db_path) as conn:
        e = conn.execute("SELECT status, finalized_reason FROM exports WHERE export_id=?", (exp["export_id"],)).fetchone()
    assert e["status"] == "failed" and e["finalized_reason"] == "approval_invalid"
    # 입력 변경 후도 동일(새 승인본으로 다시 진행한 뒤 입력을 바꾼다)
    flow2 = Flow(app, settings)
    a2, exp2 = flow2.approved_pdf()
    r = flow2.c.patch(f"/api/v1/sessions/{flow2.sid}/inputs", json={"expected_input_revision": flow2.rev_in, "brief": dict(BRIEF, purpose="변경")})
    assert r.status_code == 200
    assert flow2.export(a2["approval_id"]).status_code == 409 and flow2.download(exp2["export_id"]).status_code == 409


@needs_browser
def test_export_reuse_concurrency_and_idempotency_conflict(app, settings):
    flow = Flow(app, settings)
    v, lc = flow.ready_pdf()
    a = flow.approve(v["validation_id"], lc["layout_check_id"]).json()
    results, errors = [], []

    def run(i):
        try:
            c = TestClient(app)
            c.cookies = flow.c.cookies
            results.append(c.post(f"/api/v1/sessions/{flow.sid}/exports", json={"approval_id": a["approval_id"], "format": "pdf"}))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and all(r.status_code in (200, 202) for r in results), [r.text for r in results]
    ids = {r.json()["export"]["export_id"] for r in results}
    assert len(ids) == 1
    for r in results:
        if r.json()["job_id"]:
            flow.job(r.json()["job_id"])
    assert _count(settings, "SELECT COUNT(*) FROM exports WHERE approval_id=?", a["approval_id"]) == 1
    assert _count(settings, "SELECT COUNT(*) FROM jobs WHERE kind='export' AND session_id=?", flow.sid) == 1
    r1 = flow.export(a["approval_id"], key="K1")
    assert r1.status_code == 200 and r1.json()["export"]["status"] == "ready" and r1.json()["export"]["export_id"] in ids
    r2 = flow.export(a["approval_id"], key="K1")
    assert r2.status_code == 200 and r2.json() == r1.json()
    r3 = flow.c.post(f"/api/v1/sessions/{flow.sid}/exports", json={"approval_id": a["approval_id"], "format": "docx"}, headers={"Idempotency-Key": "K1"})
    assert r3.status_code in (409, 422)
    if r3.status_code == 409:
        assert r3.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


@needs_browser
def test_export_uses_checked_artifact_without_browser_or_agent(app, settings, monkeypatch):
    flow = Flow(app, settings)
    v, lc = flow.ready_pdf()
    a = flow.approve(v["validation_id"], lc["layout_check_id"]).json()
    MockAgent.validate_calls = 0
    calls = {"render": 0, "agent": 0}
    monkeypatch.setattr(export_render, "find_browser", lambda *a, **k: None)
    real_render = export_render.render

    def spy_render(*a, **k):
        calls["render"] += 1
        return real_render(*a, **k)
    monkeypatch.setattr(export_render, "render", spy_render)
    from app import agent_bridge

    real_get = agent_bridge.get_bridge

    def spy_agent(*a, **k):
        calls["agent"] += 1
        return real_get(*a, **k)
    monkeypatch.setattr(agent_bridge, "get_bridge", spy_agent)
    exp = flow.export_ready(a["approval_id"])
    d = flow.download(exp["export_id"])
    assert d.status_code == 200 and calls == {"render": 0, "agent": 0} and MockAgent.validate_calls == 0
    with connect(settings.db_path) as conn:
        art = conn.execute("SELECT sha256, stored_path FROM artifacts WHERE artifact_id=?", (lc["artifact_id"],)).fetchone()
    assert hashlib.sha256(d.content).hexdigest() == art["sha256"]
    # 다운로드 재요청은 파일을 다시 만들지 않는다
    path = settings.private_runs_dir / art["stored_path"]
    mtime = path.stat().st_mtime_ns
    d2 = flow.download(exp["export_id"])
    assert d2.status_code == 200 and path.stat().st_mtime_ns == mtime and d2.content == d.content


@needs_browser
def test_missing_or_tampered_artifact_fails_and_invalidates_approval_without_rollback(app, settings):
    flow = Flow(app, settings)
    a, exp = flow.approved_pdf()
    with connect(settings.db_path) as conn:
        art = conn.execute("SELECT stored_path FROM artifacts WHERE artifact_id=?", (a["artifact_id"],)).fetchone()
    path = settings.private_runs_dir / art["stored_path"]
    original = path.read_bytes()
    path.write_bytes(original + b"tampered")
    d = flow.download(exp["export_id"])
    assert d.status_code == 422 and d.json()["error"]["code"] == "ARTIFACT_INVALID" and d.json()["error"]["details"]["recheck_required"]
    with connect(settings.db_path) as conn:
        ap = conn.execute("SELECT status, invalidated_reason FROM approvals WHERE approval_id=?", (a["approval_id"],)).fetchone()
        ex = conn.execute("SELECT status, finalized_reason FROM exports WHERE export_id=?", (exp["export_id"],)).fetchone()
    assert (ap["status"], ap["invalidated_reason"]) == ("invalidated", "artifact_invalid")   # 오류 응답에도 커밋됨
    assert (ex["status"], ex["finalized_reason"]) == ("failed", "artifact_invalid")
    assert flow.get()["approval"] is None
    # 자동 재렌더 없음: 파일은 변조된 채 그대로, 새 artifact 없음
    assert path.read_bytes() == original + b"tampered" and _count(settings, "SELECT COUNT(*) FROM artifacts") == 1
    # 재요청: 승인이 무효라 APPROVAL_NOT_ACTIVE. 누락 사례(파일 삭제) → 요청 시점 ARTIFACT_INVALID + 승인 무효화
    assert flow.export(a["approval_id"]).json()["error"]["code"] == "APPROVAL_NOT_ACTIVE"
    flow2 = Flow(app, settings)
    v, lc = flow2.ready_pdf()
    a2 = flow2.approve(v["validation_id"], lc["layout_check_id"]).json()
    with connect(settings.db_path) as conn:
        p2 = settings.private_runs_dir / conn.execute("SELECT stored_path FROM artifacts WHERE artifact_id=?", (a2["artifact_id"],)).fetchone()[0]
    p2.unlink()
    r = flow2.export(a2["approval_id"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "ARTIFACT_INVALID" and r.json()["error"]["details"]["reason"] == "missing"
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT status FROM approvals WHERE approval_id=?", (a2["approval_id"],)).fetchone()[0] == "invalidated"
    # 재검사·재승인으로만 회복
    v, lc2 = flow2.ready_pdf()
    assert lc2["layout_check_id"] != lc["layout_check_id"]
    assert flow2.approve(v["validation_id"], lc2["layout_check_id"]).status_code == 201


@needs_browser
def test_recheck_does_not_replace_approval_artifact_and_explicit_reapproval_supersedes(app, settings):
    flow = Flow(app, settings)
    a, exp = flow.approved_pdf()
    with connect(settings.db_path) as conn:
        art = conn.execute("SELECT stored_path, sha256 FROM artifacts WHERE artifact_id=?", (a["artifact_id"],)).fetchone()
    path = settings.private_runs_dir / art["stored_path"]
    before = path.read_bytes()
    flow.layout_check("pdf")                                     # 같은 revision 재검사 → 새 artifact
    lc_new = flow.get()["layout_checks"]["pdf"]
    assert lc_new["artifact_id"] != a["artifact_id"] and path.read_bytes() == before
    assert flow.get()["approval"]["approval_id"] == a["approval_id"] and flow.get()["approval"]["artifact_id"] == a["artifact_id"]
    d = flow.download(exp["export_id"])
    assert d.status_code == 200 and hashlib.sha256(d.content).hexdigest() == art["sha256"]
    # 같은 검사 재승인 요청 → 같은 승인 재사용
    v = flow.get()["validation"]
    same = flow.approve(v["validation_id"], a["layout_check_id"])
    assert same.status_code == 201 and same.json()["approval_id"] == a["approval_id"]
    # 새 검사를 명시적으로 승인 → 새 Approval, 이전은 superseded, 형식별 active 1건
    r = flow.approve(v["validation_id"], lc_new["layout_check_id"])
    assert r.status_code == 201 and r.json()["approval_id"] != a["approval_id"] and r.json()["artifact_id"] == lc_new["artifact_id"]
    with connect(settings.db_path) as conn:
        rows = conn.execute("SELECT approval_id, status, invalidated_reason FROM approvals WHERE document_id=? ORDER BY rowid", (flow.did,)).fetchall()
    assert [(r_["status"], r_["invalidated_reason"]) for r_ in rows] == [("invalidated", "superseded"), ("active", None)]
    assert _count(settings, "SELECT COUNT(*) FROM approvals WHERE document_id=? AND status='active' AND format='pdf'", flow.did) == 1
    assert flow.download(exp["export_id"]).status_code == 409   # 옛 승인의 Export는 더 이상 내려주지 않는다


# ================= 재시도·재시작·만료 =================

@needs_browser
def test_retry_policy_and_restart_recovery(app, settings, monkeypatch):
    flow = Flow(app, settings)
    v, lc = flow.ready_pdf()
    a = flow.approve(v["validation_id"], lc["layout_check_id"]).json()
    real_publish = exports.publish
    state = {"fail": True}

    def flaky_publish(conn, s, row):
        if state["fail"]:
            state["fail"] = False
            raise RuntimeError("io glitch")
        return real_publish(conn, s, row)
    monkeypatch.setattr(exports, "publish", flaky_publish)
    r = flow.export(a["approval_id"], key="K-a")
    assert r.status_code == 202
    job = flow.job(r.json()["job_id"], expect="failed")
    assert job["error"]["code"] == "EXPORT_FAILED" and job["error"]["retryable"] is True
    eid = r.json()["export"]["export_id"]
    same = flow.export(a["approval_id"], key="K-a")             # 같은 키: 최초 응답(queued) 그대로
    assert same.status_code == 202 and same.json() == r.json()
    new = flow.export(a["approval_id"], key="K-b")              # 새 키: 같은 승인본 Export를 재사용해 attempt 2
    assert new.status_code == 202 and new.json()["export"]["export_id"] == eid and new.json()["export"]["attempt"] == 2
    flow.job(new.json()["job_id"])
    assert flow.export(a["approval_id"]).json()["export"]["status"] == "ready"
    assert _count(settings, "SELECT COUNT(*) FROM exports WHERE approval_id=?", a["approval_id"]) == 1
    # 재시작 복구: generating으로 남은 Export → 유효하면 ready + Job succeeded(오류 제거)
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE exports SET status='generating', published_at=NULL WHERE export_id=?", (eid,))
        jid = conn.execute("SELECT job_id FROM exports WHERE export_id=?", (eid,)).fetchone()[0]
        conn.execute("UPDATE jobs SET status='running' WHERE job_id=?", (jid,))
    app2 = create_app(settings)
    with connect(settings.db_path) as conn:
        e = conn.execute("SELECT status, artifact_id FROM exports WHERE export_id=?", (eid,)).fetchone()
        j = conn.execute("SELECT status, result_ref_json, error_json FROM jobs WHERE job_id=?", (jid,)).fetchone()
    assert e["status"] == "ready" and e["artifact_id"] == a["artifact_id"]
    assert j["status"] == "succeeded" and json.loads(j["result_ref_json"])["export_id"] == eid and j["error_json"] is None
    # 재시작 복구 중 승인이 무효면 실제 사유로 failed(일시 오류 아님)
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE exports SET status='generating' WHERE export_id=?", (eid,))
        conn.execute("UPDATE approvals SET status='invalidated', invalidated_reason='document_changed' WHERE approval_id=?", (a["approval_id"],))
    create_app(settings)
    with connect(settings.db_path) as conn:
        e = conn.execute("SELECT status, error_json FROM exports WHERE export_id=?", (eid,)).fetchone()
        j = conn.execute("SELECT status, error_json FROM jobs WHERE job_id=?", (jid,)).fetchone()
    assert e["status"] == "failed" and json.loads(e["error_json"])["code"] == "APPROVAL_NOT_ACTIVE" and json.loads(e["error_json"])["retryable"] is False
    assert j["status"] == "failed" and json.loads(j["error_json"])["code"] == "APPROVAL_NOT_ACTIVE"
    del app2


@needs_browser
def test_expired_export_is_finalized_not_reused_and_new_key_creates_new_row(app, settings):
    flow = Flow(app, settings)
    a, exp = flow.approved_pdf()
    r = flow.export(a["approval_id"], key="K-old")
    assert r.status_code == 200
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE exports SET expires_at='2000-01-01T00:00:00Z' WHERE export_id=?", (exp["export_id"],))
    d = flow.download(exp["export_id"])
    assert d.status_code == 410 and d.json()["error"]["code"] == "ARTIFACT_EXPIRED"
    r = flow.export(a["approval_id"], key="K-old")                # 같은 키(캐시): 410, 되살리지 않음
    assert r.status_code == 410 and r.json()["error"]["code"] == "ARTIFACT_EXPIRED"
    with connect(settings.db_path) as conn:
        old = conn.execute("SELECT status, finalized_reason, expires_at FROM exports WHERE export_id=?", (exp["export_id"],)).fetchone()
    assert (old["status"], old["finalized_reason"], old["expires_at"]) == ("failed", "expired", "2000-01-01T00:00:00Z")
    r = flow.export(a["approval_id"], key="K-new")                # 새 키: UNIQUE 충돌 없이 새 행
    assert r.status_code in (200, 202), r.text
    new_id = r.json()["export"]["export_id"]
    assert new_id != exp["export_id"]
    if r.json()["job_id"]:
        flow.job(r.json()["job_id"])
    assert flow.download(new_id).status_code == 200
    assert _count(settings, "SELECT COUNT(*) FROM exports WHERE approval_id=?", a["approval_id"]) == 2
    # 만료된 세션에서는 새 키라도 생성 불가
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00Z' WHERE session_id=?", (flow.sid,))
    assert flow.export(a["approval_id"], key="K-late").status_code == 410


# ================= 완료 직전 재확인(늦은 결과 미공개) =================

@needs_browser
def test_layout_check_discards_late_results(app, settings, monkeypatch):
    flow = Flow(app, settings)
    flow.clean()
    flow.validate()
    real_render = export_render.render
    mode = {"action": None}

    def render_then_change(snapshot, fmt, out_dir, s=None):
        result = real_render(snapshot, fmt, out_dir, s)
        if mode["action"] == "edit":
            flow.patch([{"op": "rename_page", "page_id": flow.doc()["pages"][0]["page_id"], "title": "렌더 중 변경"}])
        elif mode["action"] == "close":
            assert flow.c.delete(f"/api/v1/sessions/{flow.sid}").status_code == 200
        elif mode["action"] == "asset":
            with connect(settings.db_path) as conn:
                conn.execute("UPDATE assets SET content_hash='changed' WHERE session_id=?", (flow.sid,))
        mode["action"] = None
        return result
    monkeypatch.setattr(export_render, "render", render_then_change)
    mode["action"] = "edit"
    _, job = flow.layout_check("pdf", expect_job="failed")
    assert job["error"]["code"] == "DOCUMENT_REVISION_CONFLICT"
    mode["action"] = "asset"
    _, job = flow.layout_check("pdf", expect_job="failed")
    assert job["error"]["code"] == "ASSET_MANIFEST_CHANGED"
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE assets SET content_hash=(SELECT content_hash FROM sources WHERE sources.source_id=assets.source_id) WHERE session_id=?", (flow.sid,))
    assert _count(settings, "SELECT COUNT(*) FROM layout_checks") == 0 and _count(settings, "SELECT COUNT(*) FROM artifacts") == 0
    assert _count(settings, "SELECT COUNT(*) FROM layout_previews") == 0
    # 임시 산출물(HTML·PDF·PNG·DOCX)은 남지 않는다. 브라우저 프로필 찌꺼기 폴더는 자식 프로세스 때문에 잠시 남을 수 있어 정기 정리 대상
    leftovers = [p for p in (settings.private_runs_dir / flow.sid / "artifacts").rglob("*")
                 if p.is_file() and p.suffix.lower() in (".pdf", ".png", ".docx", ".html")]
    assert leftovers == []
    mode["action"] = "close"
    r = flow.c.post(f"/api/v1/sessions/{flow.sid}/documents/{flow.did}/layout-checks", json={"expected_revision": flow.rev(), "format": "pdf"})
    assert r.status_code == 202
    with connect(settings.db_path) as conn:
        for _ in range(600):
            j = conn.execute("SELECT status, error_json FROM jobs WHERE job_id=?", (r.json()["job_id"],)).fetchone()
            if j["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.05)
    assert j["status"] == "failed" and json.loads(j["error_json"])["code"] == "SESSION_EXPIRED"
    assert _count(settings, "SELECT COUNT(*) FROM layout_checks") == 0


# ================= 등록 사진 공개 허가 =================

def _fake_bundle(root: Path, *, approved) -> Path:
    """비mock 등록 자료 1건(clean TXT) + 사진 1장(photo_id FAKE_IMG01)의 최소 묶음."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "orig.txt").write_text("회사명: 예시 회사\n회사 개요: 예시 회사는 가상 부품 표면처리와 검사를 하는 테스트 기업입니다.\n", encoding="utf-8")
    img_dir = root / "05_이미지" / "전체_추출이미지" / "01_제품"
    img_dir.mkdir(parents=True, exist_ok=True)
    (img_dir / "FAKE_IMG01.png").write_bytes(_png(24, 16, (30, 120, 40)))
    sha_txt = hashlib.sha256((root / "orig.txt").read_bytes()).hexdigest()
    sha_png = hashlib.sha256((img_dir / "FAKE_IMG01.png").read_bytes()).hexdigest()
    (root / "sources.json").write_text(json.dumps([{"source_id": "FAKE01", "name": "가상 등록 자료", "status": "ready", "path": "orig.txt",
                                                    "sha256": sha_txt, "available_in_package": True, "use_as_company_evidence": True}], ensure_ascii=False), encoding="utf-8")
    (root / "company_chunks.jsonl").write_text(json.dumps({"chunk_id": "FAKE01_C001", "source_id": "FAKE01", "text": "회사명: 예시 회사",
                                                           "locator": "TXT 1행", "evidence_status": "자료에 기재됨"}, ensure_ascii=False) + "\n", encoding="utf-8")
    with (root / "00_이미지목록.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["폴더", "파일명", "PPT페이지", "원본이미지", "처리", "가로px", "세로px"])
        w.writerow(["01_제품", "FAKE_IMG01.png", "1", "image1.png", "원본 그대로 추출", "24", "16"])
    (root / "photo_candidates.json").write_text(json.dumps([{"photo_id": "FAKE_IMG01", "path": "05_이미지/전체_추출이미지/01_제품/FAKE_IMG01.png",
                                                             "source_id": "FAKE01", "sha256": sha_png, "selected_as_candidate": True,
                                                             "approved_for_external_use": approved, "caption_candidate": "공장 사진"}], ensure_ascii=False), encoding="utf-8")
    return root


def _cli(settings, bundle: Path, *flags: str) -> dict:
    env = dict(os.environ, DB_PATH=str(settings.db_path), PRIVATE_RUNS_DIR=str(settings.private_runs_dir), PYTHONIOENCODING="utf-8", AGENT_MODE="mock")
    cp = subprocess.run([sys.executable, str(ROOT / "scripts" / "import_registered.py"), "--source-dir", str(bundle), *flags],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=str(ROOT), timeout=120)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    return json.loads(cp.stdout.strip().splitlines()[-1])


@needs_browser
def test_registered_photo_publication_blocks_until_true_and_cli_update_revokes(app, settings, tmp_path):
    bundle = _fake_bundle(tmp_path / "bundle", approved=None)
    first = _cli(settings, bundle)
    assert first["added_sources"] == 1 and first["added_assets"] == 1
    with connect(settings.db_path) as conn:
        asset = conn.execute("SELECT asset_id FROM assets WHERE photo_id='FAKE_IMG01'").fetchone()["asset_id"]
    flow = Flow(app, settings, upload_png=False, registered_ids=["FAKE01"])
    flow.clean()
    page = flow.doc()["pages"][0]["page_id"]
    flow.patch([{"op": "insert_block", "page_id": page, "after_block_id": None,
                 "block": {"block_id": "b_reg", "type": "image", "content": {"asset_id": asset, "alt": "공장 사진", "caption": "공장 사진", "fit": "contain"}}}])
    v = flow.validate()
    assert v["status"] == "passed", v
    # null → 배치 검사 실패(publication:not_decided), Issue 형식 무관, 승인 차단
    _, job = flow.layout_check("pdf")
    lc = flow.get()["layout_checks"]["pdf"]
    assert lc["status"] == "failed" and lc["layout_ok"] is True and lc["publication_policy_ok"] is False
    assert lc["fail_reasons"] == ["publication:not_decided"] and lc["publication_blocks"][0]["reason"] == "not_decided"
    issue = flow.open_issues("IMAGE_PUBLICATION_UNCONFIRMED")
    assert len(issue) == 1 and issue[0]["layout_format"] is None and issue[0]["origin"] == "layout"
    r = flow.approve(v["validation_id"], lc["layout_check_id"])
    assert r.status_code == 422 and r.json()["error"]["details"]["reason"] == "status_failed"
    # false → denied
    _cli(settings, _fake_bundle(tmp_path / "bundle_false", approved=False), "--update-publication")
    _, job = flow.layout_check("pdf")
    assert flow.get()["layout_checks"]["pdf"]["fail_reasons"] == ["publication:denied"]
    # 플래그 없는 재적재는 값을 바꾸지 않는다(pending만 셈). dry-run도 변경 없음
    noflag = _cli(settings, _fake_bundle(tmp_path / "bundle_true", approved=True))
    assert noflag["publication_pending"] == 1 and noflag["publication_updated"] == 0 and noflag["added_assets"] == 0
    dry = _cli(settings, tmp_path / "bundle_true", "--update-publication", "--dry-run")
    assert dry["publication_updated"] == 0
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT approved_for_external_use FROM assets WHERE asset_id=?", (asset,)).fetchone()[0] == 0
    # true → 통과 → 승인 → Export ready(source/asset 중복 없음)
    upd = _cli(settings, tmp_path / "bundle_true", "--update-publication")
    assert upd["publication_updated"] == 1 and upd["added_sources"] == 0 and upd["added_assets"] == 0
    _, job = flow.layout_check("pdf")
    lc = flow.get()["layout_checks"]["pdf"]
    assert lc["status"] == "passed" and flow.open_issues("IMAGE_PUBLICATION_UNCONFIRMED") == []
    v = flow.get()["validation"]
    a = flow.approve(v["validation_id"], lc["layout_check_id"]).json()
    exp = flow.export_ready(a["approval_id"], key="K-pub")
    assert flow.download(exp["export_id"]).status_code == 200
    # true → null 철회(실제 CLI): 같은 트랜잭션으로 승인 무효화·Export 확정 실패, 중복 생성 없음
    revoke = _cli(settings, _fake_bundle(tmp_path / "bundle_null", approved=None), "--update-publication")
    assert revoke["publication_updated"] == 1 and revoke["approvals_invalidated"] == 1 and revoke["exports_finalized"] == 1
    assert _count(settings, "SELECT COUNT(*) FROM sources WHERE source_id='FAKE01'") == 1 and _count(settings, "SELECT COUNT(*) FROM assets WHERE photo_id='FAKE_IMG01'") == 1
    with connect(settings.db_path) as conn:
        ap = conn.execute("SELECT status, invalidated_reason FROM approvals WHERE approval_id=?", (a["approval_id"],)).fetchone()
    assert (ap["status"], ap["invalidated_reason"]) == ("invalidated", "publication_changed")
    assert flow.download(exp["export_id"]).status_code == 409
    r = flow.export(a["approval_id"], key="K-pub")                   # 멱등 재전송도 차단
    assert r.status_code == 409 and r.json()["error"]["code"] == "APPROVAL_NOT_ACTIVE"
    assert flow.export(a["approval_id"], key="K-pub2").status_code == 409
    # photo_id 같은데 바이트가 다르면 충돌
    conflict = _fake_bundle(tmp_path / "bundle_conflict", approved=True)
    (conflict / "05_이미지" / "전체_추출이미지" / "01_제품" / "FAKE_IMG01.png").write_bytes(_png(24, 16, (9, 9, 9)))
    cands = json.loads((conflict / "photo_candidates.json").read_text(encoding="utf-8"))
    cands[0]["sha256"] = hashlib.sha256(_png(24, 16, (9, 9, 9))).hexdigest()
    (conflict / "photo_candidates.json").write_text(json.dumps(cands, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, DB_PATH=str(settings.db_path), PRIVATE_RUNS_DIR=str(settings.private_runs_dir), PYTHONIOENCODING="utf-8", AGENT_MODE="mock")
    cp = subprocess.run([sys.executable, str(ROOT / "scripts" / "import_registered.py"), "--source-dir", str(conflict), "--update-publication"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=str(ROOT), timeout=120)
    assert cp.returncode == 1 and "PHOTO_ID_CONFLICT" in cp.stdout


@needs_browser
def test_publication_revoked_during_render_is_not_saved_as_passed(app, settings, tmp_path, monkeypatch):
    bundle = _fake_bundle(tmp_path / "bundle", approved=True)
    _cli(settings, bundle)
    with connect(settings.db_path) as conn:
        asset = conn.execute("SELECT asset_id FROM assets WHERE photo_id='FAKE_IMG01'").fetchone()["asset_id"]
    flow = Flow(app, settings, upload_png=False, registered_ids=["FAKE01"])
    flow.clean()
    page = flow.doc()["pages"][0]["page_id"]
    flow.patch([{"op": "insert_block", "page_id": page, "after_block_id": None,
                 "block": {"block_id": "b_reg", "type": "image", "content": {"asset_id": asset, "alt": "공장 사진", "caption": "공장 사진", "fit": "contain"}}}])
    flow.validate()
    real_render = export_render.render

    def render_then_revoke(snapshot, fmt, out_dir, s=None):
        result = real_render(snapshot, fmt, out_dir, s)
        with connect(settings.db_path) as conn:
            conn.execute("UPDATE assets SET approved_for_external_use=NULL WHERE asset_id=?", (asset,))
        return result
    monkeypatch.setattr(export_render, "render", render_then_revoke)
    _, job = flow.layout_check("pdf")
    lc = flow.get()["layout_checks"]["pdf"]
    assert lc["status"] == "failed" and lc["publication_policy_ok"] is False and lc["fail_reasons"] == ["publication:not_decided"]
    assert flow.open_issues("IMAGE_PUBLICATION_UNCONFIRMED")


# ================= 미리보기·격리·경로 비노출·마이그레이션 =================

@needs_browser
def test_preview_isolation_and_no_leak_into_sources_or_evidence(app, settings):
    flow = Flow(app, settings)
    v, lc = flow.ready_pdf()
    pid = lc["preview_asset_ids"][0]
    assert flow.c.get(f"/api/v1/sessions/{flow.sid}/assets/{pid}").status_code == 200
    other = TestClient(app)
    other.post("/api/v1/sessions", json={"brief": BRIEF})
    assert other.get(f"/api/v1/sessions/{flow.sid}/assets/{pid}").status_code == 404
    flow2 = Flow(app, settings)
    assert flow2.c.get(f"/api/v1/sessions/{flow2.sid}/assets/{pid}").status_code == 404
    listed = flow.c.get(f"/api/v1/sessions/{flow.sid}/sources").json()["items"]
    assert not any(pid in i["asset_ids"] for i in listed)
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM assets WHERE asset_id=?", (pid,)).fetchone()[0] == 0
    r = flow.c.patch(f"/api/v1/sessions/{flow.sid}/documents/{flow.did}",
                     json={"expected_revision": flow.rev(), "operations": [{"op": "insert_block", "page_id": flow.doc()["pages"][0]["page_id"], "after_block_id": None,
                           "block": {"block_id": "b_prv", "type": "image", "content": {"asset_id": pid, "alt": "", "caption": "", "fit": "contain"}}}]})
    assert r.status_code == 422                                   # 미리보기는 문서 image asset으로 쓸 수 없다
    assert flow.c.delete(f"/api/v1/sessions/{flow.sid}").status_code == 200
    assert flow.c.get(f"/api/v1/sessions/{flow.sid}/assets/{pid}").status_code == 410


@needs_browser
def test_responses_expose_no_server_paths_and_mock_document_still_blocked(app, settings):
    flow = Flow(app, settings)
    a, exp = flow.approved_pdf()
    _no_paths(flow.get())
    _no_paths(flow.export(a["approval_id"]).json())
    with connect(settings.db_path) as conn:
        for row in conn.execute("SELECT result_ref_json FROM jobs WHERE session_id=?", (flow.sid,)):
            if row["result_ref_json"]:
                _no_paths(json.loads(row["result_ref_json"]))
    # mock 등록 문서는 배치 검사와 무관하게 승인 불가(MOCK_VALUE)
    registered.run(settings, ROOT / "tests" / "fixtures" / "ddalgi_mock_bundle_v1" / "ingest", with_mock=True)
    mock_flow = Flow(app, settings, upload_png=False, registered_ids=["MOCK01", "MOCK07"])
    mock_flow.clean()
    v = mock_flow.validate()
    assert v["status"] == "failed"
    _, job = mock_flow.layout_check("pdf")
    lc = mock_flow.get()["layout_checks"]["pdf"]
    r = mock_flow.approve(v["validation_id"], lc["layout_check_id"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_NOT_PASSED"
