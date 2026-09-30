"""ERD v2 이력 저장. 호출자의 트랜잭션을 사용하며 기존 v9 DB에는 쓰지 않는다.

과거 상태를 추측해 복원하지 않는다. 새로 발생한 입력/읽기/승인만 기록한다.
영향 검토와 개별 경고 확인은 별도 기능에서 실제 사용자 행동으로 기록해야 한다.
"""
from __future__ import annotations

import json
import uuid

from app.db import Connection, Row
from app.timeutil import now, to_iso


def enabled(conn: Connection) -> bool:
    return conn.execute("PRAGMA user_version").fetchone()[0] in (10, 11)


def source_version(conn: Connection, source_id: str, *, provenance: str) -> None:
    if not enabled(conn):
        return
    conn.execute(
        "INSERT OR IGNORE INTO source_versions "
        "(source_id, version, session_id, original_name, mime_type, size_bytes, stored_path, "
        "content_hash, created_at, expires_at, provenance) "
        "SELECT source_id, source_version, session_id, name, mime_type, size_bytes, stored_path, "
        "content_hash, created_at, expires_at, ? FROM sources WHERE source_id=?",
        (provenance, source_id))


def input_revision(conn: Connection, session_id: str) -> None:
    if not enabled(conn):
        return
    row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    conn.execute(
        "INSERT INTO input_revisions (session_id, revision, brief_json, created_at, provenance) VALUES (?, ?, ?, ?, ?)",
        (session_id, row["input_revision"], row["brief_json"], to_iso(now()),
         "session_create" if row["input_revision"] == 1 else "inputs_update"))
    for source_id in dict.fromkeys(json.loads(row["selected_source_ids"])):
        # 선택의 소유/허용 범위는 기존 요청 검사가 책임진다. 없는 자료를 이력으로 만들지 않는다.
        src = conn.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
        if src is None:
            raise ValueError("선택 자료가 존재하지 않습니다")
        source_version(conn, source_id, provenance="selected")
        conn.execute(
            "INSERT INTO session_source_selections "
            "(session_id, input_revision, source_id, source_version, run_id) VALUES (?, ?, ?, ?, ?)",
            (session_id, row["input_revision"], source_id, src["source_version"],
             src["current_run_id"] if src["parse_status"] in {"complete", "partial"} else None))


def finish_extraction(conn: Connection, source_id: str, *, method: str) -> str | None:
    """이번에 생성된(null run_id) 근거만 새 실행에 연결한다. 이전 실행은 보존한다."""
    if not enabled(conn):
        return None
    source_version(conn, source_id, provenance=method)
    src = conn.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
    run_id = f"run_{uuid.uuid4().hex[:16]}"
    stamp = to_iso(now())
    counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table} WHERE source_id=? AND run_id IS NULL",
                                 (source_id,)).fetchone()[0] for table in ("segments", "assets")}
    conn.execute(
        "INSERT INTO extraction_runs (run_id, source_id, source_version, session_id, method, status, "
        "warnings_json, summary_json, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, source_id, src["source_version"], src["session_id"], method, src["parse_status"],
         src["warnings_json"], json.dumps(counts), stamp, stamp))
    for table in ("segments", "assets"):
        conn.execute(f"UPDATE {table} SET run_id=? WHERE source_id=? AND source_version=? AND run_id IS NULL",
                     (run_id, source_id, src["source_version"]))
    conn.execute("UPDATE sources SET current_run_id=? WHERE source_id=?", (run_id, source_id))
    # 읽기 완료 전 선택한 자료는 처음 완료된 실행에만 연결한다. 기존 실행 연결은 덮어쓰지 않는다.
    if src["parse_status"] in {"complete", "partial"}:
        conn.execute(
            "UPDATE session_source_selections SET run_id=? "
            "WHERE source_id=? AND source_version=? AND run_id IS NULL",
            (run_id, source_id, src["source_version"]))
    return run_id


def current_run_filter(conn: Connection, source_id: str) -> tuple[str, tuple]:
    """자료 목록에는 최신 읽기 실행의 근거만 표시한다."""
    if not enabled(conn):
        return "", ()
    row = conn.execute("SELECT current_run_id FROM sources WHERE source_id=?", (source_id,)).fetchone()
    return " AND run_id IS ?", (row[0] if row else None,)


def bind_document(conn: Connection, document_id: str, revision: int, session_id: str,
                  preflight_id: str | None = None) -> None:
    if enabled(conn):
        conn.execute("UPDATE document_revisions SET session_id=?, preflight_id=? WHERE document_id=? AND revision=?",
                     (session_id, preflight_id, document_id, revision))


def record_confirmation(conn: Connection, approval_id: str) -> None:
    if not enabled(conn):
        return
    confirmation_id = f"cfm_{uuid.uuid4().hex[:16]}"
    conn.execute(
        "INSERT INTO confirmations (confirmation_id, session_id, document_id, document_revision, "
        "input_revision, kind, confirmed_by, confirmed_at, validation_id, layout_check_id, artifact_id, "
        "reasons_json, status) SELECT ?, session_id, document_id, document_revision, input_revision, "
        "'final_consent', approved_by, approved_at, validation_id, layout_check_id, artifact_id, '[]', 'active' "
        "FROM approvals WHERE approval_id=?", (confirmation_id, approval_id))
    conn.execute("UPDATE approvals SET confirmation_id=? WHERE approval_id=?", (confirmation_id, approval_id))


def invalidate_warning(conn: Connection, issue_id: str) -> None:
    if enabled(conn):
        conn.execute("UPDATE confirmations SET status='invalidated', invalidated_at=COALESCE(invalidated_at, ?) "
                     "WHERE kind='warning_ack' AND issue_id=? AND status='active'", (to_iso(now()), issue_id))


def record_warning(conn: Connection, issue: Row, resolution: dict) -> None:
    """명시 확인을 저장하거나, 동일 내용의 재검증에 원 확인을 연결한다. 확인자/시각은 보존한다."""
    if not enabled(conn):
        return
    vid = resolution["validated_validation_id"]
    existing = conn.execute("SELECT confirmation_id FROM confirmations WHERE issue_id=? AND kind='warning_ack' "
                            "AND validation_id=? AND status='active'", (issue["issue_id"], vid)).fetchone()
    if existing:
        return
    previous = conn.execute("SELECT confirmation_id FROM confirmations WHERE issue_id=? AND kind='warning_ack' "
                            "ORDER BY rowid DESC LIMIT 1", (issue["issue_id"],)).fetchone()
    invalidate_warning(conn, issue["issue_id"])
    proof = dict(resolution)
    if previous and resolution["validation_id"] != vid:
        proof["reused_from_confirmation_id"] = previous[0]
    conn.execute(
        "INSERT INTO confirmations (confirmation_id, session_id, document_id, document_revision, input_revision, "
        "kind, confirmed_by, confirmed_at, issue_id, validation_id, reasons_json, status) "
        "VALUES (?, ?, ?, ?, ?, 'warning_ack', ?, ?, ?, ?, ?, 'active')",
        (f"cfm_{uuid.uuid4().hex[:16]}", issue["session_id"], issue["document_id"],
         resolution["validated_document_revision"], resolution["input_revision"], resolution["by"], resolution["at"],
         issue["issue_id"], vid, json.dumps([proof], ensure_ascii=False)))


def warning_record_exists(conn: Connection, issue: Row, validation: Row) -> bool:
    return not enabled(conn) or conn.execute(
        "SELECT 1 FROM confirmations WHERE kind='warning_ack' AND issue_id=? AND session_id=? AND document_id=? "
        "AND document_revision=? AND input_revision=? AND validation_id=? AND status='active'",
        (issue["issue_id"], issue["session_id"], issue["document_id"], validation["document_revision"],
         validation["input_revision"], validation["validation_id"])).fetchone() is not None


def sync_invalidations(conn: Connection) -> None:
    if enabled(conn):
        conn.execute(
            "UPDATE confirmations SET status='invalidated', invalidated_at=COALESCE(invalidated_at, ?) "
            "WHERE status='active' AND confirmation_id IN "
            "(SELECT confirmation_id FROM approvals WHERE status='invalidated')", (to_iso(now()),))
        conn.execute(
            "UPDATE confirmations SET status='invalidated', invalidated_at=COALESCE(invalidated_at, ?) "
            "WHERE kind IN ('warning_ack', 'impact_keep') AND status='active' AND "
            "(document_revision<>(SELECT current_revision FROM documents WHERE documents.document_id=confirmations.document_id) "
            "OR input_revision<>(SELECT input_revision FROM sessions WHERE sessions.session_id=confirmations.session_id))",
            (to_iso(now()),))
        conn.execute(
            "UPDATE impact_reviews SET status='stale' WHERE status='pending' AND "
            "(document_revision<>(SELECT current_revision FROM documents WHERE documents.document_id=impact_reviews.document_id) "
            "OR to_input_revision<>(SELECT input_revision FROM sessions WHERE sessions.session_id=impact_reviews.session_id))")
        conn.execute(
            "UPDATE confirmations SET status='invalidated', invalidated_at=COALESCE(invalidated_at, ?) "
            "WHERE kind='impact_keep' AND status='active' AND impact_review_id IN "
            "(SELECT review_id FROM impact_reviews r WHERE r.preflight_id IS NOT "
            "(SELECT p.preflight_id FROM preflights p WHERE p.session_id=r.session_id "
            "AND p.input_revision=r.to_input_revision ORDER BY p.created_at DESC, p.rowid DESC LIMIT 1))",
            (to_iso(now()),))


def purge_source(conn: Connection, source_id: str, stamp: str) -> None:
    if not enabled(conn):
        return
    conn.execute("UPDATE source_versions SET original_name='', stored_path='', provenance='', "
                 "purged_at=COALESCE(purged_at, ?) WHERE source_id=?", (stamp, source_id))
    conn.execute("UPDATE extraction_runs SET warnings_json='[]', summary_json='{}', "
                 "purged_at=COALESCE(purged_at, ?) WHERE source_id=?", (stamp, source_id))


def purge_session(conn: Connection, session_id: str, stamp: str) -> None:
    if not enabled(conn):
        return
    for row in conn.execute("SELECT source_id FROM sources WHERE session_id=?", (session_id,)).fetchall():
        purge_source(conn, row[0], stamp)
    conn.execute("UPDATE input_revisions SET brief_json='{}', provenance='', purged_at=COALESCE(purged_at, ?) WHERE session_id=?",
                 (stamp, session_id))
    conn.execute("UPDATE impact_reviews SET items_json='[]', purged_at=COALESCE(purged_at, ?) WHERE session_id=?",
                 (stamp, session_id))
    conn.execute("UPDATE confirmations SET confirmed_by='', reasons_json='[]', status='invalidated', "
                 "invalidated_at=COALESCE(invalidated_at, ?), purged_at=COALESCE(purged_at, ?) WHERE session_id=?",
                 (stamp, stamp, session_id))
