"""POST /sessions/{sid}/preflights · GET /sessions/{sid}/preflights/{pid} (조회 경로는 계약 확인 ⑤ 백엔드 제안)"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Header, Request
from fastapi.responses import JSONResponse

from app.access import require_owner, settings_of
from app.db import connect
from app.errors import ApiError
from app.models import JobAccepted, PreflightCreate, PreflightOut
from app.services import ai_jobs, idempotency, jobs, preflights, sessions

router = APIRouter(prefix="/sessions/{sid}/preflights", tags=["preflights"])


@router.post("", status_code=202, response_model=JobAccepted)
def create_preflight(request: Request, sid: str, body: PreflightCreate, background_tasks: BackgroundTasks,
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    settings = settings_of(request)
    owner = require_owner(request)
    digest = idempotency.body_hash(body.model_dump())
    with connect(settings.db_path) as conn:
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest)
        if replay is not None:
            return replay
        row = sessions.load_active(conn, owner, sid)
        if body.expected_input_revision != row["input_revision"]:
            raise ApiError(409, "INPUT_REVISION_CONFLICT", "입력이 변경되었습니다. 최신 상태를 불러온 뒤 다시 요청해 주세요.",
                           details={"expected_input_revision": body.expected_input_revision,
                                    "current_input_revision": row["input_revision"]})
        # 같은 입력 버전으로 이미 도는 점검이 있으면 새로 만들지 않는다(중복 실행 방지).
        active = jobs.find_active(conn, sid, "preflight", row["input_revision"])
        if active is not None:
            out = JobAccepted(job_id=active.job_id, status=active.status, kind="preflight", session_id=sid,
                              created_at=active.created_at)
            return JSONResponse(status_code=202, content=out.model_dump())
        job = jobs.create(conn, sid, "preflight", "사전 점검 대기 중", input_revision=row["input_revision"])
        sessions.touch(conn, settings, row)
        out = JobAccepted(job_id=job.job_id, status="queued", kind="preflight", session_id=sid, created_at=job.created_at)
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 202, out.model_dump())
    background_tasks.add_task(ai_jobs.run_preflight_job, settings, sid, job.job_id, row["input_revision"])
    return JSONResponse(status_code=202, content=out.model_dump())


@router.get("/{pid}", response_model=PreflightOut)
def get_preflight(request: Request, sid: str, pid: str):
    owner = require_owner(request)
    with connect(settings_of(request).db_path) as conn:
        sessions.load_active(conn, owner, sid)
        return preflights.get(conn, sid, pid)
