"""임시 DB와 mock AI로 실제 localhost HTTP 연결을 검사한다.

  .venv\\Scripts\\python.exe -X utf8 -B scripts/check_s01_http.py

기존 .env/DB/등록 자료를 사용하지 않으며 종료 시 서버와 임시 자료를 정리한다.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import socket
import subprocess
import shutil
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BRIEF = {"purpose": "HTTP 연결 확인", "emphasis": [], "direction": "balanced",
         "target_pages": 4, "photo_preference": "none"}
SOURCE = "회사명: 예시 회사\n회사 개요: 연결 확인용 가상 기업입니다.\n사업 분야: 예시 사업\n공정 수: 2개\n".encode("utf-8")


def _photo_png() -> bytes:
    """실회사 사진 대신 메모리에서 만든 가상 PNG. 원본 파일은 만들지 않는다."""
    from PIL import Image, ImageDraw

    picture = Image.new("RGB", (640, 480), (30, 90, 150))
    ImageDraw.Draw(picture).rectangle((80, 80, 560, 400), fill=(220, 180, 60))
    output = io.BytesIO()
    picture.save(output, format="PNG")
    return output.getvalue()


class CheckError(RuntimeError):
    """실제 HTTP 동작이 기대한 계약과 다르다."""


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise CheckError(message)


def _response(response: httpx.Response, status: int, code: str | None = None) -> dict:
    _check(response.status_code == status,
           f"{response.request.method} {response.request.url.path}: expected {status}, got {response.status_code}: {response.text}")
    _check(bool(response.headers.get("X-Request-Id")), "X-Request-Id header missing")
    payload = response.json()
    if code is not None:
        _check(payload["error"]["code"] == code, f"Expected error {code}: {payload}")
        _check(payload["error"]["request_id"] == response.headers["X-Request-Id"], "Error request ID mismatch")
    return payload


@contextmanager
def temporary_server(*, timeout_s: float = 30, export_browser_path: Path | None = None):
    """Yield (base_url, Settings). This helper owns its temporary directory/server."""
    # app.config imports dotenv at module load. Disable it before importing app;
    # explicit Settings below also isolate already-imported test/application modules.
    previous = os.environ.get("PYTHON_DOTENV_DISABLED")
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    try:
        from app import create_app
        from app.config import Settings
        from app.db import get_engine, init_orm_db
    finally:
        if previous is None:
            os.environ.pop("PYTHON_DOTENV_DISABLED", None)
        else:
            os.environ["PYTHON_DOTENV_DISABLED"] = previous

    with tempfile.TemporaryDirectory(prefix="ddalgi-s01-http-") as directory:
        temporary_root = Path(directory)
        settings = Settings(private_runs_dir=temporary_root / "runs", db_path=temporary_root / "runs" / "check.sqlite3",
                            agent_mode="mock", demo_mode=False, cleanup_sweep_interval_s=0,
                            export_browser_path=str(export_browser_path or temporary_root / "no-browser.exe"))
        server = thread = None
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            try:
                init_orm_db(settings.db_path, settings.private_runs_dir)
                application = create_app(settings)
                listener.bind(("127.0.0.1", 0))
                address = f"http://127.0.0.1:{listener.getsockname()[1]}"
                server = uvicorn.Server(uvicorn.Config(application, log_level="error", access_log=False,
                                                     timeout_graceful_shutdown=5))
                failures = []

                def serve():
                    try:
                        server.run(sockets=[listener])
                    except BaseException as exc:
                        failures.append(exc)

                thread = threading.Thread(target=serve, name="s01-http-check", daemon=True)
                thread.start()
                deadline = time.monotonic() + timeout_s
                while not server.started:
                    if not thread.is_alive() or time.monotonic() >= deadline:
                        raise CheckError(f"Temporary server failed to start: {failures}")
                    time.sleep(0.02)
                yield address, settings
            finally:
                if thread is not None:
                    server.should_exit = True
                    thread.join(timeout=10)
                    if thread.is_alive():
                        server.force_exit = True
                        thread.join(timeout=2)
                    _check(not thread.is_alive(), "Temporary server did not stop")
                get_engine(settings.db_path).dispose()


def _job(client: httpx.Client, sid: str, job_id: str, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = _response(client.get(f"/api/v1/sessions/{sid}/jobs/{job_id}"), 200)
        if job["status"] == "succeeded":
            return job
        _check(job["status"] not in {"failed", "cancelled", "waiting_user"}, f"Job did not succeed: {job}")
        time.sleep(0.05)
    raise CheckError(f"Job {job_id} exceeded {timeout_s}s")


def _publication_check(client: httpx.Client, sid: str, document_route: str, timeout_s: float,
                       photo_asset_id: str | None = None) -> dict:
    """Real HTTP edits and explicit approval, with an actual Chromium PDF render."""
    route = f"/api/v1/sessions/{sid}"
    document = _response(client.get(document_route), 200)["document"]
    blocks = [block for page in document["pages"] for block in page["blocks"]]
    original_photos = [block for block in blocks if block["type"] == "image"]
    if photo_asset_id:
        _check(len(original_photos) == 1 and original_photos[0]["content"]["asset_id"] == photo_asset_id,
               "Draft did not use the selected photo exactly once")
    operations = [{"op": "delete_block", "block_id": block["block_id"]} for block in blocks
                  if block["type"] == "image_placeholder" or
                  (block["type"] == "paragraph" and block["content"].get("text") == "추가 확인 필요")]
    edited = next(block for block in blocks if block["type"] == "paragraph" and block["fact_ids"])
    operations.append({"op": "replace_block_content", "block_id": edited["block_id"],
                       "content": {**edited["content"], "text": edited["content"]["text"] + " 검토 완료."}})
    patch = {"expected_revision": document["document_revision"], "operations": operations}
    changed = _response(client.patch(document_route, json=patch), 200)
    _check(changed["document_revision"] == document["document_revision"] + 1, "Edit did not create a new revision")
    _response(client.patch(document_route, json=patch), 409, "DOCUMENT_REVISION_CONFLICT")
    document = _response(client.get(document_route), 200)["document"]
    if photo_asset_id:
        _check([block for page in document["pages"] for block in page["blocks"] if block["type"] == "image"]
               == original_photos, "Text edit changed or removed the photo")
    checked = _response(client.post(document_route + "/validate", json={
        "expected_revision": document["document_revision"], "input_revision": document["input_revision"]}), 202)
    _job(client, sid, checked["job_id"], timeout_s)
    state = _response(client.get(document_route), 200)
    _check(state["validation"]["status"] == "passed", f"Content validation failed: {state['validation']}")
    layout = _response(client.post(document_route + "/layout-checks", json={
        "expected_revision": document["document_revision"], "format": "pdf"}), 202)
    _job(client, sid, layout["job_id"], max(timeout_s, 120))
    state = _response(client.get(document_route), 200)
    pdf_layout = state["layout_checks"]["pdf"]
    _check(pdf_layout["status"] == "passed" and pdf_layout["actual_pages"] > 0,
           f"Actual PDF layout did not pass: {pdf_layout}")
    _check(pdf_layout["renderer"].startswith(("chrome/", "edge/", "chromium/")), "PDF used no actual browser renderer")
    _check(pdf_layout["layout_ok"] and pdf_layout["publication_policy_ok"] and not pdf_layout["findings"],
           "PDF layout or publication policy did not pass")
    _check(len(pdf_layout["checks"]) == 3 and all(check["required"] for check in pdf_layout["checks"]) and
           {check["check_key"]: check["result"] for check in pdf_layout["checks"]} == {
        "overflow": "ok", "broken_image": "ok", "placeholder_remaining": "ok"}, "Required PDF checks did not pass")
    if photo_asset_id:
        from PIL import Image

        _check(len(pdf_layout["preview_asset_ids"]) == len(set(pdf_layout["preview_asset_ids"]))
               == pdf_layout["actual_pages"], "PDF preview pages are missing or repeated")
        for asset_id in pdf_layout["preview_asset_ids"]:
            preview = client.get(route + f"/assets/{asset_id}")
            _check(preview.status_code == 200 and preview.headers.get("content-type", "").startswith("image/png"),
                   "PDF preview is not an accessible PNG")
            _check("no-store" in preview.headers.get("cache-control", ""), "Preview cache protection missing")
            with Image.open(io.BytesIO(preview.content)) as picture:
                _check(picture.format == "PNG" and min(picture.size) > 0, "Invalid PDF preview")
                picture.verify()
    approval_body = {"expected_revision": document["document_revision"], "input_revision": document["input_revision"],
                     "format": "pdf", "validation_id": state["validation"]["validation_id"],
                     "layout_check_id": pdf_layout["layout_check_id"], "confirmed": False}
    _response(client.post(document_route + "/approvals", json=approval_body), 422, "APPROVAL_NOT_CONFIRMED")
    approval_body["confirmed"] = True
    approval = _response(client.post(document_route + "/approvals", json=approval_body,
                                     headers={"Idempotency-Key": "http-final-approval"}), 201)
    _check(approval["artifact_id"] == pdf_layout["artifact_id"], "Approval did not use the checked PDF artifact")
    _check(_response(client.post(document_route + "/approvals", json=approval_body,
                                 headers={"Idempotency-Key": "http-final-approval"}), 201) == approval,
           "Approval replay changed")
    export_body = {"approval_id": approval["approval_id"], "format": "pdf"}
    accepted = _response(client.post(route + "/exports", json=export_body), 202)
    _job(client, sid, accepted["job_id"], timeout_s)
    ready = _response(client.post(route + "/exports", json=export_body), 200)["export"]
    _check(ready["artifact_id"] == approval["artifact_id"], "Export did not use the approved PDF artifact")
    download_route = route + f"/exports/{ready['export_id']}/download"
    first = client.get(download_route)
    _check(first.status_code == 200 and first.content.startswith(b"%PDF-"), "Download is not a real PDF")
    _check(first.headers.get("content-type", "").startswith("application/pdf"), "PDF content type missing")
    _check("no-store" in first.headers.get("cache-control", ""), "Download cache protection missing")
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(first.content))
    _check(len(reader.pages) == pdf_layout["actual_pages"], "PDF page count mismatch")
    pdf_images = sum(len(page.images) for page in reader.pages)
    if photo_asset_id:
        _check(pdf_images == 1 and len(reader.pages[0].images) == 1, "Selected photo is missing or repeated in the PDF")
        embedded = reader.pages[0].images[0].image.convert("RGB")
        with Image.open(io.BytesIO(_photo_png())) as expected:
            _check(embedded.size == expected.size and embedded.tobytes() == expected.convert("RGB").tobytes(),
                   "Embedded photo pixels differ from the uploaded PNG")
    second = client.get(download_route)
    _check(second.status_code == 200 and second.content == first.content, "Repeated download changed approved bytes")
    _check(_response(client.post(route + "/exports", json=export_body), 200)["export"]["export_id"] == ready["export_id"],
           "Repeated export failed to reuse the existing file")
    # A later edit invalidates the exact approval and prevents old downloads.
    _response(client.patch(document_route, json={"expected_revision": document["document_revision"], "operations": [
        {"op": "rename_page", "page_id": document["pages"][0]["page_id"], "title": "승인 후 수정된 페이지"}]}), 200)
    refreshed = _response(client.get(document_route), 200)
    _check(refreshed["approval"] is None and refreshed["validation"] is None,
           "Old approval or validation survived an edit")
    _response(client.get(download_route), 409, "APPROVAL_NOT_ACTIVE")
    _response(client.post(document_route + "/approvals", json=approval_body), 409, "DOCUMENT_REVISION_CONFLICT")
    return {"status": "passed", "renderer": pdf_layout["renderer"], "pdf_pages": pdf_layout["actual_pages"],
            "pdf_bytes": len(first.content), "pdf_images": pdf_images,
            "pdf_sha256": hashlib.sha256(first.content).hexdigest(),
            "checks": ["edit_save", "stale_edit_409", "explicit_validation",
                "actual_pdf_layout", "explicit_final_consent", "approval_replay", "pdf_download_and_reuse",
                "edit_invalidates_approval", "stale_approval_409", "old_download_409"]
                + (["photo_preserved_after_edit", "photo_embedded_in_pdf", "pdf_preview_png"] if photo_asset_id else [])}


def run_check(*, timeout_s: float = 30, publication: bool = False, photos: bool = False,
              export_browser_path: Path | None = None) -> dict:
    """Exercise cookie ownership, the S01 happy path, replays, errors and cleanup."""
    _check(not publication or export_browser_path is not None, "Publication check requires an explicit installed browser")
    _check(not photos or publication, "Photo check requires publication=True")
    publication_result = None
    brief = {**BRIEF, "photo_preference": "balanced"} if photos else BRIEF
    photo_data = _photo_png() if photos else None
    photo_asset_id = None
    with temporary_server(timeout_s=timeout_s, export_browser_path=export_browser_path) as (base_url, settings):
        from app.db import ORM_SCHEMA_VERSION, connect

        temporary_root = settings.private_runs_dir.parent
        with httpx.Client(base_url=base_url, timeout=timeout_s, trust_env=False) as client:
            session = _response(client.post("/api/v1/sessions", json={"brief": brief},
                                            headers={"Idempotency-Key": "http-session"}), 201)
            sid = session["session_id"]
            route = f"/api/v1/sessions/{sid}"
            _check(settings.owner_cookie_name in client.cookies, "Session did not set owner cookie")
            replay = _response(client.post("/api/v1/sessions", json={"brief": brief},
                                           headers={"Idempotency-Key": "http-session"}), 201)
            _check(replay == session, "Session replay changed")

            def upload():
                files = [("files", ("company.txt", SOURCE, "text/plain"))]
                if photo_data is not None:
                    files.append(("files", ("photo.png", photo_data, "image/png")))
                return client.post(f"{route}/sources", data={} if photos else {"kind": "company"}, files=files,
                                   headers={"Idempotency-Key": "http-upload"})

            accepted = _response(upload(), 202)
            _job(client, sid, accepted["job_id"], timeout_s)
            _check(_response(upload(), 202) == accepted, "Upload replay changed")
            sources = _response(client.get(f"{route}/sources"), 200)["items"]
            expected_sources = 2 if photos else 1
            _check(len(sources) == expected_sources and all(source["parse_status"] == "complete" for source in sources),
                   "Sources were not read exactly once")
            if photos:
                pictures = [source for source in sources if source["image_available"]]
                _check(len(pictures) == 1 and pictures[0]["kind"] == "photo" and not pictures[0]["text_available"],
                       "PNG was not classified as a photo without extracted text")
                _check(len(pictures[0]["asset_ids"]) == 1, "PNG did not produce exactly one asset")
                photo_asset_id = pictures[0]["asset_ids"][0]
                image = client.get(route + f"/assets/{photo_asset_id}")
                _check(image.status_code == 200 and image.content == photo_data, "Photo asset changed the uploaded bytes")
                _check(image.headers.get("content-type", "").startswith("image/png") and
                       "no-store" in image.headers.get("cache-control", ""), "Photo response headers missing")
            selection = {"expected_input_revision": 1, "selected_source_ids": [source["source_id"] for source in sources]}
            selected = _response(client.patch(f"{route}/inputs", json=selection,
                                             headers={"Idempotency-Key": "http-selection"}), 200)
            _check(_response(client.patch(f"{route}/inputs", json=selection,
                                         headers={"Idempotency-Key": "http-selection"}), 200) == selected,
                   "Selection replay changed")
            revision = selected["input_revision"]
            _response(client.patch(f"{route}/inputs", json=selection), 409, "INPUT_REVISION_CONFLICT")
            _response(client.patch(f"{route}/inputs", json={"expected_input_revision": revision}), 400, "INVALID_REQUEST")

            preflight_body = {"expected_input_revision": revision}
            accepted_preflight = _response(client.post(f"{route}/preflights", json=preflight_body,
                                                       headers={"Idempotency-Key": "http-preflight"}), 202)
            job = _job(client, sid, accepted_preflight["job_id"], timeout_s)
            preflight = _response(client.get(f"{route}/preflights/{job['result_ref']['preflight_id']}"), 200)
            _check(preflight["can_generate"] and preflight["confirmed_at"] is None, "Preflight is not ready for confirmation")
            _check(_response(client.post(f"{route}/preflights", json=preflight_body,
                                         headers={"Idempotency-Key": "http-preflight"}), 202) == accepted_preflight,
                   "Preflight replay changed")
            draft_body = {"preflight_id": preflight["preflight_id"], "input_revision": revision, "confirmed": False}
            _response(client.post(f"{route}/drafts", json=draft_body), 422, "PREFLIGHT_NOT_CONFIRMED")
            draft_body["confirmed"] = True
            accepted_draft = _response(client.post(f"{route}/drafts", json=draft_body,
                                                   headers={"Idempotency-Key": "http-draft"}), 202)
            job = _job(client, sid, accepted_draft["job_id"], timeout_s)
            document_route = f"{route}/documents/{job['result_ref']['document_id']}"
            document = _response(client.get(document_route), 200)["document"]
            _check(document["document_revision"] == 1 and len(document["pages"]) == 4, "Draft document did not match request")
            _check(_response(client.post(f"{route}/drafts", json=draft_body,
                                         headers={"Idempotency-Key": "http-draft"}), 202) == accepted_draft,
                   "Draft replay changed")

            with httpx.Client(base_url=base_url, timeout=timeout_s, trust_env=False) as stranger:
                _response(stranger.get(route), 401, "UNAUTHORIZED")
                if photo_asset_id:
                    _response(stranger.get(route + f"/assets/{photo_asset_id}"), 401, "UNAUTHORIZED")
                other = _response(stranger.post("/api/v1/sessions", json={"brief": BRIEF}), 201)
                _response(stranger.get(route), 404, "RESOURCE_NOT_FOUND")
                _response(stranger.get(document_route), 404, "RESOURCE_NOT_FOUND")
                if photo_asset_id:
                    _response(stranger.get(route + f"/assets/{photo_asset_id}"), 404, "RESOURCE_NOT_FOUND")
                    _response(stranger.get(f"/api/v1/sessions/{other['session_id']}/assets/{photo_asset_id}"),
                              404, "RESOURCE_NOT_FOUND")
                _response(stranger.delete(f"/api/v1/sessions/{other['session_id']}"), 200)

            with connect(settings.db_path) as conn:
                counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                          for table in ("sources", "source_versions", "extraction_runs", "input_revisions",
                                        "session_source_selections", "jobs", "preflights", "documents", "document_revisions")}
                _check(counts == {"sources": expected_sources, "source_versions": expected_sources,
                                  "extraction_runs": expected_sources, "input_revisions": 3,
                                  "session_source_selections": expected_sources, "jobs": 3, "preflights": 1, "documents": 1,
                                  "document_revisions": 1}, f"Unexpected DB counts: {counts}")
                _check(conn.execute("PRAGMA user_version").fetchone()[0] == ORM_SCHEMA_VERSION,
                       f"Expected ERD v{ORM_SCHEMA_VERSION}")
                _check(conn.execute("PRAGMA foreign_key_check").fetchall() == [], "Foreign key violation")
            if publication:
                publication_result = _publication_check(client, sid, document_route, timeout_s, photo_asset_id)
                with connect(settings.db_path) as conn:
                    _check(conn.execute("SELECT COUNT(*) FROM jobs WHERE kind='draft'").fetchone()[0] == 1,
                           "Publication or repeated download generated another draft")
                    _check(conn.execute("PRAGMA foreign_key_check").fetchall() == [], "Publication foreign key violation")
                    artifact = conn.execute("SELECT a.sha256, a.size_bytes FROM exports e JOIN artifacts a "
                                            "ON a.artifact_id=e.artifact_id WHERE e.session_id=?", (sid,)).fetchall()
                    _check(len(artifact) == 1 and artifact[0]["sha256"] == publication_result["pdf_sha256"] and
                           artifact[0]["size_bytes"] == publication_result["pdf_bytes"], "Download differs from the saved artifact")
                    publication_result["checks"].append("saved_artifact_hash")
            closed = _response(client.delete(route), 200)
            _check(closed["cleanup"] == "done", "Session cleanup did not finish")
            _check(not (settings.private_runs_dir / sid).exists(), "Session files remain after cleanup")
            _response(client.get(document_route), 410, "SESSION_EXPIRED")
            if photo_asset_id:
                _response(client.get(route + f"/assets/{photo_asset_id}"), 410, "SESSION_EXPIRED")
            _response(upload(), 410, "SESSION_EXPIRED")
            with connect(settings.db_path) as conn:
                _check(conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 0, "Private extracted text remains")
                _check(conn.execute("SELECT COUNT(*) FROM document_revisions WHERE content_json!='{}'").fetchone()[0] == 0,
                       "Private document content remains")
                _check(conn.execute("PRAGMA foreign_key_check").fetchall() == [], "Foreign key violation after cleanup")

    _check(not temporary_root.exists(), "Temporary test directory remains")
    return {"status": "passed", "transport": "localhost HTTP", "agent_mode": "mock", "schema_version": ORM_SCHEMA_VERSION,
            "checks": ["session_cookie", "upload_read_select", "preflight_confirm_draft", "idempotency_replays",
                       "invalid_400", "revision_409", "unconfirmed_422", "no_owner_401", "other_owner_404",
                       "closed_410", "foreign_keys", "session_purge", "server_and_temp_cleanup"]
                       + (["photo_upload_and_asset_bytes", "photo_owner_isolation", "closed_photo_410"] if photos else []),
            "counts_before_close": counts, **({"publication": publication_result} if publication_result else {})}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=30, help="Startup/job/request timeout in seconds (default: 30)")
    parser.add_argument("--frontend", type=Path, help="Also run the frontend's isolated S01 browser check (Node + Chrome/Edge)")
    parser.add_argument("--publication", action="store_true", help="Also edit, validate, approve and download an actual PDF")
    parser.add_argument("--photos", action="store_true", help="Include a generated PNG in the HTTP/PDF check (requires --publication)")
    parser.add_argument("--browser-path", type=Path, help="Installed Chrome/Edge for --publication (otherwise auto-detect)")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 120:
        parser.error("--timeout must be between 1 and 120 seconds")
    if args.photos and not args.publication:
        parser.error("--photos requires --publication")
    try:
        browser_path = None
        if args.publication:
            candidates = [args.browser_path, os.environ.get("S01_BROWSER_PATH"),
                          r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                          r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                          "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                          shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser")]
            browser_path = next((Path(candidate).resolve() for candidate in candidates if candidate and Path(candidate).is_file()), None)
            _check(browser_path is not None, "--publication requires an installed Chrome/Edge browser")
        result = run_check(timeout_s=args.timeout, publication=args.publication, photos=args.photos, export_browser_path=browser_path)
        if args.frontend:
            frontend = args.frontend.resolve()
            script = frontend / "scripts" / "check-s01-browser.mjs"
            node = shutil.which("node")
            _check(script.is_file() and node is not None, "Frontend browser script and Node are required")
            with temporary_server(timeout_s=args.timeout, export_browser_path=browser_path) as (address, _settings):
                browser_arguments = [node, str(script), "--backend", address]
                if args.publication:
                    browser_arguments.append("--publication")
                browser_result = subprocess.run(browser_arguments, cwd=frontend,
                                                capture_output=True, text=True, encoding="utf-8", timeout=420 if args.publication else 180,
                                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                if browser_result.stdout:
                    print(browser_result.stdout)
                _check(browser_result.returncode == 0, f"Frontend browser check failed: {browser_result.stderr}")
            result["browser_check"] = "passed"
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
