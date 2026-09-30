"""문서 저장·조회. 버전 구조(documents 머리 + document_revisions 내용).

- BE-04: rev.1 생성·읽기.
- BE-05: add_revision(낙관적 검사로 새 버전 추가), get_revision, on_revision_created 훅(Proposal stale 전환·승인 무효화 자리).
테이블은 바꾸지 않는다. 새 버전 = document_revisions 행 추가 + documents.current_revision 갱신.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict

from app.db import Connection, Row
from app.errors import ApiError
from app.models import Document, DocumentSummary, EditorialRecord, Fact, ImpactApply, ImpactItem, ImpactReviewOut, Page
from app.services import db_history
from app.timeutil import now, to_iso


def create_initial(conn: Connection, session_id: str, input_revision: int, title: str,
                   target_pages: int, pages: list[Page], status: str, *, preflight_id: str | None = None,
                   editorial: EditorialRecord | None = None) -> str:
    document_id = f"doc_{uuid.uuid4().hex[:16]}"
    stamp = to_iso(now())
    conn.execute(
        "INSERT INTO documents (document_id, session_id, current_revision, title, target_pages, created_at, updated_at) "
        "VALUES (?, ?, 1, ?, ?, ?, ?)", (document_id, session_id, title, target_pages, stamp, stamp))
    conn.execute(
        "INSERT INTO document_revisions (document_id, revision, input_revision, status, content_json, origin, source_ref, created_at) "
        "VALUES (?, 1, ?, ?, ?, 'draft', NULL, ?)",
        (document_id, input_revision, status,
         json.dumps({"title": title, "pages": [p.model_dump() for p in pages],
                     "editorial": editorial.model_dump() if editorial else None}, ensure_ascii=False), stamp))
    db_history.bind_document(conn, document_id, 1, session_id, preflight_id)
    return document_id


def _head(conn: Connection, session_id: str, document_id: str) -> Row:
    head = conn.execute("SELECT * FROM documents WHERE document_id=? AND session_id=?",
                        (document_id, session_id)).fetchone()
    if head is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    return head


def _session_input_revision(conn: Connection, session_id: str) -> int:
    return conn.execute("SELECT input_revision FROM sessions WHERE session_id=?", (session_id,)).fetchone()[0]


def _to_document(conn: Connection, head: Row, rev: Row, *, computed_status: bool) -> Document:
    from app.services import validation  # 순환 import 방지

    content = json.loads(rev["content_json"])
    # Document.status는 저장값이 아니라 현재 revision + 세션 최신 input_revision 기준의 검증·승인으로 계산한다(BE-06).
    status = (validation.compute_document_status(conn, head["document_id"], rev["revision"],
                                                 _session_input_revision(conn, head["session_id"]))
              if computed_status else rev["status"])
    return Document(
        document_id=head["document_id"], session_id=head["session_id"], document_revision=rev["revision"],
        input_revision=rev["input_revision"], title=content["title"], target_pages=head["target_pages"],
        status=status, pages=[Page.model_validate(p) for p in content["pages"]], editorial=content.get("editorial"),
    )


def get_current(conn: Connection, session_id: str, document_id: str) -> Document:
    head = _head(conn, session_id, document_id)
    rev = conn.execute("SELECT * FROM document_revisions WHERE document_id=? AND revision=?",
                       (document_id, head["current_revision"])).fetchone()
    return _to_document(conn, head, rev, computed_status=True)


def refresh_status_cache(conn: Connection, session_id: str, document_id: str) -> str:
    """document_revisions.status는 호환용 캐시. 판정의 원본이 아니며 여기서 계산값을 써 둔다."""
    from app.services import validation

    head = _head(conn, session_id, document_id)
    status = validation.compute_document_status(conn, document_id, head["current_revision"],
                                                _session_input_revision(conn, session_id))
    conn.execute("UPDATE document_revisions SET status=? WHERE document_id=? AND revision=?",
                 (status, document_id, head["current_revision"]))
    return status


def get_revision(conn: Connection, session_id: str, document_id: str, revision: int) -> Document:
    head = _head(conn, session_id, document_id)
    rev = conn.execute("SELECT * FROM document_revisions WHERE document_id=? AND revision=?",
                       (document_id, revision)).fetchone()
    if rev is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 문서 버전이 없습니다.",
                       details={"restore_from_revision": revision})
    return _to_document(conn, head, rev, computed_status=False)  # 과거 버전의 저장 캐시값(참고용)


def next_status(previous: str) -> str:
    """편집 뒤 상태(BE-06 검증 전 임시 규칙). review_required는 유지, 승인·승인 대기는 편집으로 무효화되어 draft."""
    return "review_required" if previous == "review_required" else "draft"


def add_revision(conn: Connection, session_id: str, document_id: str, expected_revision: int,
                 input_revision: int, title: str, pages: list[Page], status: str, origin: str,
                 source_ref: str | None) -> int:
    """새 버전을 만든다. expected_revision이 현재와 다르면 아무것도 바꾸지 않고 409.

    documents.current_revision 갱신을 WHERE current_revision=expected로 걸어 동시 요청 중 하나만 성공한다.
    """
    head = _head(conn, session_id, document_id)
    if head["current_revision"] != expected_revision:
        raise ApiError(409, "DOCUMENT_REVISION_CONFLICT", "문서가 변경되었습니다. 최신 문서에서 다시 요청해 주세요.",
                       details={"expected_revision": expected_revision, "current_revision": head["current_revision"]})
    new_revision = expected_revision + 1
    # Keep the original generation audit with its basis revision; never present it as a fresh review.
    previous_content = json.loads(conn.execute(
        "SELECT content_json FROM document_revisions WHERE document_id=? AND revision=?",
        (document_id, expected_revision)).fetchone()[0])
    stamp = to_iso(now())
    cur = conn.execute(
        "UPDATE documents SET current_revision=?, title=?, updated_at=? WHERE document_id=? AND current_revision=?",
        (new_revision, title, stamp, document_id, expected_revision))
    if cur.rowcount != 1:
        raise ApiError(409, "DOCUMENT_REVISION_CONFLICT", "문서가 변경되었습니다. 최신 문서에서 다시 요청해 주세요.",
                       details={"expected_revision": expected_revision})
    conn.execute(
        "INSERT INTO document_revisions (document_id, revision, input_revision, status, content_json, origin, source_ref, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (document_id, new_revision, input_revision, status,
         json.dumps({"title": title, "pages": [p.model_dump() for p in pages],
                     "editorial": previous_content.get("editorial")}, ensure_ascii=False),
         origin, source_ref, stamp))
    # 수동 편집/복원에는 이전 생성의 preflight를 현재 근거처럼 추정해 넣지 않는다.
    db_history.bind_document(conn, document_id, new_revision, session_id)
    on_revision_created(conn, document_id, new_revision)
    return new_revision


def on_revision_created(conn: Connection, document_id: str, new_revision: int) -> None:
    """문서가 바뀌면: 이전 기준 편집안은 stale, active 승인은 invalidated(document_changed)."""
    from app.services import approvals  # 순환 import 방지

    conn.execute(
        "UPDATE proposals SET status='stale', updated_at=? "
        "WHERE document_id=? AND status='proposed' AND base_document_revision<>?",
        (to_iso(now()), document_id, new_revision))
    approvals.invalidate_for_document(conn, document_id, "document_changed")


def summary_for_session(conn: Connection, session_id: str) -> DocumentSummary | None:
    from app.services import validation

    row = conn.execute("SELECT document_id, current_revision FROM documents WHERE session_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                       (session_id,)).fetchone()
    if row is None:
        return None
    # 문서 조회와 같은 함수로 계산한다.
    status = validation.compute_document_status(conn, row["document_id"], row["current_revision"],
                                                _session_input_revision(conn, session_id))
    session = conn.execute("SELECT demo FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    return DocumentSummary(document_id=row["document_id"], document_revision=row["current_revision"], status=status,
                           demo=bool(session and session["demo"]))


def exists_for_session(conn: Connection, session_id: str) -> str | None:
    row = conn.execute("SELECT document_id FROM documents WHERE session_id=? LIMIT 1", (session_id,)).fetchone()
    return row["document_id"] if row else None


# C-05: 본문은 명시적 적용 전까지 그대로 두고, 자료 변경 영향과 확인 이력만 저장한다.
def require_impact_history(conn: Connection) -> None:
    if not db_history.enabled(conn):
        raise ApiError(409, "IMPACT_HISTORY_UNAVAILABLE", "자료 변경 복귀에는 DB v11 이력이 필요합니다. 기존 DB 이전 절차를 먼저 진행해 주세요.")


def _latest_preflight_id(conn: Connection, sid: str, input_revision: int) -> str | None:
    row = conn.execute(
        "SELECT preflight_id FROM preflights WHERE session_id=? AND input_revision=? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1", (sid, input_revision)).fetchone()
    return row[0] if row else None


def _impact_context(conn: Connection, session: Row, preflight_id: str):
    from app.services import jobs, preflights

    sid, revision = session["session_id"], session["input_revision"]
    preflight = preflights.get(conn, sid, preflight_id)
    if preflight.input_revision != revision:
        raise ApiError(409, "INPUT_REVISION_CONFLICT", "현재 자료로 사전 점검을 다시 실행해 주세요.")
    if (_latest_preflight_id(conn, sid, revision) != preflight_id
            or jobs.find_active(conn, sid, "preflight", revision) is not None):
        raise ApiError(409, "PREFLIGHT_STALE", "최신 사전 점검이 끝난 뒤 그 결과를 확인해 주세요.")
    sources = preflights.build_sources(conn, sid, json.loads(session["selected_source_ids"]))
    return preflight, sources


def _impact_snapshot(preflight, sources) -> str:
    # 같은 입력 안의 읽기/허용 범위 변경도 검토 결과를 조용히 바꾸지 않게 한다.
    payload = {"preflight": preflight.model_dump(exclude={"confirmed_at"}),
               "sources": [asdict(source) for source in sources]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _fact_signature(fact: Fact) -> str | None:
    if fact.status != "supported" or not fact.value or not fact.evidence_refs:
        return None
    return json.dumps({"field_key": fact.field_key, "value": fact.value, "conditions": fact.conditions,
                       "evidence": sorted({json.dumps(ref.model_dump(), sort_keys=True, ensure_ascii=False)
                                           for ref in fact.evidence_refs})},
                      sort_keys=True, ensure_ascii=False)


def bound_preflight_id(conn: Connection, current: Document) -> str | None:
    # 수동 편집은 preflight_id가 없으므로 같은 입력에서 마지막으로 연결한 점검을 찾는다.
    bound = conn.execute(
        "SELECT preflight_id FROM document_revisions WHERE document_id=? AND revision<=? "
        "AND input_revision=? AND preflight_id IS NOT NULL ORDER BY revision DESC LIMIT 1",
        (current.document_id, current.document_revision, current.input_revision)).fetchone()
    return bound[0] if bound else None


def _impact_payload(conn: Connection, current: Document, preflight, sources) -> dict:
    from app.services import preflights, refs

    bound = bound_preflight_id(conn, current)
    old_facts = preflights.get(conn, current.session_id, bound).facts if bound else []
    old_by_id = {fact.fact_id: fact for fact in old_facts}
    ambiguous_old = {fact.fact_id for fact in old_facts if sum(f.fact_id == fact.fact_id for f in old_facts) != 1}
    new_by_signature: dict[str, list[str]] = {}
    for fact in preflight.facts:
        signature = _fact_signature(fact)
        if signature and all(refs.evidence_problem(ref, sources) is None for ref in fact.evidence_refs):
            new_by_signature.setdefault(signature, []).append(fact.fact_id)
    rebindings = {}
    for page in current.pages:
        for block in page.blocks:
            for fid in block.fact_ids:
                old = old_by_id.get(fid)
                candidates = new_by_signature.get(_fact_signature(old), []) if old else []
                if fid not in ambiguous_old and len(candidates) == 1:
                    rebindings[fid] = candidates[0]
    items = [ImpactItem(code="INPUT_CHANGED", message="자료·작성 요청이 바뀌었습니다. 기존 문장과 구성을 검토하고 유지할 내용의 이유를 남겨 주세요.")]
    assets = {aid for source in sources for aid in source.asset_ids}
    for page in current.pages:
        for block in page.blocks:
            if any(fid not in rebindings for fid in block.fact_ids):
                items.append(ImpactItem(block_id=block.block_id, code="FACT_REVIEW_REQUIRED", requires_change=True,
                                        message="이전 사실과 정확히 일치하는 근거를 찾지 못했습니다. 내용을 검토하고 참조를 명시적으로 수정하거나 블록을 삭제해 주세요."))
            elif any(fid != rebindings[fid] for fid in block.fact_ids):
                items.append(ImpactItem(block_id=block.block_id, code="FACT_REBOUND",
                                        message="사실과 근거가 일치합니다. 적용할 때 최신 점검의 사실 ID로 연결합니다."))
            if any(refs.evidence_problem(ref, sources) for ref in block.evidence_refs):
                items.append(ImpactItem(block_id=block.block_id, code="EVIDENCE_REMOVED", requires_change=True,
                                        message="현재 선택 자료에서 확인할 수 없는 근거가 있습니다. 참조를 수정하거나 블록을 삭제해 주세요."))
            if block.type == "image" and block.content.get("asset_id") not in assets:
                items.append(ImpactItem(block_id=block.block_id, code="PHOTO_REMOVED", requires_change=True,
                                        message="사진이 현재 선택 자료에 없습니다. 사진을 교체하거나 블록을 삭제해 주세요."))
    return {"items": [item.model_dump() for item in items], "fact_rebindings": rebindings,
            "snapshot": _impact_snapshot(preflight, sources)}


def _impact_row(conn: Connection, sid: str, did: str, review_id: str) -> Row:
    require_impact_history(conn)
    row = conn.execute("SELECT * FROM impact_reviews WHERE review_id=? AND session_id=? AND document_id=? AND purged_at IS NULL",
                       (review_id, sid, did)).fetchone()
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 영향 검토를 찾을 수 없습니다.")
    return row


def _impact_is_current(conn: Connection, session: Row, current: Document, review: Row) -> bool:
    if (review["document_revision"] != current.document_revision
            or review["from_input_revision"] != current.input_revision
            or review["to_input_revision"] != session["input_revision"]):
        return False
    try:
        preflight, sources = _impact_context(conn, session, review["preflight_id"])
    except ApiError:
        return False
    return (preflight.confirmed_at is not None
            and json.loads(review["items_json"])["snapshot"] == _impact_snapshot(preflight, sources))


def get_impact_review(conn: Connection, session: Row, current: Document, review_id: str) -> ImpactReviewOut:
    review = _impact_row(conn, current.session_id, current.document_id, review_id)
    payload = json.loads(review["items_json"])
    status = review["status"]
    if status == "pending" and not _impact_is_current(conn, session, current, review):
        status = "stale"
    return ImpactReviewOut(review_id=review_id, document_id=review["document_id"],
                           document_revision=review["document_revision"], from_input_revision=review["from_input_revision"],
                           to_input_revision=review["to_input_revision"], preflight_id=review["preflight_id"], status=status,
                           items=payload["items"], fact_rebindings=payload["fact_rebindings"],
                           created_at=review["created_at"], completed_at=review["completed_at"])


def create_impact_review(conn: Connection, session: Row, current: Document, preflight_id: str, confirmed: bool) -> ImpactReviewOut:
    from app.services import approvals, preflights

    require_impact_history(conn)
    if current.input_revision == session["input_revision"] and bound_preflight_id(conn, current) == preflight_id:
        raise ApiError(409, "INPUT_REVISION_CONFLICT", "이미 현재 입력에 연결된 문서입니다.")
    preflight, sources = _impact_context(conn, session, preflight_id)
    if not confirmed:
        raise ApiError(422, "PREFLIGHT_NOT_CONFIRMED", "사전 점검 결과를 확인한 뒤 영향 검토를 진행해 주세요.")
    payload = _impact_payload(conn, current, preflight, sources)
    preflights.confirm(conn, preflight_id)
    approvals.invalidate_for_document(conn, current.document_id, "preflight_changed")
    # 다른 키로 같은 검토를 다시 요청해도 대기 중인 결과를 재사용한다.
    existing = conn.execute(
        "SELECT review_id, items_json FROM impact_reviews WHERE document_id=? AND document_revision=? "
        "AND to_input_revision=? AND preflight_id=? AND status='pending' AND purged_at IS NULL ORDER BY rowid DESC LIMIT 1",
        (current.document_id, current.document_revision, session["input_revision"], preflight_id)).fetchone()
    if existing and json.loads(existing["items_json"]) == payload:
        return get_impact_review(conn, session, current, existing["review_id"])
    conn.execute("UPDATE impact_reviews SET status='stale' WHERE document_id=? AND status='pending'", (current.document_id,))
    rid = f"impact_{uuid.uuid4().hex[:16]}"
    conn.execute(
        "INSERT INTO impact_reviews (review_id, session_id, document_id, document_revision, from_input_revision, "
        "to_input_revision, preflight_id, items_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (rid, current.session_id, current.document_id, current.document_revision, current.input_revision,
         session["input_revision"], preflight_id, json.dumps(payload, ensure_ascii=False), to_iso(now())))
    return get_impact_review(conn, session, current, rid)


def apply_impact_review(conn: Connection, session: Row, current: Document, review_id: str, body: ImpactApply, owner: str) -> int:
    from app.services import refs
    from app.services.doc_ops import apply_operations

    review = _impact_row(conn, current.session_id, current.document_id, review_id)
    if review["status"] != "pending" or not _impact_is_current(conn, session, current, review):
        raise ApiError(409, "IMPACT_REVIEW_STALE", "자료·문서·점검 결과가 바뀌었습니다. 영향 검토부터 다시 진행해 주세요.")
    payload = json.loads(review["items_json"])
    pages = [page.model_copy(deep=True) for page in current.pages]
    for page in pages:
        for block in page.blocks:
            block.fact_ids = [payload["fact_rebindings"].get(fid, fid) for fid in block.fact_ids]
    pages = apply_operations(pages, body.operations)
    blocks = {block.block_id: block for page in pages for block in page.blocks}
    for update in body.reference_updates:
        if update.block_id not in blocks:
            raise ApiError(422, "IMPACT_REFERENCE_INVALID", "참조를 수정할 블록이 문서에 없습니다.")
        blocks[update.block_id].fact_ids = list(update.fact_ids)
        blocks[update.block_id].evidence_refs = list(update.evidence_refs)
    explicit = {update.block_id for update in body.reference_updates}
    unresolved = [item["block_id"] for item in payload["items"] if item["code"] == "FACT_REVIEW_REQUIRED"
                  and item["block_id"] in blocks and item["block_id"] not in explicit]
    if unresolved:
        raise ApiError(422, "IMPACT_REFERENCE_INVALID", "변경된 사실은 유지 사유만으로 연결할 수 없습니다. 참조 수정 또는 블록 삭제가 필요합니다.",
                       details={"block_ids": unresolved})
    preflight, sources = _impact_context(conn, session, review["preflight_id"])
    if (problem := refs.selected_problem(pages, sources, preflight)) is not None:
        raise ApiError(422, "IMPACT_REFERENCE_INVALID", "현재 선택 자료와 확인된 사실에 맞게 참조를 수정해 주세요.",
                       details={"reason": problem})
    revision = add_revision(conn, current.session_id, current.document_id, current.document_revision,
                            session["input_revision"], current.title, pages, "review_required", "impact_review", review_id)
    db_history.bind_document(conn, current.document_id, revision, current.session_id, review["preflight_id"])
    stamp = to_iso(now())
    conn.execute("UPDATE impact_reviews SET status='applied', completed_at=? WHERE review_id=?", (stamp, review_id))
    conn.execute(
        "INSERT INTO confirmations (confirmation_id, session_id, document_id, document_revision, input_revision, "
        "kind, confirmed_by, confirmed_at, impact_review_id, reasons_json, status) "
        "VALUES (?, ?, ?, ?, ?, 'impact_keep', ?, ?, ?, ?, 'active')",
        (f"cfm_{uuid.uuid4().hex[:16]}", current.session_id, current.document_id, revision, session["input_revision"],
         owner, stamp, review_id, json.dumps([body.model_dump()], ensure_ascii=False)))
    return revision
