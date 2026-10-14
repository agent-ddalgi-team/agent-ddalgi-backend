"""POST /sessions/{sid}/issues/{iid}/resolve"""
from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Header, Request

from app.access import require_owner, settings_of
from app.db import connect
from app.errors import ApiError
from app.models import IssueResolveBody, IssueResolveOut, SourceCorrectionBody, JobAccepted
from app.services import ai_jobs, approvals, documents, idempotency, issues, jobs, preflights, sessions, validation

router = APIRouter(prefix="/sessions/{sid}/issues", tags=["issues"])


@router.post("/{iid}/source-corrections", status_code=202, response_model=JobAccepted)
def correct_source(request: Request, sid: str, iid: str, body: SourceCorrectionBody,
                   background_tasks: BackgroundTasks,
                   idempotency_key: str = Header(alias="Idempotency-Key", min_length=1)):
    settings, owner = settings_of(request), require_owner(request)
    digest = idempotency.body_hash(body.model_dump())
    with connect(settings.db_path, immediate=True) as conn:
        row = sessions.load_active(conn, owner, sid, settings)
        issue = conn.execute("SELECT * FROM issues WHERE issue_id=? AND session_id=?", (iid, sid)).fetchone()
        if issue is None:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
        document = documents.get_current(conn, sid, issue["document_id"])
        pf_row = conn.execute("SELECT preflight_id FROM preflights WHERE session_id=? AND input_revision=? "
                              "ORDER BY created_at DESC, rowid DESC LIMIT 1", (sid, row["input_revision"])).fetchone()
        preflight = preflights.get(conn, sid, pf_row[0]) if pf_row else None
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest, settings)
        if replay is not None:
            sources = preflights.build_sources(conn, sid, json.loads(row["selected_source_ids"]))
            active = issues.active_source_corrections(conn, document, row["input_revision"], preflight, sources)
            if not any(r["issue_id"] == iid and all(r[k] == v for k, v in body.model_dump().items()) for r in active):
                raise ApiError(422, "REVALIDATION_REQUIRED", "정정 당시 문서·자료가 바뀌었습니다. 최신 결과를 확인해 주세요.")
            return replay
        record = issues.record_source_correction(conn, row, owner, document, issue, body, preflight)
        approvals.invalidate_for_document(conn, document.document_id, "source_correction")
        key = f"{document.document_id}@{document.document_revision}@{row['input_revision']}"
        job = jobs.create(conn, sid, "validate", "원문 정정 기록을 포함해 재검증 대기 중",
                          input_revision=row["input_revision"], target_key=key)
        jobs.record_trace(conn, job.job_id, "source_correction", {"correction_id": record["correction_id"], "issue_id": iid})
        sessions.touch(conn, settings, row)
        out = JobAccepted(job_id=job.job_id, status="queued", kind="validate", session_id=sid, created_at=job.created_at)
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 202, out.model_dump(), session_id=sid)
    background_tasks.add_task(ai_jobs.run_validate_job, settings, sid, job.job_id, row["input_revision"],
                              document.document_id, document.document_revision)
    return out


@router.post("/{iid}/resolve", response_model=IssueResolveOut)
def resolve_issue(request: Request, sid: str, iid: str, body: IssueResolveBody,
                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    settings = settings_of(request)
    owner = require_owner(request)
    digest = idempotency.body_hash(body.model_dump())
    with connect(settings.db_path, immediate=True) as conn:
        row = sessions.load_active(conn, owner, sid, settings)
        issue = conn.execute("SELECT * FROM issues WHERE issue_id=? AND session_id=?", (iid, sid)).fetchone()
        if issue is None:
            raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
        document = documents.get_current(conn, sid, issue["document_id"])
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest, settings)
        if replay is not None:
            if body.resolution.action == "acknowledged":
                checked = issues.acknowledgement_context(conn, row, document, issue, body)
                if not validation.warning_ack_valid(conn, issue, checked, demo=bool(row["demo"])):
                    raise ApiError(422, "REVALIDATION_REQUIRED", "이전 경고 확인이 더 이상 유효하지 않습니다. 다시 검증해 주세요.")
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
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 200, out.model_dump(), session_id=sid)
    return out
