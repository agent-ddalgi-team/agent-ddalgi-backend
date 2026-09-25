"""POST /sessions/{sid}/drafts — 사용자가 사전 점검을 확인한 뒤 초안 생성 Job."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Header, Request
from fastapi.responses import JSONResponse

from app.access import require_owner, settings_of
from app.db import connect
from app.errors import ApiError
from app.models import DraftCreate, JobAccepted
from app.services import ai_jobs, documents, idempotency, jobs, preflights, sessions

router = APIRouter(prefix="/sessions/{sid}/drafts", tags=["drafts"])


@router.post("", status_code=202, response_model=JobAccepted)
def create_draft(request: Request, sid: str, body: DraftCreate, background_tasks: BackgroundTasks,
                 idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    settings = settings_of(request)
    owner = require_owner(request)
    digest = idempotency.body_hash(body.model_dump())
    with connect(settings.db_path) as conn:
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest)
        if replay is not None:
            return replay
        row = sessions.load_active(conn, owner, sid)
        if body.input_revision != row["input_revision"]:
            raise ApiError(409, "INPUT_REVISION_CONFLICT", "입력이 변경되었습니다. 사전 점검을 다시 실행해 주세요.",
                           details={"expected_input_revision": body.input_revision,
                                    "current_input_revision": row["input_revision"]})
        preflight = preflights.get(conn, sid, body.preflight_id)
        if preflight.input_revision != row["input_revision"]:
            raise ApiError(409, "INPUT_REVISION_CONFLICT", "사전 점검이 이전 입력 기준입니다. 다시 실행해 주세요.",
                           details={"preflight_input_revision": preflight.input_revision,
                                    "current_input_revision": row["input_revision"]})
        if not body.confirmed:
            raise ApiError(422, "PREFLIGHT_NOT_CONFIRMED", "사전 점검 결과를 확인한 뒤 생성할 수 있습니다.",
                           details={"preflight_id": body.preflight_id})
        if not preflight.can_generate:
            raise ApiError(422, "NO_USABLE_TEXT", "텍스트 근거가 있는 자료가 없어 초안을 만들 수 없습니다.",
                           details={"preflight_id": body.preflight_id, "needed": preflight.recommendations.needed})
        if (existing := documents.exists_for_session(conn, sid)) is not None:
            raise ApiError(409, "DOCUMENT_EXISTS", "이미 초안이 있습니다. 편집 화면에서 이어가 주세요.",
                           details={"document_id": existing})
        preflights.confirm(conn, body.preflight_id)
        active = jobs.find_active(conn, sid, "draft", row["input_revision"])
        if active is not None:
            out = JobAccepted(job_id=active.job_id, status=active.status, kind="draft", session_id=sid,
                              created_at=active.created_at)
            return JSONResponse(status_code=202, content=out.model_dump())
        job = jobs.create(conn, sid, "draft", "초안 생성 대기 중", input_revision=row["input_revision"])
        sessions.touch(conn, settings, row)
        out = JobAccepted(job_id=job.job_id, status="queued", kind="draft", session_id=sid, created_at=job.created_at)
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 202, out.model_dump())
    background_tasks.add_task(ai_jobs.run_draft_job, settings, sid, job.job_id, row["input_revision"], body.preflight_id)
    return JSONResponse(status_code=202, content=out.model_dump())
