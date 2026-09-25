"""문서 저장·조회. 버전 구조(documents 머리 + document_revisions 내용).

- BE-04: rev.1 생성·읽기.
- BE-05: add_revision(낙관적 검사로 새 버전 추가), get_revision, on_revision_created 훅(Proposal stale 전환·승인 무효화 자리).
테이블은 바꾸지 않는다. 새 버전 = document_revisions 행 추가 + documents.current_revision 갱신.
"""
from __future__ import annotations

import json
import sqlite3
import uuid

from app.errors import ApiError
from app.models import Document, DocumentSummary, Page
from app.timeutil import now, to_iso


def create_initial(conn: sqlite3.Connection, session_id: str, input_revision: int, title: str,
                   target_pages: int, pages: list[Page], status: str) -> str:
    document_id = f"doc_{uuid.uuid4().hex[:16]}"
    stamp = to_iso(now())
    conn.execute(
        "INSERT INTO documents (document_id, session_id, current_revision, title, target_pages, created_at, updated_at) "
        "VALUES (?, ?, 1, ?, ?, ?, ?)", (document_id, session_id, title, target_pages, stamp, stamp))
    conn.execute(
        "INSERT INTO document_revisions (document_id, revision, input_revision, status, content_json, origin, source_ref, created_at) "
        "VALUES (?, 1, ?, ?, ?, 'draft', NULL, ?)",
        (document_id, input_revision, status,
         json.dumps({"title": title, "pages": [p.model_dump() for p in pages]}, ensure_ascii=False), stamp))
    return document_id


def _head(conn: sqlite3.Connection, session_id: str, document_id: str) -> sqlite3.Row:
    head = conn.execute("SELECT * FROM documents WHERE document_id=? AND session_id=?",
                        (document_id, session_id)).fetchone()
    if head is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    return head


def _to_document(head: sqlite3.Row, rev: sqlite3.Row) -> Document:
    content = json.loads(rev["content_json"])
    return Document(
        document_id=head["document_id"], session_id=head["session_id"], document_revision=rev["revision"],
        input_revision=rev["input_revision"], title=content["title"], target_pages=head["target_pages"],
        status=rev["status"], pages=[Page.model_validate(p) for p in content["pages"]],
    )


def get_current(conn: sqlite3.Connection, session_id: str, document_id: str) -> Document:
    head = _head(conn, session_id, document_id)
    rev = conn.execute("SELECT * FROM document_revisions WHERE document_id=? AND revision=?",
                       (document_id, head["current_revision"])).fetchone()
    return _to_document(head, rev)


def get_revision(conn: sqlite3.Connection, session_id: str, document_id: str, revision: int) -> Document:
    head = _head(conn, session_id, document_id)
    rev = conn.execute("SELECT * FROM document_revisions WHERE document_id=? AND revision=?",
                       (document_id, revision)).fetchone()
    if rev is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 문서 버전이 없습니다.",
                       details={"restore_from_revision": revision})
    return _to_document(head, rev)


def next_status(previous: str) -> str:
    """편집 뒤 상태(BE-06 검증 전 임시 규칙). review_required는 유지, 승인·승인 대기는 편집으로 무효화되어 draft."""
    return "review_required" if previous == "review_required" else "draft"


def add_revision(conn: sqlite3.Connection, session_id: str, document_id: str, expected_revision: int,
                 input_revision: int, title: str, pages: list[Page], status: str, origin: str,
                 source_ref: str | None) -> int:
    """새 버전을 만든다. expected_revision이 현재와 다르면 아무것도 바꾸지 않고 409.

    documents.current_revision 갱신을 WHERE current_revision=expected로 걸어 동시 요청 중 하나만 성공한다.
    """
    head = _head(conn, session_id, document_id)
    if head["current_revision"] != expected_revision:
        raise ApiError(409, "DOCUMENT_REVISION_CONFLICT", "문서가 변경되었습니다. 최신 문서에서 다시 요청해 주세요.",
                       details={"expected_revision": expected_revision, "current_revision": head["current_revision"]})
    new_revision = expected_revision + 1
    stamp = to_iso(now())
    cur = conn.execute(
        "UPDATE documents SET current_revision=?, title=?, updated_at=? WHERE document_id=? AND current_revision=?",
        (new_revision, title, stamp, document_id, expected_revision))
    if cur.rowcount != 1:
        raise ApiError(409, "DOCUMENT_REVISION_CONFLICT", "문서가 변경되었습니다. 최신 문서에서 다시 요청해 주세요.",
                       details={"expected_revision": expected_revision})
    conn.execute(
        "INSERT INTO document_revisions (document_id, revision, input_revision, status, content_json, origin, source_ref, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (document_id, new_revision, input_revision, status,
         json.dumps({"title": title, "pages": [p.model_dump() for p in pages]}, ensure_ascii=False),
         origin, source_ref, stamp))
    on_revision_created(conn, document_id, new_revision)
    return new_revision


def on_revision_created(conn: sqlite3.Connection, document_id: str, new_revision: int) -> None:
    """문서가 바뀌면 그 이전 버전을 기준으로 만든 편집안은 더 이상 적용할 수 없다(stale).

    승인 무효화(Approval.status=invalidated)는 BE-06에서 Approval 테이블이 생기면 여기에 붙인다.
    """
    conn.execute(
        "UPDATE proposals SET status='stale', updated_at=? "
        "WHERE document_id=? AND status='proposed' AND base_document_revision<>?",
        (to_iso(now()), document_id, new_revision))


def summary_for_session(conn: sqlite3.Connection, session_id: str) -> DocumentSummary | None:
    row = conn.execute(
        "SELECT d.document_id, d.current_revision, r.status FROM documents d "
        "JOIN document_revisions r ON r.document_id=d.document_id AND r.revision=d.current_revision "
        "WHERE d.session_id=? ORDER BY d.created_at DESC LIMIT 1", (session_id,)).fetchone()
    if row is None:
        return None
    return DocumentSummary(document_id=row["document_id"], document_revision=row["current_revision"],
                           status=row["status"])


def exists_for_session(conn: sqlite3.Connection, session_id: str) -> str | None:
    row = conn.execute("SELECT document_id FROM documents WHERE session_id=? LIMIT 1", (session_id,)).fetchone()
    return row["document_id"] if row else None
