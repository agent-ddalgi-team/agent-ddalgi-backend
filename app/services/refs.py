"""문서 내용의 참조(segment_id·source_version·asset_id·fact_id)가 이 세션에 실제로 있는지 검사한다.

초안 생성(BE-04)·직접 편집·편집안 적용·복원(BE-05)이 같은 검사를 쓴다. 문제가 있으면 이유 문자열을 돌려준다.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from app.models import Page


@dataclass
class SessionRefs:
    segment_ids: set[str]
    source_versions: dict[str, int]
    asset_ids: set[str]
    fact_ids: set[str]


def load(conn: sqlite3.Connection, session_id: str) -> SessionRefs:
    """이 세션의 첨부 + 등록 자료(근거 사용 허용). use_as_company_evidence=false 등록 자료의 구간·사진은 근거로 인정하지 않는다."""
    scope = ("((src.scope='session' AND src.session_id=?) OR (src.scope='registered' AND src.use_as_company_evidence=1)) "
             "AND src.deleted_at IS NULL")
    segs = {r["segment_id"] for r in conn.execute(
        f"SELECT s.segment_id FROM segments s JOIN sources src ON src.source_id=s.source_id WHERE {scope}", (session_id,))}
    versions = {r["source_id"]: r["source_version"] for r in conn.execute(
        f"SELECT src.source_id, src.source_version FROM sources src WHERE {scope}", (session_id,))}
    assets = {r["asset_id"] for r in conn.execute(
        f"SELECT a.asset_id FROM assets a JOIN sources src ON src.source_id=a.source_id "
        f"WHERE a.status='ready' AND a.deleted_at IS NULL AND {scope}", (session_id,))}
    facts: set[str] = set()
    for r in conn.execute("SELECT facts_json FROM preflights WHERE session_id=?", (session_id,)):
        facts.update(f["fact_id"] for f in json.loads(r["facts_json"]))
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
