"""POST /sessions/{sid}/documents/{did}/approvals — 승인 7조건을 한 트랜잭션에서 검사·생성·멱등 저장."""
from __future__ import annotations

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from app.access import require_owner, settings_of
from app.db import connect
from app.models import ApprovalCreate, ApprovalOut
from app.services import approvals, documents, idempotency, sessions

router = APIRouter(prefix="/sessions/{sid}/documents/{did}/approvals", tags=["approvals"])


@router.post("", status_code=201, response_model=ApprovalOut)
def create_approval(request: Request, sid: str, did: str, body: ApprovalCreate,
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    settings = settings_of(request)
    owner = require_owner(request)
    digest = idempotency.body_hash(body.model_dump())
    with connect(settings.db_path, immediate=True) as conn:
        row = sessions.load_active(conn, owner, sid)          # ① 세션 유효·접근 가능
        document = documents.get_current(conn, sid, did)
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest)
        if replay is not None:
            return replay                                       # 최초 성공 응답. invalidated를 되살리지 않는다
        v, manifest = approvals.check_conditions(conn, row, document, body)   # ②~⑦
        existing = approvals.active_for(conn, did, document.document_revision, row["input_revision"])
        if existing is not None and existing["format"] == body.format:
            out = approvals.to_out(existing)                    # 같은 버전·형식의 active 승인은 하나만
        else:
            out = approvals.create(conn, row, owner, document, body, manifest)
        documents.refresh_status_cache(conn, sid, did)
        sessions.touch(conn, settings, row)
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 201, out.model_dump())
    return JSONResponse(status_code=201, content=out.model_dump())
