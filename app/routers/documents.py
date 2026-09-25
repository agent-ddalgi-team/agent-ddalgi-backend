"""문서 API.

GET   /sessions/{sid}/documents/{did}            현재 버전(BE-04)
PATCH /sessions/{sid}/documents/{did}            직접 편집: expected_revision + operations[] — 원자적, 실패 시 무변경
POST  /sessions/{sid}/documents/{did}/restore    과거 버전 내용을 새 버전으로(승인·검증 상태는 복원하지 않음)
POST  /sessions/{sid}/documents/{did}/proposals  AI 편집안 생성 Job(적용 전 문서 불변)

멱등 재전송도 소유자·세션·문서 접근 검사를 먼저 통과해야 최초 응답을 돌려준다.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Header, Request
from fastapi.responses import JSONResponse

from app.access import require_owner, settings_of
from app.db import connect
from app.errors import ApiError
from app.models import DocumentChangeOut, DocumentOut, DocumentPatch, JobAccepted, ProposalCreate, RestoreBody
from app.services import ai_jobs, documents, idempotency, jobs, refs, sessions
from app.services.doc_ops import OpError, apply_operations

router = APIRouter(prefix="/sessions/{sid}/documents", tags=["documents"])


def _op_error(exc: OpError) -> ApiError:
    # 계약 확인 ⑭: 연산 실패 코드가 계약에 없어 422 INVALID_OPERATION으로 임시 배정.
    return ApiError(422, "INVALID_OPERATION", f"{exc.op} 연산을 적용할 수 없습니다: {exc.reason}",
                    details={"index": exc.index, "op": exc.op, "reason": exc.reason, **exc.details})


def _require_current_input(row, document) -> None:
    if document.input_revision != row["input_revision"]:
        raise ApiError(409, "INPUT_REVISION_CONFLICT",
                       "자료·목적이 바뀐 뒤의 문서 편집은 영향 검사 후에 가능합니다. 사전 점검을 다시 실행해 주세요.",
                       details={"document_input_revision": document.input_revision,
                                "current_input_revision": row["input_revision"]})


@router.get("/{did}", response_model=DocumentOut)
def get_document(request: Request, sid: str, did: str):
    owner = require_owner(request)
    with connect(settings_of(request).db_path) as conn:
        sessions.load_active(conn, owner, sid)
        return DocumentOut(document=documents.get_current(conn, sid, did), validation=None, approval=None)


@router.patch("/{did}", response_model=DocumentChangeOut)
def patch_document(request: Request, sid: str, did: str, body: DocumentPatch,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    settings = settings_of(request)
    owner = require_owner(request)
    digest = idempotency.body_hash(body.model_dump())
    with connect(settings.db_path, immediate=True) as conn:
        row = sessions.load_active(conn, owner, sid)
        current = documents.get_current(conn, sid, did)
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest)
        if replay is not None:
            return replay
        _require_current_input(row, current)
        if body.expected_revision != current.document_revision:
            raise ApiError(409, "DOCUMENT_REVISION_CONFLICT", "문서가 변경되었습니다. 최신 문서에서 다시 요청해 주세요.",
                           details={"expected_revision": body.expected_revision,
                                    "current_revision": current.document_revision})
        try:
            new_pages = apply_operations(current.pages, body.operations)
        except OpError as exc:
            raise _op_error(exc) from exc
        if (problem := refs.check_pages(new_pages, refs.load(conn, sid))) is not None:
            raise ApiError(422, "INVALID_OPERATION", "삽입한 내용의 참조가 이 세션 자료에 없습니다.",
                           details={"reason": problem})
        status = documents.next_status(current.status)
        new_revision = documents.add_revision(conn, sid, did, body.expected_revision, current.input_revision,
                                              current.title, new_pages, status, "user_edit", None)
        sessions.touch(conn, settings, row)
        out = DocumentChangeOut(document_id=did, document_revision=new_revision, input_revision=current.input_revision,
                                status=status, validation_job_id=None)
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 200, out.model_dump())
    return out


@router.post("/{did}/restore", response_model=DocumentChangeOut)
def restore_document(request: Request, sid: str, did: str, body: RestoreBody,
                     idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    settings = settings_of(request)
    owner = require_owner(request)
    digest = idempotency.body_hash(body.model_dump())
    with connect(settings.db_path, immediate=True) as conn:
        row = sessions.load_active(conn, owner, sid)
        current = documents.get_current(conn, sid, did)
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest)
        if replay is not None:
            return replay
        if body.expected_revision != current.document_revision:
            raise ApiError(409, "DOCUMENT_REVISION_CONFLICT", "문서가 변경되었습니다. 최신 문서에서 다시 요청해 주세요.",
                           details={"expected_revision": body.expected_revision,
                                    "current_revision": current.document_revision})
        if body.restore_from_revision == current.document_revision:
            raise ApiError(422, "INVALID_OPERATION", "현재 버전은 복원 대상이 될 수 없습니다.",
                           details={"restore_from_revision": body.restore_from_revision})
        old = documents.get_revision(conn, sid, did, body.restore_from_revision)
        # 현재 자료와의 정합성: 그 사이 지워진 자료·사진을 가리키면 복원하지 않는다(계약 3절 되돌리기).
        if (problem := refs.check_pages(old.pages, refs.load(conn, sid))) is not None:
            raise ApiError(422, "RESTORE_REFERENCE_INVALID",
                           "복원하려는 버전이 지금은 없는 자료를 참조합니다. 자료를 다시 올리거나 다른 버전을 고르세요.",
                           details={"restore_from_revision": body.restore_from_revision, "reason": problem})
        # 과거 승인·검증 완료 상태는 부활시키지 않는다. 입력 버전은 현재 세션 기준으로 잇는다.
        status = documents.next_status(old.status)
        new_revision = documents.add_revision(conn, sid, did, body.expected_revision, row["input_revision"],
                                              old.title, old.pages, status, "restore", str(body.restore_from_revision))
        sessions.touch(conn, settings, row)
        out = DocumentChangeOut(document_id=did, document_revision=new_revision, input_revision=row["input_revision"],
                                status=status, validation_job_id=None)
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 200, out.model_dump())
    return out


@router.post("/{did}/proposals", status_code=202, response_model=JobAccepted)
def create_proposal(request: Request, sid: str, did: str, body: ProposalCreate, background_tasks: BackgroundTasks,
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
                           details={"expected_revision": body.expected_revision,
                                    "current_revision": current.document_revision})
        if body.input_revision != row["input_revision"]:
            raise ApiError(409, "INPUT_REVISION_CONFLICT", "입력이 변경되었습니다. 최신 상태를 불러온 뒤 다시 요청해 주세요.",
                           details={"expected_input_revision": body.input_revision,
                                    "current_input_revision": row["input_revision"]})
        _require_current_input(row, current)
        known = {b.block_id for p in current.pages for b in p.blocks}
        missing = [b for b in body.target_block_ids if b not in known]
        if missing:
            raise ApiError(422, "INVALID_OPERATION", "선택한 블록이 문서에 없습니다.", details={"missing_block_ids": missing})
        job = jobs.create(conn, sid, "propose", "편집안 생성 대기 중", input_revision=row["input_revision"])
        sessions.touch(conn, settings, row)
        out = JobAccepted(job_id=job.job_id, status="queued", kind="propose", session_id=sid, created_at=job.created_at)
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 202, out.model_dump())
    background_tasks.add_task(ai_jobs.run_propose_job, settings, sid, job.job_id, row["input_revision"], did,
                              current.document_revision, list(body.target_block_ids), body.instruction, body.kind)
    return JSONResponse(status_code=202, content=out.model_dump())
