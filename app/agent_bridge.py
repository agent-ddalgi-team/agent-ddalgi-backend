"""Agent 연결 지점 — 백엔드가 정하는 함수 서명. 실제 AI 구현은 Agent 담당(AG-03)이 같은 서명으로 붙인다.

역할 ① 자료 분석·기획: analyze(AnalyzeRequest) -> AnalyzeResult   (Preflight의 facts/issues/recommendations)
역할 ② 초안 작성:      draft(DraftRequest)     -> DraftResult     (Document의 title/pages)

규칙
- 구현은 동기 함수여도, async 함수여도 된다. 실행기(app/services/ai_jobs.py)가 awaitable이면 이벤트 루프에서 돌린다.
- 서버가 넘긴 segment_id·asset_id·fact_id만 결과에 쓸 수 있다. 결과는 서버가 다시 검사하며(ai_jobs), 없는 ID는 거부한다.
- 실패는 AgentError(code, message, retryable)로 알린다. mock으로 몰래 대체하지 않는다.
- AGENT_MODE=llm인데 구현(app/agent_llm.py의 create_bridge())이 없으면 AgentUnavailable → Job failed.
"""
from __future__ import annotations

import importlib
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import Settings
from app.models import Brief, Fact, Issue, Page, PreflightOut, Recommendations


# ---------------- 입력 ----------------

@dataclass
class SegmentIn:
    segment_id: str
    locator: dict[str, Any]
    text: str


@dataclass
class SourceIn:
    source_id: str
    source_version: int
    kind: str
    name: str
    parse_status: str
    segments: list[SegmentIn] = field(default_factory=list)   # 글자를 읽은 구간(없으면 빈 목록)
    asset_ids: list[str] = field(default_factory=list)        # 화면·출력에 쓸 수 있는 이미지


@dataclass
class AnalyzeRequest:
    session_id: str
    input_revision: int
    brief: Brief
    sources: list[SourceIn]          # 사용자가 선택한 자료만. 세션 밖 자료는 절대 들어오지 않는다.


@dataclass
class DraftRequest:
    session_id: str
    input_revision: int
    brief: Brief
    sources: list[SourceIn]
    preflight: PreflightOut          # 사용자가 확인한 사전 점검(facts·issues)


# ---------------- 출력 ----------------

@dataclass
class AnalyzeResult:
    facts: list[Fact]
    issues: list[Issue]
    recommendations: Recommendations


@dataclass
class DraftResult:
    title: str
    pages: list[Page]


class AgentError(Exception):
    """Agent가 알리는 실패. code는 contracts.md 오류 코드(AI_RATE_LIMIT, SERVICE_TEMPORARY_FAILURE 등)."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


class AgentUnavailable(Exception):
    """llm 모드인데 Agent 구현이 없거나 불러올 수 없음."""


class AgentBridge(Protocol):
    def analyze(self, request: AnalyzeRequest) -> AnalyzeResult | Awaitable[AnalyzeResult]: ...
    def draft(self, request: DraftRequest) -> DraftResult | Awaitable[DraftResult]: ...


def get_bridge(settings: Settings) -> AgentBridge:
    if settings.agent_mode == "mock":
        from app.agent_mock import MockAgent

        return MockAgent()
    try:
        module = importlib.import_module("app.agent_llm")  # Agent 담당 소유. 백엔드가 만들지 않는다.
    except ImportError as exc:
        raise AgentUnavailable("Agent 구현(app/agent_llm.py)이 아직 연결되지 않았습니다.") from exc
    factory = getattr(module, "create_bridge", None)
    if factory is None:
        raise AgentUnavailable("app/agent_llm.py에 create_bridge()가 없습니다.")
    return factory(settings)
