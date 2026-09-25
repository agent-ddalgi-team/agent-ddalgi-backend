"""Idempotency-Key 처리 (contracts.md 4절 '비동기·중복 요청').

같은 (키, 소유자, 경로)로 같은 본문이 다시 오면 최초 응답을 그대로 돌려준다.
같은 키에 다른 본문이면 409. 키가 없으면 그냥 처리한다(권장이지 필수는 아님).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from fastapi.responses import JSONResponse

from app.errors import ApiError
from app.timeutil import now, to_iso


def body_hash(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def replay_or_none(conn: sqlite3.Connection, key: str | None, owner_id: str, path: str,
                   digest: str) -> JSONResponse | None:
    if not key:
        return None
    row = conn.execute(
        "SELECT body_hash, status_code, response_json FROM idempotency_keys "
        "WHERE idem_key=? AND owner_id=? AND path=?",
        (key, owner_id, path),
    ).fetchone()
    if row is None:
        return None
    if row["body_hash"] != digest:
        raise ApiError(409, "IDEMPOTENCY_KEY_CONFLICT",
                       "같은 Idempotency-Key로 다른 요청이 이미 처리되었습니다.",
                       details={"path": path})
    return JSONResponse(status_code=row["status_code"], content=json.loads(row["response_json"]))


def remember(conn: sqlite3.Connection, key: str | None, owner_id: str, path: str,
             digest: str, status_code: int, payload: Any) -> None:
    if not key:
        return
    conn.execute(
        "INSERT OR IGNORE INTO idempotency_keys (idem_key, owner_id, path, body_hash, status_code, response_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (key, owner_id, path, digest, status_code, json.dumps(payload, ensure_ascii=False), to_iso(now())),
    )
