"""작업(Job) 레코드와 상태 전이. 실제 처리는 services/reading.py(BE-03), AI 작업은 BE-04에서 붙는다.

상태: queued → running → succeeded / failed. (waiting_user·cancelled는 BE-04에서.)
서버가 재시작되면 진행 중이던 작업은 이어갈 수 없으므로 시작 시 failed로 정리한다(fail_stale).
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from app.errors import ApiError
from app.models import JobOut
from app.timeutil import now, to_iso


def create(conn: sqlite3.Connection, session_id: str, kind: str, stage_message: str,
           input_revision: int | None = None) -> JobOut:
    job_id = f"job_{uuid.uuid4().hex[:16]}"
    stamp = to_iso(now())
    progress = {"stage": "queued", "message": stage_message}
    conn.execute(
        "INSERT INTO jobs (job_id, session_id, kind, status, progress_json, input_revision, created_at, updated_at) "
        "VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)",
        (job_id, session_id, kind, json.dumps(progress, ensure_ascii=False), input_revision, stamp, stamp),
    )
    return get(conn, session_id, job_id)


def find_active(conn: sqlite3.Connection, session_id: str, kind: str, input_revision: int) -> JobOut | None:
    """같은 세션·종류·입력 버전으로 아직 끝나지 않은 작업. 중복 실행 대신 이 작업을 돌려준다."""
    row = conn.execute(
        "SELECT job_id FROM jobs WHERE session_id=? AND kind=? AND input_revision=? "
        "AND status IN ('queued', 'running', 'waiting_user') ORDER BY created_at DESC LIMIT 1",
        (session_id, kind, input_revision)).fetchone()
    return get(conn, session_id, row["job_id"]) if row else None


def get(conn: sqlite3.Connection, session_id: str, job_id: str) -> JobOut:
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


def set_progress(conn: sqlite3.Connection, job_id: str, stage: str, message: str | None,
                 status: str = "running") -> None:
    conn.execute(
        "UPDATE jobs SET status=?, progress_json=?, updated_at=? WHERE job_id=?",
        (status, json.dumps({"stage": stage, "message": message}, ensure_ascii=False), to_iso(now()), job_id),
    )


def succeed(conn: sqlite3.Connection, job_id: str, result_ref: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE jobs SET status='succeeded', progress_json=?, result_ref_json=?, updated_at=? WHERE job_id=?",
        (json.dumps({"stage": "done", "message": None}), json.dumps(result_ref, ensure_ascii=False),
         to_iso(now()), job_id),
    )


def fail(conn: sqlite3.Connection, job_id: str, code: str, message: str, retryable: bool,
         details: dict[str, Any] | None = None) -> None:
    error = {"code": code, "message": message, "retryable": retryable, "details": details or {}, "request_id": None}
    conn.execute(
        "UPDATE jobs SET status='failed', error_json=?, updated_at=? WHERE job_id=?",
        (json.dumps(error, ensure_ascii=False), to_iso(now()), job_id),
    )


def fail_stale(conn: sqlite3.Connection) -> int:
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
