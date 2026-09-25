"""문서 저장·조회. 처음부터 버전 구조(documents 머리 + document_revisions 내용). BE-04는 rev.1 생성과 읽기만.

BE-05가 여기에 새 revision 추가(편집·수정안 적용·복원)를 붙인다. 테이블은 바꾸지 않는다.
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
        "INSERT INTO document_revisions (document_id, revision, input_revision, status, content_json, created_at) "
        "VALUES (?, 1, ?, ?, ?, ?)",
        (document_id, input_revision, status,
         json.dumps({"title": title, "pages": [p.model_dump() for p in pages]}, ensure_ascii=False), stamp))
    return document_id


def get_current(conn: sqlite3.Connection, session_id: str, document_id: str) -> Document:
    head = conn.execute("SELECT * FROM documents WHERE document_id=? AND session_id=?",
                        (document_id, session_id)).fetchone()
    if head is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    rev = conn.execute("SELECT * FROM document_revisions WHERE document_id=? AND revision=?",
                       (document_id, head["current_revision"])).fetchone()
    content = json.loads(rev["content_json"])
    return Document(
        document_id=document_id, session_id=session_id, document_revision=rev["revision"],
        input_revision=rev["input_revision"], title=content["title"], target_pages=head["target_pages"],
        status=rev["status"], pages=[Page.model_validate(p) for p in content["pages"]],
    )


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
