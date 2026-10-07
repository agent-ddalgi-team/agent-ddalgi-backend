"""Preflight 저장·조회와 Agent 입력 조립."""
from __future__ import annotations

import json
import uuid

from app.db import Connection
from app.agent_bridge import SegmentIn, SourceIn
from app.errors import ApiError
from app.models import Brief, DataSufficiency, Fact, Issue, PreflightOut, Recommendations, SufficiencyCategory
from app.services import db_history
from app.services.sources import evidence_scope
from app.timeutil import now, to_iso


def build_sources(conn: Connection, session_id: str, selected_source_ids: list[str]) -> list[SourceIn]:
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
            continue
        run_sql, run_params = "", ()
        source_version, name, parse_status = row["source_version"], row["name"], row["parse_status"]
        if has_history:
            pinned = conn.execute(
                "SELECT sel.source_version, sel.run_id, v.original_name, r.status FROM session_source_selections sel "
                "JOIN sessions s ON s.session_id=sel.session_id AND s.input_revision=sel.input_revision "
                "JOIN source_versions v ON v.source_id=sel.source_id AND v.version=sel.source_version "
                "JOIN extraction_runs r ON r.run_id=sel.run_id "
                "WHERE sel.session_id=? AND sel.source_id=? AND v.purged_at IS NULL AND r.purged_at IS NULL",
                (session_id, source_id)).fetchone()
            if pinned is None:
                continue
            source_version, name, parse_status = pinned["source_version"], pinned["original_name"], pinned["status"]
            run_sql, run_params = " AND run_id=?", (pinned["run_id"],)
        if parse_status not in {"complete", "partial"}:
            continue
        segments = [SegmentIn(r["segment_id"], json.loads(r["locator_json"]), r["text"]) for r in conn.execute(
            "SELECT segment_id, locator_json, text FROM segments WHERE source_id=?" + run_sql + " ORDER BY ordinal",
            (source_id, *run_params))]
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
                               asset_descriptions=asset_descriptions))
    return result


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
    return PreflightOut(
        preflight_id=row["preflight_id"], session_id=row["session_id"], input_revision=row["input_revision"],
        usable_source_ids=json.loads(row["usable_source_ids"]),
        facts=facts, sufficiency=assess_sufficiency(facts, issues),
        issues=issues,
        recommendations=Recommendations.model_validate_json(row["recommendations_json"]),
        can_generate=bool(row["can_generate"]), confirmed_at=row["confirmed_at"],
    )


def confirm(conn: Connection, preflight_id: str) -> str:
    stamp = to_iso(now())
    conn.execute("UPDATE preflights SET confirmed_at=COALESCE(confirmed_at, ?) WHERE preflight_id=?", (stamp, preflight_id))
    return conn.execute("SELECT confirmed_at FROM preflights WHERE preflight_id=?", (preflight_id,)).fetchone()[0]
