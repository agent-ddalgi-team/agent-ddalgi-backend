"""POST /sessions · GET/DELETE /sessions/{sid} · PATCH /sessions/{sid}/inputs"""
from __future__ import annotations

import json

from fastapi import APIRouter, Header, Request, Response

from app.access import ensure_owner, require_owner, settings_of
from app.db import connect
from app.errors import ApiError
from app.models import InputsOut, InputsPatch, SessionCreate, SessionDeleteOut, SessionOut
from app.services import cleanup, idempotency, sessions, sources

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", status_code=201, response_model=SessionOut)
def create_session(request: Request, response: Response, body: SessionCreate,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    settings = settings_of(request)
    owner = ensure_owner(request, response)
    digest = idempotency.body_hash(body.model_dump())
    with connect(settings.db_path) as conn:
        # 연결된 세션이 종료·만료됐으면 최초 응답(brief)을 돌려주지 않고 410(만료 확정·정리 등록 포함, BE-09).
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest, settings)
        if replay is not None:
            # 재전송은 이미 쿠키를 가진 소유자만 가능하므로 Set-Cookie를 다시 보낼 필요가 없다.
            return replay
        session = sessions.create(conn, settings, owner, body.brief)
        payload = session.model_dump()
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 201, payload, session_id=session.session_id)
    return session


@router.get("/{sid}", response_model=SessionOut)
def get_session(request: Request, sid: str):
    # 조회는 활동으로 치지 않는다(배경 폴링으로 만료가 연장되지 않도록).
    settings = settings_of(request)
    owner = require_owner(request)
    with connect(settings.db_path) as conn:
        return sessions.get(conn, owner, sid, settings)


@router.delete("/{sid}", response_model=SessionDeleteOut)
def delete_session(request: Request, sid: str):
    settings = settings_of(request)
    owner = require_owner(request)
    with connect(settings.db_path, immediate=True) as conn:
        sessions.close(conn, settings, owner, sid)      # 확정 트랜잭션: 상태·내용 제거·Job 취소·Export 확정·멱등 본문 비움·큐 등록 → 커밋
    cleanup.run_for_session(settings, sid)             # 커밋 뒤 바이트 삭제 즉시 시도(실패해도 큐가 백오프로 재시도)
    with connect(settings.db_path, immediate=True) as conn:
        state = cleanup.verify_state(conn, settings, sid)   # done = 내용 제거 + 폴더 없음 + 미완료 작업 없음
    return SessionDeleteOut(session_id=sid, status="closed", cleanup=state)


@router.patch("/{sid}/inputs", response_model=InputsOut)
def patch_inputs(request: Request, sid: str, body: InputsPatch,
                 idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    settings = settings_of(request)
    owner = require_owner(request)
    digest = idempotency.body_hash(body.model_dump())
    # 세션 상태 검사·버전 검사·내용 변경·멱등 저장을 BEGIN IMMEDIATE 한 트랜잭션으로(BE-09 리뷰 1): 종료 확정과 직렬화돼
    # 종료가 먼저면 410, PATCH가 먼저면 종료(finalize)가 그 결과까지 제거한다. purged 세션에 brief·응답이 다시 저장되지 않는다.
    with connect(settings.db_path, immediate=True) as conn:
        row = sessions.load_active(conn, owner, sid, settings)  # 멱등 재전송도 소유자·세션 검사를 먼저 통과해야 한다
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest, settings)
        if replay is not None:
            return replay
        if body.expected_input_revision != row["input_revision"]:
            raise ApiError(
                409, "INPUT_REVISION_CONFLICT",
                "입력이 변경되었습니다. 최신 상태를 불러온 뒤 다시 요청해 주세요.",
                details={"expected_input_revision": body.expected_input_revision,
                         "current_input_revision": row["input_revision"]},
            )
        if body.selected_source_ids is not None:
            missing = sources.exist_in_session(conn, sid, body.selected_source_ids)
            if missing:
                raise ApiError(404, "RESOURCE_NOT_FOUND", "선택한 자료 중 이 세션에 없는 것이 있습니다.",
                               details={"missing_source_ids": missing})
        new_revision = sessions.bump_input_revision(
            conn, sid, brief=body.brief, selected_source_ids=body.selected_source_ids)
        sessions.touch(conn, settings, row)
        selected = conn.execute("SELECT selected_source_ids FROM sessions WHERE session_id=?", (sid,)).fetchone()[0]
        out = InputsOut(session_id=sid, input_revision=new_revision,
                        selected_source_ids=json.loads(selected), preflight_invalidated=True)
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 200, out.model_dump(), session_id=sid)
    return out
