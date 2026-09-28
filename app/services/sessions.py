"""세션 생성·조회·만료·삭제.

만료(D-02): expires_at = min(마지막 활동 + idle, 생성 + max). 상태를 바꾸는 요청만 활동으로 친다.
GET 조회·작업 폴링은 활동으로 치지 않아 배경 폴링만으로 만료가 무한 연장되지 않는다.
BE-09: 요청 경로에서 만료·종료를 발견하면(raise_gone) 요청 트랜잭션을 롤백해 끝내고 정리 전용 트랜잭션에서 만료 확정·내용 제거·
큐 등록을 커밋한 뒤 410을 낸다(잠금 중첩·관련 없는 변경 커밋 없음). 바이트 삭제는 services/cleanup.py 큐가 한다.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from app.db import Connection, Row
from app.config import Settings
from app.errors import ApiError
from app.models import Brief, SessionOut
from app.services import db_history
from app.timeutil import from_iso, now, plus, to_iso


def _expires_at(settings: Settings, created: datetime, last_activity: datetime) -> datetime:
    return min(plus(last_activity, minutes=settings.session_idle_minutes),
               plus(created, hours=settings.session_max_hours))


def session_dir(settings: Settings, session_id: str) -> Path:
    return settings.private_runs_dir / session_id


def demo_allowed(settings: Settings) -> bool:
    return bool(settings.demo_mode)


def usable(settings: Settings, row: Row | None) -> str | None:
    """Pure policy check shared by requests and final Job writes; expiry wins over demo mode."""
    if row is None:
        return "expired"
    if row["status"] != "active":
        return "closed" if row["status"] == "closed" else "expired"
    if now() >= from_iso(row["expires_at"]):
        return "expired"
    if row["demo"] and not demo_allowed(settings):
        return "demo_disabled"
    return None


def demo_disabled_error() -> ApiError:
    return ApiError(403, "DEMO_MODE_DISABLED", "현재 서버에서 시연 모드가 꺼져 있습니다.")


def create(conn: Connection, settings: Settings, owner_id: str, brief: Brief, *, demo: bool = False) -> SessionOut:
    if demo and not demo_allowed(settings):
        raise demo_disabled_error()
    session_id = f"sess_{uuid.uuid4().hex[:16]}"
    created = now()
    conn.execute(
        "INSERT INTO sessions (session_id, owner_id, status, input_revision, brief_json, selected_source_ids, "
        "created_at, last_activity_at, expires_at, demo) VALUES (?, ?, 'active', 1, ?, '[]', ?, ?, ?, ?)",
        (session_id, owner_id, brief.model_dump_json(), to_iso(created), to_iso(created),
         to_iso(_expires_at(settings, created, created)), int(demo)),
    )
    db_history.input_revision(conn, session_id)
    return get(conn, owner_id, session_id, settings)


def _row_to_out(row: Row) -> SessionOut:
    return SessionOut(
        demo=bool(row["demo"]),
        session_id=row["session_id"],
        status=row["status"],
        input_revision=row["input_revision"],
        created_at=row["created_at"],
        last_activity_at=row["last_activity_at"],
        expires_at=row["expires_at"],
        brief=Brief.model_validate_json(row["brief_json"]),
        selected_source_ids=json.loads(row["selected_source_ids"]),
        document_summary=None,  # get()에서 채운다
    )


def _gone_error(status: str, cleanup: str) -> ApiError:
    message = "종료된 세션입니다. 새 세션을 시작해 주세요." if status == "closed" else "세션이 만료되었습니다. 새 세션을 시작해 주세요."
    return ApiError(410, "SESSION_EXPIRED", message, details={"status": status, "cleanup": cleanup})


def raise_gone(conn: Connection, settings: Settings, owner_id: str, session_id: str) -> Row:
    """종료·만료로 보이는 세션(BE-09). 요청 트랜잭션을 롤백해 끝내고(잠금 해제, 관련 없는 변경은 커밋되지 않음), 정리 전용
    BEGIN IMMEDIATE 트랜잭션에서 현재 상태를 다시 확인해 만료 확정·내용 제거·큐 등록(cleanup.finalize)·정리 상태 판정을 커밋한 뒤
    410을 낸다. 그 사이 연장돼 살아 있으면(경쟁, 드묾) 이 트랜잭션을 요청 트랜잭션으로 이어 쓰고 행을 돌려준다.

    호출 조건: 요청이 아직 아무것도 쓰지 않았을 때(load_active는 모든 세션 경로의 첫 DB 작업이다)."""
    from app.services import cleanup  # 순환 import 방지

    conn.rollback()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None or row["owner_id"] != owner_id:
            conn.commit()
            raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
        if row["status"] == "active" and now() < from_iso(row["expires_at"]):
            if usable(settings, row) == "demo_disabled":
                raise demo_disabled_error()
            return row
        reason = cleanup.REASON_CLOSED if row["status"] == "closed" else cleanup.REASON_EXPIRED
        result = cleanup.finalize(conn, settings, session_id, reason)
        state = cleanup.verify_state(conn, settings, session_id)
        conn.commit()
    except ApiError:
        raise
    except Exception:
        conn.rollback()
        raise
    raise _gone_error(result["status"], state)


def load_active(conn: Connection, owner_id: str, session_id: str, settings: Settings) -> Row:
    """소유자가 맞고 살아 있는 세션 행을 돌려준다. 남의 세션은 존재 여부를 숨기려고 404. 종료·만료면 raise_gone(410)."""
    row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if row is None or row["owner_id"] != owner_id:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    reason = usable(settings, row)
    if reason in {"closed", "expired"}:
        return raise_gone(conn, settings, owner_id, session_id)
    if reason == "demo_disabled":
        raise demo_disabled_error()
    return row


def get(conn: Connection, owner_id: str, session_id: str, settings: Settings) -> SessionOut:
    from app.services import documents  # 순환 import 방지

    out = _row_to_out(load_active(conn, owner_id, session_id, settings))
    out.document_summary = documents.summary_for_session(conn, session_id)
    return out


def touch(conn: Connection, settings: Settings, row: Row) -> None:
    """상태를 바꾸는 요청에서만 부른다. 마지막 활동과 만료 시각을 갱신한다."""
    current = now()
    conn.execute(
        "UPDATE sessions SET last_activity_at=?, expires_at=? WHERE session_id=?",
        (to_iso(current), to_iso(_expires_at(settings, from_iso(row["created_at"]), current)), row["session_id"]),
    )


def bump_input_revision(conn: Connection, session_id: str, *, brief: Brief | None,
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
    # 입력이 바뀌면(자료 선택·정정·제외, 목적 변경 — PATCH inputs·첨부 삭제 모두 이 함수를 지난다) 승인은 무효.
    from app.services import approvals  # 순환 import 방지

    db_history.input_revision(conn, session_id)
    approvals.invalidate_for_session(conn, session_id, "input_changed")
    return new_revision


def close(conn: Connection, settings: Settings, owner_id: str, session_id: str) -> str:
    """DELETE: 세션을 closed로 확정한다(BEGIN IMMEDIATE 안, 커밋은 호출자). 상태·내용 제거·Job 취소·Export 확정·멱등 본문 비움·
    큐 등록이 한 트랜잭션이다. 바이트 삭제는 커밋 뒤 cleanup.run_for_session이 즉시 시도하고 실패하면 큐가 재시도한다.
    이미 닫힌 세션이면 상태만 맞춘다(멱등)."""
    row = conn.execute("SELECT owner_id FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if row is None or row["owner_id"] != owner_id:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    from app.services import cleanup  # 순환 import 방지

    return cleanup.finalize(conn, settings, session_id, cleanup.REASON_CLOSED)["status"]
