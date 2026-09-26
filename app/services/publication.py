"""등록 사진 공개 허가(BE-08, 6.6-6 후속). 등록 자료(scope=registered) 이미지는 approved_for_external_use=true일 때만 출력할 수 있다.

- null = 미확인(not_decided), false = 불허(denied). 둘 다 차단하되 사유를 구분한다. 세션 업로드 이미지는 해당 없음.
- 배치 검사·승인·Export 요청·발행·다운로드·멱등 재전송에서 **매번 현재 값**을 확인한다(check_document). 이미지 content_hash에는
  허가가 들어 있지 않으므로 asset_manifest_hash 일치만으로 허가 변경을 검출했다고 보지 않는다.
- 허가를 바꾸는 유일한 경로는 등록 자료 재적재(app/services/registered.py --update-publication)이며 공개 API에는 없다.
  값이 바뀌면 on_publication_changed()가 같은 트랜잭션에서 관련 승인을 무효화하고 활성 Export를 확정 실패시킨다.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from app.models import Document
from app.timeutil import now, to_iso

ISSUE_CODE = "IMAGE_PUBLICATION_UNCONFIRMED"   # 계약 확인 ㊱: scope=layout, origin=layout, blocker, 형식 무관


@dataclass(frozen=True)
class PublicationBlock:
    asset_id: str
    block_ids: tuple[str, ...]
    reason: str          # not_decided / denied


@dataclass
class PublicationResult:
    ok: bool
    blocked: list[PublicationBlock] = field(default_factory=list)
    checked_at: str = ""

    def reason_codes(self) -> list[str]:
        return sorted({f"publication:{b.reason}" for b in self.blocked})

    def as_out(self) -> list[dict]:
        return [{"asset_id": b.asset_id, "block_ids": list(b.block_ids), "reason": b.reason} for b in self.blocked]


def _image_refs(document: Document) -> dict[str, list[str]]:
    refs: dict[str, list[str]] = {}
    for page in document.pages:
        for block in page.blocks:
            if block.type == "image":
                refs.setdefault(block.content.get("asset_id"), []).append(block.block_id)
    return refs


def check_document(conn: sqlite3.Connection, document: Document) -> PublicationResult:
    """문서의 image 블록이 가리키는 등록 사진의 현재 허가 값을 본다."""
    blocked: list[PublicationBlock] = []
    for asset_id, block_ids in _image_refs(document).items():
        row = conn.execute("SELECT scope, approved_for_external_use FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
        if row is None or row["scope"] != "registered":
            continue
        value = row["approved_for_external_use"]
        if value == 1:
            continue
        blocked.append(PublicationBlock(asset_id, tuple(block_ids), "not_decided" if value is None else "denied"))
    return PublicationResult(ok=not blocked, blocked=blocked, checked_at=to_iso(now()))


def check_revision(conn: sqlite3.Connection, document_id: str, revision: int) -> PublicationResult:
    """저장된 특정 버전(승인본)의 image 블록 기준. Export·다운로드가 승인 버전으로 확인할 때 쓴다."""
    from app.models import Page

    row = conn.execute("SELECT content_json FROM document_revisions WHERE document_id=? AND revision=?", (document_id, revision)).fetchone()
    if row is None:
        return PublicationResult(ok=False, blocked=[], checked_at=to_iso(now()))
    content = json.loads(row["content_json"])
    pages = [Page.model_validate(p) for p in content["pages"]]
    doc = Document(document_id=document_id, session_id="", document_revision=revision, input_revision=0, title=content["title"],
                   target_pages=1, status="draft", pages=pages)
    return check_document(conn, doc)


def message_for(block: PublicationBlock) -> str:
    if block.reason == "denied":
        return "등록 사진의 외부 공개가 허용되지 않았습니다(불허). 사진을 바꾸거나 문서에서 제거한 뒤 배치 검사를 다시 실행하세요."
    return "등록 사진의 외부 공개 허가가 확인되지 않았습니다(미확인). 담당자가 허가를 확인하거나 사진을 제거한 뒤 배치 검사를 다시 실행하세요."


def on_publication_changed(conn: sqlite3.Connection, asset_id: str, old_value: int | None, new_value: int | None) -> dict[str, int]:
    """허가가 바뀌었을 때(재적재). 이 사진을 쓰는 승인본의 active 승인을 무효화하고 활성 Export를 확정 실패시킨다(같은 트랜잭션).

    true→false/null만 차단 효과가 있다. null/false→true는 기존 차단 Issue를 지우지 않는다(다음 배치 검사가 재확인한다).
    """
    from app.services import approvals as approvals_service

    counts = {"approvals_invalidated": 0, "exports_finalized": 0}
    if new_value == 1 or old_value == new_value:
        return counts
    stamp = to_iso(now())
    for a in conn.execute("SELECT approval_id, document_id, document_revision FROM approvals WHERE status='active'").fetchall():
        rev = conn.execute("SELECT content_json FROM document_revisions WHERE document_id=? AND revision=?",
                           (a["document_id"], a["document_revision"])).fetchone()
        if rev is None or asset_id not in _asset_ids_in(rev["content_json"]):
            continue
        counts["approvals_invalidated"] += approvals_service.invalidate_one(conn, a["approval_id"], "publication_changed")
        cur = conn.execute(
            "UPDATE exports SET status='failed', finalized_reason='publication_changed', updated_at=?, error_json=? "
            "WHERE approval_id=? AND status IN ('queued', 'generating', 'ready')",
            (stamp, json.dumps({"code": ISSUE_CODE, "message": "등록 사진의 공개 허가가 바뀌어 출력이 취소되었습니다. 배치 검사·승인을 다시 진행하세요.",
                                "retryable": False, "details": {"asset_id": asset_id}, "request_id": None}, ensure_ascii=False),
             a["approval_id"]))
        counts["exports_finalized"] += cur.rowcount
    return counts


def _asset_ids_in(content_json: str) -> set[str]:
    content = json.loads(content_json)
    return {b.get("content", {}).get("asset_id") for p in content.get("pages", []) for b in p.get("blocks", []) if b.get("type") == "image"}
