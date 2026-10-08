"""작업(Job) 레코드와 상태 전이. 실제 처리는 services/reading.py(BE-03), AI 작업은 BE-04에서 붙는다.

상태: queued → running → succeeded / failed / cancelled(세션 종료·만료, BE-09).
서버가 재시작되면 진행 중이던 작업은 이어갈 수 없으므로 시작 시 failed로 정리한다(fail_stale).
BE-09: 상태 갱신(set_progress/succeed/fail)은 아직 끝나지 않은 Job(ACTIVE)에만 적용된다. 세션 종료로 cancelled된 Job을
늦게 끝난 백그라운드 작업이 succeeded/failed로 되돌리지 못한다. 재시작 복구(exports.recover_after_restart)는 Export의 실제 결과에
연결 Job을 맞춰야 하므로(fail_stale이 failed로 바꾼 Job, 옛 succeeded 기록 모두 실제 사유로) allow=RECOVERABLE을 쓴다 — cancelled만 제외.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from app.db import Connection
from app.errors import ApiError
from app.models import JobOut
from app.timeutil import now, to_iso

# Explicit user retries only. Input/policy/confirmation failures require review.
DRAFT_RETRY_CODES = frozenset({"AGENT_OUTPUT_INVALID", "SERVICE_TEMPORARY_FAILURE", "AI_RATE_LIMIT", "INTERNAL_ERROR"})
ACTIVE = ("queued", "running", "waiting_user")
RECOVERABLE = ACTIVE + ("failed", "succeeded")   # 재시작 복구 전용: cancelled만 제외하고 Export의 실제 결과로 맞춘다
CANCELLED_ERROR = {"code": "SESSION_EXPIRED", "message": "세션이 종료되어 작업이 취소되었습니다.", "retryable": False,
                   "details": {}, "request_id": None}


def _marks(statuses: tuple[str, ...]) -> str:
    return ",".join("?" * len(statuses))


def create(conn: Connection, session_id: str, kind: str, stage_message: str,
           input_revision: int | None = None, target_key: str | None = None) -> JobOut:
    job_id = f"job_{uuid.uuid4().hex[:16]}"
    stamp = to_iso(now())
    progress = {"stage": "queued", "message": stage_message}
    conn.execute(
        "INSERT INTO jobs (job_id, session_id, kind, status, progress_json, input_revision, target_key, created_at, updated_at) "
        "VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?)",
        (job_id, session_id, kind, json.dumps(progress, ensure_ascii=False), input_revision, target_key, stamp, stamp),
    )
    return get(conn, session_id, job_id)


def find_active_by_key(conn: Connection, session_id: str, kind: str, target_key: str) -> JobOut | None:
    """같은 대상 키(예: 문서@문서버전@입력버전)로 아직 끝나지 않은 작업. 다른 버전의 Job과 섞이지 않는다."""
    row = conn.execute(
        "SELECT job_id FROM jobs WHERE session_id=? AND kind=? AND target_key=? "
        "AND status IN ('queued', 'running', 'waiting_user') ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (session_id, kind, target_key)).fetchone()
    return get(conn, session_id, row["job_id"]) if row else None


def find_active(conn: Connection, session_id: str, kind: str, input_revision: int) -> JobOut | None:
    """같은 세션·종류·입력 버전으로 아직 끝나지 않은 작업. 중복 실행 대신 이 작업을 돌려준다."""
    row = conn.execute(
        "SELECT job_id FROM jobs WHERE session_id=? AND kind=? AND input_revision=? "
        "AND status IN ('queued', 'running', 'waiting_user') ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (session_id, kind, input_revision)).fetchone()
    return get(conn, session_id, row["job_id"]) if row else None


def get(conn: Connection, session_id: str, job_id: str) -> JobOut:
    row = conn.execute("SELECT * FROM jobs WHERE job_id=? AND session_id=?", (job_id, session_id)).fetchone()
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    return JobOut(
        job_id=row["job_id"],
        kind=row["kind"],
        status=row["status"],
        progress=json.loads(row["progress_json"]),
        result_ref=json.loads(row["result_ref_json"]) if row["result_ref_json"] else None,
        error=json.loads(row["error_json"]) if row["error_json"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def record_trace(conn: Connection, job_id: str, stage: str, details: dict[str, Any]) -> int:
    """세션 Job의 수량/버전/상태만 보존한다. 원문·프롬프트·파일 경로·사유 문구는 전달하지 않는다."""
    row = conn.execute("SELECT progress_json, input_revision FROM jobs WHERE job_id=? AND status IN "
                       f"({_marks(ACTIVE)})", (job_id, *ACTIVE)).fetchone()
    if row is None:
        return 0
    progress = json.loads(row["progress_json"])
    trace = progress.setdefault("trace", {"version": 1, "input_revision": row["input_revision"], "stages": []})
    event = {"stage": stage, "recorded_at": to_iso(now()), **details}
    # 같은 단계의 최신 요약으로 치환해 파일 수에 따라 기록이 계속 늘지 않는다.
    remaining = [e for e in trace["stages"] if e["stage"] != stage]
    if len(remaining) > 15:
        trace["omitted_stage_count"] = trace.get("omitted_stage_count", 0) + len(remaining) - 15
    trace["stages"] = remaining[-15:] + [event]
    encoded = json.dumps(progress, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > 65536:
        # 관측 크기가 업무 자체를 실패시키지 않으며 잘린 사실을 조용히 확정하지 않는다.
        event = {"stage": stage, "recorded_at": to_iso(now()), "summary_omitted": "trace_size_limit"}
        trace["stages"] = [event]
        encoded = json.dumps(progress, ensure_ascii=False)
    return conn.execute(f"UPDATE jobs SET progress_json=? WHERE job_id=? AND status IN ({_marks(ACTIVE)})",
                        (encoded, job_id, *ACTIVE)).rowcount


def _progress(conn: Connection, job_id: str, stage: str | None, message: str | None,
              outcome: dict | None = None) -> str:
    row = conn.execute("SELECT progress_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    progress = json.loads(row["progress_json"]) if row else {}
    if stage is not None:
        progress.update(stage=stage, message=message)
    if outcome is not None and "trace" in progress:
        progress["trace"]["outcome"] = outcome
    return json.dumps(progress, ensure_ascii=False)


def set_progress(conn: Connection, job_id: str, stage: str, message: str | None,
                 status: str = "running") -> int:
    cur = conn.execute(
        f"UPDATE jobs SET status=?, progress_json=?, updated_at=? WHERE job_id=? AND status IN ({_marks(ACTIVE)})",
        (status, _progress(conn, job_id, stage, message), to_iso(now()), job_id, *ACTIVE),
    )
    return cur.rowcount


def succeed(conn: Connection, job_id: str, result_ref: dict[str, Any], *,
            allow: tuple[str, ...] = ACTIVE) -> int:
    cur = conn.execute(
        f"UPDATE jobs SET status='succeeded', progress_json=?, result_ref_json=?, error_json=NULL, updated_at=? "
        f"WHERE job_id=? AND status IN ({_marks(allow)})",
        (_progress(conn, job_id, "done", None, {"status": "succeeded"}), json.dumps(result_ref, ensure_ascii=False),
         to_iso(now()), job_id, *allow),
    )
    return cur.rowcount


def fail(conn: Connection, job_id: str, code: str, message: str, retryable: bool,
         details: dict[str, Any] | None = None, *, allow: tuple[str, ...] = ACTIVE) -> int:
    error = {"code": code, "message": message, "retryable": retryable, "details": details or {}, "request_id": None}
    cur = conn.execute(
        f"UPDATE jobs SET status='failed', progress_json=?, error_json=?, updated_at=? WHERE job_id=? AND status IN ({_marks(allow)})",
        (_progress(conn, job_id, None, None, {"status": "failed", "code": code}), json.dumps(error, ensure_ascii=False), to_iso(now()), job_id, *allow),
    )
    return cur.rowcount


def cancel_for_session(conn: Connection, session_id: str) -> int:
    """세션 종료·만료: 끝나지 않은 Job을 cancelled로 확정한다(고정 문구). 이후 늦은 결과는 ACTIVE 가드에 막힌다."""
    cur = conn.execute(
        f"UPDATE jobs SET status='cancelled', error_json=?, updated_at=? WHERE session_id=? AND status IN ({_marks(ACTIVE)})",
        (json.dumps(CANCELLED_ERROR, ensure_ascii=False), to_iso(now()), session_id, *ACTIVE),
    )
    return cur.rowcount


def purge_errors_for_session(conn: Connection, session_id: str) -> int:
    """세션 내용 제거: error_json의 message·details를 비운다(code·retryable은 유지). 예외 문자열에 원문·파일명이 섞일 수 있다."""
    rows = conn.execute("SELECT job_id, error_json FROM jobs WHERE session_id=? AND error_json IS NOT NULL", (session_id,)).fetchall()
    for r in rows:
        try:
            error = json.loads(r["error_json"])
        except ValueError:
            error = {}
        stripped = {"code": error.get("code", "INTERNAL_ERROR"), "message": "", "retryable": bool(error.get("retryable", False)),
                    "details": {}, "request_id": None}
        conn.execute("UPDATE jobs SET error_json=? WHERE job_id=?", (json.dumps(stripped, ensure_ascii=False), r["job_id"]))
    for r in conn.execute("SELECT job_id, progress_json FROM jobs WHERE session_id=?", (session_id,)).fetchall():
        progress = json.loads(r["progress_json"])
        progress.pop("trace", None)
        progress["message"] = None
        conn.execute("UPDATE jobs SET progress_json=? WHERE job_id=?", (json.dumps(progress), r["job_id"]))
    return len(rows)


def fail_stale(conn: Connection) -> int:
    """서버 시작 시: 이전 프로세스가 남긴 queued/running 작업을 failed로 정리한다. 정리한 개수를 돌려준다."""
    rows = conn.execute("SELECT job_id FROM jobs WHERE status IN ('queued', 'running')").fetchall()
    for r in rows:
        fail(conn, r["job_id"], "SERVICE_TEMPORARY_FAILURE",
             "서버가 재시작되어 작업이 중단되었습니다. 다시 요청해 주세요.", True)
    # 읽는 중이던 자료도 다시 올려야 한다.
    conn.execute("UPDATE sources SET parse_status='failed', warnings_json=? "
                 "WHERE parse_status IN ('queued', 'reading') AND deleted_at IS NULL",
                 (json.dumps([{"locator": None, "code": "INTERRUPTED",
                               "message": "서버가 재시작되어 읽기가 중단되었습니다.",
                               "action": "파일을 다시 올려 주세요."}], ensure_ascii=False),))
    return len(rows)
