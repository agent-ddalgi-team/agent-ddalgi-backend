"""승인(Approval) — 승인 7조건, 생성, 무효화."""
from __future__ import annotations

import json
import sqlite3
import uuid

from app.errors import ApiError
from app.models import ApprovalCreate, ApprovalOut, Document
from app.services import layout_checks, validation
from app.timeutil import now, to_iso


def to_out(row: sqlite3.Row) -> ApprovalOut:
    return ApprovalOut(approval_id=row["approval_id"], document_id=row["document_id"],
                       document_revision=row["document_revision"], input_revision=row["input_revision"],
                       format=row["format"], validation_id=row["validation_id"], layout_check_id=row["layout_check_id"],
                       template_version=row["template_version"], render_options_hash=row["render_options_hash"],
                       asset_manifest_hash=row["asset_manifest_hash"], approved_at=row["approved_at"],
                       approved_by=row["approved_by"], status=row["status"], invalidated_at=row["invalidated_at"],
                       invalidated_reason=row["invalidated_reason"])


def active_for(conn: sqlite3.Connection, document_id: str, document_revision: int, input_revision: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM approvals WHERE document_id=? AND document_revision=? AND input_revision=? AND status='active' "
        "ORDER BY approved_at DESC, rowid DESC LIMIT 1", (document_id, document_revision, input_revision)).fetchone()


def check_conditions(conn: sqlite3.Connection, session_row: sqlite3.Row, document: Document, body: ApprovalCreate) -> tuple[sqlite3.Row, str]:
    """조건 ①은 라우터(접근 검사)에서 끝났다. ②~⑦을 순서대로 검사하고 (Validation 행, asset_manifest_hash)를 돌려준다."""
    # ② 요청 버전 = 최신 저장본
    if body.expected_revision != document.document_revision:
        raise ApiError(409, "DOCUMENT_REVISION_CONFLICT", "문서가 변경되었습니다. 최신 문서에서 다시 요청해 주세요.",
                       details={"expected_revision": body.expected_revision, "current_revision": document.document_revision})
    # ③ 입력 버전 최신 + 사전 확인 완료
    if body.input_revision != session_row["input_revision"] or document.input_revision != session_row["input_revision"]:
        raise ApiError(409, "INPUT_REVISION_CONFLICT", "자료·목적이 바뀐 뒤에는 승인할 수 없습니다. 사전 점검부터 다시 진행해 주세요.",
                       details={"requested_input_revision": body.input_revision, "document_input_revision": document.input_revision,
                                "current_input_revision": session_row["input_revision"]})
    confirmed = conn.execute("SELECT 1 FROM preflights WHERE session_id=? AND input_revision=? AND confirmed_at IS NOT NULL",
                             (session_row["session_id"], session_row["input_revision"])).fetchone()
    if not confirmed:
        raise ApiError(422, "PREFLIGHT_NOT_CONFIRMED", "이 입력 버전의 사전 점검이 확인되지 않았습니다.",
                       details={"input_revision": session_row["input_revision"]})
    # ④ Validation 완료·현재 버전·blocker 없음
    v = conn.execute("SELECT * FROM validations WHERE validation_id=?", (body.validation_id,)).fetchone()
    if v is None or v["document_id"] != document.document_id or v["document_revision"] != document.document_revision \
            or v["input_revision"] != session_row["input_revision"]:
        raise ApiError(422, "VALIDATION_NOT_PASSED", "현재 문서·입력 버전의 검증 결과가 아닙니다. 검증을 다시 실행해 주세요.",
                       details={"validation_id": body.validation_id, "reason": "not_current"})
    latest = validation.latest_validation(conn, document.document_id, document.document_revision, session_row["input_revision"])
    if latest is None or latest["validation_id"] != v["validation_id"]:
        raise ApiError(422, "VALIDATION_NOT_PASSED", "더 최신 검증 결과가 있습니다. 최신 결과로 승인해 주세요.",
                       details={"validation_id": body.validation_id, "reason": "superseded"})
    open_blockers = [r["issue_id"] for r in conn.execute(
        "SELECT issue_id FROM issues WHERE document_id=? AND status='open' AND severity='blocker'", (document.document_id,))]
    if v["status"] == "pending" or open_blockers or v["status"] == "failed":
        raise ApiError(422, "VALIDATION_NOT_PASSED", "미해결 필수 문제가 있어 승인할 수 없습니다.",
                       details={"validation_id": body.validation_id, "reason": "open_blockers", "issue_ids": open_blockers})
    # ⑤ 필수 내용이 실제 블록에 — 검증과 별개로 지금 문서를 다시 본다
    preflight_row = conn.execute("SELECT * FROM preflights WHERE session_id=? AND input_revision=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                                 (session_row["session_id"], session_row["input_revision"])).fetchone()
    from app.services import preflights as pf_service
    pf = pf_service.get(conn, session_row["session_id"], preflight_row["preflight_id"]) if preflight_row else None
    ctx = validation.load_context(conn, session_row["session_id"], pf)
    if not (validation._required_present(document, ctx, validation.REQUIRED_NAME_KEYS)
            and validation._required_present(document, ctx, validation.REQUIRED_BUSINESS_KEYS)):
        raise ApiError(422, "UNRESOLVED_REQUIRED", "회사명·주요 사업/공정이 실제 문서 블록에 없습니다.")
    # ⑥ 배치 검사 일치
    manifest = layout_checks.asset_manifest_hash(conn, document)
    _, reason = layout_checks.matching_passed(conn, body.layout_check_id, document, session_row["input_revision"], body.format, manifest)
    if reason is not None:
        raise ApiError(422, "LAYOUT_NOT_READY", "요청 형식의 배치 검사가 현재 문서에서 완료되지 않았습니다.",
                       details={"layout_check_id": body.layout_check_id, "format": body.format, "reason": reason})
    # ⑦ 사용자 최종 승인
    if not body.confirmed:
        raise ApiError(422, "APPROVAL_NOT_CONFIRMED", "최종 승인(confirmed=true)이 필요합니다.")
    return v, manifest


def create(conn: sqlite3.Connection, session_row: sqlite3.Row, owner_id: str, document: Document,
           body: ApprovalCreate, manifest: str) -> ApprovalOut:
    stamp = to_iso(now())
    approval_id = f"apr_{uuid.uuid4().hex[:16]}"
    conn.execute(
        "INSERT INTO approvals (approval_id, session_id, document_id, document_revision, input_revision, format, validation_id, "
        "layout_check_id, template_version, render_options_hash, asset_manifest_hash, approved_at, approved_by, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
        (approval_id, session_row["session_id"], document.document_id, document.document_revision, body.input_revision,
         body.format, body.validation_id, body.layout_check_id, layout_checks.TEMPLATE_VERSION,
         layout_checks.RENDER_OPTIONS_HASH, manifest, stamp, owner_id, stamp))
    return to_out(conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone())


def invalidate_for_document(conn: sqlite3.Connection, document_id: str, reason: str) -> int:
    cur = conn.execute("UPDATE approvals SET status='invalidated', invalidated_at=?, invalidated_reason=? "
                       "WHERE document_id=? AND status='active'", (to_iso(now()), reason, document_id))
    return cur.rowcount


def invalidate_for_session(conn: sqlite3.Connection, session_id: str, reason: str) -> int:
    cur = conn.execute("UPDATE approvals SET status='invalidated', invalidated_at=?, invalidated_reason=? "
                       "WHERE session_id=? AND status='active'", (to_iso(now()), reason, session_id))
    return cur.rowcount
