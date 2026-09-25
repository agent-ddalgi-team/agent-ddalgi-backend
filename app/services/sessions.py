"""세션 생성·조회·만료·삭제.

만료(D-02): expires_at = min(마지막 활동 + idle, 생성 + max). 상태를 바꾸는 요청만 활동으로 친다.
GET 조회·작업 폴링은 활동으로 치지 않아 배경 폴링만으로 만료가 무한 연장되지 않는다.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.errors import ApiError
from app.models import Brief, SessionOut
from app.timeutil import from_iso, now, plus, to_iso


def _expires_at(settings: Settings, created: datetime, last_activity: datetime) -> datetime:
    return min(plus(last_activity, minutes=settings.session_idle_minutes),
               plus(created, hours=settings.session_max_hours))


def session_dir(settings: Settings, session_id: str) -> Path:
    return settings.private_runs_dir / session_id


def create(conn: sqlite3.Connection, settings: Settings, owner_id: str, brief: Brief) -> SessionOut:
    session_id = f"sess_{uuid.uuid4().hex[:16]}"
    created = now()
    conn.execute(
        "INSERT INTO sessions (session_id, owner_id, status, input_revision, brief_json, selected_source_ids, "
        "created_at, last_activity_at, expires_at) VALUES (?, ?, 'active', 1, ?, '[]', ?, ?, ?)",
        (session_id, owner_id, brief.model_dump_json(), to_iso(created), to_iso(created),
         to_iso(_expires_at(settings, created, created))),
    )
    return get(conn, owner_id, session_id)


def _row_to_out(row: sqlite3.Row) -> SessionOut:
    return SessionOut(
        session_id=row["session_id"],
        status=row["status"],
        input_revision=row["input_revision"],
        created_at=row["created_at"],
        last_activity_at=row["last_activity_at"],
        expires_at=row["expires_at"],
        brief=Brief.model_validate_json(row["brief_json"]),
        selected_source_ids=json.loads(row["selected_source_ids"]),
        document_summary=None,  # BE-05에서 채운다
    )


def load_active(conn: sqlite3.Connection, owner_id: str, session_id: str) -> sqlite3.Row:
    """소유자가 맞고 살아 있는 세션 행을 돌려준다. 남의 세션은 존재 여부를 숨기려고 404."""
    row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if row is None or row["owner_id"] != owner_id:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    if row["status"] == "closed":
        raise ApiError(410, "SESSION_EXPIRED", "종료된 세션입니다. 새 세션을 시작해 주세요.",
                       details={"status": "closed"})
    if row["status"] == "expired" or now() >= from_iso(row["expires_at"]):
        if row["status"] != "expired":
            conn.execute("UPDATE sessions SET status='expired' WHERE session_id=?", (session_id,))
        raise ApiError(410, "SESSION_EXPIRED", "세션이 만료되었습니다. 새 세션을 시작해 주세요.",
                       details={"status": "expired"})
    return row


def get(conn: sqlite3.Connection, owner_id: str, session_id: str) -> SessionOut:
    return _row_to_out(load_active(conn, owner_id, session_id))


def touch(conn: sqlite3.Connection, settings: Settings, row: sqlite3.Row) -> None:
    """상태를 바꾸는 요청에서만 부른다. 마지막 활동과 만료 시각을 갱신한다."""
    current = now()
    conn.execute(
        "UPDATE sessions SET last_activity_at=?, expires_at=? WHERE session_id=?",
        (to_iso(current), to_iso(_expires_at(settings, from_iso(row["created_at"]), current)), row["session_id"]),
    )


def bump_input_revision(conn: sqlite3.Connection, session_id: str, *, brief: Brief | None,
                        selected_source_ids: list[str] | None) -> int:
    row = conn.execute("SELECT input_revision, brief_json, selected_source_ids FROM sessions WHERE session_id=?",
                       (session_id,)).fetchone()
    new_revision = row["input_revision"] + 1
    conn.execute(
        "UPDATE sessions SET input_revision=?, brief_json=?, selected_source_ids=? WHERE session_id=?",
        (new_revision,
         brief.model_dump_json() if brief is not None else row["brief_json"],
         json.dumps(selected_source_ids) if selected_source_ids is not None else row["selected_source_ids"],
         session_id),
    )
    return new_revision


def close(conn: sqlite3.Connection, settings: Settings, owner_id: str, session_id: str) -> tuple[str, str]:
    """접근을 즉시 막고 임시 바이트를 지운다. 이미 닫힌 세션이면 같은 결과를 돌려준다(멱등).

    반환: (status, cleanup) — cleanup은 done 또는 pending(삭제 실패, 재시도 목록).
    """
    row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if row is None or row["owner_id"] != owner_id:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    if row["status"] == "closed":
        return "closed", row["cleanup_status"] or "done"

    closed_at = to_iso(now())
    conn.execute("UPDATE sessions SET status='closed', closed_at=? WHERE session_id=?", (closed_at, session_id))
    conn.execute("UPDATE sources SET deleted_at=? WHERE session_id=? AND deleted_at IS NULL", (closed_at, session_id))

    cleanup = "done"
    directory = session_dir(settings, session_id)
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
        if directory.exists():
            cleanup = "pending"
    conn.execute("UPDATE sessions SET cleanup_status=? WHERE session_id=?", (cleanup, session_id))
    return "closed", cleanup
