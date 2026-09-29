"""Proposal 저장·조회·상태 전이. 적용/거부 트랜잭션은 라우터가 connect(immediate=True) 안에서 이 함수들을 부른다."""
from __future__ import annotations

import json
import uuid

from app.db import Connection, Row
from app.errors import ApiError
from app.models import Candidate, Operation, ProposalOut
from app.timeutil import now, to_iso
from pydantic import TypeAdapter

_OPS = TypeAdapter(list[Operation])


def image_candidates(request):
    """선택 자료의 실제 사진 목록. 의미 추천이나 AI 호출 없이 명시적 선택을 기다린다."""
    from app.agent_bridge import AgentError, ProposeResult
    from app.models import Block, OpDeleteBlock, OpInsertBlock

    doc = request.document
    if doc.session_id != request.session_id or doc.input_revision != request.input_revision:
        raise AgentError("INPUT_REVISION_CONFLICT", "현재 자료와 문서 기준으로 다시 요청해 주세요.")
    targets = [(p, b) for p in doc.pages for b in p.blocks if b.block_id in request.target_block_ids]
    if len(request.target_block_ids) != 1 or len(targets) != 1:
        raise AgentError("UNSUPPORTED_PROPOSAL", "사진을 넣거나 교체할 블록 하나를 선택해 주세요.")
    page, target = targets[0]
    new_id = f"{target.block_id}_img_{uuid.uuid4().hex[:8]}"
    candidates, seen = [], set()
    for source in request.sources:
        for n, aid in enumerate(source.asset_ids, 1):
            if aid in seen:
                continue
            seen.add(aid)
            locator = source.asset_locators.get(aid, {})
            location = (f"PPT {locator['slide']}쪽 · " if locator.get("slide") else
                        f"PDF {locator['page']}쪽 · " if locator.get("page") else "")
            caption = source.asset_descriptions.get(aid, {}).get("caption") or "자료 사진"
            block = Block(block_id=new_id, type="image", content={
                "asset_id": aid, "alt": caption, "caption": caption, "fit": "contain"})
            ops = [OpInsertBlock(op="insert_block", page_id=page.page_id,
                                 after_block_id=target.block_id, block=block)]
            # 먼저 뒤에 삽입하고 기존 사진/자리를 지워 순서를 보존한다. 이전 설명·근거는 물려주지 않는다.
            if target.type in {"image", "image_placeholder"}:
                ops.append(OpDeleteBlock(op="delete_block", block_id=target.block_id))
            candidates.append(Candidate(candidate_id=f"cand_{len(candidates) + 1:02d}",
                                        label=f"{source.name} · {location}사진 {n}", changes=ops))
    if not candidates:
        raise AgentError("NO_IMAGE_CANDIDATES", "선택한 자료에 사용할 사진이 없습니다. 사진 자료를 선택한 뒤 다시 점검해 주세요.")
    return ProposeResult(changes=[], rationale="선택한 자료의 사진입니다. 적합성 순위가 아니며, 적용 후 설명을 입력하고 다시 검증해 주세요.",
                         candidates=candidates)


def save(conn: Connection, session_id: str, document_id: str, base_document_revision: int,
         base_input_revision: int, target_block_ids: list[str], kind: str, instruction: str,
         changes: list[Operation], rationale: str, candidates: list[Candidate] | None, status: str) -> str:
    proposal_id = f"prop_{uuid.uuid4().hex[:16]}"
    stamp = to_iso(now())
    conn.execute(
        "INSERT INTO proposals (proposal_id, session_id, document_id, base_document_revision, base_input_revision, "
        "target_block_ids, kind, instruction, changes_json, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (proposal_id, session_id, document_id, base_document_revision, base_input_revision,
         json.dumps(target_block_ids), kind, instruction,
         json.dumps({"changes": [c.model_dump() for c in changes], "rationale": rationale,
                     "candidates": [c.model_dump() for c in candidates] if candidates is not None else None},
                    ensure_ascii=False),
         status, stamp, stamp))
    return proposal_id


def get_row(conn: Connection, session_id: str, proposal_id: str) -> Row:
    row = conn.execute("SELECT * FROM proposals WHERE proposal_id=? AND session_id=?",
                       (proposal_id, session_id)).fetchone()
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    return row


def to_out(row: Row) -> ProposalOut:
    payload = json.loads(row["changes_json"])
    return ProposalOut(
        proposal_id=row["proposal_id"], document_id=row["document_id"],
        base_document_revision=row["base_document_revision"], base_input_revision=row["base_input_revision"],
        target_block_ids=json.loads(row["target_block_ids"]), kind=row["kind"], instruction=row["instruction"],
        changes=_OPS.validate_python(payload["changes"]), rationale=payload["rationale"],
        candidates=[Candidate.model_validate(c) for c in payload["candidates"]] if payload.get("candidates") is not None else None,
        status=row["status"], applied_revision=row["applied_revision"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def set_status(conn: Connection, proposal_id: str, status: str, applied_revision: int | None = None) -> None:
    conn.execute("UPDATE proposals SET status=?, applied_revision=COALESCE(?, applied_revision), updated_at=? WHERE proposal_id=?",
                 (status, applied_revision, to_iso(now()), proposal_id))


def operations_for_apply(out: ProposalOut, selected_candidate_id: str | None) -> list[Operation]:
    """적용할 연산을 고른다. 후보가 있는 편집안은 선택이 필요하다(선택 전 문서를 바꾸지 않음)."""
    if out.candidates is not None:
        if selected_candidate_id is None:
            raise ApiError(422, "CANDIDATE_REQUIRED", "후보 중 하나를 선택해야 적용할 수 있습니다.",
                           details={"candidate_ids": [c.candidate_id for c in out.candidates]})
        for c in out.candidates:
            if c.candidate_id == selected_candidate_id:
                return c.changes
        raise ApiError(422, "CANDIDATE_REQUIRED", "선택한 후보가 이 편집안에 없습니다.",
                       details={"selected_candidate_id": selected_candidate_id,
                                "candidate_ids": [c.candidate_id for c in out.candidates]})
    return out.changes
