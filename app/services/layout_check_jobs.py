"""배치 검사 Job(kind=layout_check) — BE-08. 스냅샷 → 렌더 → 불변 artifact → 미리보기 → layout_checks 행 + layout Issue.

규칙
- status=passed ⇔ render.layout_ok AND publication_policy_ok. required 검사가 not_checked면 passed가 될 수 없다(BE-07 규칙).
  DOCX는 overflow가 not_checked라 이 범위에서는 항상 failed(fail_reasons에 overflow:not_checked). 파일 생성·PDF 기준 미리보기만 제공한다.
- 완료 직전 재확인(같은 BEGIN IMMEDIATE 트랜잭션): 세션 active·미만료, 문서 현재 revision·입력 버전이 시작값과 같음, 공개 허가 현재 값,
  asset_manifest_hash 현재 DB값 = 스냅샷값. 하나라도 다르면 결과를 버리고(임시 산출물 삭제) Job failed.
- layout Issue(origin=layout)는 이 형식의 것만 갱신·재검출·닫는다. 내용 Issue와 다른 형식의 Issue는 건드리지 않는다.
- AI를 호출하지 않는다. 응답·result_ref에 서버 경로를 넣지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import connect
from app.models import Document, LayoutCheckOut
from app.services import artifacts, export_render, jobs, layout_checks, publication
from app.services.documents import get_current
from app.services.validation import IssueDraft
from app.timeutil import from_iso, now, to_iso

logger = logging.getLogger(__name__)

ISSUE_CODES = {"overflow": "LAYOUT_OVERFLOW", "broken_image": "BROKEN_IMAGE", "placeholder_remaining": "PLACEHOLDER_REMAINING"}
CHECK_OF_CODE = {code: key for key, code in ISSUE_CODES.items()} | {"IMAGE_PUBLICATION_UNCONFIRMED": "publication"}
LAYOUT_ISSUE_CODES = frozenset(ISSUE_CODES.values()) | {publication.ISSUE_CODE}
DOCX_PREVIEW_WARNING = "DOCX 미리보기는 같은 스냅샷의 PDF 렌더 기준이며 DOCX 배치 검사 증거가 아닙니다. DOCX 쪽 나눔은 열람 프로그램에 따라 달라질 수 있습니다."
DOCX_NOT_APPROVABLE = "DOCX는 넘침 검사를 실제로 수행하지 못해(overflow not_checked) 이 범위에서는 승인·출력할 수 없습니다."
PREVIEW_SCALE = 1.0   # pypdfium2 render scale (72dpi 기준 1.0 ≈ 595×842px)


def target_key(document_id: str, document_revision: int, input_revision: int, fmt: str) -> str:
    return f"{document_id}@{document_revision}@{input_revision}@{fmt}"


@dataclass
class _Failure(Exception):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] | None = None


def _session_valid(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if row is None or row["status"] != "active" or now() >= from_iso(row["expires_at"]):
        raise _Failure("SESSION_EXPIRED", "세션이 종료되었거나 만료되어 배치 검사 결과를 저장하지 않았습니다.")
    return row


def _document_current(conn: sqlite3.Connection, session_row: sqlite3.Row, document_id: str, document_revision: int,
                      input_revision: int) -> Document:
    document = get_current(conn, session_row["session_id"], document_id)
    if document.document_revision != document_revision:
        raise _Failure("DOCUMENT_REVISION_CONFLICT", "검사 중 문서가 바뀌어 결과를 버렸습니다. 최신 문서로 다시 요청해 주세요.",
                       details={"checked_revision": document_revision, "current_revision": document.document_revision})
    if session_row["input_revision"] != input_revision or document.input_revision != input_revision:
        raise _Failure("INPUT_REVISION_CONFLICT", "검사 중 입력이 바뀌어 결과를 버렸습니다. 사전 점검부터 다시 진행해 주세요.",
                       details={"checked_input_revision": input_revision, "current_input_revision": session_row["input_revision"]})
    return document


def _render_previews(pdf_path: Path, out_dir: Path) -> list[dict[str, Any]]:
    """PDF 각 쪽을 PNG로. pypdfium2가 없거나 실패하면 빈 목록과 오류 문자열."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise _Failure("PREVIEW_UNAVAILABLE", "미리보기 생성 도구(pypdfium2)가 없습니다.", retryable=False)
    out: list[dict[str, Any]] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        for i, page in enumerate(pdf, start=1):
            bitmap = page.render(scale=PREVIEW_SCALE)
            image = bitmap.to_pil()
            path = out_dir / f"page_{i}.png"
            image.save(path, format="PNG", optimize=True)
            out.append({"page_no": i, "path": path, "width": image.width, "height": image.height})
            bitmap.close()
            page.close()
    finally:
        pdf.close()
    return out


def _identity(d: IssueDraft) -> str:
    return d.identity_key


def _layout_drafts(result: export_render.RenderResult, fmt: str, pub: publication.PublicationResult) -> list[IssueDraft]:
    drafts: list[IssueDraft] = []
    for f in result.findings:
        drafts.append(IssueDraft("layout", ISSUE_CODES[f.kind], "blocker", f.message, block_ids=[f.block_id] if f.block_id else [],
                                 origin="layout", layout_format=fmt))
    for b in pub.blocked:
        drafts.append(IssueDraft("layout", publication.ISSUE_CODE, "blocker", publication.message_for(b),
                                 block_ids=list(b.block_ids), origin="layout", layout_format=None))
    return drafts


def executed_check_keys(checks, publication_checked: bool) -> set[str]:
    """이번 Job이 실제로 수행한 검사(not_checked 제외). 공개 허가 검사는 서버 정책 검사라 수행 여부를 따로 받는다."""
    keys = {c.check_key for c in checks if c.result != "not_checked"}
    if publication_checked:
        keys.add("publication")
    return keys


def _covered_by_this_check(row: sqlite3.Row, executed: set[str]) -> bool:
    """BE-06 _covered_by_this_validation과 같은 원칙: 그 Issue가 속한 검사를 이번에 실제로 재실행했을 때만 '원인이 사라졌다'고 본다.
    block_ids=[](문서 전체 Issue)도 검사 종류로 판단한다."""
    return CHECK_OF_CODE.get(row["code"], "") in executed


def persist_layout_issues(conn: sqlite3.Connection, session_id: str, document: Document, layout_check_id: str, fmt: str,
                          drafts: list[IssueDraft], input_revision: int, checks=(), publication_checked: bool = True) -> list[str]:
    """이 형식의 layout Issue를 갱신한다(BE-06 정체성·재개 규칙). 다른 형식·내용 Issue는 건드리지 않는다.

    - 같은 identity_key 행 갱신. resolved/excluded였다가 다시 검출되면 open(이전 resolution은 이력).
    - 이번 검사에서 다시 나오지 않은 이 형식(또는 형식 무관 공개 허가)의 open layout Issue는 **그 검사를 실제로 재실행했을 때만**
      resolved(by=server). not_checked인 검사(예: DOCX overflow, 측정 실패)의 Issue는 보존한다.
    """
    executed = executed_check_keys(list(checks), publication_checked)
    stamp = to_iso(now())
    produced: set[str] = set()
    for d in drafts:
        key = d.identity_key
        anchor = hashlib.sha256(json.dumps({"blocks": sorted(d.block_ids), "format": d.layout_format, "input": input_revision}).encode()).hexdigest()
        row = conn.execute("SELECT * FROM issues WHERE document_id=? AND identity_key=?", (document.document_id, key)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO issues (issue_id, session_id, document_id, identity_key, scope, code, severity, status, message, "
                "source_ids_json, fact_ids_json, block_ids_json, origin, anchor_fingerprint, resolution_json, resolution_history_json, "
                "first_validation_id, last_validation_id, created_at, updated_at, layout_format) "
                "VALUES (?, ?, ?, ?, 'layout', ?, 'blocker', 'open', ?, '[]', '[]', ?, 'layout', ?, NULL, '[]', NULL, NULL, ?, ?, ?)",
                (f"iss_{uuid.uuid4().hex[:16]}", session_id, document.document_id, key, d.code, d.message,
                 json.dumps(sorted(d.block_ids)), anchor, stamp, stamp, d.layout_format))
        else:
            status, resolution, history = row["status"], row["resolution_json"], json.loads(row["resolution_history_json"])
            if status != "open":
                if resolution:
                    history.append({**json.loads(resolution), "reopened_at": stamp, "reopened_by_layout_check": layout_check_id,
                                    "previous_status": status})
                status, resolution = "open", None
            conn.execute("UPDATE issues SET message=?, status=?, anchor_fingerprint=?, resolution_json=?, resolution_history_json=?, "
                         "updated_at=?, layout_format=? WHERE issue_id=?",
                         (d.message, status, anchor, resolution, json.dumps(history, ensure_ascii=False), stamp, d.layout_format, row["issue_id"]))
        produced.add(key)
    for row in conn.execute("SELECT * FROM issues WHERE document_id=? AND status='open' AND origin='layout' "
                            "AND (layout_format=? OR layout_format IS NULL)", (document.document_id, fmt)).fetchall():
        if row["identity_key"] in produced:
            continue
        if not _covered_by_this_check(row, executed):
            continue   # 재실행하지 않은(not_checked) 검사의 Issue는 보존
        resolution = {"action": "resolved", "by": "server", "reason": f"{fmt} 배치 재검사에서 원인이 더 이상 확인되지 않음",
                      "at": stamp, "document_revision": document.document_revision, "input_revision": input_revision,
                      "layout_check_id": layout_check_id}
        conn.execute("UPDATE issues SET status='resolved', resolution_json=?, updated_at=? WHERE issue_id=?",
                     (json.dumps(resolution, ensure_ascii=False), stamp, row["issue_id"]))
    return [r["issue_id"] for r in conn.execute(
        "SELECT issue_id FROM issues WHERE document_id=? AND origin='layout' AND (layout_format=? OR layout_format IS NULL) "
        "ORDER BY created_at, rowid", (document.document_id, fmt))]


def run_layout_check_job(settings: Settings, session_id: str, job_id: str, document_id: str, document_revision: int,
                         input_revision: int, fmt: str) -> None:
    tmp = artifacts.temp_dir(settings, session_id, job_id)
    artifacts.cleanup_temp_dirs(settings, older_than_s=300)   # 이전 Job이 남긴 브라우저 프로필 찌꺼기 정리(5분 지난 것)
    try:
        with connect(settings.db_path) as conn:
            jobs.set_progress(conn, job_id, "rendering", "배치 검사용 파일을 만드는 중")
            session_row = _session_valid(conn, session_id)
            document = _document_current(conn, session_row, document_id, document_revision, input_revision)
            snapshot = export_render.build_snapshot(conn, settings, session_id, document)
            pub_start = publication.check_document(conn, document)

        # 렌더는 DB 잠금 밖에서(수초). 산출물은 Job 전용 임시 폴더로.
        result = export_render.render(snapshot, fmt, tmp / "out", settings)
        preview_source = result.file_path
        if fmt == "docx":
            preview_source = export_render.render(snapshot, "pdf", tmp / "preview_pdf", settings).file_path
        previews = _render_previews(preview_source, tmp / "png")

        with connect(settings.db_path, immediate=True) as conn:
            jobs.set_progress(conn, job_id, "finalizing", "결과를 확인하는 중")
            session_row = _session_valid(conn, session_id)                      # 늦은 결과 방지: 세션 만료·종료
            document = _document_current(conn, session_row, document_id, document_revision, input_revision)
            manifest_now = layout_checks.asset_manifest_hash(conn, document)
            if manifest_now != snapshot.asset_manifest_hash:
                raise _Failure("ASSET_MANIFEST_CHANGED", "검사 중 문서 이미지가 바뀌어 결과를 버렸습니다. 다시 요청해 주세요.")
            pub = publication.check_document(conn, document)                    # 시작 당시 값이 아니라 지금 값
            layout_check_id = f"lc_{uuid.uuid4().hex[:16]}"
            artifact = artifacts.store(conn, settings, session_id, result, document_id=document_id, document_revision=document_revision,
                                       input_revision=input_revision, layout_check_id=layout_check_id)
            preview_ids = _store_previews(conn, settings, session_id, layout_check_id, artifact["artifact_id"], previews)
            drafts = _layout_drafts(result, fmt, pub)
            issue_ids = persist_layout_issues(conn, session_id, document, layout_check_id, fmt, drafts, input_revision,
                                              checks=result.checks, publication_checked=True)
            fail_reasons = [f"{c.check_key}:{c.result}" for c in result.checks if c.required and c.result != "ok"] + pub.reason_codes()
            status = "passed" if (result.layout_ok and pub.ok) else "failed"
            warnings = [DOCX_PREVIEW_WARNING, DOCX_NOT_APPROVABLE] if fmt == "docx" else []
            stamp = to_iso(now())
            conn.execute(
                "INSERT INTO layout_checks (layout_check_id, session_id, document_id, document_revision, input_revision, format, "
                "template_version, render_options_hash, asset_manifest_hash, status, actual_pages, issue_ids_json, created_at, "
                "layout_ok, publication_policy_ok, checks_json, findings_json, fail_reasons_json, renderer, artifact_id, "
                "preview_basis, preview_ids_json, job_id, publication_checked_at, warnings_json, publication_blocks_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (layout_check_id, session_id, document_id, document_revision, input_revision, fmt,
                 result.template_version, result.render_options_hash, result.asset_manifest_hash, status, result.actual_pages,
                 json.dumps(issue_ids), stamp, int(result.layout_ok), int(pub.ok),
                 json.dumps([c.__dict__ for c in result.checks], ensure_ascii=False),
                 json.dumps([f.__dict__ for f in result.findings], ensure_ascii=False),
                 json.dumps(fail_reasons), result.renderer, artifact["artifact_id"], "pdf", json.dumps(preview_ids), job_id,
                 pub.checked_at, json.dumps(warnings, ensure_ascii=False), json.dumps(pub.as_out(), ensure_ascii=False)))
            jobs.succeed(conn, job_id, {"layout_check_id": layout_check_id, "format": fmt, "status": status,
                                         "layout_ok": result.layout_ok, "publication_policy_ok": pub.ok,
                                         "actual_pages": result.actual_pages, "artifact_id": artifact["artifact_id"],
                                         "preview_asset_ids": preview_ids, "preview_basis": "pdf", "warnings": warnings})
            _ = pub_start  # 시작 시 값은 참고용. 저장은 완료 직전 값으로만 한다.
    except _Failure as exc:
        artifacts.discard_temp(tmp)          # 임시 산출물을 먼저 지운 뒤 실패를 기록한다(공개·연결되지 않음)
        with connect(settings.db_path) as conn:
            jobs.fail(conn, job_id, exc.code, exc.message, exc.retryable, exc.details)
    except export_render.RenderError as exc:
        artifacts.discard_temp(tmp)
        with connect(settings.db_path) as conn:
            jobs.fail(conn, job_id, "LAYOUT_RENDER_FAILED", f"배치 검사용 파일을 만들지 못했습니다({exc.code}).",
                      exc.code in ("render_timeout", "render_failed"), {"reason": exc.code})
    except Exception as exc:  # noqa: BLE001
        artifacts.discard_temp(tmp)
        with connect(settings.db_path) as conn:
            # 세션이 도중에 종료·만료되면 세션 폴더(임시 산출물 포함)가 지워져 파일 오류로 나타난다 → 실제 사유로 기록
            row = conn.execute("SELECT status, expires_at FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None or row["status"] != "active" or now() >= from_iso(row["expires_at"]):
                jobs.fail(conn, job_id, "SESSION_EXPIRED", "세션이 종료되었거나 만료되어 배치 검사 결과를 저장하지 않았습니다.", False)
            else:
                logger.exception("layout_check job failed")
                jobs.fail(conn, job_id, "INTERNAL_ERROR", "배치 검사 중 오류가 났습니다.", True, {"error": type(exc).__name__})
    finally:
        artifacts.discard_temp(tmp)


def _store_previews(conn: sqlite3.Connection, settings: Settings, session_id: str, layout_check_id: str, artifact_id: str,
                    previews: list[dict[str, Any]]) -> list[str]:
    import os

    ids: list[str] = []
    final_dir = artifacts.artifacts_dir(settings, session_id) / artifacts.PREVIEW_DIR
    final_dir.mkdir(parents=True, exist_ok=True)
    stamp = to_iso(now())
    for p in previews:
        pid = f"prv_{uuid.uuid4().hex[:16]}"
        final = final_dir / f"{pid}.png"
        digest = artifacts.sha256_of(p["path"])
        size = p["path"].stat().st_size
        os.replace(p["path"], final)
        conn.execute(
            "INSERT INTO layout_previews (asset_id, session_id, layout_check_id, artifact_id, page_no, stored_path, sha256, size_bytes, "
            "width, height, mime_type, status, created_at, expires_at, deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'image/png', 'ready', ?, NULL, NULL)",
            (pid, session_id, layout_check_id, artifact_id, p["page_no"], final.relative_to(settings.private_runs_dir).as_posix(),
             digest, size, p["width"], p["height"], stamp))
        ids.append(pid)
    return ids


# ---------------- 조회 ----------------

def to_out(row: sqlite3.Row) -> LayoutCheckOut:
    def _json(col: str, default):
        return json.loads(row[col]) if row[col] else default

    return LayoutCheckOut(
        layout_check_id=row["layout_check_id"], document_id=row["document_id"], document_revision=row["document_revision"],
        input_revision=row["input_revision"], format=row["format"], status=row["status"], template_version=row["template_version"],
        render_options_hash=row["render_options_hash"], asset_manifest_hash=row["asset_manifest_hash"], actual_pages=row["actual_pages"],
        issue_ids=_json("issue_ids_json", []), layout_ok=bool(row["layout_ok"]), publication_policy_ok=bool(row["publication_policy_ok"]),
        publication_blocks=_json("publication_blocks_json", []), checks=_json("checks_json", []), findings=_json("findings_json", []),
        fail_reasons=_json("fail_reasons_json", []), renderer=row["renderer"], artifact_id=row["artifact_id"],
        preview_asset_ids=_json("preview_ids_json", []), preview_basis=row["preview_basis"], warnings=_json("warnings_json", []),
        created_at=row["created_at"])


def latest_for(conn: sqlite3.Connection, document_id: str, document_revision: int, input_revision: int, fmt: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM layout_checks WHERE document_id=? AND document_revision=? AND input_revision=? AND format=? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1", (document_id, document_revision, input_revision, fmt)).fetchone()


def latest_by_format(conn: sqlite3.Connection, document_id: str, document_revision: int, input_revision: int) -> dict[str, LayoutCheckOut | None]:
    out: dict[str, LayoutCheckOut | None] = {}
    for fmt in ("pdf", "docx"):
        row = latest_for(conn, document_id, document_revision, input_revision, fmt)
        out[fmt] = to_out(row) if row else None
    return out


def open_layout_blockers(conn: sqlite3.Connection, document_id: str, fmt: str) -> list[str]:
    """이 형식의(또는 형식 무관 공개 허가) open layout blocker."""
    return [r["issue_id"] for r in conn.execute(
        "SELECT issue_id FROM issues WHERE document_id=? AND status='open' AND scope='layout' AND severity='blocker' "
        "AND (layout_format=? OR layout_format IS NULL)", (document_id, fmt))]
