"""SQLite 저장소. 표준 sqlite3 모듈만 쓰고 ORM은 없다.

스키마 버전은 PRAGMA user_version으로 관리한다.
- v1 (BE-02): sessions, sources(session_id NOT NULL, stored_path 절대경로), jobs, idempotency_keys
- v2 (BE-03): sources.session_id nullable(등록 자료용) + scope CHECK, sources.warnings_json,
              stored_path를 private_runs 기준 상대경로로, segments·assets 테이블 추가
- v3 (BE-04): preflights, documents + document_revisions(처음부터 버전 구조), jobs.input_revision
- v4 (BE-05): proposals, document_revisions.origin / source_ref
시간은 모두 UTC ISO 8601 문자열로 저장한다.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 4

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

-- 등록 자료(scope=registered)는 세션이 없다. 세션 자료는 반드시 세션이 있다.
CREATE TABLE IF NOT EXISTS sources (
    source_id       TEXT PRIMARY KEY,
    session_id      TEXT REFERENCES sessions(session_id),
    source_version  INTEGER NOT NULL,
    scope           TEXT NOT NULL,                    -- registered / session
    name            TEXT NOT NULL,
    mime_type       TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    kind            TEXT NOT NULL,
    parse_status    TEXT NOT NULL,                    -- queued / reading / complete / partial / failed
    text_available  INTEGER NOT NULL,
    image_available INTEGER NOT NULL,
    stored_path     TEXT NOT NULL,                    -- private_runs 기준 상대경로. 응답에 넣지 않는다.
    content_hash    TEXT NOT NULL,
    warnings_json   TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    expires_at      TEXT,
    deleted_at      TEXT,
    CHECK ((scope = 'session' AND session_id IS NOT NULL) OR (scope = 'registered' AND session_id IS NULL))
);
CREATE INDEX IF NOT EXISTS ix_sources_session ON sources(session_id);

-- 파서가 만든 근거 구간. EvidenceRef.segment_id가 가리키는 대상.
CREATE TABLE IF NOT EXISTS segments (
    segment_id      TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES sources(source_id),
    source_version  INTEGER NOT NULL,
    session_id      TEXT,
    ordinal         INTEGER NOT NULL,                 -- 자료 안 순서
    locator_json    TEXT NOT NULL,                    -- {"line_start","line_end"} / {"page"} / {"slide","shape"} ...
    text            TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_segments_source ON segments(source_id, ordinal);

-- 화면·출력에 쓰는 이미지. BE-03은 업로드한 이미지 파일만 asset으로 만든다.
CREATE TABLE IF NOT EXISTS assets (
    asset_id        TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES sources(source_id),
    source_version  INTEGER NOT NULL,
    scope           TEXT NOT NULL,
    session_id      TEXT,
    origin          TEXT NOT NULL,                    -- source_image / user_upload / generated_illustration
    mime_type       TEXT NOT NULL,
    width           INTEGER NOT NULL,
    height          INTEGER NOT NULL,
    content_hash    TEXT NOT NULL,
    status          TEXT NOT NULL,                    -- processing / ready / failed
    stored_path     TEXT NOT NULL,                    -- private_runs 기준 상대경로
    created_at      TEXT NOT NULL,
    expires_at      TEXT,
    deleted_at      TEXT
);
CREATE INDEX IF NOT EXISTS ix_assets_session ON assets(session_id);

CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    kind            TEXT NOT NULL,                    -- read / preflight / draft / ...
    status          TEXT NOT NULL,                    -- queued / running / waiting_user / succeeded / failed / cancelled
    progress_json   TEXT NOT NULL,
    result_ref_json TEXT,
    error_json      TEXT,
    input_revision  INTEGER,                          -- AI 작업이 기준으로 삼은 입력 버전(중복 실행 판정용)
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_jobs_session ON jobs(session_id);

-- 사전 점검 결과(역할 ①). facts/issues/recommendations는 contracts.md 2절 모양의 JSON.
CREATE TABLE IF NOT EXISTS preflights (
    preflight_id          TEXT PRIMARY KEY,
    session_id            TEXT NOT NULL REFERENCES sessions(session_id),
    input_revision        INTEGER NOT NULL,
    usable_source_ids     TEXT NOT NULL,              -- JSON 배열
    facts_json            TEXT NOT NULL,
    issues_json           TEXT NOT NULL,
    recommendations_json  TEXT NOT NULL,
    can_generate          INTEGER NOT NULL,
    confirmed_at          TEXT,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_preflights_session ON preflights(session_id, input_revision);

-- 문서는 처음부터 버전 구조. documents는 머리(현재 버전), document_revisions가 내용.
CREATE TABLE IF NOT EXISTS documents (
    document_id       TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES sessions(session_id),
    current_revision  INTEGER NOT NULL,
    title             TEXT NOT NULL,
    target_pages      INTEGER NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_documents_session ON documents(session_id);

CREATE TABLE IF NOT EXISTS document_revisions (
    document_id     TEXT NOT NULL REFERENCES documents(document_id),
    revision        INTEGER NOT NULL,
    input_revision  INTEGER NOT NULL,
    status          TEXT NOT NULL,                    -- draft / review_required / ready_for_approval / approved
    content_json    TEXT NOT NULL,                    -- {"title", "pages": [...]}
    origin          TEXT,                             -- draft / user_edit / proposal_apply / restore
    source_ref      TEXT,                             -- proposal_id 또는 복원 원본 revision 번호
    created_at      TEXT NOT NULL,
    PRIMARY KEY (document_id, revision)
);

-- AI 편집안. 적용 전에는 문서를 바꾸지 않는다(contracts.md Proposal절).
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id             TEXT PRIMARY KEY,
    session_id              TEXT NOT NULL REFERENCES sessions(session_id),
    document_id             TEXT NOT NULL REFERENCES documents(document_id),
    base_document_revision  INTEGER NOT NULL,
    base_input_revision     INTEGER NOT NULL,
    target_block_ids        TEXT NOT NULL,            -- JSON 배열
    kind                    TEXT NOT NULL,            -- text / structure / image
    instruction             TEXT NOT NULL,
    changes_json            TEXT NOT NULL,            -- {"changes": [...ops], "rationale": str, "candidates": [...] | null}
    status                  TEXT NOT NULL,            -- proposed / applied / rejected / stale
    applied_revision        INTEGER,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_proposals_document ON proposals(document_id, status);

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


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _relativize(stored_path: str, private_runs_dir: Path, session_id: str | None) -> str:
    """v1의 절대경로를 private_runs 기준 상대경로로 바꾼다. 기준 폴더 밖이면 <session_id>/<파일명>으로 추정."""
    path = Path(stored_path)
    try:
        return path.resolve().relative_to(private_runs_dir.resolve()).as_posix()
    except ValueError:
        return f"{session_id or 'registered'}/{path.name}"


def _migrate_v1_to_v2(conn: sqlite3.Connection, private_runs_dir: Path) -> None:
    """sources를 재생성한다(SQLite는 NOT NULL 해제·CHECK 추가를 ALTER로 못 한다). 행은 보존한다."""
    rows = conn.execute("SELECT * FROM sources").fetchall()
    conn.execute("ALTER TABLE sources RENAME TO sources_v1")
    conn.execute("DROP INDEX IF EXISTS ix_sources_session")
    conn.executescript(SCHEMA)  # 새 sources(및 나머지 IF NOT EXISTS) 생성
    for r in rows:
        conn.execute(
            "INSERT INTO sources (source_id, session_id, source_version, scope, name, mime_type, size_bytes, kind, "
            "parse_status, text_available, image_available, stored_path, content_hash, warnings_json, created_at, "
            "expires_at, deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?)",
            (r["source_id"], r["session_id"], r["source_version"], r["scope"], r["name"], r["mime_type"],
             r["size_bytes"], r["kind"], r["parse_status"], r["text_available"], r["image_available"],
             _relativize(r["stored_path"], private_runs_dir, r["session_id"]), r["content_hash"],
             r["created_at"], r["expires_at"], r["deleted_at"]),
        )
    conn.execute("DROP TABLE sources_v1")


def init_db(db_path: Path, private_runs_dir: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        has_sources = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sources'").fetchone() is not None
        if version == 0 and has_sources and "warnings_json" not in _columns(conn, "sources"):
            _migrate_v1_to_v2(conn, private_runs_dir)
        else:
            conn.executescript(SCHEMA)
        # v2 → v3: 새 테이블은 IF NOT EXISTS로 생기고, jobs에는 컬럼만 하나 더한다.
        if "input_revision" not in _columns(conn, "jobs"):
            conn.execute("ALTER TABLE jobs ADD COLUMN input_revision INTEGER")
        # v3 → v4: document_revisions에 감사용 컬럼 2개.
        if "origin" not in _columns(conn, "document_revisions"):
            conn.execute("ALTER TABLE document_revisions ADD COLUMN origin TEXT")
            conn.execute("ALTER TABLE document_revisions ADD COLUMN source_ref TEXT")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()


@contextmanager
def connect(db_path: Path, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    """요청마다 새 연결을 연다. 예외가 나면 롤백, 정상이면 커밋한다.

    immediate=True면 시작부터 쓰기 잠금을 잡는다(BEGIN IMMEDIATE). 같은 문서에 동시에 들어온 적용 요청이
    서로의 중간 상태를 보지 못하게 할 때 쓴다.
    """
    conn = sqlite3.connect(db_path, timeout=10, isolation_level=None if immediate else "")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if immediate:
        conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
