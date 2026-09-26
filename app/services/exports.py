"""출력(Export) — BE-08. 승인이 참조한 불변 artifact를 그대로 발행한다. 렌더·AI 호출이 없다.

- 생성 조건: Approval active, 문서가 현재 최신 revision·input_revision(아니면 409), format이 Approval과 같음(DOCX는 이 범위에서 불가),
  공개 허가 현재 통과, artifact 무결성.
- 재사용 키 = approval_id|format|template_version|render_options_hash|asset_manifest_hash. 같은 키의 queued/generating/ready(미만료)를
  재사용한다(부분 UNIQUE + BEGIN IMMEDIATE). ready라도 만료면 failed(expired)로 확정하고 ID·만료 시각을 보존한 뒤 새 행을 만든다.
- 실패 재시도: 같은 Idempotency-Key 재전송은 최초 응답(단, 접근·세션·현재 승인·허가 검사가 먼저). 새 키면 같은 키의 retryable failed 행을
  queued로 되돌리고 attempt+1. ARTIFACT_INVALID(누락·변조)는 재시도 대상이 아니다 — 승인을 무효화하고 재검사·재승인을 요구한다.
- 발행 직전·다운로드·재시작 복구에서 validity()로 모든 유효 조건을 다시 확인한다. 늦은 결과를 ready로 공개하지 않는다.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.db import connect
from app.errors import ApiError
from app.models import ExportOut, JobError
from app.services import approvals as approvals_service
from app.services import artifacts, jobs, layout_checks, publication
from app.timeutil import from_iso, now, plus, to_iso

logger = logging.getLogger(__name__)
ACTIVE = ("queued", "generating", "ready")
DOCX_WARNING = "DOCX는 이 범위에서 승인·출력이 열리지 않습니다(overflow not_checked)."


@dataclass(frozen=True)
class Verdict:
    ok: bool
    status: int = 200
    code: str | None = None
    message: str | None = None
    retryable: bool = False
    details: dict[str, Any] | None = None

    def raise_(self) -> None:
        if not self.ok:
            raise ApiError(self.status, self.code or "INTERNAL_ERROR", self.message or "", retryable=self.retryable, details=self.details)


OK = Verdict(True)


def reuse_key(approval: sqlite3.Row) -> str:
    return "|".join([approval["approval_id"], approval["format"], approval["template_version"], approval["render_options_hash"],
                     approval["asset_manifest_hash"]])


def to_out(row: sqlite3.Row) -> ExportOut:
    error = json.loads(row["error_json"]) if row["error_json"] else None
    warnings = [DOCX_WARNING] if row["format"] == "docx" else []
    return ExportOut(export_id=row["export_id"], approval_id=row["approval_id"], format=row["format"], status=row["status"],
                     artifact_id=row["artifact_id"] if row["status"] == "ready" else None, expires_at=row["expires_at"],
                     error=JobError.model_validate(error) if error else None, attempt=row["attempt"], warnings=warnings,
                     created_at=row["created_at"], updated_at=row["updated_at"])


def get(conn: sqlite3.Connection, session_id: str, export_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM exports WHERE export_id=? AND session_id=?", (export_id, session_id)).fetchone()
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    return row


# ---------------- 유효성(승인·문서·세션·허가·artifact) ----------------

def identity_mismatch(conn: sqlite3.Connection, approval: sqlite3.Row, manifest_now: str) -> str | None:
    """출력 식별값 일관성: 현재 서버 TEMPLATE_VERSION·RENDER_OPTIONS_HASH·문서 asset_manifest ↔ Approval ↔ LayoutCheck ↔ artifact.
    브라우저(renderer) 버전은 식별값이 아니므로 보지 않는다 — 온전한 artifact는 브라우저가 바뀌어도 그대로 쓴다. 불일치면 사유를 돌려준다."""
    if approval["template_version"] != layout_checks.TEMPLATE_VERSION:
        return "template_version_changed"
    if approval["render_options_hash"] != layout_checks.RENDER_OPTIONS_HASH:
        return "render_options_changed"
    if approval["asset_manifest_hash"] != manifest_now:
        return "asset_manifest_changed"
    lc = conn.execute("SELECT * FROM layout_checks WHERE layout_check_id=?", (approval["layout_check_id"],)).fetchone()
    if lc is None:
        return "layout_check_not_found"
    if (lc["format"] != approval["format"] or lc["template_version"] != approval["template_version"]
            or lc["render_options_hash"] != approval["render_options_hash"] or lc["asset_manifest_hash"] != approval["asset_manifest_hash"]
            or lc["document_id"] != approval["document_id"] or lc["document_revision"] != approval["document_revision"]):
        return "layout_check_mismatch"
    art = artifacts.get(conn, approval["artifact_id"]) if approval["artifact_id"] else None
    if art is None:
        return None   # artifact 행 누락은 식별값 불일치가 아니라 산출물 누락 → 뒤의 무결성 검사가 ARTIFACT_INVALID(not_found)로 처리(승인 무효화)
    if not artifacts.identity_matches(art, approval["template_version"], approval["render_options_hash"], approval["asset_manifest_hash"], approval["format"]):
        return "artifact_identity_mismatch"
    if art["layout_check_id"] != approval["layout_check_id"] or (lc["artifact_id"] or "") != art["artifact_id"] \
            or art["document_id"] != approval["document_id"] or art["document_revision"] != approval["document_revision"]:
        return "artifact_link_mismatch"
    return None


def approval_validity(conn: sqlite3.Connection, settings: Settings, session_row: sqlite3.Row, approval: sqlite3.Row | None,
                      fmt: str | None = None) -> Verdict:
    """승인본으로 출력할 수 있는가. Export 생성·발행·다운로드·멱등 성공 응답·재시작 복구가 모두 이 함수를 쓴다(늦은 결과·옛 승인 차단).
    검사 순서: 존재·소유 → active → 형식 → 문서 현재 버전·입력 → 출력 식별값 일관성 → 공개 허가 → artifact 무결성."""
    if approval is None or approval["session_id"] != session_row["session_id"]:
        return Verdict(False, 404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    if approval["status"] != "active":
        return Verdict(False, 409, "APPROVAL_NOT_ACTIVE", "승인이 무효화되었습니다. 배치 검사와 승인을 다시 진행해 주세요.",
                       details={"approval_id": approval["approval_id"], "invalidated_reason": approval["invalidated_reason"]})
    if fmt is not None and approval["format"] != fmt:
        return Verdict(False, 422, "EXPORT_NOT_ALLOWED", "승인된 형식과 다른 형식은 출력할 수 없습니다.",
                       details={"approved_format": approval["format"], "requested_format": fmt})
    if approval["format"] == "docx":
        return Verdict(False, 422, "EXPORT_NOT_ALLOWED", DOCX_WARNING, details={"format": "docx", "reason": "overflow_not_checked"})
    head = conn.execute("SELECT current_revision FROM documents WHERE document_id=? AND session_id=?",
                        (approval["document_id"], session_row["session_id"])).fetchone()
    if head is None:
        return Verdict(False, 404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    if head["current_revision"] != approval["document_revision"]:
        return Verdict(False, 409, "DOCUMENT_REVISION_CONFLICT", "승인 뒤 문서가 바뀌었습니다. 옛 승인본은 내려주지 않습니다. 다시 검사·승인해 주세요.",
                       details={"approved_revision": approval["document_revision"], "current_revision": head["current_revision"]})
    if session_row["input_revision"] != approval["input_revision"]:
        return Verdict(False, 409, "INPUT_REVISION_CONFLICT", "승인 뒤 입력이 바뀌었습니다. 사전 점검부터 다시 진행해 주세요.",
                       details={"approved_input_revision": approval["input_revision"], "current_input_revision": session_row["input_revision"]})
    from app.services.documents import get_current

    manifest_now = layout_checks.asset_manifest_hash(conn, get_current(conn, session_row["session_id"], approval["document_id"]))
    mismatch = identity_mismatch(conn, approval, manifest_now)
    if mismatch is not None:
        return Verdict(False, 422, "RENDER_IDENTITY_MISMATCH", "승인 당시의 출력 식별값(템플릿·렌더 옵션·이미지 목록·산출물 연결)이 현재와 다릅니다. "
                       "배치 검사와 승인을 다시 진행해 주세요.", details={"reason": mismatch, "recheck_required": True})
    pub = publication.check_revision(conn, approval["document_id"], approval["document_revision"])
    if not pub.ok:
        return Verdict(False, 422, publication.ISSUE_CODE, "등록 사진의 외부 공개 허가가 확인되지 않아 출력할 수 없습니다.",
                       details={"blocked": pub.as_out()})
    integrity = artifacts.verify(conn, settings, approval["artifact_id"])
    if not integrity.ok:
        return Verdict(False, 422, "ARTIFACT_INVALID", "승인 당시 검사한 산출물이 없거나 변조되었습니다. 배치 검사와 승인을 다시 진행해 주세요.",
                       details={"artifact_id": approval["artifact_id"], "reason": integrity.reason, "recheck_required": True})
    return OK


def session_valid(conn: sqlite3.Connection, session_id: str) -> tuple[sqlite3.Row | None, Verdict]:
    row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if row is None:
        return None, Verdict(False, 404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    if row["status"] != "active" or now() >= from_iso(row["expires_at"]):
        return row, Verdict(False, 410, "SESSION_EXPIRED", "세션이 종료되었거나 만료되었습니다.", details={"status": row["status"]})
    return row, OK


def export_expired(row: sqlite3.Row) -> bool:
    return now() >= from_iso(row["expires_at"])


def finalize_expired(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    """ready/queued/generating인데 만료된 행을 failed(expired)로 확정한다. ID·expires_at은 보존, 활성 UNIQUE 대상에서 빠진다."""
    if row["status"] not in ACTIVE:
        return
    error = {"code": "ARTIFACT_EXPIRED", "message": "출력 결과가 만료되었습니다. 다시 요청해 주세요.", "retryable": False,
             "details": {"expires_at": row["expires_at"]}, "request_id": None}
    conn.execute("UPDATE exports SET status='failed', finalized_reason='expired', error_json=?, updated_at=? WHERE export_id=?",
                 (json.dumps(error, ensure_ascii=False), to_iso(now()), row["export_id"]))


def commit_and_raise(conn: sqlite3.Connection, verdict: Verdict) -> None:
    """상태 변경(승인 무효화·Export 확정 실패)을 먼저 커밋한 뒤 오류를 낸다. connect()의 예외 롤백에 지워지지 않게."""
    conn.commit()
    verdict.raise_()


def _invalidate_for_artifact(conn: sqlite3.Connection, approval_id: str, verdict: Verdict) -> None:
    """ARTIFACT_INVALID(파일 누락·변조·행 누락): 관련 승인 무효화 + 그 승인의 활성 Export 전부 확정 실패. 호출자가 커밋한 뒤 오류를 낸다."""
    approvals_service.invalidate_one(conn, approval_id, "artifact_invalid")
    error = {"code": "ARTIFACT_INVALID", "message": verdict.message, "retryable": False, "details": verdict.details or {}, "request_id": None}
    conn.execute("UPDATE exports SET status='failed', finalized_reason='artifact_invalid', error_json=?, updated_at=? "
                 "WHERE approval_id=? AND status IN ('queued', 'generating', 'ready')",
                 (json.dumps(error, ensure_ascii=False), to_iso(now()), approval_id))


def reject_invalid_artifact(conn: sqlite3.Connection, approval: sqlite3.Row | None, verdict: Verdict) -> None:
    """요청 시점에 ARTIFACT_INVALID면 관련 승인을 무효화하고 활성 Export를 확정 실패시킨다(재검사·재승인 필요)."""
    if approval is None or verdict.code != "ARTIFACT_INVALID":
        return
    _invalidate_for_artifact(conn, approval["approval_id"], verdict)


def _finalized_reason(verdict: Verdict) -> str:
    return {"ARTIFACT_INVALID": "artifact_invalid", "RENDER_IDENTITY_MISMATCH": "identity_mismatch",
            "IMAGE_PUBLICATION_UNCONFIRMED": "publication_blocked"}.get(verdict.code or "", "approval_invalid")


def fail_with(conn: sqlite3.Connection, row: sqlite3.Row, verdict: Verdict, reason: str) -> None:
    error = {"code": verdict.code, "message": verdict.message, "retryable": verdict.retryable, "details": verdict.details or {}, "request_id": None}
    conn.execute("UPDATE exports SET status='failed', finalized_reason=?, error_json=?, updated_at=? WHERE export_id=?",
                 (reason, json.dumps(error, ensure_ascii=False), to_iso(now()), row["export_id"]))
    if verdict.code == "ARTIFACT_INVALID":
        _invalidate_for_artifact(conn, row["approval_id"], verdict)   # 승인 무효화 + 같은 승인의 다른 활성 Export도 확정 실패(같은 트랜잭션)


# ---------------- 생성·재사용·재시도 ----------------

def _expires_at(settings: Settings, session_row: sqlite3.Row) -> str:
    return to_iso(min(from_iso(session_row["expires_at"]), plus(now(), minutes=settings.export_ttl_minutes)))


def create_or_reuse(conn: sqlite3.Connection, settings: Settings, session_row: sqlite3.Row, approval: sqlite3.Row
                    ) -> tuple[sqlite3.Row, bool]:
    """(Export 행, 새 Job 필요 여부). 호출자는 BEGIN IMMEDIATE 트랜잭션 안에서 부른다(중복 생성 방지)."""
    key = reuse_key(approval)
    stamp = to_iso(now())
    active = conn.execute("SELECT * FROM exports WHERE reuse_key=? AND status IN ('queued', 'generating', 'ready') "
                          "ORDER BY created_at DESC, rowid DESC LIMIT 1", (key,)).fetchone()
    if active is not None:
        if export_expired(active):
            finalize_expired(conn, active)          # 만료 행은 되살리지 않는다. 아래에서 새 행을 만든다
        else:
            return active, False
    retryable_failed = conn.execute(
        "SELECT * FROM exports WHERE reuse_key=? AND status='failed' AND finalized_reason IS NULL "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1", (key,)).fetchone()
    if retryable_failed is not None:
        error = json.loads(retryable_failed["error_json"] or "{}")
        if error.get("retryable"):
            conn.execute("UPDATE exports SET status='queued', attempt=attempt+1, error_json=NULL, job_id=NULL, expires_at=?, updated_at=? "
                         "WHERE export_id=?", (_expires_at(settings, session_row), stamp, retryable_failed["export_id"]))
            return conn.execute("SELECT * FROM exports WHERE export_id=?", (retryable_failed["export_id"],)).fetchone(), True
    export_id = f"exp_{uuid.uuid4().hex[:16]}"
    conn.execute(
        "INSERT INTO exports (export_id, session_id, approval_id, document_id, document_revision, input_revision, format, status, "
        "artifact_id, reuse_key, attempt, job_id, expires_at, error_json, renderer, publication_checked_at, finalized_reason, "
        "created_at, updated_at, published_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, 1, NULL, ?, NULL, ?, NULL, NULL, ?, ?, NULL)",
        (export_id, session_row["session_id"], approval["approval_id"], approval["document_id"], approval["document_revision"],
         approval["input_revision"], approval["format"], approval["artifact_id"], key, _expires_at(settings, session_row),
         approval["renderer"], stamp, stamp))
    return conn.execute("SELECT * FROM exports WHERE export_id=?", (export_id,)).fetchone(), True


def attach_job(conn: sqlite3.Connection, export_id: str, job_id: str) -> None:
    conn.execute("UPDATE exports SET job_id=?, updated_at=? WHERE export_id=?", (job_id, to_iso(now()), export_id))


# ---------------- 발행(Job) ----------------

def publish(conn: sqlite3.Connection, settings: Settings, row: sqlite3.Row) -> Verdict:
    """발행 직전 가드 전부 재확인 후 ready. 실패면 실제 사유·재시도 가능 여부로 failed."""
    session_row, verdict = session_valid(conn, row["session_id"])
    if not verdict.ok:
        fail_with(conn, row, verdict, "session_invalid")
        return verdict
    if export_expired(row):
        finalize_expired(conn, row)
        return Verdict(False, 410, "ARTIFACT_EXPIRED", "출력 결과가 만료되었습니다.", details={"expires_at": row["expires_at"]})
    approval = conn.execute("SELECT * FROM approvals WHERE approval_id=?", (row["approval_id"],)).fetchone()
    verdict = approval_validity(conn, settings, session_row, approval, row["format"])
    if not verdict.ok:
        fail_with(conn, row, verdict, _finalized_reason(verdict))
        return verdict
    pub = publication.check_revision(conn, row["document_id"], row["document_revision"])
    stamp = to_iso(now())
    conn.execute("UPDATE exports SET status='ready', artifact_id=?, published_at=?, publication_checked_at=?, error_json=NULL, updated_at=? "
                 "WHERE export_id=?", (approval["artifact_id"], stamp, pub.checked_at, stamp, row["export_id"]))
    return OK


def run_export_job(settings: Settings, session_id: str, job_id: str, export_id: str) -> None:
    try:
        with connect(settings.db_path, immediate=True) as conn:
            row = conn.execute("SELECT * FROM exports WHERE export_id=? AND session_id=?", (export_id, session_id)).fetchone()
            if row is None:
                jobs.fail(conn, job_id, "RESOURCE_NOT_FOUND", "출력 요청을 찾을 수 없습니다.", False)
                return
            if row["status"] == "ready":
                jobs.succeed(conn, job_id, {"export_id": export_id, "artifact_id": row["artifact_id"], "format": row["format"]})
                return
            jobs.set_progress(conn, job_id, "publishing", "승인 산출물을 확인하는 중")
            conn.execute("UPDATE exports SET status='generating', updated_at=? WHERE export_id=?", (to_iso(now()), export_id))
            verdict = publish(conn, settings, conn.execute("SELECT * FROM exports WHERE export_id=?", (export_id,)).fetchone())
            if verdict.ok:
                fresh = conn.execute("SELECT artifact_id FROM exports WHERE export_id=?", (export_id,)).fetchone()
                jobs.succeed(conn, job_id, {"export_id": export_id, "artifact_id": fresh["artifact_id"], "format": row["format"]})
            else:
                jobs.fail(conn, job_id, verdict.code or "EXPORT_FAILED", verdict.message or "출력에 실패했습니다.", verdict.retryable, verdict.details)
    except Exception as exc:  # noqa: BLE001
        logger.exception("export job failed")
        with connect(settings.db_path) as conn:
            jobs.fail(conn, job_id, "EXPORT_FAILED", "출력 처리 중 실패했습니다. 같은 승인본으로 재시도할 수 있습니다.", True,
                      {"error": type(exc).__name__})
            # 실패 시점의 트랜잭션은 롤백됐을 수 있으므로 queued/generating 어느 쪽이든 failed로 확정한다(재시도 가능)
            conn.execute("UPDATE exports SET status='failed', error_json=?, updated_at=? WHERE export_id=? AND status IN ('queued', 'generating')",
                         (json.dumps({"code": "EXPORT_FAILED", "message": "출력 처리 중 실패했습니다. 같은 승인본으로 재시도할 수 있습니다.",
                                      "retryable": True, "details": {}, "request_id": None}, ensure_ascii=False), to_iso(now()), export_id))


# ---------------- 다운로드 ----------------

def download_check(conn: sqlite3.Connection, settings: Settings, session_row: sqlite3.Row, row: sqlite3.Row) -> sqlite3.Row:
    """소유·세션은 라우터에서 끝났다. Export ready·미만료·승인 active·현재 버전·공개 허가·artifact 무결성을 매번 확인하고 artifact 행을 돌려준다."""
    if row["status"] != "ready":
        if row["status"] == "failed":
            error = json.loads(row["error_json"] or "{}")
            if error.get("code") == "ARTIFACT_EXPIRED" or row["finalized_reason"] == "expired":
                raise ApiError(410, "ARTIFACT_EXPIRED", "출력 결과가 만료되었습니다. 다시 요청해 주세요.", details={"expires_at": row["expires_at"]})
            raise ApiError(409, "EXPORT_NOT_READY", "출력이 실패한 상태입니다.", details={"status": row["status"], "error": error})
        raise ApiError(409, "EXPORT_NOT_READY", "출력이 아직 준비되지 않았습니다.", retryable=True, details={"status": row["status"]})
    if export_expired(row):
        finalize_expired(conn, row)
        commit_and_raise(conn, Verdict(False, 410, "ARTIFACT_EXPIRED", "출력 결과가 만료되었습니다. 다시 요청해 주세요.",
                                       details={"expires_at": row["expires_at"]}))
    approval = conn.execute("SELECT * FROM approvals WHERE approval_id=?", (row["approval_id"],)).fetchone()
    verdict = approval_validity(conn, settings, session_row, approval, row["format"])
    if not verdict.ok:
        fail_with(conn, row, verdict, _finalized_reason(verdict))
        commit_and_raise(conn, verdict)
    artifact = artifacts.get(conn, row["artifact_id"])
    if artifact is None or artifact["artifact_id"] != approval["artifact_id"]:
        v = Verdict(False, 422, "ARTIFACT_INVALID", "출력 파일이 승인 산출물과 연결되지 않습니다.", details={"recheck_required": True})
        fail_with(conn, row, v, "artifact_invalid")
        commit_and_raise(conn, v)
    return artifact


# ---------------- 재시작 복구 ----------------

def recover_after_restart(conn: sqlite3.Connection, settings: Settings) -> dict[str, int]:
    """queued/generating으로 남은 Export: 유효 조건 전부 재확인 → ready(연결 Job succeeded·오류 제거) 또는 실제 사유로 failed."""
    counts = {"ready": 0, "failed": 0}
    for row in conn.execute("SELECT * FROM exports WHERE status IN ('queued', 'generating')").fetchall():
        verdict = publish(conn, settings, row)
        fresh = conn.execute("SELECT * FROM exports WHERE export_id=?", (row["export_id"],)).fetchone()
        if verdict.ok:
            counts["ready"] += 1
            if row["job_id"]:
                conn.execute("UPDATE jobs SET status='succeeded', progress_json=?, result_ref_json=?, error_json=NULL, updated_at=? WHERE job_id=?",
                             (json.dumps({"stage": "done", "message": None}),
                              json.dumps({"export_id": row["export_id"], "artifact_id": fresh["artifact_id"], "format": row["format"]}),
                              to_iso(now()), row["job_id"]))
        else:
            counts["failed"] += 1
            if row["job_id"]:
                jobs.fail(conn, row["job_id"], verdict.code or "EXPORT_FAILED", verdict.message or "", verdict.retryable, verdict.details)
    return counts


def finalize_for_session(conn: sqlite3.Connection, session_id: str, reason: str) -> int:
    """세션 종료·만료 시 활성 Export를 failed로 확정한다(접근 차단은 load_active가 즉시 한다; 바이트 정리는 BE-09)."""
    error = {"code": "SESSION_EXPIRED", "message": "세션이 종료되어 출력 결과를 더 이상 제공하지 않습니다.", "retryable": False,
             "details": {}, "request_id": None}
    cur = conn.execute("UPDATE exports SET status='failed', finalized_reason=?, error_json=?, updated_at=? "
                       "WHERE session_id=? AND status IN ('queued', 'generating', 'ready')",
                       (reason, json.dumps(error, ensure_ascii=False), to_iso(now()), session_id))
    return cur.rowcount
