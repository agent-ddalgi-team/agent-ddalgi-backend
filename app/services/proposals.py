"""Proposal 저장·조회·상태 전이. 적용/거부 트랜잭션은 라우터가 connect(immediate=True) 안에서 이 함수들을 부른다."""
from __future__ import annotations

import json
import sqlite3
import uuid

from app.errors import ApiError
from app.models import Candidate, Operation, ProposalOut
from app.timeutil import now, to_iso
from pydantic import TypeAdapter

_OPS = TypeAdapter(list[Operation])


def save(conn: sqlite3.Connection, session_id: str, document_id: str, base_document_revision: int,
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


def get_row(conn: sqlite3.Connection, session_id: str, proposal_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM proposals WHERE proposal_id=? AND session_id=?",
                       (proposal_id, session_id)).fetchone()
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    return row


def to_out(row: sqlite3.Row) -> ProposalOut:
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


def set_status(conn: sqlite3.Connection, proposal_id: str, status: str, applied_revision: int | None = None) -> None:
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
