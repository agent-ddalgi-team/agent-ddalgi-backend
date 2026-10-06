"""BE-09 세션 종료·만료 정리 테스트 — 실제 AI 없이 실행된다(배치 검사 행·artifact는 가짜 바이트로 직접 만든다).

일반 검사는 브라우저 없이, 실제 Chrome 프로필/서버 재시작 검사는 Windows+Chromium에서만 실행한다.

배경 sweep 스레드는 끄고(cleanup_sweep_interval_s=0) sweeper.sweep_once를 직접 부른다. 실제 회사 자료 없음.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.config import Settings
from app.db import SCHEMA_VERSION, connect, init_db
from app.services import ai_jobs, artifacts, cleanup, export_render, jobs, layout_check_jobs, layout_checks, reading, registered, sessions, sources, sweeper
from app.services.documents import get_current
from app.timeutil import from_iso, now, to_iso

ROOT = Path(__file__).resolve().parent.parent
INGEST = ROOT / "tests" / "fixtures" / "ddalgi_mock_bundle_v1" / "ingest"
MARK = "ZQXMARK9"     # 세션 내용에만 심는 고유 문자열. 정리 뒤 DB·응답 어디에도 남으면 안 된다.
BRIEF = {"purpose": f"테스트 {MARK}", "emphasis": [MARK], "direction": "balanced", "target_pages": 4, "photo_preference": "balanced"}
TXT = f"회사명: 예시 회사\n회사 개요: 예시 회사는 가상 부품 표면처리와 검사를 하는 테스트 기업입니다 {MARK}.\n사업 분야: 가상 부품 표면처리\n".encode()
PAST = "2000-01-01T00:00:00Z"


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 6), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def settings(tmp_path):
    return Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "t.sqlite3",
                    export_browser_path=str(tmp_path / "no-browser" / "chrome.exe"), cleanup_sweep_interval_s=0)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture(params=["legacy", "orm_v11"])
def restart_db(request, settings):
    from app.db import ORM_SCHEMA_VERSION, init_orm_db

    if request.param == "orm_v11":
        init_orm_db(settings.db_path, settings.private_runs_dir)
    yield create_app(settings), settings
    with connect(settings.db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == (ORM_SCHEMA_VERSION if request.param == "orm_v11" else SCHEMA_VERSION)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def _lock_windows_file(path):
    """실제 Windows 공유 삭제 금지 핸들. 오류나 assertion에도 호출자가 finally로 닫는다."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel, handle


@pytest.mark.skipif(sys.platform != "win32", reason="실제 Windows 파일 잠금 검사")
def test_windows_locked_long_profile_close_and_restart_recovery(restart_db):
    app, settings = restart_db
    flow = Flow(app, settings)
    root = settings.private_runs_dir / flow.sid
    # Chrome/Office가 남기는 MAX_PATH 초과 프로필 + 실제 읽기 전용 파일.
    profile = Path("\\\\?\\" + str(root.absolute())) / "chrome_profile"
    for index in range(5):
        profile /= f"component_{index}_" + "x" * 45
    profile.mkdir(parents=True)
    locked = profile / "LOCK"
    locked.write_bytes(b"synthetic browser lock")
    readonly = profile / "readonly.bin"
    readonly.write_bytes(b"synthetic component")
    readonly.chmod(0o444)
    assert len(str(locked)) > 260
    registered_file = settings.private_runs_dir / "registered" / "keep.txt"
    registered_file.parent.mkdir()
    registered_file.write_bytes(b"registered original")
    kernel, handle = _lock_windows_file(locked)
    try:
        response = flow.c.delete(f"/api/v1/sessions/{flow.sid}")
        assert response.json() == {"session_id": flow.sid, "status": "closed", "cleanup": "pending"}
        assert locked.exists() and MARK not in _db_text(settings)
        task = _row(settings, "SELECT * FROM cleanup_queue WHERE session_id=?", flow.sid)
        assert (task["status"], task["attempt"], task["last_error"]) == ("pending", 1, "remove_failed")
        # 이전 프로세스가 점유만 하고 사라진 실제 DB 상태에서 앱 시작 경로 실행.
        with connect(settings.db_path, immediate=True) as conn:
            claimed = cleanup.claim(conn, "interrupted-process", session_id=flow.sid, ignore_schedule=True)
            assert len(claimed) == 1
        restarted = TestClient(create_app(settings))
        restarted.cookies.update(flow.c.cookies)
        task = _row(settings, "SELECT * FROM cleanup_queue WHERE session_id=?", flow.sid)
        assert (task["status"], task["attempt"], task["claim_token"], task["last_error"]) == ("pending", 1, None, "stale_claim")
        response = restarted.get(f"/api/v1/sessions/{flow.sid}")
        assert response.status_code == 410 and response.json()["error"]["details"]["cleanup"] == "pending"
    finally:
        assert kernel.CloseHandle(handle)
    assert sweeper.sweep_once(settings)["done"] == 1
    assert not root.exists()
    assert restarted.get(f"/api/v1/sessions/{flow.sid}").json()["error"]["details"]["cleanup"] == "done"
    assert registered_file.read_bytes() == b"registered original"


@pytest.mark.skipif(sys.platform != "win32", reason="실제 Windows 파일 잠금 검사")
def test_windows_locked_job_temp_survives_startup_then_sweep_removes_it(restart_db):
    app, settings = restart_db
    flow = Flow(app, settings)
    with connect(settings.db_path) as conn:
        job = jobs.create(conn, flow.sid, "layout_check", "interrupted render")
        jobs.set_progress(conn, job.job_id, "rendering", "interrupted render")
    temp = artifacts.temp_dir(settings, flow.sid, job.job_id)
    locked = temp / "LOCK"
    locked.write_bytes(b"synthetic browser lock")
    kernel, handle = _lock_windows_file(locked)
    try:
        restarted = TestClient(create_app(settings))
        restarted.cookies.update(flow.c.cookies)
        assert temp.exists()
        assert _row(settings, "SELECT status FROM jobs WHERE job_id=?", job.job_id)["status"] == "failed"
        assert restarted.get(f"/api/v1/sessions/{flow.sid}").status_code == 200
        old = time.time() - cleanup.ORPHAN_TMP_MIN_AGE_S - 10
        os.utime(temp, (old, old))
        counts = sweeper.sweep_once(settings)
        assert counts["orphan_tmp"] == 1 and counts["retry"] == 1
    finally:
        assert kernel.CloseHandle(handle)
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE cleanup_queue SET next_retry_at=? WHERE session_id=?", (PAST, flow.sid))
    assert sweeper.sweep_once(settings)["done"] == 1
    assert not temp.exists()
    assert restarted.get(f"/api/v1/sessions/{flow.sid}").status_code == 200
    assert (settings.private_runs_dir / flow.sid).exists()


def _windows_child_pids(pid):
    """Toolhelp 스냅샷으로 이 테스트가 실행한 프로세스의 자손만 식별한다."""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    parents = {}
    try:
        entry = Entry(dwSize=ctypes.sizeof(Entry))
        more = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while more:
            parents[entry.pid] = entry.parent
            more = kernel.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    family = {pid}
    while True:
        found = {child for child, parent in parents.items() if parent in family} - family
        if not found:
            return family - {pid}, set(parents)
        family.update(found)


@pytest.mark.skipif(sys.platform != "win32", reason="실제 Windows Chrome 프로세스 검사")
@pytest.mark.parametrize("close_session", [True, False], ids=["closed_session", "active_orphan"])
def test_actual_chrome_profile_close_process_restart_and_cleanup(restart_db, close_session):
    import socket
    from urllib.error import URLError
    from urllib.request import urlopen

    browser = export_render.find_browser()
    if browser is None:
        pytest.skip("Chromium 없음 — 실제 프로세스 정리 미실행")
    application, settings = restart_db
    flow = Flow(application, settings)
    live = Flow(application, settings)
    live_files = {p: p.read_bytes() for p in (settings.private_runs_dir / live.sid).rglob("*") if p.is_file()}
    registered_file = settings.private_runs_dir / "registered" / "keep.txt"
    registered_file.parent.mkdir()
    registered_file.write_bytes(b"registered original")
    with connect(settings.db_path) as conn:
        job = jobs.create(conn, flow.sid, "layout_check", "interrupted Chrome")
        jobs.set_progress(conn, job.job_id, "rendering", "interrupted Chrome")
    temp = artifacts.temp_dir(settings, flow.sid, job.job_id)
    profile = temp / "profile"
    args = export_render._browser_args(browser, profile) + ["--remote-debugging-port=0", "about:blank"]
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    child_pids = set()
    try:
        deadline = time.monotonic() + 25
        while not (profile / "DevToolsActivePort").is_file() and time.monotonic() < deadline:
            assert proc.poll() is None, "Chrome exited before profile initialization"
            time.sleep(0.05)
        assert (profile / "DevToolsActivePort").is_file(), "Chrome profile initialization timed out"
        child_pids, _ = _windows_child_pids(proc.pid)
        assert child_pids, "Chrome must have real child processes"
        os.utime(temp, (time.time() - cleanup.ORPHAN_TMP_MIN_AGE_S - 10,) * 2)
        if close_session:
            response = flow.c.delete(f"/api/v1/sessions/{flow.sid}")
            assert response.status_code == 200 and response.json()["cleanup"] == "pending"
            assert _count(settings, "SELECT COUNT(*) FROM sources WHERE session_id=? AND deleted_at IS NULL", flow.sid) == 0
            assert _row(settings, "SELECT brief_json FROM sessions WHERE session_id=?", flow.sid)[0] == "{}"
            assert all(_row(settings, "SELECT name FROM sources WHERE source_id=?", sid)[0] == "" for sid in flow.source_ids)
            with connect(settings.db_path, immediate=True) as conn:
                assert len(cleanup.claim(conn, "interrupted-process", session_id=flow.sid, ignore_schedule=True)) == 1
        else:
            assert sweeper.sweep_once(settings)["orphan_tmp"] == 0
            assert temp.exists() and flow.c.get(f"/api/v1/sessions/{flow.sid}").status_code == 200
        # 별도 Uvicorn 프로세스에서 앱 시작/작업 복구 후 실제 HTTP 응답까지 확인한다.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server = subprocess.Popen([sys.executable, "-X", "utf8", "-B", "-c",
            "import sys,uvicorn; from pathlib import Path; from app import create_app; from app.config import Settings; "
            "app=create_app(Settings(private_runs_dir=Path(sys.argv[1]),db_path=Path(sys.argv[2]),cleanup_sweep_interval_s=0)); "
            "uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[3]),log_level='error')",
            str(settings.private_runs_dir), str(settings.db_path), str(port)], cwd=ROOT,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 25
            while True:
                assert server.poll() is None, "Isolated server exited before HTTP recovery"
                try:
                    with urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                        assert response.status == 200 and json.load(response)["api"] == "/api/v1"
                    break
                except (URLError, TimeoutError):
                    assert time.monotonic() < deadline, "Isolated HTTP restart timed out"
                    time.sleep(0.05)
        finally:
            server.terminate()
            server.wait(timeout=15)
        if close_session:
            task = _row(settings, "SELECT * FROM cleanup_queue WHERE session_id=?", flow.sid)
            assert task["status"] == "pending" and task["claim_token"] is None and task["last_error"] == "stale_claim"
        else:
            assert _row(settings, "SELECT status FROM jobs WHERE job_id=?", job.job_id)["status"] == "failed"
            assert temp.exists()
            counts = sweeper.sweep_once(settings)
            assert counts["orphan_tmp"] == 1 and counts["retry"] == 1
        assert proc.poll() is None and temp.exists()
    finally:
        child_pids.update(_windows_child_pids(proc.pid)[0])
        export_render._kill_tree(proc)
        proc.wait(timeout=15)
        deadline = time.monotonic() + 10
        while child_pids & _windows_child_pids(proc.pid)[1] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not (child_pids & _windows_child_pids(proc.pid)[1]), "Chrome children survived tree termination"
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE cleanup_queue SET next_retry_at=? WHERE session_id=?", (PAST, flow.sid))
    assert sweeper.sweep_once(settings)["done"] == 1
    assert not temp.exists()
    response = flow.c.get(f"/api/v1/sessions/{flow.sid}")
    if close_session:
        assert response.status_code == 410 and response.json()["error"]["details"]["cleanup"] == "done"
        assert not (settings.private_runs_dir / flow.sid).exists()
    else:
        assert response.status_code == 200 and (settings.private_runs_dir / flow.sid).exists()
        assert _count(settings, "SELECT COUNT(*) FROM sources WHERE session_id=?", flow.sid) == 2
    assert live.c.get(f"/api/v1/sessions/{live.sid}").status_code == 200
    assert all(p.read_bytes() == content for p, content in live_files.items())
    assert registered_file.read_bytes() == b"registered original"


def _row(settings, sql, *params):
    with connect(settings.db_path) as conn:
        return conn.execute(sql, params).fetchone()


def _count(settings, sql, *params) -> int:
    return _row(settings, sql, *params)[0]


def _expire(settings, sid):
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE sessions SET expires_at=? WHERE session_id=?", (PAST, sid))


def _finalize_now(settings, sid, reason=cleanup.REASON_CLOSED):
    with connect(settings.db_path, immediate=True) as conn:
        return cleanup.finalize(conn, settings, sid, reason)


def _db_text(settings) -> str:
    """모든 테이블의 모든 행을 문자열로(내용 잔존 검사용)."""
    parts = []
    with connect(settings.db_path) as conn:
        for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            for r in conn.execute(f"SELECT * FROM {t['name']}").fetchall():
                parts.append(json.dumps([str(v) for v in tuple(r)], ensure_ascii=False))
    return "\n".join(parts)


class Flow:
    """세션 + 마커가 든 TXT/PNG + mock 사전 점검·초안. 배치 검사·승인·Export는 가짜 artifact로(브라우저 없음)."""

    def __init__(self, app, settings, *, key=None):
        self.settings = settings
        self.c = TestClient(app)
        headers = {"Idempotency-Key": key} if key else {}
        r = self.c.post("/api/v1/sessions", json={"brief": BRIEF}, headers=headers)
        assert r.status_code == 201, r.text
        self.sid = r.json()["session_id"]
        up = self.c.post(f"/api/v1/sessions/{self.sid}/sources",
                         files=[("files", (f"{MARK}_memo.txt", io.BytesIO(TXT))), ("files", ("p.png", io.BytesIO(_png())))],
                         headers={"Idempotency-Key": f"{key}-up"} if key else {})
        assert up.status_code == 202, up.text
        self.source_ids = [i["source_id"] for i in up.json()["items"]]
        r = self.c.patch(f"/api/v1/sessions/{self.sid}/inputs", json={"expected_input_revision": 1, "selected_source_ids": self.source_ids},
                         headers={"Idempotency-Key": f"{key}-patch"} if key else {})
        assert r.status_code == 200, r.text
        self.rev_in = r.json()["input_revision"]
        self.did = None

    def draft(self):
        job = self.c.post(f"/api/v1/sessions/{self.sid}/preflights", json={"expected_input_revision": self.rev_in}).json()
        pf = self.job(job["job_id"])["result_ref"]["preflight_id"]
        job = self.c.post(f"/api/v1/sessions/{self.sid}/drafts", json={"preflight_id": pf, "input_revision": self.rev_in, "confirmed": True}).json()
        self.did = self.job(job["job_id"])["result_ref"]["document_id"]
        ops = [{"op": "delete_block", "block_id": b["block_id"]} for p in self.doc()["pages"] for b in p["blocks"]
               if (b["type"] == "paragraph" and b["content"]["text"] == "추가 확인 필요") or b["type"] == "image_placeholder"]
        if ops:
            self.patch(ops)
        return self

    def job(self, jid, *, expect="succeeded"):
        for _ in range(600):
            job = self.c.get(f"/api/v1/sessions/{self.sid}/jobs/{jid}").json()
            if job["status"] in ("succeeded", "failed", "cancelled"):
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

    def fabricate(self) -> dict:
        adir = artifacts.artifacts_dir(self.settings, self.sid)
        adir.mkdir(parents=True, exist_ok=True)
        art_id, lc_id = f"art_{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:16]}", f"lc_{hashlib.sha256(str(time.time_ns() + 1).encode()).hexdigest()[:16]}"
        path = adir / f"{art_id}.pdf"
        data = b"%PDF-1.4 fake artifact " + art_id.encode() + b"\n"
        path.write_bytes(data)
        with connect(self.settings.db_path) as conn:
            doc = get_current(conn, self.sid, self.did)
            manifest = layout_checks.asset_manifest_hash(conn, doc)
            conn.execute("INSERT INTO artifacts (artifact_id, session_id, document_id, document_revision, input_revision, format, stored_path, sha256, "
                         "size_bytes, template_version, render_options_hash, asset_manifest_hash, renderer, actual_pages, layout_check_id, created_at) "
                         "VALUES (?, ?, ?, ?, ?, 'pdf', ?, ?, ?, ?, ?, ?, 'chrome/1', 1, ?, 't')",
                         (art_id, self.sid, self.did, doc.document_revision, self.rev_in, path.relative_to(self.settings.private_runs_dir).as_posix(),
                          hashlib.sha256(data).hexdigest(), len(data), layout_checks.TEMPLATE_VERSION, layout_checks.RENDER_OPTIONS_HASH, manifest, lc_id))
            conn.execute("INSERT INTO layout_checks (layout_check_id, session_id, document_id, document_revision, input_revision, format, template_version, "
                         "render_options_hash, asset_manifest_hash, status, actual_pages, issue_ids_json, created_at, layout_ok, publication_policy_ok, checks_json, "
                         "findings_json, fail_reasons_json, renderer, artifact_id, preview_basis, preview_ids_json, warnings_json, publication_blocks_json) "
                         "VALUES (?, ?, ?, ?, ?, 'pdf', ?, ?, ?, 'passed', 1, '[]', 't', 1, 1, '[]', '[]', '[]', 'chrome/1', ?, 'pdf', '[]', '[]', '[]')",
                         (lc_id, self.sid, self.did, doc.document_revision, self.rev_in, layout_checks.TEMPLATE_VERSION, layout_checks.RENDER_OPTIONS_HASH,
                          manifest, art_id))
        return {"layout_check_id": lc_id, "artifact_id": art_id, "path": path}

    def approved(self) -> dict:
        v = self.validate()
        fab = self.fabricate()
        r = self.c.post(f"/api/v1/sessions/{self.sid}/documents/{self.did}/approvals",
                        json={"expected_revision": self.rev(), "input_revision": self.rev_in, "format": "pdf",
                              "validation_id": v["validation_id"], "layout_check_id": fab["layout_check_id"], "confirmed": True})
        assert r.status_code == 201, r.text
        return r.json()

    def export_ready(self, approval_id) -> dict:
        r = self.c.post(f"/api/v1/sessions/{self.sid}/exports", json={"approval_id": approval_id, "format": "pdf"})
        assert r.status_code in (200, 202), r.text
        if r.json()["job_id"] and r.json()["export"]["status"] != "ready":
            self.job(r.json()["job_id"])
        r2 = self.c.post(f"/api/v1/sessions/{self.sid}/exports", json={"approval_id": approval_id, "format": "pdf"})
        assert r2.status_code == 200 and r2.json()["export"]["status"] == "ready", r2.text
        return r2.json()["export"]

    def download(self, eid):
        return self.c.get(f"/api/v1/sessions/{self.sid}/exports/{eid}/download")

    def propose(self):
        block = next(b["block_id"] for p in self.doc()["pages"] for b in p["blocks"] if b["type"] == "paragraph")
        r = self.c.post(f"/api/v1/sessions/{self.sid}/documents/{self.did}/proposals",
                        json={"expected_revision": self.rev(), "input_revision": self.rev_in, "target_block_ids": [block],
                              "instruction": f"문장을 다듬어 주세요 {MARK}", "kind": "text"})
        assert r.status_code == 202, r.text
        return self.job(r.json()["job_id"])


# ================= 1. DELETE: 내용 제거·메타 보존·등록 자료 유지 =================

def test_delete_purges_session_content_keeps_metadata_and_registered(app, settings):
    registered.run(settings, INGEST, with_mock=True)
    reg_before = (_count(settings, "SELECT COUNT(*) FROM sources WHERE scope='registered'"),
                  _count(settings, "SELECT COUNT(*) FROM segments WHERE session_id IS NULL"),
                  _count(settings, "SELECT COUNT(*) FROM assets WHERE scope='registered'"),
                  _count(settings, "SELECT COUNT(*) FROM registered_imports"))
    assert reg_before[0] > 0 and (settings.private_runs_dir / "registered").is_dir()
    reg_files = sorted(p.name for p in (settings.private_runs_dir / "registered").rglob("*") if p.is_file())

    flow = Flow(app, settings, key="K1").draft()
    a = flow.approved()
    exp = flow.export_ready(a["approval_id"])
    flow.propose()
    with connect(settings.db_path) as conn:
        conn.execute("INSERT INTO issues (issue_id, session_id, document_id, identity_key, scope, code, severity, status, message, source_ids_json, "
                     "fact_ids_json, block_ids_json, origin, resolution_json, resolution_history_json, created_at, updated_at) "
                     "VALUES ('i_m', ?, ?, 'k', 'content', 'X', 'warning', 'resolved', ?, '[]', '[]', '[]', 'server', ?, ?, 't', 't')",
                     (flow.sid, flow.did, f"문제 {MARK}", json.dumps({"action": "resolved", "reason": f"이유 {MARK}"}),
                      json.dumps([{"reason": f"옛 이유 {MARK}"}])))
        conn.execute("INSERT INTO jobs (job_id, session_id, kind, status, progress_json, error_json, created_at, updated_at) "
                     "VALUES ('job_err', ?, 'read', 'failed', '{}', ?, 't', 't')",
                     (flow.sid, json.dumps({"code": "PARSE_ERROR", "message": f"파일 {MARK}.txt 실패", "retryable": True, "details": {"file": MARK}})))
        conn.execute("INSERT INTO jobs (job_id, session_id, kind, status, progress_json, created_at, updated_at) VALUES ('job_run', ?, 'export', 'running', '{}', 't', 't')", (flow.sid,))
    assert MARK in _db_text(settings) and (settings.private_runs_dir / flow.sid).is_dir()
    meta_before = {t: _count(settings, f"SELECT COUNT(*) FROM {t} WHERE session_id=?", flow.sid)
                   for t in ("validations", "layout_checks", "approvals", "exports", "artifacts", "jobs", "preflights", "documents", "proposals", "issues")}

    r = flow.c.delete(f"/api/v1/sessions/{flow.sid}")
    assert r.status_code == 200 and r.json() == {"session_id": flow.sid, "status": "closed", "cleanup": "done"}
    assert not (settings.private_runs_dir / flow.sid).exists()
    # 내용 제거
    assert MARK not in _db_text(settings)
    s = _row(settings, "SELECT * FROM sessions WHERE session_id=?", flow.sid)
    assert s["status"] == "closed" and s["purged_at"] and s["closed_at"] and s["cleanup_status"] == "done" and s["brief_json"] == "{}"
    assert _count(settings, "SELECT COUNT(*) FROM segments WHERE session_id=?", flow.sid) == 0
    assert _row(settings, "SELECT name, warnings_json, deleted_at FROM sources WHERE source_id=?", flow.source_ids[0])[0] == ""
    assert _row(settings, "SELECT content_json FROM document_revisions WHERE document_id=? AND revision=1", flow.did)[0] == "{}"
    assert _row(settings, "SELECT title FROM documents WHERE document_id=?", flow.did)[0] == ""
    assert _row(settings, "SELECT facts_json FROM preflights WHERE session_id=?", flow.sid)[0] == "[]"
    assert _row(settings, "SELECT instruction, changes_json FROM proposals WHERE session_id=?", flow.sid)["changes_json"] == "{}"
    iss = _row(settings, "SELECT message, resolution_json, resolution_history_json, status, code FROM issues WHERE issue_id='i_m'")
    assert (iss["message"], iss["status"], iss["code"]) == ("", "resolved", "X")
    assert json.loads(iss["resolution_json"]) == {"action": "resolved", "purged": True}            # reason 제거, action 보존
    assert json.loads(iss["resolution_history_json"]) == [{"purged": True}] and MARK not in iss["resolution_history_json"]
    err = json.loads(_row(settings, "SELECT error_json FROM jobs WHERE job_id='job_err'")[0])
    assert err == {"code": "PARSE_ERROR", "message": "", "retryable": True, "details": {}, "request_id": None}
    j = _row(settings, "SELECT status, error_json FROM jobs WHERE job_id='job_run'")
    assert j["status"] == "cancelled" and json.loads(j["error_json"])["message"] == jobs.CANCELLED_ERROR["message"]
    idem = _row(settings, "SELECT response_json, purged_at, session_id FROM idempotency_keys WHERE idem_key='K1'")
    assert idem["response_json"] == "{}" and idem["purged_at"] and idem["session_id"] == flow.sid
    assert _count(settings, "SELECT COUNT(*) FROM idempotency_keys WHERE session_id=? AND purged_at IS NULL", flow.sid) == 0
    # 메타 보존(행 수·ID)
    for t, n in meta_before.items():
        assert _count(settings, f"SELECT COUNT(*) FROM {t} WHERE session_id=?", flow.sid) == n, t
    e = _row(settings, "SELECT status, finalized_reason, artifact_id FROM exports WHERE export_id=?", exp["export_id"])
    assert (e["status"], e["finalized_reason"], e["artifact_id"]) == ("failed", "session_closed", a["artifact_id"])
    assert _row(settings, "SELECT sha256 FROM artifacts WHERE artifact_id=?", a["artifact_id"]) is not None
    assert _row(settings, "SELECT status FROM cleanup_queue WHERE session_id=?", flow.sid)["status"] == "done"
    # 등록 자료 유지
    assert (_count(settings, "SELECT COUNT(*) FROM sources WHERE scope='registered'"), _count(settings, "SELECT COUNT(*) FROM segments WHERE session_id IS NULL"),
            _count(settings, "SELECT COUNT(*) FROM assets WHERE scope='registered'"), _count(settings, "SELECT COUNT(*) FROM registered_imports")) == reg_before
    assert sorted(p.name for p in (settings.private_runs_dir / "registered").rglob("*") if p.is_file()) == reg_files
    # 접근 차단: 410에 내용 없음, details.cleanup, 다른 소유자 404, 재삭제 멱등
    for path in (f"/api/v1/sessions/{flow.sid}", f"/api/v1/sessions/{flow.sid}/sources", f"/api/v1/sessions/{flow.sid}/documents/{flow.did}",
                 f"/api/v1/sessions/{flow.sid}/exports/{exp['export_id']}/download"):
        r = flow.c.get(path)
        assert r.status_code == 410 and r.json()["error"]["code"] == "SESSION_EXPIRED", path
        assert r.json()["error"]["details"] == {"status": "closed", "cleanup": "done"} and MARK not in r.text
    assert flow.c.post("/api/v1/sessions", json={"brief": BRIEF}, headers={"Idempotency-Key": "K1"}).status_code == 410
    assert flow.c.delete(f"/api/v1/sessions/{flow.sid}").json()["cleanup"] == "done"
    other = TestClient(app)
    other.post("/api/v1/sessions", json={"brief": BRIEF})
    assert other.get(f"/api/v1/sessions/{flow.sid}").status_code == 404 and other.delete(f"/api/v1/sessions/{flow.sid}").status_code == 404


def test_delete_failure_is_pending_then_retried_to_done(app, settings, monkeypatch):
    flow = Flow(app, settings)
    monkeypatch.setattr(cleanup, "_remove_tree", lambda path: False)
    r = flow.c.delete(f"/api/v1/sessions/{flow.sid}")
    assert r.json() == {"session_id": flow.sid, "status": "closed", "cleanup": "pending"}
    assert (settings.private_runs_dir / flow.sid).is_dir()
    t = _row(settings, "SELECT * FROM cleanup_queue WHERE session_id=?", flow.sid)
    assert (t["status"], t["attempt"], t["last_error"], t["claim_token"]) == ("pending", 1, "remove_failed", None)
    assert from_iso(t["next_retry_at"]) >= now() + timedelta(minutes=1)          # 백오프(2^1분)
    assert _row(settings, "SELECT status, purged_at FROM sessions WHERE session_id=?", flow.sid)["purged_at"]
    assert flow.c.get(f"/api/v1/sessions/{flow.sid}").json()["error"]["details"] == {"status": "closed", "cleanup": "pending"}
    # 아직 때가 안 됐으면 sweep이 건드리지 않는다
    assert sweeper.sweep_once(settings)["claimed"] == 0
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE cleanup_queue SET next_retry_at=? WHERE task_id=?", (PAST, t["task_id"]))
    counts = sweeper.sweep_once(settings)
    assert counts["claimed"] == 1 and counts["retry"] == 1
    assert _row(settings, "SELECT attempt, status FROM cleanup_queue WHERE task_id=?", t["task_id"])["attempt"] == 2
    monkeypatch.undo()
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE cleanup_queue SET next_retry_at=? WHERE task_id=?", (PAST, t["task_id"]))
    assert sweeper.sweep_once(settings)["done"] == 1
    assert not (settings.private_runs_dir / flow.sid).exists()
    assert _row(settings, "SELECT status, done_at FROM cleanup_queue WHERE task_id=?", t["task_id"])["status"] == "done"
    assert flow.c.get(f"/api/v1/sessions/{flow.sid}").json()["error"]["details"] == {"status": "closed", "cleanup": "done"}
    assert flow.c.delete(f"/api/v1/sessions/{flow.sid}").json()["cleanup"] == "done"


def test_late_file_after_cleanup_returns_to_pending(app, settings):
    flow = Flow(app, settings)
    assert flow.c.delete(f"/api/v1/sessions/{flow.sid}").json()["cleanup"] == "done"
    late = settings.private_runs_dir / flow.sid / "late.txt"
    late.parent.mkdir(parents=True)
    late.write_bytes(b"late")
    assert flow.c.get(f"/api/v1/sessions/{flow.sid}").json()["error"]["details"]["cleanup"] == "pending"   # 폴더가 다시 있으면 pending + 재등록
    assert _count(settings, "SELECT COUNT(*) FROM cleanup_queue WHERE session_id=? AND status='pending'", flow.sid) == 1
    assert sweeper.sweep_once(settings)["done"] == 1
    assert not late.parent.exists() and flow.c.get(f"/api/v1/sessions/{flow.sid}").json()["error"]["details"]["cleanup"] == "done"


# ================= 2. 큐 규칙 =================

def test_queue_dedup_claim_token_stale_max_attempts_and_retry(app, settings, monkeypatch):
    settings = Settings(**{**settings.__dict__, "cleanup_max_attempts": 2})
    flow = Flow(app, settings)
    _finalize_now(settings, flow.sid)
    with connect(settings.db_path, immediate=True) as conn:
        assert cleanup.enqueue(conn, flow.sid, "session_dir", flow.sid) is None                  # pending 중복 없음
        tasks = cleanup.claim(conn, "w1", ignore_schedule=True)
        assert len(tasks) == 1 and tasks[0]["claim_token"] and tasks[0]["claimed_by"] == "w1"
        assert cleanup.claim(conn, "w2", ignore_schedule=True) == []                            # running은 다시 점유 불가
        assert cleanup.enqueue(conn, flow.sid, "session_dir", flow.sid) is None                  # running 중복 없음
    task = tasks[0]
    stolen = dict(task)
    stolen["claim_token"] = "wrong"
    cleanup._record_done(settings, _fake_row(stolen))                                              # 토큰이 다르면 기록되지 않는다
    assert _row(settings, "SELECT status FROM cleanup_queue WHERE task_id=?", task["task_id"])["status"] == "running"
    with connect(settings.db_path, immediate=True) as conn:
        assert cleanup.reclaim_stale(conn, 3600) == 0                                            # TTL 안이면 유지
        assert cleanup.reclaim_stale(conn, 3600, all_running=True) == 1                          # 서버 시작: 전부 되찾음
        r = conn.execute("SELECT status, claim_token, last_error FROM cleanup_queue WHERE task_id=?", (task["task_id"],)).fetchone()
        assert (r["status"], r["claim_token"], r["last_error"]) == ("pending", None, "stale_claim")
        conn.execute("UPDATE cleanup_queue SET status='running', claimed_at=? WHERE task_id=?", (PAST, task["task_id"]))
        assert cleanup.reclaim_stale(conn, 600) == 1                                             # TTL 지난 점유는 sweep이 되찾음
    monkeypatch.setattr(cleanup, "_remove_tree", lambda path: False)
    for expected in ("retry", "failed"):
        with connect(settings.db_path) as conn:
            conn.execute("UPDATE cleanup_queue SET next_retry_at=? WHERE task_id=?", (PAST, task["task_id"]))
        counts = cleanup.process_due(settings, "w1")
        assert counts[expected] == 1, counts
    t = _row(settings, "SELECT status, attempt FROM cleanup_queue WHERE task_id=?", task["task_id"])
    assert (t["status"], t["attempt"]) == ("failed", 2)
    with connect(settings.db_path, immediate=True) as conn:
        assert cleanup.enqueue(conn, flow.sid, "session_dir", flow.sid) is None                  # failed는 자동 재등록 없음
        assert cleanup.verify_state(conn, settings, flow.sid) == "pending"                       # 내부 failed는 pending으로 보임
        assert _count(settings, "SELECT COUNT(*) FROM cleanup_queue WHERE session_id=?", flow.sid) == 1
    assert sweeper.sweep_once(settings)["claimed"] == 0
    monkeypatch.undo()
    with connect(settings.db_path, immediate=True) as conn:
        assert cleanup.retry_failed(conn) == 1 and cleanup.list_failed(conn) == []
    assert cleanup.process_due(settings, "w1")["done"] == 1
    assert not (settings.private_runs_dir / flow.sid).exists()
    with connect(settings.db_path, immediate=True) as conn:
        assert cleanup.verify_state(conn, settings, flow.sid) == "done"


def _fake_row(d: dict):
    class R(dict):
        def __getitem__(self, k):
            return dict.__getitem__(self, k)
    return R(d)


def test_unsafe_targets_are_never_deleted(app, settings):
    flow = Flow(app, settings)
    reg = settings.private_runs_dir / "registered" / "keep.txt"
    reg.parent.mkdir(parents=True)
    reg.write_bytes(b"keep")
    bad = [("registered", "session_dir", "registered"), (flow.sid, "session_dir", "registered"), (flow.sid, "session_dir", f"{flow.sid}/.."),
           (flow.sid, "orphan_tmp", f"{flow.sid}/artifacts/notmp"), (flow.sid, "session_dir", ".."), ("sess_unknown", "session_dir", "sess_unknown")]
    with connect(settings.db_path, immediate=True) as conn:
        for i, (sid, kind, rel) in enumerate(bad):
            conn.execute("INSERT INTO cleanup_queue (task_id, session_id, kind, target_rel, status, attempt, next_retry_at, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, 'pending', 0, 't', 't', 't')", (f"bad{i}", sid, kind, rel))
        conn.execute("UPDATE cleanup_queue SET next_retry_at=?", (PAST,))
    counts = cleanup.process_due(settings, "w")
    assert counts["failed"] == len(bad) and counts["done"] == 0
    assert reg.read_bytes() == b"keep" and (settings.private_runs_dir / flow.sid).is_dir() and settings.private_runs_dir.is_dir()
    with connect(settings.db_path) as conn:
        assert {r[0] for r in conn.execute("SELECT last_error FROM cleanup_queue").fetchall()} == {"unsafe_target"}


# ================= 3. 요청 경로 만료(별도 정리 트랜잭션·잠금 중첩 없음) =================

@pytest.mark.parametrize("kind", ["get", "patch", "approval", "download", "upload", "job"])
def test_request_path_expiry_is_persisted_and_cleaned(app, settings, kind):
    flow = Flow(app, settings).draft()
    a = flow.approved()
    exp = flow.export_ready(a["approval_id"])
    job_id = _row(settings, "SELECT job_id FROM jobs WHERE session_id=? ORDER BY rowid DESC LIMIT 1", flow.sid)[0]
    _expire(settings, flow.sid)
    calls = {
        "get": lambda: flow.c.get(f"/api/v1/sessions/{flow.sid}"),
        "patch": lambda: flow.c.patch(f"/api/v1/sessions/{flow.sid}/inputs", json={"expected_input_revision": flow.rev_in, "brief": BRIEF}),
        "approval": lambda: flow.c.post(f"/api/v1/sessions/{flow.sid}/documents/{flow.did}/approvals",
                                        json={"expected_revision": 1, "input_revision": flow.rev_in, "format": "pdf", "validation_id": "v", "layout_check_id": "l", "confirmed": True}),
        "download": lambda: flow.download(exp["export_id"]),
        "upload": lambda: flow.c.post(f"/api/v1/sessions/{flow.sid}/sources", files=[("files", ("late.txt", io.BytesIO(b"late")))]),
        "job": lambda: flow.c.get(f"/api/v1/sessions/{flow.sid}/jobs/{job_id}"),
    }
    r = calls[kind]()
    assert r.status_code == 410 and r.json()["error"]["code"] == "SESSION_EXPIRED", r.text
    assert r.json()["error"]["details"]["status"] == "expired" and r.json()["error"]["details"]["cleanup"] == "pending" and MARK not in r.text
    s = _row(settings, "SELECT status, purged_at, brief_json, cleanup_status FROM sessions WHERE session_id=?", flow.sid)
    assert (s["status"], s["brief_json"], s["cleanup_status"]) == ("expired", "{}", "pending") and s["purged_at"]   # 오류 응답에도 커밋됨
    assert _row(settings, "SELECT status, finalized_reason FROM exports WHERE export_id=?", exp["export_id"])["finalized_reason"] == "session_expired"
    assert _row(settings, "SELECT status FROM cleanup_queue WHERE session_id=?", flow.sid)["status"] == "pending"
    assert MARK not in _db_text(settings)
    assert (settings.private_runs_dir / flow.sid).is_dir()                       # 바이트 삭제는 큐가 한다
    assert calls[kind]().status_code == 410                                       # 재요청도 410(같은 결과)
    counts = sweeper.sweep_once(settings)
    assert counts["done"] == 1 and counts["expired"] == 0
    assert not (settings.private_runs_dir / flow.sid).exists()
    assert calls["get"]().json()["error"]["details"] == {"status": "expired", "cleanup": "done"}


def test_gone_check_does_not_commit_unrelated_changes_or_nest_locks(app, settings):
    """closed 세션에 immediate 연결로 들어와도 잠금 중첩 없이 410. 그 요청 트랜잭션에서 세션 상태 외 변경은 커밋되지 않는다."""
    flow = Flow(app, settings).draft()
    _expire(settings, flow.sid)
    before = _count(settings, "SELECT COUNT(*) FROM document_revisions WHERE document_id=?", flow.did)
    t0 = time.time()
    r = flow.c.patch(f"/api/v1/sessions/{flow.sid}/documents/{flow.did}", json={"expected_revision": 1, "operations": [{"op": "delete_block", "block_id": "x"}]})
    assert r.status_code == 410 and time.time() - t0 < 5                         # 잠금 대기(10초 timeout)에 걸리지 않음
    assert _count(settings, "SELECT COUNT(*) FROM document_revisions WHERE document_id=?", flow.did) == before
    assert _row(settings, "SELECT status FROM sessions WHERE session_id=?", flow.sid)["status"] == "expired"


# ================= 4. 멱등 재전송 =================

def test_idempotent_replay_after_expiry_or_close_returns_410_not_content(app, settings):
    flow = Flow(app, settings, key="R1")
    same = flow.c.post("/api/v1/sessions", json={"brief": BRIEF}, headers={"Idempotency-Key": "R1"})
    assert same.status_code == 201 and same.json()["session_id"] == flow.sid            # 살아 있으면 최초 응답
    _expire(settings, flow.sid)                                                          # sweep 없이 만료 시각만 지남
    r = flow.c.post("/api/v1/sessions", json={"brief": BRIEF}, headers={"Idempotency-Key": "R1"})
    assert r.status_code == 410 and r.json()["error"]["code"] == "SESSION_EXPIRED" and MARK not in r.text
    assert r.json()["error"]["details"]["status"] == "expired"
    s = _row(settings, "SELECT status, purged_at FROM sessions WHERE session_id=?", flow.sid)
    assert s["status"] == "expired" and s["purged_at"]                                   # 재전송 경로에서도 만료 확정·정리 등록
    assert _count(settings, "SELECT COUNT(*) FROM cleanup_queue WHERE session_id=?", flow.sid) == 1
    other = {**BRIEF, "purpose": "다른 본문"}
    assert flow.c.post("/api/v1/sessions", json={"brief": other}, headers={"Idempotency-Key": "R1"}).status_code == 409   # 같은 키·다른 본문 409 유지
    for key, call in (("R1-up", lambda: flow.c.post(f"/api/v1/sessions/{flow.sid}/sources", files=[("files", (f"{MARK}_memo.txt", io.BytesIO(TXT)))],
                                                    headers={"Idempotency-Key": "R1-up"})),
                      ("R1-patch", lambda: flow.c.patch(f"/api/v1/sessions/{flow.sid}/inputs", json={"expected_input_revision": 1, "selected_source_ids": flow.source_ids},
                                                        headers={"Idempotency-Key": "R1-patch"}))):
        r = call()
        assert r.status_code == 410 and MARK not in r.text, key
        assert _row(settings, "SELECT response_json, purged_at FROM idempotency_keys WHERE idem_key=?", key)["response_json"] == "{}"
    # 종료(DELETE)된 세션의 POST /sessions 재전송도 410(status=closed)
    flow2 = Flow(app, settings, key="R2")
    flow2.c.delete(f"/api/v1/sessions/{flow2.sid}")
    r = flow2.c.post("/api/v1/sessions", json={"brief": BRIEF}, headers={"Idempotency-Key": "R2"})
    assert r.status_code == 410 and r.json()["error"]["details"]["status"] == "closed" and MARK not in r.text
    new = flow2.c.post("/api/v1/sessions", json={"brief": BRIEF}, headers={"Idempotency-Key": "R3"})
    assert new.status_code == 201 and new.json()["session_id"] not in (flow.sid, flow2.sid)   # 새 키는 새 세션


# ================= 5. 늦은 Job 결과·재시작 복구 =================

def _close_during(settings, sid, real):
    def wrapper(*args, **kwargs):
        _finalize_now(settings, sid)
        return real(*args, **kwargs)
    return wrapper


def test_late_job_results_after_close_are_discarded(app, settings, monkeypatch):
    # 사전 점검: Agent 호출 중 세션 종료 → 결과 미저장, Job은 cancelled 유지(고정 문구)
    flow = Flow(app, settings)
    monkeypatch.setattr(ai_jobs, "_run", _close_during(settings, flow.sid, ai_jobs._run))
    r = flow.c.post(f"/api/v1/sessions/{flow.sid}/preflights", json={"expected_input_revision": flow.rev_in})
    assert r.status_code == 202
    j = _row(settings, "SELECT status, error_json FROM jobs WHERE job_id=?", r.json()["job_id"])
    assert j["status"] == "cancelled" and json.loads(j["error_json"]) == jobs.CANCELLED_ERROR
    assert _count(settings, "SELECT COUNT(*) FROM preflights WHERE session_id=?", flow.sid) == 0
    monkeypatch.undo()
    # 편집안: 종료 뒤 결과는 stale로도 저장하지 않는다
    flow = Flow(app, settings).draft()
    monkeypatch.setattr(ai_jobs, "_run", _close_during(settings, flow.sid, ai_jobs._run))
    block = next(b["block_id"] for p in flow.doc()["pages"] for b in p["blocks"] if b["type"] == "paragraph")
    r = flow.c.post(f"/api/v1/sessions/{flow.sid}/documents/{flow.did}/proposals",
                    json={"expected_revision": flow.rev(), "input_revision": flow.rev_in, "target_block_ids": [block], "instruction": "다듬기", "kind": "text"})
    assert r.status_code == 202
    assert _row(settings, "SELECT status FROM jobs WHERE job_id=?", r.json()["job_id"])["status"] == "cancelled"
    assert _count(settings, "SELECT COUNT(*) FROM proposals WHERE session_id=?", flow.sid) == 0
    monkeypatch.undo()
    # 읽기: 파서 실행 중 세션 종료 → 구간 미저장
    c = TestClient(app)
    sid = c.post("/api/v1/sessions", json={"brief": BRIEF}).json()["session_id"]
    monkeypatch.setattr(reading, "parse", _close_during(settings, sid, reading.parse))
    r = c.post(f"/api/v1/sessions/{sid}/sources", files=[("files", ("a.txt", io.BytesIO(TXT)))])
    assert r.status_code == 202
    assert _row(settings, "SELECT status FROM jobs WHERE job_id=?", r.json()["job_id"])["status"] == "cancelled"
    assert _count(settings, "SELECT COUNT(*) FROM segments WHERE session_id=?", sid) == 0 and MARK not in _db_text(settings)
    # 배치 검사 Job: 시작 시 세션이 닫혀 있으면 임시 폴더를 만들지 않는다
    flow = Flow(app, settings).draft()
    with connect(settings.db_path) as conn:
        job = jobs.create(conn, flow.sid, "layout_check", "대기")
    _finalize_now(settings, flow.sid)
    cleanup.run_for_session(settings, flow.sid)
    layout_check_jobs.run_layout_check_job(settings, flow.sid, job.job_id, flow.did, 1, flow.rev_in, "pdf")
    assert not (settings.private_runs_dir / flow.sid).exists()
    assert _row(settings, "SELECT status FROM jobs WHERE job_id=?", job.job_id)["status"] == "cancelled"


def test_job_guards_protect_cancelled_but_restart_recovery_still_works(app, settings):
    flow = Flow(app, settings).draft()
    a = flow.approved()
    exp = flow.export_ready(a["approval_id"])
    jid = _row(settings, "SELECT job_id FROM exports WHERE export_id=?", exp["export_id"])[0]
    # (1) cancelled Job은 어떤 갱신에도 되살아나지 않는다(복구 전용 allow 포함)
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE jobs SET status='cancelled' WHERE job_id=?", (jid,))
        assert jobs.set_progress(conn, jid, "x", None) == 0 and jobs.succeed(conn, jid, {}) == 0 and jobs.fail(conn, jid, "E", "m", False) == 0
        assert jobs.succeed(conn, jid, {}, allow=jobs.RECOVERABLE) == 0 and jobs.fail(conn, jid, "E", "m", False, allow=jobs.RECOVERABLE) == 0
        conn.execute("UPDATE jobs SET status='succeeded' WHERE job_id=?", (jid,))
    # (2) BE-08 재시작 복구: fail_stale 뒤에도 유효한 Export는 ready + Job succeeded(오류 제거)
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE exports SET status='generating', published_at=NULL WHERE export_id=?", (exp["export_id"],))
        conn.execute("UPDATE jobs SET status='running' WHERE job_id=?", (jid,))
    create_app(settings)
    e = _row(settings, "SELECT status, artifact_id FROM exports WHERE export_id=?", exp["export_id"])
    j = _row(settings, "SELECT status, result_ref_json, error_json FROM jobs WHERE job_id=?", jid)
    assert (e["status"], e["artifact_id"]) == ("ready", a["artifact_id"])
    assert j["status"] == "succeeded" and json.loads(j["result_ref_json"])["export_id"] == exp["export_id"] and j["error_json"] is None
    # (3) 복구 중 승인이 무효면 실제 사유로 failed(fail_stale의 일시 오류를 덮어씀)
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE exports SET status='generating' WHERE export_id=?", (exp["export_id"],))
        conn.execute("UPDATE jobs SET status='running' WHERE job_id=?", (jid,))
        conn.execute("UPDATE approvals SET status='invalidated', invalidated_reason='document_changed' WHERE approval_id=?", (a["approval_id"],))
    create_app(settings)
    assert json.loads(_row(settings, "SELECT error_json FROM exports WHERE export_id=?", exp["export_id"])[0])["code"] == "APPROVAL_NOT_ACTIVE"
    j = _row(settings, "SELECT status, error_json FROM jobs WHERE job_id=?", jid)
    assert j["status"] == "failed" and json.loads(j["error_json"])["code"] == "APPROVAL_NOT_ACTIVE"
    # (4) 종료된 세션의 진행 중 Export·Job: 재시작 복구가 cancelled를 건드리지 않는다
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE exports SET status='generating' WHERE export_id=?", (exp["export_id"],))
        conn.execute("UPDATE jobs SET status='running' WHERE job_id=?", (jid,))
    assert flow.c.delete(f"/api/v1/sessions/{flow.sid}").status_code == 200
    assert _row(settings, "SELECT status FROM jobs WHERE job_id=?", jid)["status"] == "cancelled"
    create_app(settings)
    j = _row(settings, "SELECT status, error_json FROM jobs WHERE job_id=?", jid)
    assert j["status"] == "cancelled" and json.loads(j["error_json"])["message"] == jobs.CANCELLED_ERROR["message"]
    assert _row(settings, "SELECT status, finalized_reason FROM exports WHERE export_id=?", exp["export_id"])["finalized_reason"] == "session_closed"


# ================= 6. 배경 sweep·임시 폴더·첫 실행 정리 =================

def test_sweep_expires_due_sessions_and_keeps_live_ones(app, settings):
    dead = Flow(app, settings)
    live = Flow(app, settings)
    _expire(settings, dead.sid)
    counts = sweeper.sweep_once(settings)
    assert counts["expired"] == 1 and counts["done"] == 1
    s = _row(settings, "SELECT status, purged_at, cleanup_status FROM sessions WHERE session_id=?", dead.sid)
    assert (s["status"], s["cleanup_status"]) == ("expired", "done") and s["purged_at"] and not (settings.private_runs_dir / dead.sid).exists()
    assert dead.c.get(f"/api/v1/sessions/{dead.sid}").json()["error"]["details"] == {"status": "expired", "cleanup": "done"}
    l = _row(settings, "SELECT status, purged_at, brief_json FROM sessions WHERE session_id=?", live.sid)
    assert l["status"] == "active" and l["purged_at"] is None and MARK in l["brief_json"] and (settings.private_runs_dir / live.sid).is_dir()
    assert live.c.get(f"/api/v1/sessions/{live.sid}").status_code == 200
    assert sweeper.sweep_once(settings) == {"expired": 0, "purged": 0, "reclaimed": 0, "late_dirs": 0, "orphan_tmp": 0, "claimed": 0, "done": 0, "retry": 0, "failed": 0}


def test_orphan_tmp_dirs_protect_running_jobs(app, settings):
    flow = Flow(app, settings)
    adir = artifacts.artifacts_dir(settings, flow.sid)
    old = time.time() - 900
    with connect(settings.db_path) as conn:
        running = jobs.create(conn, flow.sid, "layout_check", "x")
        done = jobs.create(conn, flow.sid, "layout_check", "x")
        jobs.fail(conn, done.job_id, "E", "m", False)
    dirs = {}
    for name in (f"tmp_{running.job_id}", f"tmp_{done.job_id}", "tmp_job_unknown", "tmp_fresh"):
        d = adir / name
        d.mkdir(parents=True)
        (d / "x.bin").write_bytes(b"x")
        if name != "tmp_fresh":
            os.utime(d, (old, old))
        dirs[name] = d
    # Job 시작 시 정리(artifacts.cleanup_temp_dirs)도 실행 중 Job을 보호
    assert artifacts.cleanup_temp_dirs(settings, older_than_s=300, protected_job_ids={running.job_id}) == 2
    assert dirs[f"tmp_{running.job_id}"].is_dir() and dirs["tmp_fresh"].is_dir() and not dirs[f"tmp_{done.job_id}"].exists()
    for name in (f"tmp_{done.job_id}", "tmp_job_unknown"):
        dirs[name].mkdir(parents=True, exist_ok=True)
        os.utime(dirs[name], (old, old))
    counts = sweeper.sweep_once(settings)
    assert counts["orphan_tmp"] == 2 and counts["done"] == 2
    assert dirs[f"tmp_{running.job_id}"].is_dir() and dirs["tmp_fresh"].is_dir()
    assert not dirs[f"tmp_{done.job_id}"].exists() and not dirs["tmp_job_unknown"].exists()
    assert _row(settings, "SELECT status FROM sessions WHERE session_id=?", flow.sid)["status"] == "active"
    assert _count(settings, "SELECT COUNT(*) FROM cleanup_queue WHERE kind='orphan_tmp' AND status='done'") == 2


def test_first_run_purges_pre_v8_closed_and_expired_sessions(app, settings):
    registered.run(settings, INGEST, with_mock=True)
    reg = _count(settings, "SELECT COUNT(*) FROM segments WHERE session_id IS NULL")
    live, closed, expired = Flow(app, settings), Flow(app, settings), Flow(app, settings)
    with connect(settings.db_path) as conn:   # v7 방식의 종료·만료 행: 내용 그대로, purged_at 없음
        conn.execute("UPDATE sessions SET status='closed', closed_at='t', cleanup_status='pending' WHERE session_id=?", (closed.sid,))
        conn.execute("UPDATE sessions SET status='expired' WHERE session_id=?", (expired.sid,))
        conn.execute("DELETE FROM cleanup_queue")
    import shutil
    shutil.rmtree(settings.private_runs_dir / expired.sid)          # 폴더가 없는 세션도 같은 기준
    create_app(settings)                                             # 재시작 = 첫 실행 정리
    for sid in (closed.sid, expired.sid):
        s = _row(settings, "SELECT purged_at, brief_json, cleanup_status FROM sessions WHERE session_id=?", sid)
        assert s["purged_at"] and s["brief_json"] == "{}" and s["cleanup_status"] == "pending"
        assert _row(settings, "SELECT status FROM cleanup_queue WHERE session_id=?", sid)["status"] == "pending"
    l = _row(settings, "SELECT status, purged_at, brief_json FROM sessions WHERE session_id=?", live.sid)
    assert l["status"] == "active" and l["purged_at"] is None and MARK in l["brief_json"]
    counts = sweeper.sweep_once(settings)
    assert counts["done"] == 2 and not (settings.private_runs_dir / closed.sid).exists()
    for sid in (closed.sid, expired.sid):
        assert _row(settings, "SELECT cleanup_status FROM sessions WHERE session_id=?", sid)[0] == "done"
    assert _count(settings, "SELECT COUNT(*) FROM segments WHERE session_id IS NULL") == reg and (settings.private_runs_dir / "registered").is_dir()
    assert _count(settings, "SELECT COUNT(*) FROM segments WHERE session_id=?", live.sid) > 0


def test_upload_into_finalized_session_is_rejected_before_writing(app, settings):
    flow = Flow(app, settings)
    _finalize_now(settings, flow.sid)
    cleanup.run_for_session(settings, flow.sid)
    from app.errors import ApiError
    with pytest.raises(ApiError) as exc, connect(settings.db_path) as conn:
        sources.store(conn, settings, flow.sid, "2099-01-01T00:00:00Z", None, [(".txt", "late.txt", "text/plain", b"late")])
    assert exc.value.status_code == 410 and exc.value.code == "SESSION_EXPIRED"
    assert not (settings.private_runs_dir / flow.sid).exists()
    assert _count(settings, "SELECT COUNT(*) FROM sources WHERE session_id=? AND name='late.txt'", flow.sid) == 0


# ================= 7. 마이그레이션·CLI =================

def test_v7_to_v8_migration_is_rerun_safe_and_backfills(tmp_path):
    db, runs = tmp_path / "v7.sqlite3", tmp_path / "runs"
    init_db(db, runs)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE cleanup_queue")
        conn.execute("DROP INDEX ix_idempotency_session")
        conn.execute("ALTER TABLE idempotency_keys DROP COLUMN session_id")
        conn.execute("ALTER TABLE idempotency_keys DROP COLUMN purged_at")
        conn.execute("ALTER TABLE sessions DROP COLUMN purged_at")
        conn.execute("INSERT INTO sessions (session_id, owner_id, status, input_revision, brief_json, selected_source_ids, created_at, last_activity_at, expires_at) "
                     "VALUES ('sess_a','o','active',1,'{}','[]','t','t','2099-01-01T00:00:00Z')")
        conn.execute("INSERT INTO idempotency_keys (idem_key, owner_id, path, body_hash, status_code, response_json, created_at) VALUES "
                     "('k1','o','/api/v1/sessions','h',201,'{\"session_id\": \"sess_a\"}','t'), "
                     "('k2','o','/api/v1/sessions/sess_a/inputs','h',200,'{}','t'), ('k3','o','/other','h',200,'{}','t')")
        conn.execute("PRAGMA user_version=7")
    init_db(db, runs)
    init_db(db, runs)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION and SCHEMA_VERSION >= 8
        assert "purged_at" in [r[1] for r in conn.execute("PRAGMA table_info(sessions)")]
        assert {"session_id", "purged_at"} <= {r[1] for r in conn.execute("PRAGMA table_info(idempotency_keys)")}
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='cleanup_queue'").fetchone()
        rows = {r["idem_key"]: r["session_id"] for r in conn.execute("SELECT idem_key, session_id FROM idempotency_keys")}
        assert rows == {"k1": "sess_a", "k2": "sess_a", "k3": None}
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_cli_once_dry_run_and_retry_failed(app, settings, monkeypatch):
    flow = Flow(app, settings)
    _expire(settings, flow.sid)
    env = {**os.environ, "PRIVATE_RUNS_DIR": str(settings.private_runs_dir), "DB_PATH": str(settings.db_path), "AGENT_MODE": "mock",
           "CLEANUP_SWEEP_INTERVAL_S": "0", "PYTHONIOENCODING": "utf-8"}

    def cli(*args):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "cleanup_sessions.py"), *args], capture_output=True, text=True,
                              encoding="utf-8", env=env, cwd=ROOT, timeout=120)
    r = cli("--once", "--dry-run")
    assert r.returncode == 0 and "would_expire=1" in r.stdout and MARK not in r.stdout and flow.sid not in r.stdout, r.stdout + r.stderr
    assert _row(settings, "SELECT status FROM sessions WHERE session_id=?", flow.sid)["status"] == "active"      # dry-run은 무변경
    r = cli("--once")
    assert r.returncode == 0 and "expired=1" in r.stdout and "done=1" in r.stdout and MARK not in r.stdout, r.stdout + r.stderr
    assert not (settings.private_runs_dir / flow.sid).exists()
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE cleanup_queue SET status='failed', last_error='remove_failed' WHERE session_id=?", (flow.sid,))
    r = cli("--list-failed")
    assert "failed tasks: 1" in r.stdout and "remove_failed" in r.stdout and "private_runs" not in r.stdout
    r = cli("--retry-failed")
    assert "1 task(s) -> pending" in r.stdout
    assert cli("--once").stdout.count("done=1") == 1
    assert _row(settings, "SELECT status FROM cleanup_queue WHERE session_id=?", flow.sid)["status"] == "done"


def test_sweeper_thread_starts_only_with_interval_and_stops(settings):
    assert not sweeper.Sweeper(settings).start()                                       # 0 → 스레드 없음
    fast = Settings(**{**settings.__dict__, "cleanup_sweep_interval_s": 1})
    create_app(fast)
    sw = sweeper.Sweeper(fast)
    assert sw.start() and sw.running
    sw.stop()
    assert not sw.running


# ================= 8. Codex 리뷰 회귀(2026-09-27) =================

def _closer_hook(monkeypatch, settings, sid, wait_s=1.5):
    """세션 검사(load_active)가 끝난 직후 다른 연결에서 종료 확정을 시도한다. 라우트가 BEGIN IMMEDIATE로 잠금을 잡고 있으면
    종료는 라우트 커밋 뒤에야 들어간다(interleaved=False). 대기는 sqlite timeout(10초)보다 짧아 교착이 없다."""
    real = sessions.load_active
    state = {"armed": True, "interleaved": None, "done": threading.Event()}

    def closer():
        with connect(settings.db_path, immediate=True) as c2:
            cleanup.finalize(c2, settings, sid, cleanup.REASON_CLOSED)
        state["done"].set()

    def hooked(conn, owner, sid_, settings_):
        row = real(conn, owner, sid_, settings_)
        if state["armed"] and sid_ == sid:
            state["armed"] = False
            threading.Thread(target=closer, daemon=True).start()
            state["interleaved"] = state["done"].wait(wait_s)
        return row
    monkeypatch.setattr(sessions, "load_active", hooked)
    return state


def _settled(settings, sid, timeout=15):
    for _ in range(int(timeout / 0.05)):
        if _count(settings, f"SELECT COUNT(*) FROM jobs WHERE session_id=? AND status IN ({','.join('?' * len(jobs.ACTIVE))})", sid, *jobs.ACTIVE) == 0:
            return True
        time.sleep(0.05)
    return False


def test_input_patch_and_close_do_not_repopulate_purged_session(app, settings, monkeypatch):
    """리뷰 1(P1). 재현: PATCH가 세션을 읽은 뒤 쓰기 전에 다른 연결이 finalize하면 purged 세션에 brief·멱등 응답이 다시 저장됐다.
    규칙: PATCH가 먼저면 종료가 그 결과까지 제거, 종료가 먼저면 PATCH는 410. 순서는 훅으로 고정한다."""
    flow = Flow(app, settings)
    state = _closer_hook(monkeypatch, settings, flow.sid)
    r = flow.c.patch(f"/api/v1/sessions/{flow.sid}/inputs", json={"expected_input_revision": flow.rev_in, "brief": BRIEF},
                     headers={"Idempotency-Key": "P-race"})
    assert state["done"].wait(15)
    assert r.status_code == 200, r.text                      # PATCH가 먼저 커밋됐고
    assert state["interleaved"] is False                     # 종료는 PATCH 트랜잭션 중간에 끼어들지 못했다
    s_ = _row(settings, "SELECT status, purged_at, brief_json, input_revision FROM sessions WHERE session_id=?", flow.sid)
    assert s_["status"] == "closed" and s_["purged_at"] and s_["brief_json"] == "{}" and s_["input_revision"] == flow.rev_in + 1
    idem = _row(settings, "SELECT response_json, purged_at FROM idempotency_keys WHERE idem_key='P-race'")
    assert idem["response_json"] == "{}" and idem["purged_at"]                                     # 종료가 PATCH 결과까지 제거
    assert MARK not in _db_text(settings)
    monkeypatch.undo()
    # 종료가 먼저면 PATCH는 410(내용 미저장)
    flow2 = Flow(app, settings)
    assert flow2.c.delete(f"/api/v1/sessions/{flow2.sid}").status_code == 200
    r = flow2.c.patch(f"/api/v1/sessions/{flow2.sid}/inputs", json={"expected_input_revision": flow2.rev_in, "brief": BRIEF}, headers={"Idempotency-Key": "P-late"})
    assert r.status_code == 410 and r.json()["error"]["details"] == {"status": "closed", "cleanup": "done"}
    assert _row(settings, "SELECT brief_json, input_revision FROM sessions WHERE session_id=?", flow2.sid)["brief_json"] == "{}"
    assert _row(settings, "SELECT COUNT(*) FROM idempotency_keys WHERE idem_key='P-late'")[0] == 0


@pytest.mark.parametrize("route", ["delete_source", "preflight", "draft", "proposal", "validate", "layout_check", "upload"])
def test_other_state_changing_routes_serialize_with_close(app, settings, monkeypatch, route):
    """리뷰 1 후속: 다른 상태 변경 라우트도 세션 검사부터 쓰기까지 종료 확정과 직렬화된다(끼어들기 없음, 결과는 종료가 제거)."""
    flow = Flow(app, settings)
    if route in ("proposal", "validate", "layout_check"):
        flow.draft()
    calls = {
        "delete_source": lambda: flow.c.delete(f"/api/v1/sessions/{flow.sid}/sources/{flow.source_ids[1]}?expected_input_revision={flow.rev_in}"),
        "preflight": lambda: flow.c.post(f"/api/v1/sessions/{flow.sid}/preflights", json={"expected_input_revision": flow.rev_in}, headers={"Idempotency-Key": f"K-{route}"}),
        "draft": lambda: flow.c.post(f"/api/v1/sessions/{flow.sid}/drafts", json={"preflight_id": "pf_x", "input_revision": flow.rev_in, "confirmed": True}, headers={"Idempotency-Key": f"K-{route}"}),
        "proposal": lambda: flow.c.post(f"/api/v1/sessions/{flow.sid}/documents/{flow.did}/proposals",
                                        json={"expected_revision": 1, "input_revision": flow.rev_in, "target_block_ids": ["b"], "instruction": f"x {MARK}", "kind": "text"},
                                        headers={"Idempotency-Key": f"K-{route}"}),
        "validate": lambda: flow.c.post(f"/api/v1/sessions/{flow.sid}/documents/{flow.did}/validate", json={"expected_revision": 1, "input_revision": flow.rev_in},
                                        headers={"Idempotency-Key": f"K-{route}"}),
        "layout_check": lambda: flow.c.post(f"/api/v1/sessions/{flow.sid}/documents/{flow.did}/layout-checks", json={"expected_revision": 1, "format": "pdf"},
                                            headers={"Idempotency-Key": f"K-{route}"}),
        "upload": lambda: flow.c.post(f"/api/v1/sessions/{flow.sid}/sources", files=[("files", (f"{MARK}_late.txt", io.BytesIO(TXT)))], headers={"Idempotency-Key": f"K-{route}"}),
    }
    state = _closer_hook(monkeypatch, settings, flow.sid)
    r = calls[route]()
    assert state["done"].wait(15)
    assert r.status_code in (200, 202, 404, 409, 410), r.text          # 요청은 정상 처리(또는 검증 오류)되고 예외·잠금 오류가 없다
    if route != "upload":
        assert state["interleaved"] is False                            # 잠금을 잡은 채 세션을 검사하므로 종료가 끼어들지 못한다
    assert _settled(settings, flow.sid)
    s_ = _row(settings, "SELECT status, purged_at, brief_json FROM sessions WHERE session_id=?", flow.sid)
    assert s_["status"] == "closed" and s_["purged_at"] and s_["brief_json"] == "{}"
    assert MARK not in _db_text(settings)
    assert _count(settings, "SELECT COUNT(*) FROM idempotency_keys WHERE session_id=? AND purged_at IS NULL", flow.sid) == 0
    assert _count(settings, f"SELECT COUNT(*) FROM jobs WHERE session_id=? AND status IN ({','.join('?' * len(jobs.ACTIVE))})", flow.sid, *jobs.ACTIVE) == 0


def _make_link(target: Path, link: Path) -> str:
    """Windows: junction(권한 불필요). 그 외: 디렉터리 symlink. 만들 수 없으면 skip."""
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        return "junction"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"이 OS에서 디렉터리 링크를 만들 수 없음(미검증): {exc}")
    return "symlink"


def test_cleanup_rejects_junction_to_registered_or_other_session(app, settings):
    """리뷰 2(P1). 재현(Windows): 세션 폴더가 registered로 향하는 junction이면 옛 _safe_target이 resolve된 등록 폴더를 삭제 대상으로 넘겼다.
    Windows에서는 junction으로 실험하고, 다른 OS에서는 symlink로만 확인한다(junction 동작은 미검증)."""
    reg = settings.private_runs_dir / "registered"
    (reg / "sub").mkdir(parents=True)
    (reg / "keep.txt").write_bytes(b"keep")
    (reg / "sub" / "keep2.txt").write_bytes(b"keep2")
    (reg / "tmp_fake").mkdir()
    (reg / "tmp_fake" / "x.bin").write_bytes(b"x")
    victim = Flow(app, settings)                                   # 다른 세션(살아 있음)
    victim_file = next(p for p in (settings.private_runs_dir / victim.sid).iterdir())
    import shutil

    # (a) 세션 루트가 registered로 향하는 링크
    a = Flow(app, settings)
    _finalize_now(settings, a.sid)
    shutil.rmtree(settings.private_runs_dir / a.sid)
    kind = _make_link(reg, settings.private_runs_dir / a.sid)
    counts = cleanup.run_for_session(settings, a.sid)
    assert counts["failed"] == 1 and counts["done"] == 0
    t = _row(settings, "SELECT status, last_error FROM cleanup_queue WHERE session_id=?", a.sid)
    assert (t["status"], t["last_error"]) == ("failed", "unsafe_target")
    assert (reg / "keep.txt").exists() and (reg / "sub" / "keep2.txt").exists() and os.path.lexists(settings.private_runs_dir / a.sid)
    with connect(settings.db_path, immediate=True) as conn:
        assert cleanup.verify_state(conn, settings, a.sid) == "pending"      # 링크가 남아 있으므로 done이 아니다
    # (b) 세션 루트가 다른 세션 폴더로 향하는 링크
    b = Flow(app, settings)
    _finalize_now(settings, b.sid)
    shutil.rmtree(settings.private_runs_dir / b.sid)
    _make_link(settings.private_runs_dir / victim.sid, settings.private_runs_dir / b.sid)
    assert cleanup.run_for_session(settings, b.sid)["failed"] == 1
    assert victim_file.exists() and victim.c.get(f"/api/v1/sessions/{victim.sid}").status_code == 200
    # (c) 세션 폴더 안쪽의 링크: 링크만 끊고 대상은 보존, 세션 폴더는 삭제
    c = Flow(app, settings)
    _make_link(reg, settings.private_runs_dir / c.sid / "link")
    (settings.private_runs_dir / c.sid / "artifacts").mkdir()
    _make_link(reg / "sub", settings.private_runs_dir / c.sid / "artifacts" / "tmp_link")
    assert c.c.delete(f"/api/v1/sessions/{c.sid}").json()["cleanup"] == "done"
    assert not os.path.lexists(settings.private_runs_dir / c.sid)
    assert (reg / "keep.txt").exists() and (reg / "sub" / "keep2.txt").exists()
    # (d) 활성 세션의 artifacts 폴더·tmp 폴더가 링크: Job 시작 시 정리·sweep 모두 등록 자료에 닿지 않는다
    d = Flow(app, settings)
    _make_link(reg, settings.private_runs_dir / d.sid / "artifacts")
    old = time.time() - 900
    os.utime(reg / "tmp_fake", (old, old))
    assert artifacts.cleanup_temp_dirs(settings) == 0
    assert sweeper.sweep_once(settings)["orphan_tmp"] == 0 and (reg / "tmp_fake" / "x.bin").exists()
    e = Flow(app, settings)
    (settings.private_runs_dir / e.sid / "artifacts").mkdir()
    _make_link(reg / "tmp_fake", settings.private_runs_dir / e.sid / "artifacts" / "tmp_linked")
    os.utime(settings.private_runs_dir / e.sid / "artifacts" / "tmp_linked", (old, old)) if kind == "symlink" else None
    assert artifacts.cleanup_temp_dirs(settings) == 0 and (reg / "tmp_fake" / "x.bin").exists()
    with connect(settings.db_path, immediate=True) as conn:
        conn.execute("INSERT INTO cleanup_queue (task_id, session_id, kind, target_rel, status, attempt, next_retry_at, created_at, updated_at) "
                     "VALUES ('t_link', ?, 'orphan_tmp', ?, 'pending', 0, ?, 't', 't')", (e.sid, f"{e.sid}/artifacts/tmp_linked", PAST))
    assert cleanup.process_due(settings, "w")["failed"] == 1
    assert _row(settings, "SELECT last_error FROM cleanup_queue WHERE task_id='t_link'")[0] == "unsafe_target"
    assert (reg / "tmp_fake" / "x.bin").exists() and os.path.lexists(settings.private_runs_dir / e.sid / "artifacts" / "tmp_linked")
    assert sorted(p.name for p in reg.rglob("*")) == ["keep.txt", "keep2.txt", "sub", "tmp_fake", "x.bin"]


def test_failed_orphan_does_not_block_late_session_directory_cleanup(app, settings):
    """리뷰 3(P1). 재현: 이미 사라진 tmp의 failed 행 하나가 세션 전체 정리(재등록·done 판정)를 영구 차단했다."""
    flow = Flow(app, settings)
    with connect(settings.db_path, immediate=True) as conn:
        conn.execute("INSERT INTO cleanup_queue (task_id, session_id, kind, target_rel, status, attempt, next_retry_at, last_error, created_at, updated_at) "
                     "VALUES ('t_orphan', ?, 'orphan_tmp', ?, 'failed', 10, 't', 'remove_failed', 't', 't')", (flow.sid, f"{flow.sid}/artifacts/tmp_gone"))
    r = flow.c.delete(f"/api/v1/sessions/{flow.sid}")
    assert r.json()["cleanup"] == "done" and not (settings.private_runs_dir / flow.sid).exists()
    t = _row(settings, "SELECT status, attempt, last_error FROM cleanup_queue WHERE task_id='t_orphan'")
    assert (t["status"], t["attempt"], t["last_error"]) == ("done", 10, "target_gone")           # 대상이 사라진 하위 작업은 정합하게 완료(attempt 유지)
    # 늦은 파일: 같은 대상(session_dir)의 failed가 없으므로 재등록되고 다시 done
    late = settings.private_runs_dir / flow.sid / "late.txt"
    late.parent.mkdir()
    late.write_bytes(b"late")
    assert flow.c.get(f"/api/v1/sessions/{flow.sid}").json()["error"]["details"]["cleanup"] == "pending"
    assert _count(settings, "SELECT COUNT(*) FROM cleanup_queue WHERE session_id=? AND kind='session_dir' AND status='pending'", flow.sid) == 1
    assert sweeper.sweep_once(settings)["done"] == 1 and not late.parent.exists()
    assert flow.c.get(f"/api/v1/sessions/{flow.sid}").json()["error"]["details"]["cleanup"] == "done"
    # 같은 대상(session_dir)의 failed는 여전히 자동 재등록하지 않는다(attempt 초기화 없음)
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE cleanup_queue SET status='failed', attempt=10, last_error='remove_failed' WHERE session_id=? AND kind='session_dir'", (flow.sid,))
    rows_before = _count(settings, "SELECT COUNT(*) FROM cleanup_queue WHERE session_id=?", flow.sid)
    late.parent.mkdir()
    late.write_bytes(b"late")
    assert flow.c.get(f"/api/v1/sessions/{flow.sid}").json()["error"]["details"]["cleanup"] == "pending"
    assert _count(settings, "SELECT COUNT(*) FROM cleanup_queue WHERE session_id=?", flow.sid) == rows_before            # 새 행 없음
    with connect(settings.db_path) as conn:
        assert {r[0] for r in conn.execute("SELECT attempt FROM cleanup_queue WHERE session_id=? AND kind='session_dir'", (flow.sid,)).fetchall()} == {10}
    # running 하위 작업은 점유자가 마무리한다(자동 완료 대상 아님)
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE cleanup_queue SET status='running', claim_token='tok', claimed_at='t' WHERE task_id='t_orphan'")
        conn.execute("UPDATE cleanup_queue SET status='done' WHERE session_id=? AND kind='session_dir'", (flow.sid,))
    import shutil
    shutil.rmtree(late.parent)
    with connect(settings.db_path, immediate=True) as conn:
        assert cleanup.verify_state(conn, settings, flow.sid) == "pending"
        assert conn.execute("SELECT status FROM cleanup_queue WHERE task_id='t_orphan'").fetchone()[0] == "running"


def test_purge_preserves_resolution_audit_metadata_without_content(app, settings):
    """리뷰 4(P2). 해결 기록의 action·by·at·버전·참조 ID·근거 위치는 남기고 reason·excerpt(원문)만 지운다."""
    flow = Flow(app, settings).draft()
    resolution = {"action": "excluded", "by": "own_x", "at": "2026-09-27T00:00:00Z", "reason": f"이유 {MARK}",
                  "evidence_refs": [{"source_id": "src_1", "source_version": 1, "segment_id": "seg_1", "locator": {"line_start": 3, "line_end": 3}, "excerpt": f"발췌 {MARK}"}],
                  "document_revision": 2, "input_revision": 3}
    history = [{"action": "resolved", "by": "server", "reason": f"재검증 {MARK}", "at": "2026-09-26T00:00:00Z", "document_revision": 1, "input_revision": 2,
                "validation_id": "val_1", "reopened_at": "2026-09-26T01:00:00Z", "reopened_by_validation": "val_2", "previous_status": "resolved"}]
    with connect(settings.db_path) as conn:
        conn.execute("INSERT INTO issues (issue_id, session_id, document_id, identity_key, scope, code, severity, status, message, source_ids_json, "
                     "fact_ids_json, block_ids_json, origin, resolution_json, resolution_history_json, first_validation_id, last_validation_id, created_at, updated_at) "
                     "VALUES ('i_a', ?, ?, 'k', 'content', 'X', 'warning', 'excluded', ?, '[\"src_1\"]', '[]', '[]', 'server', ?, ?, 'val_1', 'val_2', 't', 't')",
                     (flow.sid, flow.did, f"문제 {MARK}", json.dumps(resolution, ensure_ascii=False), json.dumps(history, ensure_ascii=False)))
        conn.execute("INSERT INTO issues (issue_id, session_id, document_id, identity_key, scope, code, severity, status, message, source_ids_json, "
                     "fact_ids_json, block_ids_json, origin, resolution_json, resolution_history_json, created_at, updated_at) "
                     "VALUES ('i_open', ?, ?, 'k2', 'content', 'Y', 'blocker', 'open', ?, '[]', '[]', '[]', 'agent', NULL, '[]', 't', 't')", (flow.sid, flow.did, f"열림 {MARK}"))
    _finalize_now(settings, flow.sid)
    row = _row(settings, "SELECT message, resolution_json, resolution_history_json, status, source_ids_json, first_validation_id, last_validation_id FROM issues WHERE issue_id='i_a'")
    assert row["message"] == "" and row["status"] == "excluded" and (row["first_validation_id"], row["last_validation_id"]) == ("val_1", "val_2")
    assert json.loads(row["resolution_json"]) == {
        "action": "excluded", "by": "own_x", "at": "2026-09-27T00:00:00Z", "document_revision": 2, "input_revision": 3,
        "evidence_refs": [{"source_id": "src_1", "source_version": 1, "segment_id": "seg_1", "locator": {"line_start": 3, "line_end": 3}}], "purged": True}
    assert json.loads(row["resolution_history_json"]) == [{
        "action": "resolved", "by": "server", "at": "2026-09-26T00:00:00Z", "document_revision": 1, "input_revision": 2, "validation_id": "val_1",
        "reopened_at": "2026-09-26T01:00:00Z", "reopened_by_validation": "val_2", "previous_status": "resolved", "purged": True}]
    open_row = _row(settings, "SELECT message, resolution_json, resolution_history_json FROM issues WHERE issue_id='i_open'")
    assert (open_row["message"], open_row["resolution_json"], open_row["resolution_history_json"]) == ("", None, "[]")
    assert MARK not in _db_text(settings)


def test_purged_create_replay_reports_current_cleanup_state(app, settings, monkeypatch):
    """리뷰 5(P2). purged 캐시 거부 410에도 details.cleanup(공용 판정)이 있다 — 삭제 실패(pending)·성공(done) 각각. 409·접근 순서 유지."""
    flow = Flow(app, settings, key="C1")
    monkeypatch.setattr(cleanup, "_remove_tree", lambda path: False)
    assert flow.c.delete(f"/api/v1/sessions/{flow.sid}").json()["cleanup"] == "pending"
    r = flow.c.post("/api/v1/sessions", json={"brief": BRIEF}, headers={"Idempotency-Key": "C1"})
    assert r.status_code == 410 and r.json()["error"]["details"] == {"status": "closed", "cleanup": "pending"} and MARK not in r.text
    assert _row(settings, "SELECT purged_at FROM idempotency_keys WHERE idem_key='C1'")[0] is not None
    assert flow.c.post("/api/v1/sessions", json={"brief": {**BRIEF, "purpose": "다른 본문"}}, headers={"Idempotency-Key": "C1"}).status_code == 409
    other = TestClient(app)
    other.post("/api/v1/sessions", json={"brief": BRIEF})
    assert other.post("/api/v1/sessions", json={"brief": BRIEF}, headers={"Idempotency-Key": "C1"}).status_code == 201   # 다른 소유자의 같은 키는 별개
    monkeypatch.undo()
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE cleanup_queue SET next_retry_at=? WHERE session_id=?", (PAST, flow.sid))
    assert sweeper.sweep_once(settings)["done"] == 1
    r = flow.c.post("/api/v1/sessions", json={"brief": BRIEF}, headers={"Idempotency-Key": "C1"})
    assert r.status_code == 410 and r.json()["error"]["details"] == {"status": "closed", "cleanup": "done"}
    # 만료 경로도 같은 모양
    flow2 = Flow(app, settings, key="C2")
    _expire(settings, flow2.sid)
    r = flow2.c.post("/api/v1/sessions", json={"brief": BRIEF}, headers={"Idempotency-Key": "C2"})
    assert r.status_code == 410 and r.json()["error"]["details"]["status"] == "expired" and r.json()["error"]["details"]["cleanup"] in ("pending", "done")
    # 연결 세션을 알 수 없는 옛 행(backfill 불가)이 purged면 done
    with connect(settings.db_path) as conn:
        conn.execute("INSERT INTO idempotency_keys (idem_key, owner_id, path, body_hash, status_code, response_json, created_at, session_id, purged_at) "
                     "VALUES ('C3', (SELECT owner_id FROM sessions WHERE session_id=?), '/api/v1/sessions', ?, 201, '{}', 't', NULL, 't')",
                     (flow.sid, __import__("app.services.idempotency", fromlist=["body_hash"]).body_hash({"brief": BRIEF})))
    r = flow.c.post("/api/v1/sessions", json={"brief": BRIEF}, headers={"Idempotency-Key": "C3"})
    assert r.status_code == 410 and r.json()["error"]["details"] == {"status": "closed", "cleanup": "done"}


def test_parent_and_orphan_completion_updates_session_cleanup_status(app, settings):
    """리뷰 6(P2). 재현: 같은 배치에서 session_dir → orphan_tmp 순서로 완료되면 마지막(자식) 완료 때 재계산이 없어
    폴더 없음·미완료 큐 0건인데 sessions.cleanup_status가 pending으로 남고 다음 sweep도 고치지 않았다."""
    flow = Flow(app, settings)
    tmp = settings.private_runs_dir / flow.sid / "artifacts" / "tmp_job_old"
    tmp.mkdir(parents=True)
    (tmp / "x.bin").write_bytes(b"x")
    _finalize_now(settings, flow.sid)                                              # session_dir 작업 등록(부모)
    with connect(settings.db_path, immediate=True) as conn:
        assert cleanup.enqueue(conn, flow.sid, "orphan_tmp", f"{flow.sid}/artifacts/tmp_job_old")   # 자식(먼저 등록됐던 tmp 정리라고 가정)
        tasks = cleanup.claim(conn, "batch", ignore_schedule=True)                # 두 작업을 같은 배치에서 선점
    assert {t["kind"] for t in tasks} == {"session_dir", "orphan_tmp"} and all(t["claim_token"] for t in tasks)
    tasks.sort(key=lambda t: t["kind"] != "session_dir")                          # 부모 → 자식 순서로 처리
    assert cleanup.process(settings, tasks[0]) == "done"
    assert not (settings.private_runs_dir / flow.sid).exists()
    assert _row(settings, "SELECT cleanup_status FROM sessions WHERE session_id=?", flow.sid)[0] == "pending"   # 자식이 아직 running
    assert cleanup.process(settings, tasks[1]) == "done"                          # 대상은 부모 삭제로 이미 없음 → 성공
    # 상태를 보정하는 GET 요청 없이: 폴더 없음·큐 전부 done·세션 cleanup=done
    assert not (settings.private_runs_dir / flow.sid).exists()
    assert _count(settings, "SELECT COUNT(*) FROM cleanup_queue WHERE session_id=? AND status!='done'", flow.sid) == 0
    assert _count(settings, "SELECT COUNT(*) FROM cleanup_queue WHERE session_id=?", flow.sid) == 2
    s_ = _row(settings, "SELECT status, purged_at, cleanup_status FROM sessions WHERE session_id=?", flow.sid)
    assert (s_["status"], s_["cleanup_status"]) == ("closed", "done") and s_["purged_at"]
    # 다음 sweep에서도 done 유지
    assert sweeper.sweep_once(settings)["claimed"] == 0
    assert _row(settings, "SELECT cleanup_status FROM sessions WHERE session_id=?", flow.sid)[0] == "done"
    # 활성 세션의 tmp 정리는 세션 전체 정리 상태를 만들지 않는다
    live = Flow(app, settings)
    ltmp = settings.private_runs_dir / live.sid / "artifacts" / "tmp_job_done"
    ltmp.mkdir(parents=True)
    with connect(settings.db_path, immediate=True) as conn:
        cleanup.enqueue(conn, live.sid, "orphan_tmp", f"{live.sid}/artifacts/tmp_job_done")
        lt = cleanup.claim(conn, "batch", session_id=live.sid, ignore_schedule=True)
    assert cleanup.process(settings, lt[0]) == "done" and not ltmp.exists()
    l_ = _row(settings, "SELECT status, cleanup_status, purged_at FROM sessions WHERE session_id=?", live.sid)
    assert (l_["status"], l_["cleanup_status"], l_["purged_at"]) == ("active", None, None)
    assert live.c.get(f"/api/v1/sessions/{live.sid}").status_code == 200
