"""문서 내용의 참조(segment_id·source_version·asset_id·fact_id)가 이 세션에 실제로 있는지 검사한다.

초안 생성(BE-04)·직접 편집·편집안 적용·복원(BE-05)이 같은 검사를 쓴다. 문제가 있으면 이유 문자열을 돌려준다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.agent_bridge import SourceIn
from app.db import Connection
from app.models import EvidenceRef, Page, PreflightOut
from app.services.sources import evidence_scope


@dataclass
class SessionRefs:
    segment_ids: set[str]
    source_versions: dict[str, int]
    asset_ids: set[str]
    fact_ids: set[str]


def load(conn: Connection, session_id: str) -> SessionRefs:
    """이 세션의 첨부 + 등록 자료(근거 사용 허용). use_as_company_evidence=false 등록 자료의 구간·사진은 근거로 인정하지 않는다."""
    scope, params = evidence_scope(conn, session_id)
    segs = {r["segment_id"] for r in conn.execute(
        f"SELECT s.segment_id FROM segments s JOIN sources src ON src.source_id=s.source_id WHERE {scope}", params)}
    versions = {r["source_id"]: r["source_version"] for r in conn.execute(
        f"SELECT src.source_id, src.source_version FROM sources src WHERE {scope}", params)}
    assets = {r["asset_id"] for r in conn.execute(
        f"SELECT a.asset_id FROM assets a JOIN sources src ON src.source_id=a.source_id "
        f"WHERE a.status='ready' AND a.deleted_at IS NULL AND {scope}", params)}
    facts: set[str] = set()
    for r in conn.execute("SELECT facts_json FROM preflights WHERE session_id=?", (session_id,)):
        facts.update(f["fact_id"] for f in json.loads(r["facts_json"])
                     if all(ref.get("segment_id") in segs and versions.get(ref.get("source_id")) == ref.get("source_version")
                            for ref in f.get("evidence_refs", [])))
    return SessionRefs(segs, versions, assets, facts)


def check_pages(pages: list[Page], refs: SessionRefs) -> str | None:
    page_ids: set[str] = set()
    block_ids: set[str] = set()
    for page in pages:
        if page.page_id in page_ids:
            return f"page_id 중복: {page.page_id}"
        page_ids.add(page.page_id)
        for block in page.blocks:
            if block.block_id in block_ids:
                return f"block_id 중복: {block.block_id}"
            block_ids.add(block.block_id)
            for fid in block.fact_ids:
                if fid not in refs.fact_ids:
                    return f"이 세션의 사전 점검에 없는 fact_id: {fid}"
            for ref in block.evidence_refs:
                if ref.segment_id not in refs.segment_ids:
                    return f"세션 자료에 없는 segment_id: {ref.segment_id}"
                if refs.source_versions.get(ref.source_id) != ref.source_version:
                    return f"자료 버전 불일치: {ref.source_id}"
            if block.type == "image" and block.content.get("asset_id") not in refs.asset_ids:
                return f"세션 자료에 없는 asset_id: {block.content.get('asset_id')}"
    if not pages:
        return "페이지가 없음"
    return None


def evidence_problem(ref: EvidenceRef, sources: list[SourceIn]) -> str | None:
    """선택 자료의 고정된 버전·구간·위치·원문에 근거가 정확히 속하는지 검사한다."""
    matches = [source for source in sources if source.source_id == ref.source_id]
    if not matches:
        return f"선택 자료에 없는 source_id: {ref.source_id}"
    if len(matches) != 1:
        return f"선택 자료의 source_id 중복: {ref.source_id}"
    source = matches[0]
    if source.source_version != ref.source_version:
        return f"선택 자료 버전 불일치: {ref.source_id}"
    segments = [segment for segment in source.segments if segment.segment_id == ref.segment_id]
    if not segments:
        return f"해당 선택 자료에 없는 segment_id: {ref.segment_id}"
    if len(segments) != 1:
        return f"선택 자료의 segment_id 중복: {ref.segment_id}"
    segment = segments[0]
    if ref.locator != segment.locator:
        return f"선택 자료의 근거 위치 불일치: {ref.segment_id}"
    if not ref.excerpt.strip() or ref.excerpt not in segment.text:
        return f"선택 자료의 원문과 일치하지 않는 인용: {ref.segment_id}"
    return None


def selected_problem(pages: list[Page], sources: list[SourceIn], preflight: PreflightOut) -> str | None:
    """선택 자료와 지정 점검에 한정해 문서 참조를 검사한다. 내용의 의미 검증은 별도다.

    호출자가 세션·입력 버전·최신 점검 및 사용자 확인을 검사한 뒤 sources를 구성한다.
    미참조 충돌/미확인 사실의 차단 여부와 사진 공개 허가는 각 기존 검사가 담당한다.
    """
    versions = {}
    for source in sources:
        if source.source_id in versions:
            return f"선택 자료의 source_id 중복: {source.source_id}"
        versions[source.source_id] = source.source_version
    facts = {}
    for fact in preflight.facts:
        if fact.fact_id in facts:
            return f"현재 점검의 fact_id 중복: {fact.fact_id}"
        facts[fact.fact_id] = fact

    selected = SessionRefs(
        {segment.segment_id for source in sources for segment in source.segments},
        versions,
        {asset_id for source in sources for asset_id in source.asset_ids},
        set(facts),
    )
    if problem := check_pages(pages, selected):
        return problem
    checked_facts: set[str] = set()
    for page in pages:
        for block in page.blocks:
            if len(set(block.fact_ids)) != len(block.fact_ids):
                return f"블록의 fact_id 중복: {block.block_id}"
            for fact_id in block.fact_ids:
                if fact_id in checked_facts:
                    continue
                fact = facts[fact_id]
                if fact.status != "supported" or not fact.value or not fact.value.strip():
                    return f"현재 점검에서 확인되지 않은 fact_id: {fact_id}"
                if not fact.evidence_refs:
                    return f"현재 점검의 사실에 근거가 없음: {fact_id}"
                for ref in fact.evidence_refs:
                    if problem := evidence_problem(ref, sources):
                        return problem
                checked_facts.add(fact_id)
            for ref in block.evidence_refs:
                if problem := evidence_problem(ref, sources):
                    return problem
    return None
