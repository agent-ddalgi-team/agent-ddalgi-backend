"""POST /sessions · GET/DELETE /sessions/{sid} · PATCH /sessions/{sid}/inputs"""
from __future__ import annotations

import json

from fastapi import APIRouter, Header, Request, Response

from app.access import ensure_owner, require_owner, settings_of
from app.db import connect
from app.errors import ApiError
from app.models import InputsOut, InputsPatch, SessionCreate, SessionDeleteOut, SessionOut
from app.services import idempotency, sessions, sources

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", status_code=201, response_model=SessionOut)
def create_session(request: Request, response: Response, body: SessionCreate,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    settings = settings_of(request)
    owner = ensure_owner(request, response)
    digest = idempotency.body_hash(body.model_dump())
    with connect(settings.db_path) as conn:
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest)
        if replay is not None:
            # 재전송은 이미 쿠키를 가진 소유자만 가능하므로 Set-Cookie를 다시 보낼 필요가 없다.
            return replay
        session = sessions.create(conn, settings, owner, body.brief)
        payload = session.model_dump()
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 201, payload)
    return session


@router.get("/{sid}", response_model=SessionOut)
def get_session(request: Request, sid: str):
    # 조회는 활동으로 치지 않는다(배경 폴링으로 만료가 연장되지 않도록).
    owner = require_owner(request)
    with connect(settings_of(request).db_path) as conn:
        return sessions.get(conn, owner, sid)


@router.delete("/{sid}", response_model=SessionDeleteOut)
def delete_session(request: Request, sid: str):
    settings = settings_of(request)
    owner = require_owner(request)
    with connect(settings.db_path) as conn:
        status, cleanup = sessions.close(conn, settings, owner, sid)
    return SessionDeleteOut(session_id=sid, status=status, cleanup=cleanup)


@router.patch("/{sid}/inputs", response_model=InputsOut)
def patch_inputs(request: Request, sid: str, body: InputsPatch,
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
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 200, out.model_dump())
    return out
