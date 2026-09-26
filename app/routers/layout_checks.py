"""POST /sessions/{sid}/documents/{did}/layout-checks — 배치 검사 Job(kind=layout_check, 202). 결과는 Job.result_ref와
GET /documents/{did}의 layout_checks{pdf,docx}로 본다(새 조회 경로 없음, 계약 확인 ㉟)."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Header, Request
from fastapi.responses import JSONResponse

from app.access import require_owner, settings_of
from app.db import connect
from app.errors import ApiError
from app.models import JobAccepted, LayoutCheckCreate
from app.services import documents, idempotency, jobs, layout_check_jobs, sessions

router = APIRouter(prefix="/sessions/{sid}/documents/{did}/layout-checks", tags=["layout"])


@router.post("", status_code=202, response_model=JobAccepted)
def create_layout_check(request: Request, sid: str, did: str, body: LayoutCheckCreate, background_tasks: BackgroundTasks,
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
        if current.input_revision != row["input_revision"]:
            raise ApiError(409, "INPUT_REVISION_CONFLICT", "입력이 변경되었습니다. 사전 점검부터 다시 진행해 주세요.",
                           details={"document_input_revision": current.input_revision, "current_input_revision": row["input_revision"]})
        key = layout_check_jobs.target_key(did, current.document_revision, row["input_revision"], body.format)
        active = jobs.find_active_by_key(conn, sid, "layout_check", key)
        if active is not None:
            out = JobAccepted(job_id=active.job_id, status=active.status, kind="layout_check", session_id=sid, created_at=active.created_at)
            return JSONResponse(status_code=202, content=out.model_dump())
        job = jobs.create(conn, sid, "layout_check", "배치 검사 대기 중", input_revision=row["input_revision"], target_key=key)
        sessions.touch(conn, settings, row)
        out = JobAccepted(job_id=job.job_id, status="queued", kind="layout_check", session_id=sid, created_at=job.created_at)
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 202, out.model_dump())
    background_tasks.add_task(layout_check_jobs.run_layout_check_job, settings, sid, job.job_id, did, current.document_revision,
                              row["input_revision"], body.format)
    return JSONResponse(status_code=202, content=out.model_dump())
