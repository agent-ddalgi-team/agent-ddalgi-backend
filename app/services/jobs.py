"""작업(Job) 레코드. BE-02는 만들고 조회만 한다. 실제 처리(읽기·AI)는 BE-03/04에서 붙는다."""
from __future__ import annotations

import json
import sqlite3
import uuid

from app.errors import ApiError
from app.models import JobOut
from app.timeutil import now, to_iso


def create(conn: sqlite3.Connection, session_id: str, kind: str, stage_message: str) -> JobOut:
    job_id = f"job_{uuid.uuid4().hex[:16]}"
    stamp = to_iso(now())
    progress = {"stage": "queued", "message": stage_message}
    conn.execute(
        "INSERT INTO jobs (job_id, session_id, kind, status, progress_json, created_at, updated_at) "
        "VALUES (?, ?, ?, 'queued', ?, ?, ?)",
        (job_id, session_id, kind, json.dumps(progress, ensure_ascii=False), stamp, stamp),
    )
    return get(conn, session_id, job_id)


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
