"""읽기 Job 실행기. 업로드 응답(202) 뒤 백그라운드에서 돈다.

파일마다: reading → 파서 → segments/asset 반영 → parse_status(complete/partial/failed).
파일 하나가 실패해도 다른 파일은 계속 읽는다(결과는 파일별 상태로). Job 자체는 모든 파일을 돈 뒤 succeeded.
예상 못 한 예외로 Job 전체가 멈추면 failed로 남겨 영구 진행 중 상태를 막는다.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid

from app.config import Settings
from app.db import connect
from app.parsers import TEXT_LIMIT, ParseResult, parse, warning
from app.services import jobs
from app.services.sources import resolve_path
from app.timeutil import now, to_iso

logger = logging.getLogger(__name__)


def _apply_char_limit(result: ParseResult, limit: int) -> ParseResult:
    """상한을 넘으면 넘는 구간부터 버리고 partial + TEXT_LIMIT 경고. 조용히 자르지 않는다."""
    total = 0
    for i, seg in enumerate(result.segments):
        total += len(seg.text)
        if total > limit:
            dropped = len(result.segments) - i
            result.segments = result.segments[:i]
            result.status = "partial"
            result.warnings.append(warning(
                TEXT_LIMIT, f"글자 수 상한({limit:,}자)을 넘어 뒤쪽 {dropped}개 구간은 근거로 쓰지 않습니다.",
                result.segments[-1].locator if result.segments else None,
                "자료를 나누어 올리거나 필요한 부분만 올려 주세요."))
            break
    return result


def _apply_result(conn: sqlite3.Connection, row: sqlite3.Row, result: ParseResult) -> None:
    source_id = row["source_id"]
    stamp = to_iso(now())
    conn.execute("DELETE FROM segments WHERE source_id=?", (source_id,))
    for ordinal, seg in enumerate(result.segments, start=1):
        conn.execute(
            "INSERT INTO segments (segment_id, source_id, source_version, session_id, ordinal, locator_json, text, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f"seg_{uuid.uuid4().hex[:16]}", source_id, row["source_version"], row["session_id"], ordinal,
             json.dumps(seg.locator, ensure_ascii=False), seg.text, stamp),
        )
    if result.image_available and result.width and result.height:
        conn.execute(
            "INSERT INTO assets (asset_id, source_id, source_version, scope, session_id, origin, mime_type, width, height, "
            "content_hash, status, stored_path, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, 'source_image', ?, ?, ?, ?, 'ready', ?, ?, ?)",
            (f"asset_{uuid.uuid4().hex[:16]}", source_id, row["source_version"], row["scope"], row["session_id"],
             row["mime_type"], result.width, result.height, row["content_hash"], row["stored_path"], stamp,
             row["expires_at"]),
        )
    conn.execute(
        "UPDATE sources SET parse_status=?, text_available=?, image_available=?, warnings_json=? WHERE source_id=?",
        (result.status, int(result.text_available), int(result.image_available),
         json.dumps(result.warnings, ensure_ascii=False), source_id),
    )


def run_read_job(settings: Settings, session_id: str, job_id: str, source_ids: list[str]) -> None:
    try:
        total = len(source_ids)
        with connect(settings.db_path) as conn:
            jobs.set_progress(conn, job_id, "reading", f"0/{total}")
        for i, source_id in enumerate(source_ids, start=1):
            with connect(settings.db_path) as conn:
                row = conn.execute("SELECT * FROM sources WHERE source_id=? AND deleted_at IS NULL",
                                   (source_id,)).fetchone()
                if row is None:
                    continue  # 읽기 전에 삭제됨
                conn.execute("UPDATE sources SET parse_status='reading' WHERE source_id=?", (source_id,))
            try:
                data = resolve_path(settings, row["stored_path"]).read_bytes()
                result = _apply_char_limit(parse(data, resolve_path(settings, row["stored_path"]).suffix),
                                           settings.max_source_chars)
            except Exception:
                logger.exception("read failed: %s", source_id)
                result = ParseResult(status="failed", warnings=[warning(
                    "PARSE_ERROR", "파일을 읽는 중 오류가 났습니다.", None, "파일을 다시 올리거나 이 자료를 제외해 주세요.")])
            with connect(settings.db_path) as conn:
                current = conn.execute("SELECT * FROM sources WHERE source_id=? AND deleted_at IS NULL",
                                       (source_id,)).fetchone()
                if current is not None:
                    _apply_result(conn, current, result)
                jobs.set_progress(conn, job_id, "reading", f"{i}/{total}")
        with connect(settings.db_path) as conn:
            jobs.succeed(conn, job_id, {"type": "sources", "source_ids": source_ids})
    except Exception:
        logger.exception("read job failed: %s", job_id)
        with connect(settings.db_path) as conn:
            jobs.fail(conn, job_id, "INTERNAL_ERROR", "파일 읽기 작업이 실패했습니다. 다시 올려 주세요.", True)
