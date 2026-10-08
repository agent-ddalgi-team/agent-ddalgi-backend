"""Preflight 저장·조회와 Agent 입력 조립."""
from __future__ import annotations

import json
import hashlib
import uuid
from pathlib import Path

from app.db import Connection
from app.config import Settings
from app.agent_bridge import SegmentIn, SourceIn
from app.errors import ApiError
from app.models import Brief, DataSufficiency, Fact, Issue, PreflightOut, PreflightReviewCreate, Recommendations, SufficiencyCategory
from app.services import db_history
from app.services.sources import evidence_scope
from app.timeutil import now, to_iso


def build_sources(conn: Connection, session_id: str, selected_source_ids: list[str], *,
                  exclusions: list[dict] | None = None, settings: Settings | None = None) -> list[SourceIn]:
    """선택한 자료만 Agent 입력으로 만든다. 이 세션의 첨부 또는 등록 자료(근거 사용 허용)만.

    다른 세션의 자료와 use_as_company_evidence=false인 등록 자료는 선택돼 있어도 절대 포함하지 않는다.
    """
    result: list[SourceIn] = []
    scope, params = evidence_scope(conn, session_id)
    has_history = db_history.enabled(conn)
    for source_id in selected_source_ids:
        row = conn.execute(
            f"SELECT src.* FROM sources src WHERE src.source_id=? AND {scope}",
            (source_id, *params)).fetchone()
        if row is None:
            if exclusions is not None:
                exclusions.append({"source_id": source_id, "reason": "outside_evidence_scope"})
            continue
        run_sql, run_params = "", ()
        source_version, name, parse_status = row["source_version"], row["name"], row["parse_status"]
        stored_path, content_hash = row["stored_path"], row["content_hash"]
        if has_history:
            pinned = conn.execute(
                "SELECT sel.source_version, sel.run_id, v.original_name, v.stored_path, v.content_hash, r.status FROM session_source_selections sel "
                "JOIN sessions s ON s.session_id=sel.session_id AND s.input_revision=sel.input_revision "
                "JOIN source_versions v ON v.source_id=sel.source_id AND v.version=sel.source_version "
                "JOIN extraction_runs r ON r.run_id=sel.run_id "
                "WHERE sel.session_id=? AND sel.source_id=? AND v.purged_at IS NULL AND r.purged_at IS NULL",
                (session_id, source_id)).fetchone()
            if pinned is None:
                if exclusions is not None:
                    exclusions.append({"source_id": source_id, "reason": "selected_run_unavailable"})
                continue
            source_version, name, parse_status = pinned["source_version"], pinned["original_name"], pinned["status"]
            stored_path, content_hash = pinned["stored_path"], pinned["content_hash"]
            run_sql, run_params = " AND run_id=?", (pinned["run_id"],)
        if parse_status not in {"complete", "partial"}:
            if exclusions is not None:
                exclusions.append({"source_id": source_id, "reason": "parse_not_usable"})
            continue
        segment_rows = conn.execute(
            "SELECT segment_id, locator_json, text, chunk_id, evidence_status, document_date, extraction_method "
            "FROM segments WHERE source_id=?" + run_sql + " ORDER BY ordinal", (source_id, *run_params)).fetchall()
        segments = [SegmentIn(r["segment_id"], json.loads(r["locator_json"]), r["text"]) for r in segment_rows]
        # 버전별 source 날짜 스냅샷은 기존 이력에 없다. 옛 버전에 최신 날짜를 붙이지 않는다.
        metadata = {"source_version": source_version,
                    "document_date": row["document_date"] if source_version == row["source_version"] else None,
                    "document_date_verified": bool(row["document_date_verified"]) if source_version == row["source_version"] and row["document_date_verified"] is not None else None,
                    "date_from_filename": row["date_from_filename"] if source_version == row["source_version"] else None,
                    "segments": {r["segment_id"]: {key: r[key] for key in
                        ("chunk_id", "evidence_status", "document_date", "extraction_method") if r[key] is not None}
                        for r in segment_rows}}
        if settings is not None:
            for segment_id, layout in _source_layout(settings, stored_path, content_hash, segments).items():
                metadata["segments"][segment_id]["layout"] = layout
        asset_rows = conn.execute(
            "SELECT asset_id, photo_locator_json, caption_candidate, width, height, scope, approved_for_external_use "
            "FROM assets WHERE source_id=? AND status='ready' AND deleted_at IS NULL" + run_sql,
            (source_id, *run_params)).fetchall()
        assets = [r["asset_id"] for r in asset_rows]
        asset_locators = {r["asset_id"]: photo_locator(r["photo_locator_json"]) for r in asset_rows}
        asset_descriptions = {r["asset_id"]: {
            "caption": r["caption_candidate"] or "자료 사진",
            "width": r["width"], "height": r["height"],
        } for r in asset_rows if row["origin_kind"] != "mock" and
            (r["scope"] == "session" or r["approved_for_external_use"] == 1)}
        result.append(SourceIn(source_id=row["source_id"], source_version=source_version, kind=row["kind"],
                               name=name, parse_status=parse_status, segments=segments,
                               asset_ids=assets, origin_kind=row["origin_kind"], asset_locators=asset_locators,
                               asset_descriptions=asset_descriptions, metadata=metadata))
    return result


def _source_layout(settings: Settings, stored_path: str | None, content_hash: str | None,
                   segments: list[SegmentIn]) -> dict[str, dict]:
    """Match the selected file version and unique text on the same slide.

    Coordinates are context, not new evidence. Unselected boxes, duplicate
    matches, changed files and unsupported layouts supply no extra context.
    """
    if not stored_path or not content_hash or Path(stored_path).suffix.lower() != ".pptx":
        return {}
    try:
        root = settings.private_runs_dir.resolve()
        path = (root / stored_path).resolve()
        if not path.is_relative_to(root) or path.stat().st_size > settings.max_file_bytes:
            return {}
        with path.open("rb") as stream:
            data = stream.read(settings.max_file_bytes + 1)
        if len(data) > settings.max_file_bytes or hashlib.sha256(data).hexdigest() != content_hash:
            return {}
        from app.parsers.office import pptx_text_layout
        matches: dict[tuple, list[dict]] = {}
        for item in pptx_text_layout(data):
            key = (item["layout"]["slide"], " ".join(item["text"].split()))
            matches.setdefault(key, []).append(item["layout"])
        result = {}
        for segment in segments:
            slide = segment.locator.get("slide")
            if type(slide) is not int or slide <= 0:
                continue
            options = matches.get((slide, " ".join(segment.text.split())), [])
            if len(options) == 1:
                result[segment.segment_id] = options[0]
        return result
    except (OSError, ValueError):
        return {}


def photo_locator(raw: str | None) -> dict[str, int]:
    """원본 위치 중 양의 쪽수만 전달한다. 경로·해시·임의 문구는 모델 입력에 복사하지 않는다."""
    try:
        value = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in ("slide", "page")
            if type(value.get(key)) is int and 0 < value[key] <= 1_000_000}


def allowed_ids(sources: list[SourceIn]) -> tuple[set[str], set[str], dict[str, int]]:
    """(segment_id 집합, asset_id 집합, source_id→version). AI 결과 검사에 쓴다."""
    segs = {s.segment_id for src in sources for s in src.segments}
    assets = {a for src in sources for a in src.asset_ids}
    versions = {src.source_id: src.source_version for src in sources}
    return segs, assets, versions


def save(conn: Connection, session_id: str, input_revision: int, usable_source_ids: list[str],
         facts: list[Fact], issues: list[Issue], recommendations: Recommendations, can_generate: bool) -> str:
    preflight_id = f"pf_{uuid.uuid4().hex[:16]}"
    conn.execute(
        "INSERT INTO preflights (preflight_id, session_id, input_revision, usable_source_ids, facts_json, issues_json, "
        "recommendations_json, can_generate, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (preflight_id, session_id, input_revision, json.dumps(usable_source_ids),
         json.dumps([f.model_dump() for f in facts], ensure_ascii=False),
         json.dumps([i.model_dump() for i in issues], ensure_ascii=False),
         recommendations.model_dump_json(), int(can_generate), to_iso(now())),
    )
    return preflight_id


def assess_sufficiency(facts: list[Fact], issues: list[Issue]) -> DataSufficiency:
    """Four UI categories measure evidence coverage, never approval or factual truth."""
    groups = [
        ("overview", "기업 개요·연혁", {"company_name", "company_summary", "business_areas", "history"}),
        ("process", "제조 공정·설비", {"processes", "technology", "capabilities"}),
        ("performance", "고객사·납품 실적", {"customers_markets"}),
        ("certification", "품질·공인 인증", {"certifications"}),
    ]
    blockers = [issue for issue in issues if issue.status == "open" and issue.severity == "blocker"]
    blocked_facts = {fid for issue in blockers for fid in issue.fact_ids}
    blocked_sources = {sid for issue in blockers if issue.scope == "source" for sid in issue.source_ids}
    categories = []
    for key, label, fields in groups:
        relevant = [fact for fact in facts if fact.field_key in fields]
        if any(fact.status == "conflict" for fact in relevant):
            status = "conflict"
        elif any(fact.status == "needs_confirmation" or fact.fact_id in blocked_facts
                 or any(ref.source_id in blocked_sources for ref in fact.evidence_refs) for fact in relevant):
            status = "needs_confirmation"
        elif any(fact.status == "supported" and fact.value and fact.value.strip()
                 and fact.evidence_refs for fact in relevant):
            status = "supported"
        else:
            status = "missing"
        categories.append(SufficiencyCategory(key=key, label=label, status=status))
    return DataSufficiency(score=25 * sum(c.status == "supported" for c in categories),
                           categories=categories, has_blockers=bool(blockers))


def latest_id(conn: Connection, session_id: str, input_revision: int) -> str | None:
    row = conn.execute("SELECT preflight_id FROM preflights WHERE session_id=? AND input_revision=? "
                       "ORDER BY created_at DESC, rowid DESC LIMIT 1", (session_id, input_revision)).fetchone()
    return row[0] if row else None


def draft_problem(brief: Brief, facts: list[Fact], agent_mode: str) -> tuple[str, str] | None:
    if agent_mode != "llm":
        return None
    from app.agent_llm import draft_input_problem
    return draft_input_problem(brief, facts)


def apply_draft_readiness(result: PreflightOut, brief: Brief, agent_mode: str) -> PreflightOut:
    if not result.can_generate:
        return result
    if problem := draft_problem(brief, result.facts, agent_mode):
        result = result.model_copy(deep=True)
        result.can_generate = False
        if problem[1] not in result.recommendations.needed:
            result.recommendations.needed.append(problem[1])
    return result


def get(conn: Connection, session_id: str, preflight_id: str) -> PreflightOut:
    row = conn.execute("SELECT * FROM preflights WHERE preflight_id=? AND session_id=?",
                       (preflight_id, session_id)).fetchone()
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    facts = [Fact.model_validate(f) for f in json.loads(row["facts_json"])]
    issues = [Issue.model_validate(i) for i in json.loads(row["issues_json"])]
    excluded = {fid for issue in issues if issue.code == "FACT_EXCLUDED" and issue.status == "excluded"
                and issue.resolution and issue.resolution.get("action") == "excluded"
                for fid in issue.fact_ids}
    excluded_facts = [fact for fact in facts if fact.fact_id in excluded]
    facts = [fact for fact in facts if fact.fact_id not in excluded]
    # Original issues stay in storage: restoration reopens them without guessing.
    visible_issues = []
    for issue in issues:
        if (issue.code != "FACT_EXCLUDED" and issue.scope == "content" and issue.fact_ids
                and set(issue.fact_ids) <= excluded and issue.code not in {"REQUIRED_MISSING", "MOCK_VALUE"}):
            issue = issue.model_copy(update={"status": "excluded", "resolution": {
                "action": "excluded", "reason": "관련 선택 항목을 사용자가 이번 문서에서 제외했습니다."}})
        visible_issues.append(issue)
    issues = visible_issues
    session = conn.execute("SELECT input_revision, brief_json FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    reviewable = []
    if session and session["input_revision"] == row["input_revision"]:
        brief = Brief.model_validate_json(session["brief_json"])
        reviewable = reviewable_fact_ids(brief, facts, issues)
    return PreflightOut(
        preflight_id=row["preflight_id"], session_id=row["session_id"], input_revision=row["input_revision"],
        usable_source_ids=json.loads(row["usable_source_ids"]),
        facts=facts, excluded_facts=excluded_facts, reviewable_fact_ids=reviewable,
        sufficiency=assess_sufficiency(facts, issues),
        issues=issues,
        recommendations=Recommendations.model_validate_json(row["recommendations_json"]),
        can_generate=bool(row["can_generate"]), confirmed_at=row["confirmed_at"],
    )


def confirm(conn: Connection, preflight_id: str) -> str:
    stamp = to_iso(now())
    conn.execute("UPDATE preflights SET confirmed_at=COALESCE(confirmed_at, ?) WHERE preflight_id=?", (stamp, preflight_id))
    return conn.execute("SELECT confirmed_at FROM preflights WHERE preflight_id=?", (preflight_id,)).fetchone()[0]


_BUSINESS_FIELDS = {"company_summary", "business_areas", "products_services", "technology", "processes"}


def reviewable_fact_ids(brief: Brief, facts: list[Fact], issues: list[Issue]) -> list[str]:
    protected_fields = {"company_name", *brief.required_fields}
    business = [fact.fact_id for fact in facts if fact.field_key in _BUSINESS_FIELDS and fact.status == "supported"]
    protected_ids = set(business) if len(business) == 1 else set()
    protected_ids.update(fid for issue in issues if issue.code in {"REQUIRED_MISSING", "MOCK_VALUE"}
                         for fid in issue.fact_ids)
    return [fact.fact_id for fact in facts if fact.status != "missing" and fact.field_key not in protected_fields
            and fact.fact_id not in protected_ids]


def review(conn: Connection, session_row, preflight_id: str, body: PreflightReviewCreate, owner: str) -> str:
    """Persist an explicit publication choice in a new snapshot; never upgrade truth status."""
    old = conn.execute("SELECT * FROM preflights WHERE session_id=? AND preflight_id=?",
                       (session_row["session_id"], preflight_id)).fetchone()
    if old is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 점검 결과가 없습니다.")
    raw_facts = [Fact.model_validate(fact) for fact in json.loads(old["facts_json"])]
    raw_issues = [Issue.model_validate(issue) for issue in json.loads(old["issues_json"])]
    facts = {fact.fact_id: fact for fact in raw_facts}
    selected = set(body.fact_ids)
    if not selected <= facts.keys():
        raise ApiError(422, "INVALID_OPERATION", "현재 점검에 없는 항목입니다.")
    current = get(conn, session_row["session_id"], preflight_id)
    excluded = {fact.fact_id for fact in current.excluded_facts}
    brief = Brief.model_validate_json(session_row["brief_json"])
    if body.action == "exclude":
        if not (selected - excluded) <= set(current.reviewable_fact_ids):
            raise ApiError(422, "RESOLUTION_NOT_ALLOWED", "회사명·필수 내용·마지막 사업 설명은 제외할 수 없습니다. 근거를 보완해 주세요.")
        remaining = [fact for fact in current.facts if fact.fact_id not in selected]
        if (any(fact.field_key in _BUSINESS_FIELDS and fact.status == "supported" for fact in current.facts)
                and not any(fact.field_key in _BUSINESS_FIELDS and fact.status == "supported" for fact in remaining)):
            raise ApiError(422, "RESOLUTION_NOT_ALLOWED", "확인된 사업 설명을 모두 제외할 수 없습니다.")
        next_excluded = excluded | selected
    else:
        if not selected <= excluded:
            raise ApiError(422, "INVALID_OPERATION", "이번 점검에서 제외된 항목만 복원할 수 있습니다.")
        next_excluded = excluded - selected
    from app.services import refs
    sources = build_sources(conn, session_row["session_id"], json.loads(session_row["selected_source_ids"]))
    for fid in selected:
        for evidence in facts[fid].evidence_refs:
            if refs.evidence_problem(evidence, sources):
                raise ApiError(422, "EVIDENCE_INVALID", "항목의 원문 근거가 현재 선택 자료와 맞지 않습니다. 자료를 다시 점검해 주세요.")
    if next_excluded == excluded:
        return preflight_id
    # A marker is server-authored; Agent output cannot return resolved issues.
    raw_issues = [issue for issue in raw_issues if issue.code != "FACT_EXCLUDED" or not set(issue.fact_ids) & selected]
    for fid in sorted(selected):
        raw_issues.append(Issue(issue_id=f"iss_{uuid.uuid4().hex[:16]}", scope="content", code="FACT_EXCLUDED",
            severity="info", status="excluded" if body.action == "exclude" else "resolved",
            message="사용자가 선택 항목을 이번 문서에서 제외했습니다." if body.action == "exclude" else "사용자가 제외 항목을 복원했습니다.",
            fact_ids=[fid], source_ids=sorted({ref.source_id for ref in facts[fid].evidence_refs}),
            resolution={"action": "excluded" if body.action == "exclude" else "restored", "reason": body.reason,
                        "by": owner, "at": to_iso(now()), "basis_preflight_id": preflight_id,
                        "input_revision": session_row["input_revision"]}))
    return save(conn, session_row["session_id"], session_row["input_revision"], json.loads(old["usable_source_ids"]),
                raw_facts, raw_issues, Recommendations.model_validate_json(old["recommendations_json"]), bool(old["can_generate"]))
