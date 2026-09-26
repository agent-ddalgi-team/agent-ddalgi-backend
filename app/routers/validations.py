"""POST /sessions/{sid}/documents/{did}/validate (202 Job) · GET /sessions/{sid}/documents/{did}/issues (계약 확인 ㉒)"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Header, Request
from fastapi.responses import JSONResponse

from app.access import require_owner, settings_of
from app.db import connect
from app.errors import ApiError
from app.models import IssueListOut, JobAccepted, ValidateBody
from app.services import ai_jobs, documents, idempotency, jobs, sessions, validation

router = APIRouter(prefix="/sessions/{sid}/documents/{did}", tags=["validation"])


@router.post("/validate", status_code=202, response_model=JobAccepted)
def validate_document(request: Request, sid: str, did: str, body: ValidateBody, background_tasks: BackgroundTasks,
                      idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    settings = settings_of(request)
    owner = require_owner(request)
    digest = idempotency.body_hash(body.model_dump())
    with connect(settings.db_path) as conn:
        row = sessions.load_active(conn, owner, sid)
        current = documents.get_current(conn, sid, did)
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest)
        if replay is not None:
            return replay
        if body.expected_revision != current.document_revision:
            raise ApiError(409, "DOCUMENT_REVISION_CONFLICT", "문서가 변경되었습니다. 최신 문서에서 다시 요청해 주세요.",
                           details={"expected_revision": body.expected_revision, "current_revision": current.document_revision})
        if body.input_revision != row["input_revision"] or current.input_revision != row["input_revision"]:
            raise ApiError(409, "INPUT_REVISION_CONFLICT", "입력이 변경되었습니다. 사전 점검부터 다시 진행해 주세요.",
                           details={"requested_input_revision": body.input_revision,
                                    "document_input_revision": current.input_revision,
                                    "current_input_revision": row["input_revision"]})
        key = f"{did}@{current.document_revision}@{row['input_revision']}"
        active = jobs.find_active_by_key(conn, sid, "validate", key)
        if active is not None:
            out = JobAccepted(job_id=active.job_id, status=active.status, kind="validate", session_id=sid, created_at=active.created_at)
            return JSONResponse(status_code=202, content=out.model_dump())
        job = jobs.create(conn, sid, "validate", "검증 대기 중", input_revision=row["input_revision"], target_key=key)
        sessions.touch(conn, settings, row)
        out = JobAccepted(job_id=job.job_id, status="queued", kind="validate", session_id=sid, created_at=job.created_at)
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 202, out.model_dump())
    background_tasks.add_task(ai_jobs.run_validate_job, settings, sid, job.job_id, row["input_revision"], did,
                              current.document_revision)
    return JSONResponse(status_code=202, content=out.model_dump())


@router.get("/issues", response_model=IssueListOut)
def list_issues(request: Request, sid: str, did: str):
    owner = require_owner(request)
    with connect(settings_of(request).db_path) as conn:
        row = sessions.load_active(conn, owner, sid)
        current = documents.get_current(conn, sid, did)
        latest = validation.latest_validation(conn, did, current.document_revision, row["input_revision"])
        return IssueListOut(document_id=did, document_revision=current.document_revision,
                            validation_id=latest["validation_id"] if latest else None,
                            issues=validation.list_issues(conn, did))
