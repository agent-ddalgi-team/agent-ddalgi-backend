"""GET /sessions/{sid}/proposals/{pid}(계약 확인 ⑯) · POST .../apply · POST .../reject

apply는 하나의 쓰기 트랜잭션(BEGIN IMMEDIATE) 안에서: 편집안 상태 확인 → 기준 버전 확인(다르면 stale 저장 후 409)
→ 연산 적용 → 새 문서 버전 → applied 전환 → 멱등 응답 저장. 멱등 재전송은 접근 검사를 통과한 뒤 최초 응답을 돌려준다.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, Request

from app.access import require_owner, settings_of
from app.db import connect
from app.errors import ApiError
from app.models import ApplyBody, DocumentChangeOut, ProposalOut, ProposalStatusOut
from app.services import documents, idempotency, proposals, refs, sessions
from app.services.doc_ops import OpError, apply_operations

router = APIRouter(prefix="/sessions/{sid}/proposals", tags=["proposals"])


def _not_applicable(row) -> ApiError:
    # 계약 확인 ⑱: applied/rejected/stale 모두 409 PROPOSAL_STALE로 답하고 details.status로 구분.
    return ApiError(409, "PROPOSAL_STALE", "이 편집안은 더 이상 적용할 수 없습니다. 최신 문서에서 다시 요청해 주세요.",
                    details={"status": row["status"], "applied_revision": row["applied_revision"]})


@router.get("/{pid}", response_model=ProposalOut)
def get_proposal(request: Request, sid: str, pid: str):
    owner = require_owner(request)
    with connect(settings_of(request).db_path) as conn:
        sessions.load_active(conn, owner, sid)
        return proposals.to_out(proposals.get_row(conn, sid, pid))


@router.post("/{pid}/apply", response_model=DocumentChangeOut)
def apply_proposal(request: Request, sid: str, pid: str, body: ApplyBody,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    settings = settings_of(request)
    owner = require_owner(request)
    digest = idempotency.body_hash(body.model_dump())
    with connect(settings.db_path, immediate=True) as conn:
        row = sessions.load_active(conn, owner, sid)
        prop = proposals.get_row(conn, sid, pid)
        documents.get_current(conn, sid, prop["document_id"])  # 문서 접근 검사
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest)
        if replay is not None:
            return replay
        if prop["status"] != "proposed":
            raise _not_applicable(prop)
        current = documents.get_current(conn, sid, prop["document_id"])
        if prop["base_document_revision"] != current.document_revision or prop["base_input_revision"] != row["input_revision"]:
            # 기준이 바뀐 편집안은 stale로 남긴다. 오류 응답 때문에 이 상태 변경이 롤백되지 않도록 먼저 커밋한다.
            proposals.set_status(conn, pid, "stale")
            conn.commit()
            raise ApiError(409, "PROPOSAL_STALE", "문서 또는 입력이 바뀌어 이 편집안은 적용할 수 없습니다. 다시 요청해 주세요.",
                           details={"status": "stale", "base_document_revision": prop["base_document_revision"],
                                    "current_revision": current.document_revision,
                                    "base_input_revision": prop["base_input_revision"],
                                    "current_input_revision": row["input_revision"]})
        if body.expected_revision != current.document_revision:
            raise ApiError(409, "DOCUMENT_REVISION_CONFLICT", "문서가 변경되었습니다. 최신 문서에서 다시 요청해 주세요.",
                           details={"expected_revision": body.expected_revision,
                                    "current_revision": current.document_revision})
        out_prop = proposals.to_out(prop)
        ops = proposals.operations_for_apply(out_prop, body.selected_candidate_id)
        try:
            new_pages = apply_operations(current.pages, ops)
        except OpError as exc:
            raise ApiError(422, "INVALID_OPERATION", f"{exc.op} 연산을 적용할 수 없습니다: {exc.reason}",
                           details={"index": exc.index, "op": exc.op, "reason": exc.reason}) from exc
        if (problem := refs.check_pages(new_pages, refs.load(conn, sid))) is not None:
            raise ApiError(422, "INVALID_OPERATION", "편집안의 참조가 이 세션 자료에 없습니다.", details={"reason": problem})
        status = documents.next_status(current.status)
        new_revision = documents.add_revision(conn, sid, prop["document_id"], body.expected_revision,
                                              current.input_revision, current.title, new_pages, status,
                                              "proposal_apply", pid)
        proposals.set_status(conn, pid, "applied", applied_revision=new_revision)
        sessions.touch(conn, settings, row)
        out = DocumentChangeOut(document_id=prop["document_id"], document_revision=new_revision,
                                input_revision=current.input_revision, status=status, validation_job_id=None)
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 200, out.model_dump())
    return out


@router.post("/{pid}/reject", response_model=ProposalStatusOut)
def reject_proposal(request: Request, sid: str, pid: str):
    settings = settings_of(request)
    owner = require_owner(request)
    with connect(settings.db_path, immediate=True) as conn:
        row = sessions.load_active(conn, owner, sid)
        prop = proposals.get_row(conn, sid, pid)
        current = documents.get_current(conn, sid, prop["document_id"])
        if prop["status"] == "applied":
            raise _not_applicable(prop)
        if prop["status"] != "rejected":
            proposals.set_status(conn, pid, "rejected")
        sessions.touch(conn, settings, row)
    return ProposalStatusOut(proposal_id=pid, status="rejected", document_id=prop["document_id"],
                             document_revision=current.document_revision)
