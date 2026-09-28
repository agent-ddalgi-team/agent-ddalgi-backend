"""Idempotency-Key 처리 (contracts.md 4절 '비동기·중복 요청').

같은 (키, 소유자, 경로)로 같은 본문이 다시 오면 최초 응답을 그대로 돌려준다.
같은 키에 다른 본문이면 409. 키가 없으면 그냥 처리한다(권장이지 필수는 아님).
BE-09: 저장된 응답은 세션에 연결된다(session_id). 재전송 때 그 세션이 종료·만료됐거나 응답 본문이 정리(purged_at)됐으면
최초 응답(brief·파일명·메시지 등 세션 내용)을 돌려주지 않고 410 SESSION_EXPIRED를 낸다. 같은 키·다른 본문 409가 먼저다.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi.responses import JSONResponse

from app.db import Connection
from app.config import Settings
from app.errors import ApiError
from app.timeutil import from_iso, now, to_iso


def body_hash(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def replay_or_none(conn: Connection, key: str | None, owner_id: str, path: str,
                   digest: str, settings: Settings) -> JSONResponse | None:
    """연결 세션이 종료·만료됐거나 저장 응답이 정리됐으면 sessions.raise_gone(만료 확정·정리 등록·cleanup 판정)을 거쳐
    410 {status, cleanup}을 낸다(㊵). 순서: 같은 키·다른 본문 409 → 세션 상태 → 최초 응답."""
    if not key:
        return None
    row = conn.execute(
        "SELECT body_hash, status_code, response_json, session_id, purged_at FROM idempotency_keys "
        "WHERE idem_key=? AND owner_id=? AND path=?",
        (key, owner_id, path),
    ).fetchone()
    if row is None:
        return None
    if row["body_hash"] != digest:
        raise ApiError(409, "IDEMPOTENCY_KEY_CONFLICT",
                       "같은 Idempotency-Key로 다른 요청이 이미 처리되었습니다.",
                       details={"path": path})
    if row["session_id"]:
        from app.services import sessions as session_service

        session = conn.execute("SELECT * FROM sessions WHERE session_id=?", (row["session_id"],)).fetchone()
        policy = session_service.usable(settings, session)
        gone = policy in {"closed", "expired"} or row["purged_at"] is not None
        if gone:
            if session is None:
                raise ApiError(410, "SESSION_EXPIRED", "세션이 종료되어 이전 응답을 다시 제공하지 않습니다.",
                               details={"status": "closed", "cleanup": "done"})
            from app.services import cleanup, sessions as sessions_service   # 순환 import 방지

            # 종료·만료 확정(내용 제거·큐 등록 포함)과 현재 cleanup 판정을 담아 410. 그 사이 연장돼 살아 있으면(경쟁, 드묾) 행을 돌려준다.
            sessions_service.raise_gone(conn, settings, owner_id, row["session_id"])
            if row["purged_at"] is None:
                return JSONResponse(status_code=row["status_code"], content=json.loads(row["response_json"]))
            raise ApiError(410, "SESSION_EXPIRED", "세션이 종료되어 이전 응답을 다시 제공하지 않습니다.",
                           details={"status": session["status"], "cleanup": cleanup.verify_state(conn, settings, row["session_id"])})
        if policy == "demo_disabled":
            raise session_service.demo_disabled_error()
    elif row["purged_at"] is not None:
        # 연결 세션을 알 수 없는(backfill 불가) 옛 행: 정리할 폴더가 없으므로 done
        raise ApiError(410, "SESSION_EXPIRED", "세션이 종료되어 이전 응답을 다시 제공하지 않습니다.", details={"status": "closed", "cleanup": "done"})
    return JSONResponse(status_code=row["status_code"], content=json.loads(row["response_json"]))


def remember(conn: Connection, key: str | None, owner_id: str, path: str,
             digest: str, status_code: int, payload: Any, *, session_id: str | None = None) -> None:
    if not key:
        return
    conn.execute(
        "INSERT OR IGNORE INTO idempotency_keys (idem_key, owner_id, path, body_hash, status_code, response_json, created_at, session_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (key, owner_id, path, digest, status_code, json.dumps(payload, ensure_ascii=False), to_iso(now()), session_id),
    )


def purge_for_session(conn: Connection, session_id: str, stamp: str) -> int:
    """세션 정리: 연결된 저장 응답 본문을 비운다(키·경로·상태 코드는 남아 같은 키 재전송이 409/410으로 판정된다)."""
    cur = conn.execute("UPDATE idempotency_keys SET response_json='{}', purged_at=? WHERE session_id=? AND purged_at IS NULL",
                       (stamp, session_id))
    return cur.rowcount
