"""POST /sessions/{sid}/issues/{iid}/resolve"""
from __future__ import annotations

from fastapi import APIRouter, Header, Request

from app.access import require_owner, settings_of
from app.db import connect
from app.errors import ApiError
from app.models import IssueResolveBody, IssueResolveOut
from app.services import documents, idempotency, issues, preflights, sessions, validation

router = APIRouter(prefix="/sessions/{sid}/issues", tags=["issues"])


@router.post("/{iid}/resolve", response_model=IssueResolveOut)
def resolve_issue(request: Request, sid: str, iid: str, body: IssueResolveBody,
                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    settings = settings_of(request)
    owner = require_owner(request)
    digest = idempotency.body_hash(body.model_dump())
    with connect(settings.db_path, immediate=True) as conn:
        row = sessions.load_active(conn, owner, sid)
        issue = conn.execute("SELECT * FROM issues WHERE issue_id=? AND session_id=?", (iid, sid)).fetchone()
        if issue is None:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
        document = documents.get_current(conn, sid, issue["document_id"])
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest)
        if replay is not None:
            return replay
        pf_row = conn.execute("SELECT preflight_id FROM preflights WHERE session_id=? AND input_revision=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                              (sid, row["input_revision"])).fetchone()
        preflight = preflights.get(conn, sid, pf_row["preflight_id"]) if pf_row else None
        issue_out = issues.resolve(conn, row, owner, document, issue, body, preflight)
        latest = validation.latest_validation(conn, document.document_id, document.document_revision, row["input_revision"])
        doc_status = documents.refresh_status_cache(conn, sid, document.document_id)
        sessions.touch(conn, settings, row)
        out = IssueResolveOut(issue=issue_out, validation=validation.to_validation_out(latest) if latest else None,
                              document_status=doc_status)
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 200, out.model_dump())
    return out
