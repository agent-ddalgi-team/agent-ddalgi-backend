"""Issue 해결(resolve). 클라이언트가 보낸 상태를 믿지 않고 조치와 현재 내용의 일치를 서버가 확인한다.

resolved     서버 검사 Issue: 그 자리에서 서버 검사를 다시 돌려 사라졌을 때만. agent Issue: validate Job으로만(422 REVALIDATION_REQUIRED)
excluded     선택 주장·자료가 실제로 문서/선택에서 빠졌을 때만. REQUIRED_MISSING·MOCK_VALUE 불가
acknowledged warning만. blocker(MOCK_VALUE·UNSUPPORTED_CLAIM 포함)는 확인 클릭으로 통과하지 않는다
"""
from __future__ import annotations

import json
import hashlib
import uuid

from app.db import Connection, Row
from app.errors import ApiError
from app.models import Document, IssueOut, IssueResolveBody, PreflightOut, SourceCorrectionBody
from app.services import db_history, preflights, validation
from app.timeutil import now, to_iso


def _not_allowed(message: str, **details) -> ApiError:
    # 계약 확인 ㉗: 해결 거부 코드(임시)
    return ApiError(422, "RESOLUTION_NOT_ALLOWED", message, details=details)


def _still_present(message: str, **details) -> ApiError:
    return ApiError(422, "ISSUE_STILL_PRESENT", message, details=details)


def _correction_source(body: SourceCorrectionBody, document: Document, source_list):
    block = next((b for p in document.pages for b in p.blocks if b.block_id == body.block_id), None)
    ref = body.evidence_ref
    source = next((s for s in source_list if s.source_id == ref.source_id and s.source_version == ref.source_version), None)
    segment = next((s for s in source.segments if s.segment_id == ref.segment_id), None) if source else None
    if (block is None or block.type not in {"heading", "paragraph", "list"}
            or ref not in block.evidence_refs or segment is None
            or ref.locator != segment.locator or ref.excerpt not in segment.text
            or body.original_text not in ref.excerpt):
        raise _not_allowed("현재 문서에 연결된 원문의 구절을 선택해 주세요.")
    text = "\n".join(block.content.get("items", [])) if block.type == "list" else str(block.content.get("text", ""))
    if body.corrected_text not in text:
        raise _not_allowed("정정할 표기를 문서에 먼저 입력·저장한 뒤 정정 근거를 기록해 주세요.")
    return block, segment


def record_source_correction(conn: Connection, session_row: Row, owner: str, document: Document,
                             issue: Row, body: SourceCorrectionBody, preflight: PreflightOut | None) -> dict:
    if body.expected_revision != document.document_revision:
        raise ApiError(409, "DOCUMENT_REVISION_CONFLICT", "문서가 바뀌었습니다. 최신 문서에서 정정해 주세요.")
    if body.input_revision != session_row["input_revision"] or document.input_revision != body.input_revision:
        raise ApiError(409, "INPUT_REVISION_CONFLICT", "자료가 바뀌었습니다. 변경 내용을 반영하고 다시 검증해 주세요.")
    if (issue["status"] != "open" or issue["origin"] != "agent" or issue["scope"] != "content"
            or issue["code"] != "VALUE_MISMATCH" or body.block_id not in json.loads(issue["block_ids_json"])):
        raise _not_allowed("원문 오타 정정은 현재 내용 검증의 값 불일치 문제에만 기록할 수 있습니다.")
    latest = validation.latest_validation(conn, document.document_id, document.document_revision, body.input_revision)
    if (latest is None or latest["validation_id"] != body.validation_id
            or issue["last_validation_id"] != body.validation_id or latest["status"] == "pending"
            or preflight is None or preflight.input_revision != body.input_revision):
        raise ApiError(422, "REVALIDATION_REQUIRED", "완료된 최신 내용 검증에서 정정해 주세요.")
    if validation.validation_in_progress(conn, document.document_id, document.document_revision, body.input_revision):
        raise ApiError(409, "INVALID_OPERATION", "진행 중인 내용 검증이 끝난 뒤 정정해 주세요.")
    source_list = preflights.build_sources(conn, session_row["session_id"], json.loads(session_row["selected_source_ids"]))
    block, segment = _correction_source(body, document, source_list)
    history = json.loads(issue["resolution_history_json"])
    if len([h for h in history if h.get("action") == "source_typo_correction"]) >= 20:
        raise _not_allowed("한 문제의 정정 기록은 20개까지 지원합니다. 자료를 보완해 다시 점검해 주세요.")
    record = {**body.model_dump(), "action": "source_typo_correction", "correction_id": f"cor_{uuid.uuid4().hex[:16]}",
              "by": owner, "at": to_iso(now()), "issue_id": issue["issue_id"], "preflight_id": preflight.preflight_id,
              "block_fingerprint": validation.fingerprint_block(block, {segment.segment_id: segment.text}),
              "source_hash": hashlib.sha256(segment.text.encode()).hexdigest()}
    history.append(record)
    conn.execute("UPDATE issues SET resolution_history_json=?, updated_at=? WHERE issue_id=?",
                 (json.dumps(history, ensure_ascii=False), record["at"], issue["issue_id"]))
    return record


def active_source_corrections(conn: Connection, document: Document, input_revision: int,
                              preflight: PreflightOut | None, source_list) -> list[dict]:
    """Only exact saved document/input/preflight/source snapshots may reuse a user correction."""
    result = []
    for row in conn.execute("SELECT resolution_history_json FROM issues WHERE document_id=?", (document.document_id,)):
        records = [h for h in json.loads(row[0]) if h.get("action") == "source_typo_correction" and not h.get("purged")]
        if not records:
            continue
        record = records[-1]
        if (record["expected_revision"] != document.document_revision or record["input_revision"] != input_revision
                or preflight is None or record["preflight_id"] != preflight.preflight_id):
            continue
        body = SourceCorrectionBody.model_validate({k: record[k] for k in SourceCorrectionBody.model_fields})
        try:
            block, segment = _correction_source(body, document, source_list)
        except ApiError:
            continue
        if (record["source_hash"] == hashlib.sha256(segment.text.encode()).hexdigest()
                and record["block_fingerprint"] == validation.fingerprint_block(block, {segment.segment_id: segment.text})):
            result.append(record)
    return result


def acknowledgement_context(conn: Connection, session_row: Row, document: Document,
                            issue: Row, body: IssueResolveBody) -> Row:
    if body.expected_revision != document.document_revision:
        raise ApiError(409, "DOCUMENT_REVISION_CONFLICT", "문서가 변경되었습니다. 최신 문서에서 다시 확인해 주세요.")
    if body.input_revision != session_row["input_revision"] or document.input_revision != session_row["input_revision"]:
        raise ApiError(409, "INPUT_REVISION_CONFLICT", "자료·목적이 변경되었습니다. 최신 입력으로 다시 검증해 주세요.")
    if not validation.acknowledgeable(issue, demo=bool(session_row["demo"])):
        raise _not_allowed("이 문제는 확인 클릭으로 넘길 수 없습니다. 원인을 수정하고 다시 검증하세요.", code=issue["code"])
    latest = validation.latest_validation(conn, document.document_id, document.document_revision, session_row["input_revision"])
    if (latest is None or latest["validation_id"] != body.validation_id
            or validation.validation_in_progress(conn, document.document_id, document.document_revision, session_row["input_revision"])):
        raise ApiError(422, "REVALIDATION_REQUIRED", "완료된 최신 검증 결과에서 경고를 확인해 주세요.",
                       details={"validation_id": body.validation_id})
    pf = conn.execute("SELECT preflight_id FROM preflights WHERE session_id=? AND input_revision=? "
                      "ORDER BY created_at DESC, rowid DESC LIMIT 1", (session_row["session_id"], session_row["input_revision"])).fetchone()
    context = validation.load_context(conn, session_row["session_id"],
                                      preflights.get(conn, session_row["session_id"], pf[0]) if pf else None)
    if validation.issue_anchor(issue, validation.fingerprints(document, context.seg_texts), context,
                               session_row["input_revision"]) != issue["anchor_fingerprint"]:
        raise ApiError(422, "REVALIDATION_REQUIRED", "경고의 내용·근거가 바뀌었습니다. 다시 검증해 주세요.")
    return latest


def resolve(conn: Connection, session_row: Row, owner_id: str, document: Document,
            issue: Row, body: IssueResolveBody, preflight: PreflightOut | None) -> IssueOut:
    if body.expected_revision != document.document_revision:
        raise ApiError(409, "DOCUMENT_REVISION_CONFLICT", "문서가 변경되었습니다. 최신 문서에서 다시 요청해 주세요.",
                       details={"expected_revision": body.expected_revision, "current_revision": document.document_revision})
    action = body.resolution.action
    checked = acknowledgement_context(conn, session_row, document, issue, body) if action == "acknowledged" else None
    if issue["status"] != "open":
        if issue["status"] == action:
            if checked is not None and not validation.warning_ack_valid(conn, issue, checked, demo=bool(session_row["demo"])):
                raise ApiError(422, "REVALIDATION_REQUIRED", "확인 기록이 현재 검증에 유효하지 않습니다. 다시 검증해 주세요.")
            return validation.issue_to_out(issue)  # 같은 조치 재요청은 그대로(멱등)
        raise _not_allowed("이미 처리된 문제입니다.", issue_id=issue["issue_id"], status=issue["status"])

    ctx = validation.load_context(conn, session_row["session_id"], preflight)
    for ref in body.evidence_refs:
        if ref.segment_id not in ctx.refs.segment_ids:
            raise ApiError(422, "INVALID_OPERATION", "첨부한 근거가 이 세션 자료에 없습니다.", details={"segment_id": ref.segment_id})

    code, severity, origin = issue["code"], issue["severity"], issue["origin"]
    block_ids = set(json.loads(issue["block_ids_json"]))
    fact_ids = set(json.loads(issue["fact_ids_json"]))
    source_ids = set(json.loads(issue["source_ids_json"]))
    doc_blocks = {b.block_id: b for p in document.pages for b in p.blocks}

    if origin == "preflight" and severity == "blocker" and action in ("resolved", "excluded"):
        if any(draft.identity_key == issue["identity_key"] for draft in validation.preflight_conflicts(ctx)):
            raise _still_present("사전 점검의 필수 문제가 남아 있습니다. 자료를 보완·재점검한 뒤 다시 검증하세요.",
                                 issue_id=issue["issue_id"], code=code)
        raise ApiError(422, "REVALIDATION_REQUIRED", "사전 점검의 필수 문제는 문서 검증을 다시 실행해야 해결됩니다.",
                       details={"issue_id": issue["issue_id"]})

    if action == "excluded":
        if origin == "layout" or code in validation.NON_EXCLUDABLE:
            raise _not_allowed("필수 내용 결핍·mock 자료·배치 문제는 제외로 처리할 수 없습니다.", code=code)
        remaining_blocks = sorted(b for b in block_ids if b in doc_blocks)
        referencing = sorted(b.block_id for b in doc_blocks.values() if fact_ids & set(b.fact_ids))
        selected = set(json.loads(session_row["selected_source_ids"]))
        still_selected = sorted(s for s in source_ids if s in selected)
        evidence_using = sorted(b.block_id for b in doc_blocks.values() if any(r.source_id in source_ids for r in b.evidence_refs))
        if remaining_blocks or referencing or still_selected or evidence_using:
            raise _still_present("주장이나 자료가 아직 문서·선택에 남아 있어 제외로 처리할 수 없습니다.",
                                 remaining_block_ids=remaining_blocks, referencing_block_ids=referencing,
                                 still_selected_source_ids=still_selected, evidence_block_ids=evidence_using)
    elif action == "resolved":
        if origin == "layout":
            raise ApiError(422, "LAYOUT_RECHECK_REQUIRED", "배치 문제는 해당 형식의 배치 검사를 다시 실행해야 해결됩니다.",
                           details={"issue_id": issue["issue_id"], "format": issue["layout_format"] if "layout_format" in issue.keys() else None})
        if origin == "agent":
            raise ApiError(422, "REVALIDATION_REQUIRED", "AI 의미 검증 문제는 검증을 다시 실행해야 해결됩니다.",
                           details={"issue_id": issue["issue_id"]})
        drafts, _ = validation.server_checks(document, ctx)
        if any(d.identity_key == issue["identity_key"] for d in drafts):
            raise _still_present("문제의 원인이 아직 문서에 남아 있습니다. 내용을 고친 뒤 다시 시도하세요.",
                                 issue_id=issue["issue_id"], code=code)

    stamp = to_iso(now())
    resolution = {"action": action, "by": owner_id, "at": stamp, "reason": body.resolution.reason,
                  "evidence_refs": [r.model_dump() for r in body.evidence_refs],
                  "document_revision": document.document_revision, "input_revision": session_row["input_revision"]}
    if checked is not None:
        resolution.update(validation_id=checked["validation_id"], anchor_fingerprint=issue["anchor_fingerprint"],
                          validated_document_revision=document.document_revision,
                          validated_validation_id=checked["validation_id"])
    conn.execute("UPDATE issues SET status=?, resolution_json=?, updated_at=? WHERE issue_id=?",
                 (action, json.dumps(resolution, ensure_ascii=False), stamp, issue["issue_id"]))
    if checked is not None:
        db_history.record_warning(conn, issue, resolution)
    latest = validation.latest_validation(conn, document.document_id, document.document_revision, session_row["input_revision"])
    if latest is not None:
        validation.refresh_validation_status(conn, latest["validation_id"], document.document_id)
    return validation.issue_to_out(conn.execute("SELECT * FROM issues WHERE issue_id=?", (issue["issue_id"],)).fetchone())
