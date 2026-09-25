"""SQLite 저장소. 표준 sqlite3 모듈만 쓰고 ORM은 없다.

테이블은 BE-02에 필요한 4개만 만든다(sessions, sources, jobs, idempotency_keys).
이후 작업(BE-03~)에서 필요한 테이블·컬럼을 그때 추가하고 plan.md 4절에 기록한다.
시간은 모두 UTC ISO 8601 문자열로 저장한다.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id            TEXT PRIMARY KEY,
    owner_id              TEXT NOT NULL,
    status                TEXT NOT NULL,              -- active / closed / expired
    input_revision        INTEGER NOT NULL,
    brief_json            TEXT NOT NULL,
    selected_source_ids   TEXT NOT NULL,              -- JSON 배열
    created_at            TEXT NOT NULL,
    last_activity_at      TEXT NOT NULL,
    expires_at            TEXT NOT NULL,
    closed_at             TEXT,
    cleanup_status        TEXT                        -- done / pending (삭제 실패 재시도 대상)
);
CREATE INDEX IF NOT EXISTS ix_sessions_owner ON sessions(owner_id);

CREATE TABLE IF NOT EXISTS sources (
    source_id       TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    source_version  INTEGER NOT NULL,
    scope           TEXT NOT NULL,                    -- registered / session (BE-02는 session만)
    name            TEXT NOT NULL,
    mime_type       TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    kind            TEXT NOT NULL,
    parse_status    TEXT NOT NULL,                    -- queued / reading / complete / partial / failed
    text_available  INTEGER NOT NULL,
    image_available INTEGER NOT NULL,
    stored_path     TEXT NOT NULL,                    -- 서버 내부 경로. 응답에 넣지 않는다.
    content_hash    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    expires_at      TEXT,
    deleted_at      TEXT
);
CREATE INDEX IF NOT EXISTS ix_sources_session ON sources(session_id);

CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    kind            TEXT NOT NULL,                    -- read / preflight / draft / ...
    status          TEXT NOT NULL,                    -- queued / running / waiting_user / succeeded / failed / cancelled
    progress_json   TEXT NOT NULL,
    result_ref_json TEXT,
    error_json      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_jobs_session ON jobs(session_id);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    idem_key        TEXT NOT NULL,
    owner_id        TEXT NOT NULL,
    path            TEXT NOT NULL,
    body_hash       TEXT NOT NULL,
    status_code     INTEGER NOT NULL,
    response_json   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (idem_key, owner_id, path)
);
"""


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """요청마다 새 연결을 연다. 예외가 나면 롤백, 정상이면 커밋한다."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
