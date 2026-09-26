"""Asset 조회. 화면은 asset_id를 서버 조회 주소에 연결한다(contracts.md Asset절)."""
from __future__ import annotations

import sqlite3

from app.errors import ApiError


def get_ready(conn: sqlite3.Connection, session_id: str, asset_id: str) -> sqlite3.Row:
    """이 세션의 asset 또는 등록 자료 asset. 다른 세션의 asset은 존재를 숨긴다(404)."""
    row = conn.execute(
        "SELECT * FROM assets WHERE asset_id=? AND deleted_at IS NULL "
        "AND ((scope='session' AND session_id=?) OR scope='registered')", (asset_id, session_id)
    ).fetchone()
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    if row["status"] != "ready":
        raise ApiError(409, "ASSET_NOT_READY", "이미지가 아직 준비되지 않았습니다.", retryable=True,
                       details={"status": row["status"]})
    return row
