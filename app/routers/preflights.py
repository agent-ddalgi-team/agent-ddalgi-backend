"""POST /sessions/{sid}/preflights · GET /sessions/{sid}/preflights/{pid} (조회 경로는 계약 확인 ⑤ 백엔드 제안)"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Header, Request
from fastapi.responses import JSONResponse

from app.access import require_owner, settings_of
from app.db import connect
from app.errors import ApiError
from app.models import Brief, JobAccepted, PreflightCreate, PreflightOut, PreflightReviewCreate
from app.services import ai_jobs, idempotency, jobs, preflights, sessions

router = APIRouter(prefix="/sessions/{sid}/preflights", tags=["preflights"])


@router.post("", status_code=202, response_model=JobAccepted)
def create_preflight(request: Request, sid: str, body: PreflightCreate, background_tasks: BackgroundTasks,
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    settings = settings_of(request)
    owner = require_owner(request)
    digest = idempotency.body_hash(body.model_dump())
    with connect(settings.db_path, immediate=True) as conn:   # 세션 검사부터 Job 생성·멱등 저장까지 한 잠금(BE-09 리뷰 1)
        row = sessions.load_active(conn, owner, sid, settings)  # 멱등 재전송도 소유자·세션 검사를 먼저 통과해야 한다
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest, settings)
        if replay is not None:
            return replay
        if body.expected_input_revision != row["input_revision"]:
            raise ApiError(409, "INPUT_REVISION_CONFLICT", "입력이 변경되었습니다. 최신 상태를 불러온 뒤 다시 요청해 주세요.",
                           details={"expected_input_revision": body.expected_input_revision,
                                    "current_input_revision": row["input_revision"]})
        # 같은 입력 버전으로 이미 도는 점검이 있으면 새로 만들지 않는다(중복 실행 방지).
        active = jobs.find_active(conn, sid, "preflight", row["input_revision"])
        if active is not None:
            out = JobAccepted(job_id=active.job_id, status=active.status, kind="preflight", session_id=sid,
                              created_at=active.created_at)
            idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 202,
                                 out.model_dump(), session_id=sid)
            return JSONResponse(status_code=202, content=out.model_dump())
        job = jobs.create(conn, sid, "preflight", "사전 점검 대기 중", input_revision=row["input_revision"])
        sessions.touch(conn, settings, row)
        out = JobAccepted(job_id=job.job_id, status="queued", kind="preflight", session_id=sid, created_at=job.created_at)
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 202, out.model_dump(), session_id=sid)
    background_tasks.add_task(ai_jobs.run_preflight_job, settings, sid, job.job_id, row["input_revision"])
    return JSONResponse(status_code=202, content=out.model_dump())


@router.get("/{pid}", response_model=PreflightOut)
def get_preflight(request: Request, sid: str, pid: str):
    settings = settings_of(request)
    owner = require_owner(request)
    with connect(settings.db_path) as conn:
        row = sessions.load_active(conn, owner, sid, settings)
        result = preflights.get(conn, sid, pid)
        if result.input_revision == row["input_revision"]:
            result = preflights.apply_draft_readiness(result, Brief.model_validate_json(row["brief_json"]), settings.agent_mode)
            result.latest_preflight_id = preflights.latest_id(conn, sid, row["input_revision"])
            if result.latest_preflight_id != pid:
                result = result.model_copy(deep=True)
                result.can_generate = False
                result.recommendations.needed.append("새로운 점검 결과가 있습니다. 상태를 새로고침하고 최신 점검을 확인해 주세요.")
        return result


@router.post("/{pid}/reviews", response_model=PreflightOut)
def review_preflight(request: Request, sid: str, pid: str, body: PreflightReviewCreate,
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    settings, owner = settings_of(request), require_owner(request)
    digest = idempotency.body_hash(body.model_dump())
    with connect(settings.db_path, immediate=True) as conn:
        row = sessions.load_active(conn, owner, sid, settings)
        if body.expected_input_revision != row["input_revision"]:
            raise ApiError(409, "INPUT_REVISION_CONFLICT", "입력이 변경되었습니다. 최신 점검을 불러와 주세요.")
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest, settings)
        if replay is not None:
            return replay
        basis = preflights.get(conn, sid, pid)
        if basis.input_revision != row["input_revision"] or preflights.latest_id(conn, sid, row["input_revision"]) != pid:
            raise ApiError(409, "INPUT_REVISION_CONFLICT", "최신 점검에서 항목을 처리해 주세요.",
                           details={"latest_preflight_id": preflights.latest_id(conn, sid, row["input_revision"])})
        if conn.execute("SELECT 1 FROM jobs WHERE session_id=? AND status IN ('queued','running') "
                        "AND kind IN ('preflight','draft','validate','proposal','impact_review')", (sid,)).fetchone():
            raise ApiError(409, "INPUT_REVISION_CONFLICT", "진행 중인 문서 작업이 끝난 뒤 항목을 처리해 주세요.")
        next_id = preflights.review(conn, row, pid, body, owner)
        result = preflights.get(conn, sid, next_id)
        if next_id != pid:
            from app.services import approvals, validation, documents
            bridge = ai_jobs.get_bridge(settings)
            if (wait := getattr(bridge, "wait_for_confirmation", None)) is not None:
                wait(conn, sid, row["input_revision"], next_id)
            for head in conn.execute("SELECT document_id FROM documents WHERE session_id=?", (sid,)).fetchall():
                document = documents.get_current(conn, sid, head["document_id"])
                validation.record_preflight_conflicts(conn, sid, document, result)
                approvals.invalidate_for_document(conn, document.document_id, "preflight_changed")
                documents.refresh_status_cache(conn, sid, document.document_id)
        result = preflights.apply_draft_readiness(result, Brief.model_validate_json(row["brief_json"]), settings.agent_mode)
        result.latest_preflight_id = next_id
        sessions.touch(conn, settings, row)
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 200,
                             result.model_dump(), session_id=sid)
        return result
