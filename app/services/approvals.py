"""승인(Approval) — 승인 7조건, 생성, 무효화."""
from __future__ import annotations

import json
import sqlite3
import uuid

from app.errors import ApiError
from app.models import ApprovalCreate, ApprovalOut, Document
from app.services import layout_checks, publication, validation
from app.timeutil import now, to_iso


def _col(row: sqlite3.Row, name: str):
    return row[name] if name in row.keys() else None


def to_out(row: sqlite3.Row) -> ApprovalOut:
    return ApprovalOut(approval_id=row["approval_id"], document_id=row["document_id"],
                       document_revision=row["document_revision"], input_revision=row["input_revision"],
                       format=row["format"], validation_id=row["validation_id"], layout_check_id=row["layout_check_id"],
                       template_version=row["template_version"], render_options_hash=row["render_options_hash"],
                       asset_manifest_hash=row["asset_manifest_hash"], approved_at=row["approved_at"],
                       approved_by=row["approved_by"], status=row["status"], invalidated_at=row["invalidated_at"],
                       invalidated_reason=row["invalidated_reason"], renderer=_col(row, "renderer"), artifact_id=_col(row, "artifact_id"))


def active_for(conn: sqlite3.Connection, document_id: str, document_revision: int, input_revision: int,
               fmt: str | None = None) -> sqlite3.Row | None:
    """현재 문서·입력 버전의 active 승인. fmt를 주면 그 형식만(형식별 승인, BE-08)."""
    query = ("SELECT * FROM approvals WHERE document_id=? AND document_revision=? AND input_revision=? AND status='active' ")
    params: list = [document_id, document_revision, input_revision]
    if fmt is not None:
        query += "AND format=? "
        params.append(fmt)
    return conn.execute(query + "ORDER BY approved_at DESC, rowid DESC LIMIT 1", params).fetchone()


def find_matching_active(conn: sqlite3.Connection, document_id: str, document_revision: int, input_revision: int,
                         body: ApprovalCreate) -> sqlite3.Row | None:
    """형식뿐 아니라 요청한 validation_id·layout_check_id(따라서 artifact·식별값)까지 같은 active 승인만 재사용한다."""
    return conn.execute(
        "SELECT * FROM approvals WHERE document_id=? AND document_revision=? AND input_revision=? AND status='active' "
        "AND format=? AND validation_id=? AND layout_check_id=? ORDER BY approved_at DESC, rowid DESC LIMIT 1",
        (document_id, document_revision, input_revision, body.format, body.validation_id, body.layout_check_id)).fetchone()


def supersede_active(conn: sqlite3.Connection, document_id: str, document_revision: int, input_revision: int, fmt: str) -> int:
    """새 검사를 명시적으로 승인하면 같은 형식의 이전 active 승인은 superseded로 무효화한다(형식별 active 1건)."""
    cur = conn.execute("UPDATE approvals SET status='invalidated', invalidated_at=?, invalidated_reason='superseded' "
                       "WHERE document_id=? AND document_revision=? AND input_revision=? AND format=? AND status='active'",
                       (to_iso(now()), document_id, document_revision, input_revision, fmt))
    return cur.rowcount


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
    # 내용 blocker만. 배치(scope=layout) blocker는 해당 형식의 승인 조건 ⑥에서 판단한다(BE-08).
    open_blockers = [r["issue_id"] for r in conn.execute(
        "SELECT issue_id FROM issues WHERE document_id=? AND status='open' AND severity='blocker' AND scope<>'layout'", (document.document_id,))]
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
    # ⑥ 배치 검사 일치(실제 행). BE-08: 그 형식의 open 배치 blocker 없음 + 등록 사진 공개 허가 현재 값 통과까지.
    manifest = layout_checks.asset_manifest_hash(conn, document)
    lc_row, reason = layout_checks.matching_passed(conn, body.layout_check_id, document, session_row["input_revision"], body.format, manifest)
    if reason is not None:
        raise ApiError(422, "LAYOUT_NOT_READY", "요청 형식의 배치 검사가 현재 문서에서 완료되지 않았습니다.",
                       details={"layout_check_id": body.layout_check_id, "format": body.format, "reason": reason})
    layout_blockers = [r["issue_id"] for r in conn.execute(
        "SELECT issue_id FROM issues WHERE document_id=? AND status='open' AND scope='layout' AND severity='blocker' "
        "AND (layout_format=? OR layout_format IS NULL)", (document.document_id, body.format))]
    if layout_blockers:
        raise ApiError(422, "LAYOUT_NOT_READY", "이 형식의 배치 문제가 남아 있습니다. 배치 검사를 다시 실행해 주세요.",
                       details={"layout_check_id": body.layout_check_id, "format": body.format, "reason": "open_layout_blockers",
                                "issue_ids": layout_blockers})
    pub = publication.check_document(conn, document)
    if not pub.ok:
        raise ApiError(422, "LAYOUT_NOT_READY", "등록 사진의 외부 공개 허가가 확인되지 않아 승인할 수 없습니다.",
                       details={"layout_check_id": body.layout_check_id, "format": body.format,
                                "reason": "publication_policy", "blocked": pub.as_out()})
    # ⑦ 사용자 최종 승인
    if not body.confirmed:
        raise ApiError(422, "APPROVAL_NOT_CONFIRMED", "최종 승인(confirmed=true)이 필요합니다.")
    return v, manifest, lc_row, pub


def create(conn: sqlite3.Connection, session_row: sqlite3.Row, owner_id: str, document: Document,
           body: ApprovalCreate, manifest: str, lc_row: sqlite3.Row | None = None,
           pub: publication.PublicationResult | None = None) -> ApprovalOut:
    stamp = to_iso(now())
    approval_id = f"apr_{uuid.uuid4().hex[:16]}"
    renderer = _col(lc_row, "renderer") if lc_row is not None else None
    artifact_id = _col(lc_row, "artifact_id") if lc_row is not None else None
    conn.execute(
        "INSERT INTO approvals (approval_id, session_id, document_id, document_revision, input_revision, format, validation_id, "
        "layout_check_id, template_version, render_options_hash, asset_manifest_hash, approved_at, approved_by, status, created_at, "
        "renderer, artifact_id, publication_checked_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
        (approval_id, session_row["session_id"], document.document_id, document.document_revision, body.input_revision,
         body.format, body.validation_id, body.layout_check_id, layout_checks.TEMPLATE_VERSION,
         layout_checks.RENDER_OPTIONS_HASH, manifest, stamp, owner_id, stamp, renderer, artifact_id,
         pub.checked_at if pub is not None else None))
    return to_out(conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone())


def invalidate_one(conn: sqlite3.Connection, approval_id: str, reason: str) -> int:
    cur = conn.execute("UPDATE approvals SET status='invalidated', invalidated_at=?, invalidated_reason=? "
                       "WHERE approval_id=? AND status='active'", (to_iso(now()), reason, approval_id))
    return cur.rowcount


def invalidate_for_document(conn: sqlite3.Connection, document_id: str, reason: str) -> int:
    cur = conn.execute("UPDATE approvals SET status='invalidated', invalidated_at=?, invalidated_reason=? "
                       "WHERE document_id=? AND status='active'", (to_iso(now()), reason, document_id))
    return cur.rowcount


def invalidate_for_session(conn: sqlite3.Connection, session_id: str, reason: str) -> int:
    cur = conn.execute("UPDATE approvals SET status='invalidated', invalidated_at=?, invalidated_reason=? "
                       "WHERE session_id=? AND status='active'", (to_iso(now()), reason, session_id))
    return cur.rowcount
