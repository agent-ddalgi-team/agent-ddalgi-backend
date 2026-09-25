"""AI 작업(preflight / draft) 실행기. 업로드 읽기와 같이 백그라운드에서 돈다.

- 시작할 때 세션이 살아 있고 input_revision이 아직 최신인지 다시 본다. 아니면 failed(INPUT_REVISION_CONFLICT).
- Agent 결과는 서버가 검사한다: segment_id·asset_id는 선택 자료에 실제로 있어야 하고, fact_id는 이번 사실 목록에 있어야 한다.
  없는 ID가 있으면 저장하지 않고 failed(AGENT_OUTPUT_INVALID). 자동 보정하지 않는다.
- Agent 구현이 async면 여기서 이벤트 루프를 열어 돌린다(백그라운드 스레드에는 루프가 없다).
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Callable
from typing import Any

from app.agent_bridge import (AgentError, AgentUnavailable, AnalyzeRequest, AnalyzeResult, DraftRequest,
                              DraftResult, SourceIn, get_bridge)
from app.config import Settings
from app.db import connect
from app.models import Brief, Page
from app.services import documents, jobs, preflights

logger = logging.getLogger(__name__)


def _run(fn: Callable[[Any], Any], request: Any) -> Any:
    result = fn(request)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _load_session_for_job(conn, session_id: str, input_revision: int):
    row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if row is None or row["status"] != "active":
        return None, ("SESSION_EXPIRED", "세션이 종료되었거나 만료되었습니다.", False)
    if row["input_revision"] != input_revision:
        return None, ("INPUT_REVISION_CONFLICT", "작업 중 입력이 바뀌어 결과를 버렸습니다. 다시 요청해 주세요.", False)
    return row, None


def _check_refs(evidence_refs, allowed_segments: set[str], versions: dict[str, int]) -> str | None:
    for ref in evidence_refs:
        if ref.segment_id not in allowed_segments:
            return f"선택 자료에 없는 segment_id: {ref.segment_id}"
        if versions.get(ref.source_id) != ref.source_version:
            return f"자료 버전 불일치: {ref.source_id}"
    return None


def validate_analyze(result: AnalyzeResult, sources: list[SourceIn]) -> str | None:
    segs, _, versions = preflights.allowed_ids(sources)
    seen: set[str] = set()
    for fact in result.facts:
        if fact.fact_id in seen:
            return f"fact_id 중복: {fact.fact_id}"
        seen.add(fact.fact_id)
        if fact.status == "missing" and (fact.value is not None or fact.evidence_refs):
            return f"missing인데 값·근거가 있음: {fact.fact_id}"
        if fact.status != "missing" and not fact.evidence_refs:
            return f"근거 없는 사실: {fact.fact_id}"
        if (problem := _check_refs(fact.evidence_refs, segs, versions)) is not None:
            return problem
    for issue in result.issues:
        if any(fid not in seen for fid in issue.fact_ids):
            return f"없는 fact_id를 가리키는 issue: {issue.issue_id}"
    return None


def validate_draft(result: DraftResult, sources: list[SourceIn], fact_ids: set[str]) -> str | None:
    segs, assets, versions = preflights.allowed_ids(sources)
    page_ids: set[str] = set()
    block_ids: set[str] = set()
    for page in result.pages:
        if page.page_id in page_ids:
            return f"page_id 중복: {page.page_id}"
        page_ids.add(page.page_id)
        for block in page.blocks:
            if block.block_id in block_ids:
                return f"block_id 중복: {block.block_id}"
            block_ids.add(block.block_id)
            if any(fid not in fact_ids for fid in block.fact_ids):
                return f"사전 점검에 없는 fact_id: {block.block_id}"
            if (problem := _check_refs(block.evidence_refs, segs, versions)) is not None:
                return problem
            if block.type == "image":
                if block.content.get("asset_id") not in assets:
                    return f"선택 자료에 없는 asset_id: {block.content.get('asset_id')}"
            if block.type == "paragraph" and not block.fact_ids and block.content.get("text") not in {"추가 확인 필요", "자료에서 확인되지 않음"}:
                return f"근거 없는 문단: {block.block_id}"
    if not result.pages:
        return "페이지가 없음"
    return None


def _fail_agent(conn, job_id: str, exc: Exception) -> None:
    if isinstance(exc, AgentError):
        jobs.fail(conn, job_id, exc.code, exc.message, exc.retryable)
    elif isinstance(exc, AgentUnavailable):
        jobs.fail(conn, job_id, "SERVICE_TEMPORARY_FAILURE", str(exc), True)
    else:
        logger.exception("agent call failed: %s", job_id)
        jobs.fail(conn, job_id, "SERVICE_TEMPORARY_FAILURE", "AI 호출이 실패했습니다. 잠시 후 다시 시도해 주세요.", True)


def run_preflight_job(settings: Settings, session_id: str, job_id: str, input_revision: int) -> None:
    try:
        with connect(settings.db_path) as conn:
            row, err = _load_session_for_job(conn, session_id, input_revision)
            if err:
                jobs.fail(conn, job_id, *err)
                return
            jobs.set_progress(conn, job_id, "analyzing", "자료에서 사실을 정리하는 중")
            brief = Brief.model_validate_json(row["brief_json"])
            sources = preflights.build_sources(conn, session_id, json.loads(row["selected_source_ids"]))
        try:
            bridge = get_bridge(settings)
            result: AnalyzeResult = _run(bridge.analyze, AnalyzeRequest(session_id, input_revision, brief, sources))
        except Exception as exc:
            with connect(settings.db_path) as conn:
                _fail_agent(conn, job_id, exc)
            return
        problem = validate_analyze(result, sources)
        with connect(settings.db_path) as conn:
            if problem:
                logger.error("agent analyze output rejected (%s): %s", job_id, problem)
                jobs.fail(conn, job_id, "AGENT_OUTPUT_INVALID", "AI 분석 결과가 자료와 맞지 않아 저장하지 않았습니다.", True)
                return
            _, err = _load_session_for_job(conn, session_id, input_revision)
            if err:
                jobs.fail(conn, job_id, *err)
                return
            usable = [s.source_id for s in sources if s.segments]
            can_generate = bool(usable)  # 생성 조건: 텍스트 근거가 있는 선택 자료 1개 이상(사용자 확인은 별도)
            preflight_id = preflights.save(conn, session_id, input_revision, usable, result.facts, result.issues,
                                           result.recommendations, can_generate)
            jobs.succeed(conn, job_id, {"type": "preflight", "preflight_id": preflight_id})
    except Exception:
        logger.exception("preflight job crashed: %s", job_id)
        with connect(settings.db_path) as conn:
            jobs.fail(conn, job_id, "INTERNAL_ERROR", "사전 점검 작업이 실패했습니다.", True)


def run_draft_job(settings: Settings, session_id: str, job_id: str, input_revision: int, preflight_id: str) -> None:
    try:
        with connect(settings.db_path) as conn:
            row, err = _load_session_for_job(conn, session_id, input_revision)
            if err:
                jobs.fail(conn, job_id, *err)
                return
            jobs.set_progress(conn, job_id, "drafting", "초안을 작성하는 중")
            brief = Brief.model_validate_json(row["brief_json"])
            sources = preflights.build_sources(conn, session_id, json.loads(row["selected_source_ids"]))
            preflight = preflights.get(conn, session_id, preflight_id)
        try:
            bridge = get_bridge(settings)
            result: DraftResult = _run(bridge.draft, DraftRequest(session_id, input_revision, brief, sources, preflight))
        except Exception as exc:
            with connect(settings.db_path) as conn:
                _fail_agent(conn, job_id, exc)
            return
        problem = validate_draft(result, sources, {f.fact_id for f in preflight.facts})
        with connect(settings.db_path) as conn:
            if problem:
                logger.error("agent draft output rejected (%s): %s", job_id, problem)
                jobs.fail(conn, job_id, "AGENT_OUTPUT_INVALID", "AI 초안이 자료와 맞지 않아 저장하지 않았습니다.", True)
                return
            _, err = _load_session_for_job(conn, session_id, input_revision)
            if err:
                jobs.fail(conn, job_id, *err)
                return
            if documents.exists_for_session(conn, session_id):
                jobs.fail(conn, job_id, "DOCUMENT_EXISTS", "이미 초안이 있습니다. 편집 화면에서 이어가 주세요.", False)
                return
            # 미해결 blocker가 있으면 검토 필요. 검증(BE-06) 전까지는 사전 점검의 문제로만 판단한다.
            status = "review_required" if any(i.severity == "blocker" and i.status == "open" for i in preflight.issues) else "draft"
            document_id = documents.create_initial(conn, session_id, input_revision, result.title,
                                                   brief.target_pages, result.pages, status)
            jobs.succeed(conn, job_id, {"type": "document", "document_id": document_id, "document_revision": 1})
    except Exception:
        logger.exception("draft job crashed: %s", job_id)
        with connect(settings.db_path) as conn:
            jobs.fail(conn, job_id, "INTERNAL_ERROR", "초안 생성 작업이 실패했습니다.", True)
