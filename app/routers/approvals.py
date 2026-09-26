"""POST /sessions/{sid}/documents/{did}/approvals — 승인 7조건을 한 트랜잭션에서 검사·생성·멱등 저장."""
from __future__ import annotations

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from app.access import require_owner, settings_of
from app.db import connect
from app.models import ApprovalCreate, ApprovalOut
from app.services import approvals, documents, idempotency, publication, sessions

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
        # 등록 사진 공개 허가는 멱등 재전송보다 먼저 현재 값을 본다(BE-08). 깨졌으면 캐시 응답을 돌려주지 않고
        # 조건 ②~⑦을 다시 검사한다(오류 순서는 BE-06 그대로: ④ 내용 검증 → ⑥ 배치·허가).
        pub_now = publication.check_document(conn, document)
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest) if pub_now.ok else None
        if replay is not None:
            return replay                                       # 최초 성공 응답. invalidated를 되살리지 않는다
        v, manifest, lc_row, pub = approvals.check_conditions(conn, row, document, body)   # ②~⑦
        existing = approvals.find_matching_active(conn, did, document.document_revision, row["input_revision"], body)
        if existing is not None:
            out = approvals.to_out(existing)                    # 같은 버전·형식·검증·배치 검사(=같은 artifact)의 active 승인은 하나만
        else:
            # 다른 배치 검사를 명시적으로 승인: 같은 형식의 이전 승인은 superseded. 재검사만으로는 기존 승인이 바뀌지 않는다.
            approvals.supersede_active(conn, did, document.document_revision, row["input_revision"], body.format)
            out = approvals.create(conn, row, owner, document, body, manifest, lc_row, pub)
        documents.refresh_status_cache(conn, sid, did)
        sessions.touch(conn, settings, row)
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 201, out.model_dump())
    return JSONResponse(status_code=201, content=out.model_dump())
