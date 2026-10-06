"""Issue 해결(resolve). 클라이언트가 보낸 상태를 믿지 않고 조치와 현재 내용의 일치를 서버가 확인한다.

resolved     서버 검사 Issue: 그 자리에서 서버 검사를 다시 돌려 사라졌을 때만. agent Issue: validate Job으로만(422 REVALIDATION_REQUIRED)
excluded     선택 주장·자료가 실제로 문서/선택에서 빠졌을 때만. REQUIRED_MISSING·MOCK_VALUE 불가
acknowledged warning만. blocker(MOCK_VALUE·UNSUPPORTED_CLAIM 포함)는 확인 클릭으로 통과하지 않는다
"""
from __future__ import annotations

import json

from app.db import Connection, Row
from app.errors import ApiError
from app.models import Document, IssueOut, IssueResolveBody, PreflightOut
from app.services import db_history, preflights, validation
from app.timeutil import now, to_iso


def _not_allowed(message: str, **details) -> ApiError:
    # 계약 확인 ㉗: 해결 거부 코드(임시)
    return ApiError(422, "RESOLUTION_NOT_ALLOWED", message, details=details)


def _still_present(message: str, **details) -> ApiError:
    return ApiError(422, "ISSUE_STILL_PRESENT", message, details=details)


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
