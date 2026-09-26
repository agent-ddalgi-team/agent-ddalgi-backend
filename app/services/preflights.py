"""Preflight 저장·조회와 Agent 입력 조립."""
from __future__ import annotations

import json
import sqlite3
import uuid

from app.agent_bridge import SegmentIn, SourceIn
from app.errors import ApiError
from app.models import Fact, Issue, PreflightOut, Recommendations
from app.timeutil import now, to_iso


def build_sources(conn: sqlite3.Connection, session_id: str, selected_source_ids: list[str]) -> list[SourceIn]:
    """선택한 자료만 Agent 입력으로 만든다. 이 세션의 첨부 또는 등록 자료(근거 사용 허용)만.

    다른 세션의 자료와 use_as_company_evidence=false인 등록 자료는 선택돼 있어도 절대 포함하지 않는다.
    """
    result: list[SourceIn] = []
    for source_id in selected_source_ids:
        row = conn.execute(
            "SELECT * FROM sources WHERE source_id=? AND deleted_at IS NULL AND parse_status IN ('complete', 'partial') "
            "AND ((scope='session' AND session_id=?) OR (scope='registered' AND use_as_company_evidence=1))",
            (source_id, session_id)).fetchone()
        if row is None:
            continue
        segments = [SegmentIn(r["segment_id"], json.loads(r["locator_json"]), r["text"]) for r in conn.execute(
            "SELECT segment_id, locator_json, text FROM segments WHERE source_id=? ORDER BY ordinal", (source_id,))]
        assets = [r["asset_id"] for r in conn.execute(
            "SELECT asset_id FROM assets WHERE source_id=? AND status='ready' AND deleted_at IS NULL", (source_id,))]
        result.append(SourceIn(source_id=row["source_id"], source_version=row["source_version"], kind=row["kind"],
                               name=row["name"], parse_status=row["parse_status"], segments=segments,
                               asset_ids=assets))
    return result


def allowed_ids(sources: list[SourceIn]) -> tuple[set[str], set[str], dict[str, int]]:
    """(segment_id 집합, asset_id 집합, source_id→version). AI 결과 검사에 쓴다."""
    segs = {s.segment_id for src in sources for s in src.segments}
    assets = {a for src in sources for a in src.asset_ids}
    versions = {src.source_id: src.source_version for src in sources}
    return segs, assets, versions


def save(conn: sqlite3.Connection, session_id: str, input_revision: int, usable_source_ids: list[str],
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


def get(conn: sqlite3.Connection, session_id: str, preflight_id: str) -> PreflightOut:
    row = conn.execute("SELECT * FROM preflights WHERE preflight_id=? AND session_id=?",
                       (preflight_id, session_id)).fetchone()
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    return PreflightOut(
        preflight_id=row["preflight_id"], session_id=row["session_id"], input_revision=row["input_revision"],
        usable_source_ids=json.loads(row["usable_source_ids"]),
        facts=[Fact.model_validate(f) for f in json.loads(row["facts_json"])],
        issues=[Issue.model_validate(i) for i in json.loads(row["issues_json"])],
        recommendations=Recommendations.model_validate_json(row["recommendations_json"]),
        can_generate=bool(row["can_generate"]), confirmed_at=row["confirmed_at"],
    )


def confirm(conn: sqlite3.Connection, preflight_id: str) -> str:
    stamp = to_iso(now())
    conn.execute("UPDATE preflights SET confirmed_at=COALESCE(confirmed_at, ?) WHERE preflight_id=?", (stamp, preflight_id))
    return conn.execute("SELECT confirmed_at FROM preflights WHERE preflight_id=?", (preflight_id,)).fetchone()[0]
