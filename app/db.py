"""SQLite 저장소. 표준 sqlite3 모듈만 쓰고 ORM은 없다.

스키마 버전은 PRAGMA user_version으로 관리한다.
- v1 (BE-02): sessions, sources(session_id NOT NULL, stored_path 절대경로), jobs, idempotency_keys
- v2 (BE-03): sources.session_id nullable(등록 자료용) + scope CHECK, sources.warnings_json,
              stored_path를 private_runs 기준 상대경로로, segments·assets 테이블 추가
- v3 (BE-04): preflights, documents + document_revisions(처음부터 버전 구조), jobs.input_revision
- v4 (BE-05): proposals, document_revisions.origin / source_ref
- v5 (등록 자료 적재): sources·segments·assets에 등록 자료 메타 컬럼, registered_imports(적재 이력)
- v6 (BE-06): issues, validations, layout_checks(저장 구조만; 실행은 BE-08), approvals, jobs.target_key
- v7 (BE-08): artifacts(불변 산출물), exports, layout_previews(미리보기; assets와 분리), layout_checks·approvals·issues 컬럼 보강
시간은 모두 UTC ISO 8601 문자열로 저장한다.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 7

# v7: 배치 검사·승인·Issue 보강 컬럼(없는 것만 추가). 기존 v6 행은 NULL로 남고 읽는 쪽이 기본값으로 다룬다.
V7_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "layout_checks": [
        ("layout_ok", "INTEGER"), ("publication_policy_ok", "INTEGER"), ("checks_json", "TEXT"), ("findings_json", "TEXT"),
        ("fail_reasons_json", "TEXT"), ("renderer", "TEXT"), ("artifact_id", "TEXT"), ("preview_basis", "TEXT"),
        ("preview_ids_json", "TEXT"), ("job_id", "TEXT"), ("publication_checked_at", "TEXT"), ("warnings_json", "TEXT"),
        ("publication_blocks_json", "TEXT"),
    ],
    "approvals": [("renderer", "TEXT"), ("artifact_id", "TEXT"), ("publication_checked_at", "TEXT")],
    "issues": [("layout_format", "TEXT")],   # scope=layout Issue의 형식(pdf/docx). 공개 허가 Issue는 NULL(형식 무관)
}

# v5: 등록 자료 적재용 컬럼. 세션 업로드 자료에서는 NULL/기본값이다.
V5_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "sources": [
        ("origin_group", "TEXT"), ("document_date", "TEXT"), ("document_date_verified", "INTEGER"),
        ("date_from_filename", "TEXT"), ("extraction_method", "TEXT"),
        ("use_as_company_evidence", "INTEGER NOT NULL DEFAULT 1"),
        ("hash_verified", "INTEGER"), ("hash_note", "TEXT"), ("is_mock", "INTEGER NOT NULL DEFAULT 0"),
        ("imported_at", "TEXT"), ("note", "TEXT"),
    ],
    "segments": [("chunk_id", "TEXT"), ("evidence_status", "TEXT"), ("document_date", "TEXT"),
                 ("extraction_method", "TEXT")],
    "assets": [("photo_id", "TEXT"), ("caption_candidate", "TEXT"), ("selected_as_candidate", "INTEGER"),
               ("approved_for_external_use", "INTEGER"), ("photo_locator_json", "TEXT")],
}

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

-- 검증 결과. (document_id, document_revision, input_revision)마다 한 번의 검증이 한 행.
CREATE TABLE IF NOT EXISTS validations (
    validation_id       TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES sessions(session_id),
    document_id         TEXT NOT NULL REFERENCES documents(document_id),
    document_revision   INTEGER NOT NULL,
    input_revision      INTEGER NOT NULL,
    status              TEXT NOT NULL,                -- pending / passed / needs_review / failed
    issue_ids_json      TEXT NOT NULL,                -- 이 검증 시점의 현재 Issue ID 목록
    checks_json         TEXT NOT NULL,                -- [{check_key, kind, block_ids, result, reused_from_validation_id}]
    fingerprints_json   TEXT NOT NULL,                -- {block_id: 지문} — 다음 부분 재검증의 비교 기준
    base_validation_id  TEXT,                         -- 재사용한 마지막 유효 검증
    agent_called        INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_validations_document ON validations(document_id, document_revision, input_revision);

-- 확인할 내용. 같은 identity_key는 검증을 거듭해도 한 행을 갱신한다(해결 기록 보존).
CREATE TABLE IF NOT EXISTS issues (
    issue_id                TEXT PRIMARY KEY,
    session_id              TEXT NOT NULL REFERENCES sessions(session_id),
    document_id             TEXT NOT NULL REFERENCES documents(document_id),
    identity_key            TEXT NOT NULL,            -- code + scope + 대상 ID(정렬)
    scope                   TEXT NOT NULL,            -- source / content / layout
    code                    TEXT NOT NULL,
    severity                TEXT NOT NULL,            -- blocker / warning / info
    status                  TEXT NOT NULL,            -- open / resolved / excluded / acknowledged
    message                 TEXT NOT NULL,
    source_ids_json         TEXT NOT NULL,
    fact_ids_json           TEXT NOT NULL,
    block_ids_json          TEXT NOT NULL,
    origin                  TEXT NOT NULL,            -- server / agent / preflight
    anchor_fingerprint      TEXT,                     -- 관련 내용·근거·입력의 지문. 바뀌면 재확인(open)
    resolution_json         TEXT,
    resolution_history_json TEXT NOT NULL DEFAULT '[]',
    first_validation_id     TEXT,
    last_validation_id      TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    UNIQUE (document_id, identity_key)
);
CREATE INDEX IF NOT EXISTS ix_issues_document ON issues(document_id, status);

-- 배치 검사 결과. BE-06은 저장 구조와 승인 시 조회만 두고, 실행·Job·API는 BE-08.
CREATE TABLE IF NOT EXISTS layout_checks (
    layout_check_id     TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES sessions(session_id),
    document_id         TEXT NOT NULL REFERENCES documents(document_id),
    document_revision   INTEGER NOT NULL,
    input_revision      INTEGER NOT NULL,
    format              TEXT NOT NULL,                -- pdf / docx
    template_version    TEXT NOT NULL,
    render_options_hash TEXT NOT NULL,
    asset_manifest_hash TEXT NOT NULL,
    status              TEXT NOT NULL,                -- pending / passed / failed
    actual_pages        INTEGER,
    issue_ids_json      TEXT NOT NULL DEFAULT '[]',
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_layout_checks_document ON layout_checks(document_id, document_revision, format);

-- 승인. 문서/입력 버전이 바뀌면 invalidated로 바뀌고 다시 active가 되지 않는다.
CREATE TABLE IF NOT EXISTS approvals (
    approval_id         TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES sessions(session_id),
    document_id         TEXT NOT NULL REFERENCES documents(document_id),
    document_revision   INTEGER NOT NULL,
    input_revision      INTEGER NOT NULL,
    format              TEXT NOT NULL,
    validation_id       TEXT NOT NULL,
    layout_check_id     TEXT NOT NULL,
    template_version    TEXT NOT NULL,
    render_options_hash TEXT NOT NULL,
    asset_manifest_hash TEXT NOT NULL,
    approved_at         TEXT NOT NULL,
    approved_by         TEXT NOT NULL,                -- 서버가 확인한 소유자 ID
    status              TEXT NOT NULL,                -- active / invalidated
    invalidated_at      TEXT,
    invalidated_reason  TEXT,                         -- document_changed / input_changed
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_approvals_document ON approvals(document_id, status);

-- 등록 자료 적재 이력(scripts/import_registered.py). 원문·경로는 넣지 않고 건수만 남긴다.
CREATE TABLE IF NOT EXISTS registered_imports (
    import_id       TEXT PRIMARY KEY,
    bundle_label    TEXT NOT NULL,                    -- 묶음 루트 폴더 이름의 해시(경로 비노출)
    with_mock       INTEGER NOT NULL,
    dry_run         INTEGER NOT NULL,
    summary_json    TEXT NOT NULL,                    -- {"added": n, "skipped": {...}, "segments": n, "assets": n}
    created_at      TEXT NOT NULL
);

-- 불변 산출물(BE-08). 한 번 쓰고 덮어쓰지 않는다. 승인·Export는 artifact_id·sha256·크기로 파일을 고정한다.
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id         TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES sessions(session_id),
    document_id         TEXT NOT NULL REFERENCES documents(document_id),
    document_revision   INTEGER NOT NULL,
    input_revision      INTEGER NOT NULL,
    format              TEXT NOT NULL,                -- pdf / docx
    stored_path         TEXT NOT NULL,                -- private_runs 기준 상대경로. 응답에 넣지 않는다.
    sha256              TEXT NOT NULL,
    size_bytes          INTEGER NOT NULL,
    template_version    TEXT NOT NULL,
    render_options_hash TEXT NOT NULL,
    asset_manifest_hash TEXT NOT NULL,
    renderer            TEXT NOT NULL,
    actual_pages        INTEGER,
    layout_check_id     TEXT,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_artifacts_session ON artifacts(session_id);

-- 출력 요청(BE-08). 같은 재사용 키의 활성 행(queued/generating/ready)은 하나만(부분 UNIQUE). 만료·실패 행은 ID·시각을 보존한다.
CREATE TABLE IF NOT EXISTS exports (
    export_id               TEXT PRIMARY KEY,
    session_id              TEXT NOT NULL REFERENCES sessions(session_id),
    approval_id             TEXT NOT NULL,
    document_id             TEXT NOT NULL,
    document_revision       INTEGER NOT NULL,
    input_revision          INTEGER NOT NULL,
    format                  TEXT NOT NULL,
    status                  TEXT NOT NULL,            -- queued / generating / ready / failed
    artifact_id             TEXT,
    reuse_key               TEXT NOT NULL,            -- approval_id|format|template_version|render_options_hash|asset_manifest_hash
    attempt                 INTEGER NOT NULL DEFAULT 1,
    job_id                  TEXT,
    expires_at              TEXT NOT NULL,
    error_json              TEXT,
    renderer                TEXT,
    publication_checked_at  TEXT,
    finalized_reason        TEXT,                     -- expired / session_closed / publication_changed ... (failed로 확정한 사유)
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    published_at            TEXT
);
CREATE INDEX IF NOT EXISTS ix_exports_session ON exports(session_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS ux_exports_active ON exports(reuse_key) WHERE status IN ('queued', 'generating', 'ready');

-- 배치 검사 미리보기(쪽 PNG). assets와 분리해 자료 목록·근거·사진 후보에 섞이지 않는다. GET /assets/{asset_id}가 함께 제공한다.
CREATE TABLE IF NOT EXISTS layout_previews (
    asset_id            TEXT PRIMARY KEY,             -- prv_…
    session_id          TEXT NOT NULL REFERENCES sessions(session_id),
    layout_check_id     TEXT NOT NULL,
    artifact_id         TEXT,
    page_no             INTEGER NOT NULL,
    stored_path         TEXT NOT NULL,
    sha256              TEXT NOT NULL,
    size_bytes          INTEGER NOT NULL,
    width               INTEGER NOT NULL,
    height              INTEGER NOT NULL,
    mime_type           TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'ready',
    created_at          TEXT NOT NULL,
    expires_at          TEXT,
    deleted_at          TEXT
);
CREATE INDEX IF NOT EXISTS ix_layout_previews_session ON layout_previews(session_id);

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
        # v4 → v5: 등록 자료 컬럼(없는 것만 추가).
        for table, columns in V5_COLUMNS.items():
            existing = set(_columns(conn, table))
            for name, ddl in columns:
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        # v5 → v6: 새 테이블은 IF NOT EXISTS, jobs에 대상 키(문서@버전@입력) 컬럼.
        if "target_key" not in _columns(conn, "jobs"):
            conn.execute("ALTER TABLE jobs ADD COLUMN target_key TEXT")
        # v6 안에서 identity_key 형식이 origin 포함으로 바뀜(BE-06 리뷰). 옛 형식 행을 한 번 변환한다(재실행 안전).
        from app.services.validation import migrate_legacy_issue_keys

        migrate_legacy_issue_keys(conn)
        # v6 → v7: 새 테이블은 IF NOT EXISTS, 기존 테이블은 없는 컬럼만 추가(v6 행 보존).
        for table, columns in V7_COLUMNS.items():
            existing = set(_columns(conn, table))
            for name, ddl in columns:
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
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
