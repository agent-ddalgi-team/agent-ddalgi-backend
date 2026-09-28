"""AG-01~04 첫 연결: 가짜 회사·가짜 모델 응답만 사용한다.

실행: PYTHON_DOTENV_DISABLED=1 .venv/bin/python -B -m pytest tests/test_agent_llm.py
테스트 중 외부 소켓 연결과 실제 SDK 클라이언트 생성을 금지한다.
실제 모델의 사실 판단·표현 품질·출력 배치는 이 테스트의 통과 범위가 아니다.
"""
from __future__ import annotations

import copy
import hashlib
import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx2
import pytest
from fastapi.testclient import TestClient
from openai import APITimeoutError, AuthenticationError, BadRequestError, RateLimitError

from app import create_app
from app import agent_legacy as legacy, agent_llm as llm
from app.agent_bridge import AgentError, AnalyzeRequest, DraftRequest, SegmentIn, SourceIn
from app.config import Settings
from app.db import connect
from app.errors import ApiError
from app.models import Brief, Document, PreflightOut
from app.services import ai_jobs, cleanup, jobs, preflights, refs, sweeper, validation
from app.services.ai_jobs import validate_analyze, validate_draft
from app.services.registered import import_bundle

BRIEF = Brief(purpose="가짜 회사 소개", emphasis=[], direction="balanced", target_pages=6, photo_preference="none")
TEXTS = {
    "company_name": "연결테스트회사",
    "company_summary": "연결 확인용 부품을 생산합니다.",
    "products_services": "시험 제품 A와 시험 제품 B",
    "lead_time": "일반 주문은 승인 후 7일, 특수 주문은 별도 협의",
}


# D-04 첫 실제 시험에 사용할 입력 원본. 실제 호출 기능은 이 파일에 추가하지 않는다.
# 기존 fixture를 읽어 필요한 줄만 메모리에서 선택한다. 원본 파일은 변경하지 않는다.
_TRIAL_LINES_A = (Path(__file__).parent / "fixtures" / "mock_source_a.txt").read_text(encoding="utf-8").splitlines()
_TRIAL_TEXT_B = (Path(__file__).parent / "fixtures" / "mock_source_b.txt").read_text(encoding="utf-8").strip()
D04_TRIAL_CASES = {
    "T01": {"name": "정상", "target_pages": 1, "texts": ["\n".join(_TRIAL_LINES_A[:3])]},
    "T02": {"name": "필수 정보 누락", "target_pages": 6, "texts": ["\n".join(_TRIAL_LINES_A[2:4])]},
    "T03": {"name": "수치 충돌", "target_pages": 6,
            "texts": ["\n".join(_TRIAL_LINES_A[:4]), _TRIAL_TEXT_B]},
    "T04": {"name": "조건부 납기", "target_pages": 6, "texts": ["\n".join(_TRIAL_LINES_A[:3] + [
        "납기 조건: 일반 주문은 주문 승인 후 영업일 7일, 특수 주문은 납기 별도 협의."])]},
}

# 평가자가 정한 기대값이다. AI에게 보내는 입력·프롬프트에는 포함하지 않는다.
# 각 사실: (기대 문구, 원자료 순번, 파생 시험 자료의 줄 번호).
_D04_BASE_EXPECTED = {
    "company_name": ("supported", [("테스트 회사", 0, 1)]),
    "company_summary": ("supported", [("테스트용 기업입니다.", 0, 2)]),
    "business_areas": ("supported", [("테스트 사업 A", 0, 3)]),
}
D04_EXPECTED = {
    "T01": _D04_BASE_EXPECTED,
    "T02": {"business_areas": ("supported", [("테스트 사업 A", 0, 1)]),
            "process_count": ("supported", [("공정 수: 2개", 0, 2)])},
    "T03": _D04_BASE_EXPECTED | {"process_count": ("conflict", [
        ("공정 수: 2개", 0, 4), ("공정 수: 3개", 1, 2)])},
    "T04": _D04_BASE_EXPECTED | {"lead_time": ("supported", [
        ("일반 주문은 주문 승인 후 영업일 7일, 특수 주문은 납기 별도 협의.", 0, 4)])},
}


def build_d04_trial_request(case_id):
    """입력만 구성한다. 모델을 호출하거나 사전 확인을 자동 승인하지 않는다."""
    case = D04_TRIAL_CASES[case_id]
    selected = [SourceIn(f"src_trial_{case_id}_{i + 1}", 1, "company",
                        f"{case_id} 가짜 자료 {i + 1}", "complete", [
                            SegmentIn(f"seg_trial_{case_id}_{i + 1}_{line_no}",
                                      {"line_start": line_no, "line_end": line_no}, line)
                            for line_no, line in enumerate(text.splitlines(), 1)
                        ], origin_kind="mock") for i, text in enumerate(case["texts"])]
    brief = Brief(purpose="가짜 자료로 회사소개서 작성 연결 확인", emphasis=[], direction="balanced",
                  target_pages=case["target_pages"], photo_preference="none")
    return AnalyzeRequest(f"ses_trial_{case_id}", 1, brief, selected)


def d04_fake_extraction(case_id, payload):
    """시험용 기대 응답이다. 자료의 의미를 판단하는 실제 모델 기능은 아니다."""
    result = {key: {"status": "not_found", "facts": []} for key in legacy.COMPANY_INFO_KEYS}
    for field_key, (status, items) in D04_EXPECTED[case_id].items():
        facts = []
        for text, source_index, line_no in items:
            source_id = f"src_trial_{case_id}_{source_index + 1}"
            locator = f"segment:seg_trial_{case_id}_{source_index + 1}_{line_no}"
            unit = next(unit for unit in payload["source_units"]
                        if unit["source_id"] == source_id and unit["locator"] == locator)
            assert text in unit["text"]  # 기대값이 파생 원문의 실제 위치에 있어야 함.
            facts.append({"text": text, "evidence": [{"source_id": source_id,
                          "locator": locator, "quote": unit["text"]}]})
        result[field_key] = {"status": status, "facts": facts}
    return result


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("이 테스트에서는 외부 네트워크와 실제 SDK 클라이언트를 사용할 수 없습니다.")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "OpenAI", forbidden)
    monkeypatch.setattr(llm, "_trial", llm.TrialLedger())


def sources():
    return [SourceIn("src_demo", 3, "registered", "가짜 등록 자료", "complete", [
        SegmentIn("seg_a", {"page": 2, "paragraph": 1}, "\n".join(TEXTS.values())),
        SegmentIn("seg_b", {"page": 2, "paragraph": 2}, "공정은 3개입니다. 납기는 빠릅니다."),
    ], origin_kind="demo"), SourceIn("src_session", 1, "session", "가짜 세션 자료", "complete", [
        SegmentIn("seg_c", {"line_start": 1, "line_end": 1}, "공정은 4개입니다."),
    ])]


def extraction(payload):
    """본문에 실제 있는 가짜 문구만 찾아 모델 응답 모양으로 돌려준다."""
    result = {key: {"status": "not_found", "facts": []} for key in legacy.COMPANY_INFO_KEYS}
    for key, value in TEXTS.items():
        unit = next((u for u in payload["source_units"] if value in u["text"]), None)
        if unit:
            result[key] = {"status": "supported", "facts": [{"text": value, "evidence": [{
                "source_id": unit["source_id"], "locator": unit["locator"], "quote": value,
            }]}]}
    return result


def draft_response(payload):
    return {"draft_sections": [{"key": s["key"], "title": s["title"], "paragraphs": [
        {"text": f["text"], "fact_ids": [f["fact_id"]]}
        for f in payload["supported_facts"] if f["field"] == s["key"]
    ]} for s in payload["sections_to_write"]]}


class FakeModel:
    def __init__(self, extract_change=None, draft_change=None):
        self.calls = []
        self.extract_change, self.draft_change = extract_change, draft_change

    def __call__(self, instructions, payload, schema, schema_name):
        self.calls.append((schema_name, copy.deepcopy(payload)))
        assert instructions and schema["type"] == "object"
        if schema_name == "company_info":
            result, change = extraction(payload), self.extract_change
        else:
            assert schema_name == "draft_sections"
            result, change = draft_response(payload), self.draft_change
        if change:
            change(result)
        return result


def analyzed(model=None):
    model = model or FakeModel()
    agent = llm.LlmAgent(model)
    request = AnalyzeRequest("ses_test", 2, BRIEF, sources())
    result = agent.analyze(request)
    pf = PreflightOut(preflight_id="pf_test", session_id=request.session_id, input_revision=2,
                      usable_source_ids=[s.source_id for s in request.sources], facts=result.facts,
                      issues=result.issues, recommendations=result.recommendations, can_generate=True,
                      confirmed_at="2026-09-28T00:00:00Z")
    return agent, DraftRequest(request.session_id, 2, BRIEF, request.sources, pf), model


def analyze_sections(brief, texts, *, change=None, assets=(), parse_status="complete"):
    """추천 검사도 기존 추출·근거 변환을 거친다. 응답은 가짜 자료에서만 만든다."""
    selected = [SourceIn("src_recommend", 1, "company", "추천 검사 자료", parse_status, [
        SegmentIn(f"seg_recommend_{key}", {"line_start": n, "line_end": n}, value)
        for n, (key, value) in enumerate(texts.items(), 1)
    ], asset_ids=list(assets), origin_kind="mock")]
    def fill(info):
        for key, value in texts.items():
            info[key] = {"status": "supported", "facts": [{"text": value, "evidence": [{
                "source_id": "src_recommend", "locator": f"segment:seg_recommend_{key}", "quote": value,
            }]}]}
        if change:
            change(info)
    model = FakeModel(extract_change=fill)
    request = AnalyzeRequest("ses_recommend", 1, brief, selected)
    before = copy.deepcopy(request)
    agent = llm.LlmAgent(model)
    result = agent.analyze(request)
    assert request == before and len(model.calls) == 1
    assert validate_analyze(result, selected) is None
    return agent, request, result, model


def analyze_recommendations(brief, texts, **kwargs):
    return analyze_sections(brief, texts, **kwargs)[2]


@pytest.mark.parametrize("fields,chars_per_field,expected", [(3, 100, 1), (3, 500, 4),
                                                           (6, 650, 6), (9, 700, 8), (11, 800, 10)])
def test_recommendations_follow_supported_content_without_changing_requested_pages(fields, chars_per_field, expected):
    texts = {"company_name": "추천 검사 회사", **{
        key: key + "검사용내용" * (chars_per_field // 5) for key in legacy.SECTION_ORDER[:fields]
    }}
    brief = BRIEF.model_copy(update={"target_pages": 10})
    result = analyze_recommendations(brief, texts)
    assert result.recommendations.suggested_pages == expected
    assert brief.target_pages == 10
    assert "추천 구성:" in result.recommendations.reason
    assert "작성 설정 변경과 재점검" in result.recommendations.reason


def test_recommendations_deduplicate_facts_and_shared_evidence():
    texts = {"company_name": "추천 검사 회사", **{
        key: key + "검사용내용" * 100 for key in legacy.SECTION_ORDER[:3]
    }}
    baseline = analyze_recommendations(BRIEF, texts)
    def duplicate(info):
        for item in info.values():
            item["facts"] *= 10
    repeated = analyze_recommendations(BRIEF, texts, change=duplicate)
    assert repeated.recommendations == baseline.recommendations
    # 같은 짧은 원문을 길게 풀어 쓴 응답도 근거 분량까지만 고려한다.
    short = {key: value[:30] for key, value in texts.items()}
    def expand(info):
        for key, item in info.items():
            if key != "company_name" and item["facts"]:
                item["facts"][0]["text"] *= 100
    expanded = analyze_recommendations(BRIEF, short, change=expand)
    assert expanded.recommendations.suggested_pages == 1


@pytest.mark.parametrize("purpose,expected", [("상세 소개", 6), ("한 장 요약", 1), ("요약하지 말고 상세 소개", 6),
                                             ("보유한 장비와 상세한 공정 소개", 6), ("한 장으로 소개", 1),
                                             ("11쪽 자료를 활용한 소개", 6), ("1쪽으로 소개", 1),
                                             ("요약하지 않고 상세 소개", 6)])
def test_recommendations_use_summary_purpose_without_changing_brief(purpose, expected):
    texts = {"company_name": "추천 검사 회사", **{
        key: key + "검사용내용" * 140 for key in legacy.SECTION_ORDER[:6]
    }}
    brief = BRIEF.model_copy(update={"purpose": purpose})
    assert analyze_recommendations(brief, texts).recommendations.suggested_pages == expected


@pytest.mark.parametrize("settings,first", [
    ({"direction": "quality_process"}, "기술"),
    ({"direction": "customer_response"}, "납기 조건"),
    ({"purpose": "납기 안내용 소개"}, "납기 조건"),
    ({"direction": "quality_process", "emphasis": ["납기"]}, "납기 조건"),
])
def test_recommendations_order_supported_sections_by_direction_purpose_and_emphasis(settings, first):
    texts = {"company_name": "추천 검사 회사", "company_summary": "회사 소개입니다.",
             "technology": "검사 기술 A", "processes": "검사 공정 B", "lead_time": "주문 승인 후 7일"}
    result = analyze_recommendations(BRIEF.model_copy(update=settings), texts)
    assert f"추천 구성: 회사 개요 → {first}" in result.recommendations.reason
    assert "인증" not in result.recommendations.reason.split("추천 구성: ")[1].split(".")[0]


@pytest.mark.parametrize("excluded", ["인증 제외", "인증서 제외", "인증은 필요 없음"])
def test_recommendations_respect_explicit_exclusions_and_keep_certification_optional(excluded):
    brief = BRIEF.model_copy(update={"direction": "quality_process", "emphasis": [excluded]})
    result = analyze_recommendations(brief, TEXTS)
    assert all("인증" not in item for item in result.recommendations.needed)
    assert all(not item.startswith("사진:") for item in result.recommendations.needed)
    without_exclusion = analyze_recommendations(BRIEF.model_copy(update={"direction": "quality_process"}), TEXTS)
    assert any(item.startswith("선택 보완 — 인증") for item in without_exclusion.recommendations.needed)
    assert not without_exclusion.issues


def test_order_approval_purpose_does_not_request_certifications():
    result = analyze_recommendations(BRIEF.model_copy(update={"purpose": "주문 승인 후 납기 안내"}), TEXTS)
    assert "추천 구성: 회사 개요 → 납기 조건" in result.recommendations.reason
    assert all("인증" not in item for item in result.recommendations.needed)


def test_recommendations_keep_conflicts_uncertainty_and_partial_reading_as_supplements():
    texts = TEXTS | {"technology": "검사 기술의 조건은 확인이 필요합니다." * 100,
                     "processes": "공정 A와 공정 B 중 적용 대상은 확인이 필요합니다." * 100}
    def uncertain(info):
        info["technology"]["status"] = "needs_confirmation"
        item = info["processes"]["facts"][0]
        info["processes"] = {"status": "conflict", "facts": [dict(item, text="공정 A"), dict(item, text="공정 B")]}
    result = analyze_recommendations(BRIEF.model_copy(update={"direction": "quality_process"}), texts,
                                     change=uncertain, parse_status="partial")
    assert result.recommendations.suggested_pages == 1
    needed = " ".join(result.recommendations.needed)
    assert "서로 다른 값의 원문" in needed and "조건·적용 범위" in needed and "읽기가 완전하지" in needed
    assert {issue.code for issue in result.issues} == {"VALUE_CONFLICT", "UNSUPPORTED_CLAIM"}
    assert all(issue.severity == "blocker" and issue.status == "open" for issue in result.issues)
    outline = result.recommendations.reason.split("추천 구성: ")[1].split(".")[0]
    assert "기술" not in outline and "공정 목록" not in outline


@pytest.mark.parametrize("preference,assets", [("none", ["asset_a", "asset_b"]), ("balanced", []),
                                             ("many", ["asset_a", "asset_a", "asset_b"])])
def test_recommendations_use_photo_availability_without_assuming_contents(preference, assets):
    brief = BRIEF.model_copy(update={"photo_preference": preference})
    result = analyze_recommendations(brief, TEXTS, assets=assets)
    rec = result.recommendations
    assert rec.suggested_pages == 1
    assert not any("asset_" in text for text in [rec.reason, *rec.needed])
    if preference == "none":
        assert "사진 사용 안 함" in rec.reason and all(not item.startswith("사진:") for item in rec.needed)
    elif not assets:
        assert "사용 가능한 사진이 없어" in rec.reason and any(item.startswith("사진:") for item in rec.needed)
    else:
        assert "사진 2개" in rec.reason and "내용과 관련성을 확인" in rec.reason and "사진 비중을 높일 영역" in rec.reason


def test_extract_restores_source_version_location_and_keeps_conditions():
    model = FakeModel()
    request = AnalyzeRequest("ses_test", 2, BRIEF, sources())
    result = llm.LlmAgent(model).analyze(request)
    assert validate_analyze(result, request.sources) is None
    assert len(result.facts) == 14
    found = {f.field_key: f for f in result.facts}
    assert found["lead_time"].value == TEXTS["lead_time"]
    assert found["certifications"].value is None and found["certifications"].evidence_refs == []
    ref = found["company_name"].evidence_refs[0]
    assert (ref.source_id, ref.source_version, ref.segment_id, ref.locator) == (
        "src_demo", 3, "seg_a", {"page": 2, "paragraph": 1})
    assert request.sources[0].origin_kind == "demo"
    sent = model.calls[0][1]["source_units"]
    assert {u["locator"] for u in sent} == {"segment:seg_a", "segment:seg_b", "segment:seg_c"}
    assert all(set(u) == {"source_id", "locator", "text"} for u in sent)
    second = llm.LlmAgent(model).analyze(request)
    assert not ({f.fact_id for f in result.facts} & {f.fact_id for f in second.facts})


def test_multiple_facts_conflict_candidates_and_uncertain_text_are_preserved():
    def change(info):
        item = info["products_services"]["facts"][0]
        info["products_services"]["facts"] = [dict(item, text="시험 제품 A"), dict(item, text="시험 제품 B")]
        info["lead_time"] = {"status": "needs_confirmation", "facts": [{"text": "납기는 빠릅니다.",
            "evidence": [{"source_id": "src_demo", "locator": "segment:seg_b", "quote": "납기는 빠릅니다."}]}]}
        info["process_count"] = {"status": "conflict", "facts": [
            {"text": "공정은 3개입니다.", "evidence": [{"source_id": "src_demo", "locator": "segment:seg_b", "quote": "공정은 3개입니다."}]},
            {"text": "공정은 4개입니다.", "evidence": [{"source_id": "src_session", "locator": "segment:seg_c", "quote": "공정은 4개입니다."}]},
        ]}
    agent, request, _ = analyzed(FakeModel(extract_change=change))
    facts = request.preflight.facts
    assert len(facts) == 15
    assert [f.value for f in facts if f.field_key == "products_services"] == ["시험 제품 A", "시험 제품 B"]
    conflict = next(f for f in facts if f.status == "conflict")
    assert conflict.value is None
    assert [a["value"] for a in conflict.alternatives] == ["공정은 3개입니다.", "공정은 4개입니다."]
    assert {r.source_id for r in conflict.evidence_refs} == {"src_demo", "src_session"}
    assert all(a["evidence_refs"] for a in conflict.alternatives)
    assert all(i.severity == "blocker" for i in request.preflight.issues)
    draft = agent.draft(request)
    body = " ".join(b.content.get("text", "") for p in draft.pages for b in p.blocks)
    assert "추가 확인 필요" in body and "공정은 3개" not in body and "납기는 빠릅니다" not in body


@pytest.mark.parametrize("bad", [
    {"source_id": "other_session"}, {"source_id": "src_session"}, {"locator": "segment:unknown"},
    {"locator": "segment:seg_b"}, {"quote": "원문에 없는 회사"}, {"quote": ""},
])
def test_unknown_or_mismatched_evidence_is_rejected_without_leaking_text(bad):
    def change(info):
        info["company_name"]["facts"][0]["evidence"][0].update(bad)
    with pytest.raises(AgentError) as error:
        analyzed(FakeModel(extract_change=change))
    assert error.value.code == "AGENT_OUTPUT_INVALID"
    assert "원문에 없는 회사" not in str(error.value)


@pytest.mark.parametrize("bad_kind", ["missing_field", "extra_field", "missing_with_value", "model_fact_id", "one_conflict"])
def test_legacy_extraction_shape_checks_are_still_enforced(bad_kind):
    def change(info):
        if bad_kind == "missing_field":
            info.pop("technology")
        elif bad_kind == "extra_field":
            info["new_field"] = {"status": "not_found", "facts": []}
        elif bad_kind == "missing_with_value":
            info["company_name"]["status"] = "not_found"
        elif bad_kind == "model_fact_id":
            info["company_name"]["facts"][0]["fact_id"] = "made_by_model"
        else:
            info["company_name"]["status"] = "conflict"
    with pytest.raises(AgentError, match="AGENT_OUTPUT_INVALID"):
        analyzed(FakeModel(extract_change=change))


STRUCTURE_TEXTS = {
    "company_name": "구성 검사 회사", "company_summary": "가짜 부품을 생산하는 시험 회사입니다.",
    "products_services": "시험 제품 A를 공급합니다.", "technology": "시험 기술 T를 사용합니다.",
    "processes": "세척과 검사 공정이 있습니다.", "certifications": "시험 인증 C의 적용 범위는 제품 A입니다.",
    "lead_time": "일반 주문은 승인 후 영업일 7일이며 특수 주문은 납기를 별도 협의합니다.",
}


def structured_draft(brief, *, change=None):
    agent, selected, result, model = analyze_sections(brief, STRUCTURE_TEXTS, change=change)
    pf = PreflightOut(preflight_id="pf_structure", session_id=selected.session_id, input_revision=1,
                      usable_source_ids=[s.source_id for s in selected.sources], facts=result.facts,
                      issues=result.issues, recommendations=result.recommendations, can_generate=True,
                      confirmed_at="2026-09-28T00:00:00Z")
    return agent, DraftRequest(selected.session_id, 1, brief, selected.sources, pf), model


@pytest.mark.parametrize("pages", [1, 6])
@pytest.mark.parametrize("settings,order", [
    ({}, ["company_summary", "products_services", "technology", "certifications", "processes", "lead_time"]),
    ({"direction": "quality_process"}, ["company_summary", "technology", "processes", "certifications", "products_services", "lead_time"]),
    ({"direction": "customer_response"}, ["company_summary", "products_services", "lead_time", "technology", "certifications", "processes"]),
    ({"purpose": "납기 안내"}, ["company_summary", "lead_time", "products_services", "technology", "certifications", "processes"]),
    ({"emphasis": ["공정"], "purpose": "납기 안내", "direction": "quality_process"},
     ["company_summary", "processes", "lead_time", "technology", "certifications", "products_services"]),
])
def test_draft_uses_requested_structure_even_when_model_returns_reverse_order(pages, settings, order):
    brief = BRIEF.model_copy(update={"target_pages": pages, **settings})
    agent, request, model = structured_draft(brief)
    before = copy.deepcopy(request)
    model.draft_change = lambda result: result["draft_sections"].reverse()
    draft = agent.draft(request)
    blocks = [b for p in draft.pages for b in p.blocks]
    headings = [b.content["text"] for b in blocks if b.type == "heading" and b.content["level"] == 2 and b.fact_ids]
    assert headings == [legacy.SECTION_TITLES[key] for key in order]
    assert [s["key"] for s in model.calls[-1][1]["sections_to_write"]] == order
    # 추천의 1쪽 미리보기 4개 항목 때문에 나머지 근거 있는 항목을 버리지 않는다.
    expected_ids = {f.fact_id for f in request.preflight.facts if f.status == "supported"}
    assert {fid for b in blocks for fid in b.fact_ids} == expected_ids
    assert STRUCTURE_TEXTS["lead_time"] in [b.content.get("text") for b in blocks]
    assert validate_draft(draft, request.sources, expected_ids) is None
    assert request == before and len(draft.pages) == pages and len(model.calls) == 2


@pytest.mark.parametrize("phrase", ["인증 제외", "인증서 생략", "인증은 필요 없음",
                                    "인증은 제외하지만 납기는 강조", "인증은 생략하지만 납기는 강조"])
def test_draft_omits_explicitly_excluded_facts_only_from_generation(phrase):
    agent, request, model = structured_draft(BRIEF.model_copy(update={"emphasis": [phrase]}))
    before = copy.deepcopy(request)
    draft = agent.draft(request)
    excluded_id = next(f.fact_id for f in request.preflight.facts if f.field_key == "certifications")
    assert all(f["field"] != "certifications" for f in model.calls[-1][1]["supported_facts"])
    assert all(s["key"] != "certifications" for s in model.calls[-1][1]["sections_to_write"])
    assert all(excluded_id not in b.fact_ids and b.content.get("text") != legacy.SECTION_TITLES["certifications"]
               for p in draft.pages for b in p.blocks)
    assert request == before


@pytest.mark.parametrize("phrase", ["인증 강조하지 말고 납기 강조", "인증 제외하지 말고", "인증 빼지 마", "인증 불필요하지 않음",
                                    "인증은 빼놓지 말고", "인증을 빼먹지 마", "인증 생략 없이", "인증은 빼서는 안 됨",
                                    "인증은 제외하지는 말고", "인증은 제외가 아니라 유지"])
def test_draft_does_not_drop_facts_for_deemphasis_or_negated_exclusion(phrase):
    agent, request, model = structured_draft(BRIEF.model_copy(update={"emphasis": [phrase]}))
    draft = agent.draft(request)
    assert any(f["field"] == "certifications" for f in model.calls[-1][1]["supported_facts"])
    assert any(b.content.get("text") == STRUCTURE_TEXTS["certifications"] for p in draft.pages for b in p.blocks)


def test_draft_rejects_excluded_fact_returned_inside_another_section():
    agent, request, model = structured_draft(BRIEF.model_copy(update={"emphasis": ["인증 제외"]}))
    excluded_id = next(f.fact_id for f in request.preflight.facts if f.field_key == "certifications")
    def inject(result):
        result["draft_sections"][0]["paragraphs"][0]["fact_ids"].append(excluded_id)
    model.draft_change = inject
    with pytest.raises(AgentError, match="AGENT_OUTPUT_INVALID"):
        agent.draft(request)


def test_draft_keeps_excluded_conflicts_and_required_missing_in_preflight():
    def unresolved(info):
        info["company_name"] = {"status": "not_found", "facts": []}
        certification = info["certifications"]
        certification["status"] = "conflict"
        second = copy.deepcopy(certification["facts"][0])
        second["text"] = "다른 적용 범위: 확인 필요"
        certification["facts"].append(second)
    agent, request, model = structured_draft(BRIEF.model_copy(update={"emphasis": ["인증 제외"]}), change=unresolved)
    before = copy.deepcopy(request.preflight)
    draft = agent.draft(request)
    assert request.preflight == before
    assert {i.code for i in request.preflight.issues} == {"REQUIRED_MISSING", "VALUE_CONFLICT"}
    assert all(i.severity == "blocker" and i.status == "open" for i in request.preflight.issues)
    assert not any(f["field"] == "certifications" for f in model.calls[-1][1]["supported_facts"])
    assert any(b.content.get("text") == "회사명" for p in draft.pages for b in p.blocks)
    facts = {fact.fact_id: fact for fact in request.preflight.facts}
    context = validation.Context(
        seg_texts={seg.segment_id: seg.text for source in request.sources for seg in source.segments},
        seg_source={seg.segment_id: source.source_id for source in request.sources for seg in source.segments},
        asset_source={}, mock_sources=set(),
        refs=refs.SessionRefs({seg.segment_id for source in request.sources for seg in source.segments},
                              {source.source_id: source.source_version for source in request.sources}, set(), set(facts)),
        facts=facts, preflight_issues=request.preflight.issues)
    document = Document(document_id="doc_excluded_conflict", session_id=request.session_id, document_revision=1,
                        input_revision=request.input_revision, title=draft.title, target_pages=request.brief.target_pages,
                        status="draft", pages=draft.pages)
    conflicts = [issue for issue in validation.server_checks(document, context)[0] if issue.code == "VALUE_CONFLICT"]
    assert len(conflicts) == 1 and conflicts[0].severity == "blocker" and conflicts[0].block_ids == []
    assert conflicts[0].fact_ids == [fact.fact_id for fact in facts.values() if fact.field_key == "certifications"]


def test_draft_checks_excluded_evidence_before_calling_model():
    agent, request, model = structured_draft(BRIEF.model_copy(update={"emphasis": ["인증 제외"]}))
    fact = next(f for f in request.preflight.facts if f.field_key == "certifications")
    fact.evidence_refs[0].excerpt = "원문에 없는 내용"
    with pytest.raises(AgentError, match="AGENT_OUTPUT_INVALID"):
        agent.draft(request)
    assert len(model.calls) == 1


def test_draft_skips_model_when_all_supported_body_fields_are_excluded():
    brief = BRIEF.model_copy(update={"emphasis": [f"{key} 제외" for key in legacy.SECTION_ORDER]})
    agent, request, model = structured_draft(brief)
    draft = agent.draft(request)
    assert len(model.calls) == 1 and draft.title == STRUCTURE_TEXTS["company_name"]
    assert len(draft.pages) == 1 and len(draft.pages[0].blocks) == 1
    assert request.brief.target_pages == 6


@pytest.mark.parametrize("order", [("technology", "company_summary"), ("company_summary", "company_summary"),
                                  ("foreign", "company_summary"), ("company_summary",), (["company_summary"],)])
def test_legacy_rejects_invalid_section_order_before_model_call(order):
    model = FakeModel()
    facts = [{"field": key, "fact_id": key, "text": STRUCTURE_TEXTS[key]}
             for key in ("company_summary", "lead_time")]
    with pytest.raises(legacy.AgentInputError):
        legacy.draft_profile(facts, request_json=model, section_order=order)
    assert model.calls == []


def test_legacy_default_order_remains_compatible():
    model = FakeModel()
    facts = [{"field": key, "fact_id": key, "text": STRUCTURE_TEXTS[key]}
             for key in ("lead_time", "company_summary")]
    result = legacy.draft_profile(facts, request_json=model)
    assert [s["key"] for s in result] == ["company_summary", "lead_time"]


@pytest.mark.parametrize("pages", [1, 4, 6, 8, 10])
def test_draft_keeps_confirmed_fact_ids_and_evidence_in_text_pages(pages):
    agent, request, model = analyzed()
    request.brief = BRIEF.model_copy(update={"target_pages": pages})
    before = request.preflight.model_dump()
    result = agent.draft(request)
    assert validate_draft(result, request.sources, {f.fact_id for f in request.preflight.facts}) is None
    assert len(result.pages) == pages
    assert result.title == TEXTS["company_name"]
    facts = {f.fact_id: f for f in request.preflight.facts}
    blocks = [b for p in result.pages for b in p.blocks]
    assert len({b.block_id for b in blocks}) == len(blocks)
    for block in blocks:
        assert block.type in {"heading", "paragraph"}
        for fid in block.fact_ids:
            assert facts[fid].status == "supported"
            assert all(ref in block.evidence_refs for ref in facts[fid].evidence_refs)
    assert any(b.content.get("text") == TEXTS["lead_time"] for b in blocks)
    assert request.preflight.model_dump() == before
    assert model.calls[-1][1]["brief"]["purpose"] == BRIEF.purpose


def test_legacy_allows_company_name_reference_inside_business_paragraph():
    agent, request, model = analyzed()
    company = next(f for f in request.preflight.facts if f.field_key == "company_name")
    def change(result):
        result["draft_sections"][0]["paragraphs"][0]["fact_ids"].append(company.fact_id)
    model.draft_change = change
    assert agent.draft(request).pages


@pytest.mark.parametrize("change,code", [
    ({"confirmed_at": None}, "PREFLIGHT_NOT_CONFIRMED"),
    ({"input_revision": 1}, "INPUT_REVISION_CONFLICT"),
    ({"session_id": "other"}, "INPUT_REVISION_CONFLICT"),
    ({"can_generate": False}, "NO_USABLE_TEXT"),
])
def test_draft_requires_current_confirmed_preflight(change, code):
    agent, request, model = analyzed()
    request.preflight = request.preflight.model_copy(update=change)
    with pytest.raises(AgentError, match=code):
        agent.draft(request)
    assert len(model.calls) == 1


@pytest.mark.parametrize("change", [
    {"source_version": 999}, {"source_id": "src_session"}, {"segment_id": "seg_c"},
    {"locator": {"page": 99}}, {"excerpt": "자료 밖의 인용"},
])
def test_draft_rechecks_stored_evidence_before_requesting_ai(change):
    agent, request, model = analyzed()
    fact = next(f for f in request.preflight.facts if f.status == "supported")
    fact.evidence_refs[0] = fact.evidence_refs[0].model_copy(update=change)
    with pytest.raises(AgentError, match="AGENT_OUTPUT_INVALID"):
        agent.draft(request)
    assert len(model.calls) == 1


@pytest.mark.parametrize("bad_kind", ["unknown_fact", "missing_section", "wrong_title", "empty_paragraph", "placeholder"])
def test_invalid_draft_is_not_silently_repaired(bad_kind):
    def change(result):
        section = result["draft_sections"][0]
        if bad_kind == "unknown_fact":
            section["paragraphs"][0]["fact_ids"] = ["foreign_fact"]
        elif bad_kind == "missing_section":
            result["draft_sections"].pop()
        elif bad_kind == "wrong_title":
            section["title"] = "임의 제목"
        elif bad_kind == "empty_paragraph":
            section["paragraphs"] = []
        else:
            section["paragraphs"][0]["text"] = "추가 확인 필요"
    agent, request, _ = analyzed(FakeModel(draft_change=change))
    with pytest.raises(AgentError, match="AGENT_OUTPUT_INVALID"):
        agent.draft(request)


def test_no_text_and_over_limit_stop_before_call():
    model = FakeModel()
    agent = llm.LlmAgent(model, max_input_chars=10)
    request = AnalyzeRequest("s", 1, BRIEF, [])
    result = agent.analyze(request)
    assert all(f.status == "missing" for f in result.facts)
    assert "텍스트" in result.recommendations.reason
    request.sources = sources()
    with pytest.raises(AgentError, match="INVALID_REQUEST"):
        agent.analyze(request)
    assert model.calls == []


def test_blank_segments_are_ignored_and_duplicate_ids_are_rejected():
    model = FakeModel()
    selected = sources()
    selected[0].segments.append(SegmentIn("seg_blank", {"page": 4}, " \n"))
    request = AnalyzeRequest("s", 1, BRIEF, selected)
    llm.LlmAgent(model).analyze(request)
    assert len(model.calls[0][1]["source_units"]) == 3
    selected[1].segments[0].segment_id = "seg_a"
    with pytest.raises(AgentError, match="AGENT_OUTPUT_INVALID"):
        llm.LlmAgent(model).analyze(request)
    assert len(model.calls) == 1


def test_conflict_candidate_evidence_is_checked_again_before_draft():
    agent, request, model = analyzed()
    fact = next(f for f in request.preflight.facts if f.status == "supported")
    fact.status, fact.value = "conflict", None
    fact.alternatives = [{"value": "후보 A", "evidence_refs": [fact.evidence_refs[0].model_dump()]},
                         {"value": "후보 B", "evidence_refs": [fact.evidence_refs[0].model_dump() | {"source_id": "foreign"}]}]
    with pytest.raises(AgentError, match="AGENT_OUTPUT_INVALID"):
        agent.draft(request)
    assert len(model.calls) == 1


def test_missing_facts_make_review_placeholders_without_calling_draft_ai():
    model = FakeModel(extract_change=lambda info: info.update({
        key: {"status": "not_found", "facts": []} for key in legacy.COMPANY_INFO_KEYS}))
    agent, request, _ = analyzed(model)
    assert {i.code for i in request.preflight.issues} == {"REQUIRED_MISSING"}
    result = agent.draft(request)
    assert result.title == "회사소개서 초안"
    assert not any(b.fact_ids for page in result.pages for b in page.blocks)
    assert len(model.calls) == 1


def test_mock_proposals_and_successful_validation_are_not_used_as_fallback():
    agent = llm.LlmAgent(FakeModel())
    with pytest.raises(AgentError, match="UNSUPPORTED_PROPOSAL"):
        agent.propose(None)
    with pytest.raises(AgentError, match="SERVICE_TEMPORARY_FAILURE"):
        agent.validate(None)


def config_env():
    return {"OPENAI_API_KEY": "fake-secret-for-offline-test", "OPENAI_MODEL": "gpt-6-luna",
            "OPENAI_TIMEOUT_SECONDS": "3", "OPENAI_MAX_RETRIES": "0",
            "OPENAI_MAX_OUTPUT_TOKENS": "1000", "OPENAI_MAX_INPUT_CHARS": "10000"}


def metered_response(*, input_tokens=1000, output_tokens=100, cached=200, written=300,
                     reasoning=20, **changes):
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                            total_tokens=input_tokens + output_tokens,
                            input_tokens_details=SimpleNamespace(cached_tokens=cached, cache_write_tokens=written),
                            output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning))
    return SimpleNamespace(**({"status": "completed", "output": [], "output_text": '{"ok":true}',
                              "model": "gpt-6-luna", "service_tier": "default", "usage": usage} | changes))


@pytest.mark.parametrize("change", [{"OPENAI_MODEL": ""}, {"OPENAI_API_KEY": ""},
    {"OPENAI_TIMEOUT_SECONDS": "nan"}, {"OPENAI_TIMEOUT_SECONDS": "0"}, {"OPENAI_MAX_RETRIES": "-1"},
    {"OPENAI_MAX_OUTPUT_TOKENS": "bad"}, {"OPENAI_MAX_INPUT_CHARS": "40001"}])
def test_invalid_settings_do_not_call_sdk_or_expose_values(change):
    with pytest.raises(AgentError, match="SERVICE_TEMPORARY_FAILURE") as error:
        llm.LlmOptions.from_env(config_env() | change)
    assert "fake-secret" not in str(error.value)


def fake_sdk(monkeypatch, *, response=None, error=None):
    calls = []
    class Client:
        def __init__(self, **kwargs):
            self.responses = self
            calls.append(("client", kwargs))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def create(self, **kwargs):
            calls.append(("response", kwargs))
            if error:
                raise error
            return response(**kwargs) if callable(response) else response
    monkeypatch.setattr(llm, "OpenAI", Client)
    return calls


def test_sdk_request_uses_explicit_settings_strict_json_and_no_storage(monkeypatch):
    response = metered_response()
    calls = fake_sdk(monkeypatch, response=response)
    options = llm.LlmOptions.from_env(config_env())
    assert "fake-secret" not in repr(options)
    result = llm.OpenAIRequester(options)("지시", {"text": "가짜 자료"}, {"type": "object"}, "company_info")
    assert result == {"ok": True}
    assert calls[0][1]["max_retries"] == 0 and calls[0][1]["timeout"] == 3
    request = calls[1][1]
    assert request["store"] is False and request["text"]["format"]["strict"] is True
    assert request["instructions"] == "지시" and json.loads(request["input"]) == {"text": "가짜 자료"}
    assert request["service_tier"] == "default" and request["reasoning"] == {"effort": "medium"}
    assert request["truncation"] == "disabled"
    assert calls[0][1]["base_url"] == "https://api.openai.com/v1"


@pytest.mark.parametrize("status,body,refusal", [("incomplete", "{}", False), ("completed", "not json", False),
    ("completed", "[]", False), ("completed", "{}", True)])
def test_incomplete_invalid_and_refused_sdk_responses_fail(monkeypatch, status, body, refusal):
    output = [SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal")])] if refusal else []
    fake_sdk(monkeypatch, response=SimpleNamespace(status=status, output=output, output_text=body))
    with pytest.raises(AgentError, match="AGENT_OUTPUT_INVALID"):
        llm.OpenAIRequester(llm.LlmOptions.from_env(config_env()))("", {}, {}, "company_info")


@pytest.mark.parametrize("kind,code,retryable", [("rate", "AI_RATE_LIMIT", False),
    ("auth", "SERVICE_TEMPORARY_FAILURE", False), ("bad", "SERVICE_TEMPORARY_FAILURE", False),
    ("timeout", "SERVICE_TEMPORARY_FAILURE", False), ("unexpected", "SERVICE_TEMPORARY_FAILURE", False)])
def test_sdk_errors_are_safe_and_have_retry_information(monkeypatch, kind, code, retryable):
    response = httpx2.Response(429, request=httpx2.Request("POST", "https://invalid.example"))
    errors = {"rate": RateLimitError("secret-source", response=response, body=None),
              "auth": AuthenticationError("secret-source", response=response, body=None),
              "bad": BadRequestError("secret-source", response=response, body=None),
              "timeout": APITimeoutError(request=response.request), "unexpected": RuntimeError("secret-source")}
    fake_sdk(monkeypatch, error=errors[kind])
    with pytest.raises(AgentError) as error:
        llm.OpenAIRequester(llm.LlmOptions.from_env(config_env()))("", {}, {}, "company_info")
    assert (error.value.code, error.value.retryable) == (code, retryable)
    assert "secret-source" not in str(error.value)


def test_trial_records_usage_cost_and_time_without_content(monkeypatch):
    fake_sdk(monkeypatch, response=metered_response())
    ticks = iter((10.0, 10.25))
    monkeypatch.setattr(llm.time, "monotonic", lambda: next(ticks))
    requester = llm.OpenAIRequester(llm.LlmOptions.from_env(config_env()))
    requester("secret-instructions", {"secret-text": "private-company"}, {}, "company_info")
    report = llm.trial_report()
    row = report["records"][0]
    assert row["elapsed_ms"] == 250
    assert (row["input_tokens"], row["output_tokens"], row["reasoning_tokens"]) == (1000, 100, 20)
    assert (row["cached_input_tokens"], row["cache_write_tokens"]) == (200, 300)
    # (500*0.10 + 200*0.01 + 300*0.125 + 100*0.50) / 1M. 추론 20은 출력 100에 포함.
    assert Decimal(row["estimated_cost_usd"]) == Decimal("0.0001395")
    assert report["cost_complete"] and report["calls_started"] == 1 and not report["stopped"]
    assert row["outcome"] == "json_received"
    serialized = json.dumps(report)
    for secret in ("secret-instructions", "secret-text", "private-company", "fake-secret", '"ok"'):
        assert secret not in serialized
    report["records"][0]["input_tokens"] = -1
    assert llm.trial_report()["records"][0]["input_tokens"] == 1000


def run_d04_offline_case(monkeypatch, case_id, *, wrong_lead_time=False):
    """외부 호출 없는 대역 시험. 사전 확인도 테스트 안에서만 가짜로 구성한다."""
    def respond(**kwargs):
        payload = json.loads(kwargs["input"])
        if kwargs["text"]["format"]["name"] == "company_info":
            # 평가용 이름·기대 상태·정답은 실제 Agent 입력에 섞이지 않는다.
            assert set(payload) == {"company_name_hint", "source_units"}
            body = d04_fake_extraction(case_id, payload)
        else:
            body = draft_response(payload)
            if wrong_lead_time:
                lead_time = next(s for s in body["draft_sections"] if s["key"] == "lead_time")
                lead_time["paragraphs"][0]["text"] = "모든 주문은 7일 이내 납품을 보장합니다."
        return metered_response(output_text=json.dumps(body, ensure_ascii=False))
    calls = fake_sdk(monkeypatch, response=respond)
    options = llm.LlmOptions.from_env(config_env() | {"OPENAI_TIMEOUT_SECONDS": "60", "OPENAI_MAX_OUTPUT_TOKENS": "8000"})
    agent = llm.LlmAgent(llm.OpenAIRequester(options), max_input_chars=options.max_input_chars)
    request = build_d04_trial_request(case_id)
    analysis = agent.analyze(request)
    preflight = PreflightOut(preflight_id=f"pf_trial_{case_id}", session_id=request.session_id,
                            input_revision=request.input_revision,
                            usable_source_ids=[source.source_id for source in request.sources],
                            facts=analysis.facts, issues=analysis.issues, recommendations=analysis.recommendations,
                            can_generate=True, confirmed_at="2026-09-28T00:00:00Z")
    draft = agent.draft(DraftRequest(request.session_id, request.input_revision, request.brief, request.sources, preflight))
    return request, analysis, draft, calls


@pytest.mark.parametrize("case_id", list(D04_TRIAL_CASES))
def test_d04_trial_case_preserves_expected_facts_issues_and_draft(monkeypatch, case_id):
    request, analysis, draft, calls = run_d04_offline_case(monkeypatch, case_id)
    assert validate_analyze(analysis, request.sources) is None
    assert validate_draft(draft, request.sources, {fact.fact_id for fact in analysis.facts}) is None
    assert all(source.origin_kind == "mock" and source.kind == "company" and not source.asset_ids
               for source in request.sources)
    assert analysis.recommendations.suggested_pages == 1
    assert 0 < sum(len(segment.text) for source in request.sources for segment in source.segments) <= 10_000
    assert {fact.field_key for fact in analysis.facts} == set(legacy.COMPANY_INFO_KEYS)
    expected = D04_EXPECTED[case_id]
    by_key = {fact.field_key: fact for fact in analysis.facts}
    for key, fact in by_key.items():
        if key not in expected:
            assert fact.status == "missing" and fact.value is None and fact.evidence_refs == []
        else:
            status, items = expected[key]
            assert fact.status == status
            values = [item["value"] for item in fact.alternatives] if status == "conflict" else [fact.value]
            assert values == [item[0] for item in items]
            for ref in fact.evidence_refs:
                source = next(s for s in request.sources if s.source_id == ref.source_id)
                segment = next(s for s in source.segments if s.segment_id == ref.segment_id)
                assert ref.source_version == source.source_version and ref.locator == segment.locator
                assert ref.excerpt in segment.text
    paragraphs = [block for page in draft.pages for block in page.blocks if block.type == "paragraph"]
    body = "\n".join(block.content["text"] for block in paragraphs)
    assert len(draft.pages) == D04_TRIAL_CASES[case_id]["target_pages"]
    assert all(block.type != "image" for page in draft.pages for block in page.blocks)
    supported_ids = {fact.fact_id for fact in analysis.facts if fact.status == "supported"}
    for block in paragraphs:
        if block.fact_ids:
            assert set(block.fact_ids) <= supported_ids and block.evidence_refs
        else:
            assert block.content["text"] in {"자료에서 확인되지 않음", "추가 확인 필요"}
            assert not block.evidence_refs
    if case_id == "T02":
        assert draft.title == "회사소개서 초안" and "테스트 회사" not in body
        assert any(issue.code == "REQUIRED_MISSING" and issue.severity == "blocker"
                   and by_key["company_name"].fact_id in issue.fact_ids for issue in analysis.issues)
        assert any(item.startswith("회사명:") for item in analysis.recommendations.needed)
        assert "자료에서 확인되지 않음" in body
    elif case_id == "T03":
        conflict = by_key["process_count"]
        assert conflict.value is None
        assert {ref.source_id for ref in conflict.evidence_refs} == {source.source_id for source in request.sources}
        assert all(alt["evidence_refs"] for alt in conflict.alternatives)
        assert any(issue.code == "VALUE_CONFLICT" and issue.severity == "blocker" for issue in analysis.issues)
        assert "추가 확인 필요" in body and "공정 수: 2개" not in body and "공정 수: 3개" not in body
    else:
        assert analysis.issues == []
    if case_id == "T04":
        for phrase in ("일반 주문", "주문 승인 후", "영업일 7일", "특수 주문", "별도 협의"):
            assert phrase in by_key["lead_time"].value and phrase in body
    sent = [kwargs for kind, kwargs in calls if kind == "response"]
    assert len(sent) == 2
    assert [kwargs["text"]["format"]["name"] for kwargs in sent] == ["company_info", "draft_sections"]
    assert {unit["source_id"] for unit in json.loads(sent[0]["input"])["source_units"]} == {
        source.source_id for source in request.sources}
    draft_input = json.loads(sent[1]["input"])
    assert {fact["fact_id"] for fact in draft_input["supported_facts"]} == supported_ids
    assert all(not any(name in json.loads(kwargs["input"]) for name in ("expected", "D04_EXPECTED", "case_name"))
               for kwargs in sent)
    assert llm.trial_report()["calls_started"] == 2 and not llm.trial_report()["stopped"]


def test_d04_four_cases_fit_one_shared_eight_call_trial(monkeypatch):
    for case_id in D04_TRIAL_CASES:
        run_d04_offline_case(monkeypatch, case_id)
    report = llm.trial_report()
    assert report["calls_started"] == 8 and report["cost_complete"]
    assert [row["operation"] for row in report["records"]] == ["company_info", "draft_sections"] * 4
    assert report["stop_reason"] == "call_limit"
    assert Decimal(report["known_estimated_cost_usd"]) < Decimal("1")


@pytest.mark.parametrize("case_id", list(D04_TRIAL_CASES))
def test_generated_missing_field_labels_pass_server_claim_checks(monkeypatch, case_id):
    request, analysis, draft, _ = run_d04_offline_case(monkeypatch, case_id)
    document = Document(document_id="doc_trial", session_id=request.session_id, document_revision=1,
                        input_revision=request.input_revision, title=draft.title, target_pages=request.brief.target_pages,
                        status="draft", pages=draft.pages)
    segments = {segment.segment_id: segment for source in request.sources for segment in source.segments}
    facts = {fact.fact_id: fact for fact in analysis.facts}
    context = validation.Context(
        seg_texts={sid: segment.text for sid, segment in segments.items()},
        seg_source={segment.segment_id: source.source_id for source in request.sources for segment in source.segments},
        asset_source={}, mock_sources={source.source_id for source in request.sources},
        refs=refs.SessionRefs(set(segments), {source.source_id: source.source_version for source in request.sources},
                              set(), set(facts)), facts=facts, preflight_issues=analysis.issues)
    before = copy.deepcopy(analysis)
    issues, _ = validation.server_checks(document, context)
    assert not any(issue.code in {"UNSUPPORTED_CLAIM", "EVIDENCE_INVALID"} for issue in issues)
    placeholders = {block.block_id for page in draft.pages for block in page.blocks
                    if block.type == "paragraph" and not block.fact_ids}
    warnings = [issue for issue in issues if issue.code == "PLACEHOLDER_TEXT"]
    assert {bid for issue in warnings for bid in issue.block_ids} == placeholders
    assert all(issue.severity == "warning" for issue in warnings)
    assert all(not block.fact_ids and not block.evidence_refs for page in draft.pages for block in page.blocks
               if block.block_id in placeholders or (block.type == "heading" and not block.fact_ids))
    missing = [issue for issue in issues if issue.code == "REQUIRED_MISSING"]
    assert len(missing) == (1 if case_id == "T02" else 0)
    assert all(issue.severity == "blocker" for issue in missing)
    assert any(issue.code == "MOCK_VALUE" and issue.severity == "blocker" for issue in issues)
    assert analysis == before  # 사전 점검의 필수 누락·충돌 Issue도 그대로 남는다.
    if case_id == "T03":
        conflict = next(fact for fact in analysis.facts if fact.status == "conflict")
        carried = [issue for issue in issues if issue.code == "VALUE_CONFLICT" and issue.origin == "preflight"]
        assert len(carried) == 1
        assert carried[0].severity == "blocker" and carried[0].block_ids == []
        assert carried[0].fact_ids == [conflict.fact_id]
        assert set(carried[0].source_ids) == {source.source_id for source in request.sources}
        # 안내에는 가짜 참조를 붙이지 않는다. 실제 참조가 생기면 해당 블록의 충돌도 표시한다.
        heading = next(block for page in document.pages for block in page.blocks
                       if block.type == "heading" and block.content["text"] == "공정 수")
        heading.fact_ids = [conflict.fact_id]
        heading.evidence_refs = conflict.evidence_refs
        checked, _ = validation.server_checks(document, context)
        assert any(issue.code == "VALUE_CONFLICT" and issue.severity == "blocker"
                   and issue.block_ids == [heading.block_id] for issue in checked)


def test_all_generated_section_labels_are_recognized_by_server():
    # Agent 제목 원본이 바뀌면 서버의 명시적 라벨 예외도 함께 검토한다.
    for label in ["회사명", "회사소개서 초안", *legacy.SECTION_TITLES.values()]:
        for text in (label, f"2. {label}", f"{label} 2"):
            assert validation.is_label(text), text


def test_d04_case_inputs_are_isolated_and_do_not_confirm_or_call_ai():
    first = build_d04_trial_request("T01")
    first.sources[0].segments[0].text = "테스트 중 변경"
    second = build_d04_trial_request("T01")
    assert second.sources[0].segments[0].text == "회사명: 테스트 회사"
    assert not hasattr(second, "preflight")
    assert llm.trial_report()["calls_started"] == 0


def test_d04_shape_checks_do_not_establish_lead_time_meaning(monkeypatch):
    # 알려진 한계를 재현한다. 올바른 fact_id만 붙인다고 잘못된 납기 보장이 검출되지는 않는다.
    request, analysis, draft, _ = run_d04_offline_case(monkeypatch, "T04", wrong_lead_time=True)
    assert validate_draft(draft, request.sources, {fact.fact_id for fact in analysis.facts}) is None
    lead_id = next(fact.fact_id for fact in analysis.facts if fact.field_key == "lead_time")
    paragraphs = [block for page in draft.pages for block in page.blocks
                  if block.type == "paragraph" and lead_id in block.fact_ids]
    assert any(block.content["text"] == "모든 주문은 7일 이내 납품을 보장합니다." for block in paragraphs)
    # 이것은 내용 통과가 아니다. 실제 시험의 사람 검토에서는 실패로 판정해야 한다.
    assert llm.trial_report()["records"][-1]["outcome"] == "json_received"


def test_long_context_prices_apply_to_whole_request(monkeypatch):
    fake_sdk(monkeypatch, response=metered_response(input_tokens=300_000, output_tokens=200,
                                                  cached=100_000, written=50_000))
    llm.OpenAIRequester(llm.LlmOptions.from_env(config_env()))("", {}, {}, "company_info")
    assert Decimal(llm.trial_report()["known_estimated_cost_usd"]) == Decimal("0.04465")


def test_eight_calls_shared_across_fresh_server_bridges_and_ninth_never_sends(monkeypatch, tmp_path):
    from app.agent_bridge import get_bridge
    calls = fake_sdk(monkeypatch, response=metered_response())
    options = llm.LlmOptions.from_env(config_env())
    monkeypatch.setattr(llm.LlmOptions, "from_env", classmethod(lambda cls, env: options))
    settings = Settings(agent_mode="llm", private_runs_dir=tmp_path / "runs", db_path=tmp_path / "db.sqlite3")
    for operation in ["company_info", "draft_sections"] * 4:
        bridge = get_bridge(settings)
        assert bridge.request_json("", {}, {}, operation) == {"ok": True}
    report = llm.trial_report()
    assert report["calls_started"] == 8 and len(report["records"]) == 8
    assert report["stopped"] and report["stop_reason"] == "call_limit"
    with pytest.raises(AgentError):
        get_bridge(settings).request_json("", {}, {}, "company_info")
    assert len(calls) == 16  # 클라이언트 생성 + 실제 호출 진입, 8쌍만 존재.


def test_fifth_extraction_stops_before_sending(monkeypatch):
    calls = fake_sdk(monkeypatch, response=metered_response())
    requester = llm.OpenAIRequester(llm.LlmOptions.from_env(config_env()))
    for _ in range(4):
        requester("", {}, {}, "company_info")
    with pytest.raises(AgentError):
        requester("", {}, {}, "company_info")
    assert len(calls) == 8 and llm.trial_report()["stop_reason"] == "operation_limit"


def test_budget_reserves_next_request_before_sdk_creation(monkeypatch):
    calls = fake_sdk(monkeypatch, response=metered_response())
    # 현재 단가의 최대 1회 예약액은 0.2685. 첫 호출 뒤에는 두 번째 예약이 불가능하다.
    ledger = llm.TrialLedger(budget_usd=Decimal("0.2685"))
    requester = llm.OpenAIRequester(llm.LlmOptions.from_env(config_env()), ledger=ledger)
    requester("", {}, {}, "company_info")
    with pytest.raises(AgentError):
        requester("", {}, {}, "draft_sections")
    assert len(calls) == 2 and ledger.snapshot()["calls_started"] == 1
    assert ledger.snapshot()["stop_reason"] == "budget_reserve"


@pytest.mark.parametrize("change", [{"OPENAI_MODEL": "unapproved-secret-model"},
    {"OPENAI_MAX_RETRIES": "1"}, {"OPENAI_MAX_OUTPUT_TOKENS": "8001"},
    {"OPENAI_MAX_INPUT_CHARS": "10001"}, {"OPENAI_TIMEOUT_SECONDS": "61"}])
def test_unapproved_trial_settings_fail_before_sdk_creation(change):
    requester = llm.OpenAIRequester(llm.LlmOptions.from_env(config_env() | change))
    with pytest.raises(AgentError):
        requester("", {}, {}, "company_info")
    report = llm.trial_report()
    assert report["calls_started"] == 0 and report["stop_reason"] == "settings_outside_trial"
    assert "unapproved-secret-model" not in json.dumps(report)


@pytest.mark.parametrize("bad", ["missing_usage", "missing_write", "negative", "boolean", "fraction",
    "bad_total", "too_many_cached", "too_much_reasoning", "too_much_output", "too_much_input",
    "other_model", "other_tier"])
def test_unknown_or_inconsistent_usage_stops_and_is_not_zero_cost(monkeypatch, bad):
    response = metered_response()
    if bad == "missing_usage":
        response.usage = None
    elif bad == "missing_write":
        del response.usage.input_tokens_details.cache_write_tokens
    elif bad in ("negative", "boolean", "fraction"):
        response.usage.input_tokens = {"negative": -1, "boolean": True, "fraction": 1.5}[bad]
    elif bad == "bad_total":
        response.usage.total_tokens += 1
    elif bad == "too_many_cached":
        response.usage.input_tokens_details.cached_tokens = 1000
    elif bad == "too_much_reasoning":
        response.usage.output_tokens_details.reasoning_tokens = 101
    elif bad == "too_much_output":
        response = metered_response(output_tokens=1001)
    elif bad == "too_much_input":
        response = metered_response(input_tokens=1_050_001)
    elif bad == "other_model":
        response.model = "private-response-model"
    else:
        response.service_tier = "priority"
    calls = fake_sdk(monkeypatch, response=response)
    requester = llm.OpenAIRequester(llm.LlmOptions.from_env(config_env()))
    with pytest.raises(AgentError):
        requester("", {}, {}, "company_info")
    with pytest.raises(AgentError):
        requester("", {}, {}, "company_info")
    report = llm.trial_report()
    assert len(calls) == 2 and report["calls_started"] == 1
    assert report["stop_reason"] == "usage_unconfirmed" and not report["cost_complete"]
    assert report["records"][0]["estimated_cost_usd"] is None
    assert "private-response-model" not in json.dumps(report)


@pytest.mark.parametrize("change", [{"status": "incomplete"}, {"output_text": "secret-bad-json"},
    {"output_text": "[]"}, {"output": [SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal")])]}])
def test_failed_response_still_records_usage_and_prevents_new_call(monkeypatch, change):
    calls = fake_sdk(monkeypatch, response=metered_response(**change))
    requester = llm.OpenAIRequester(llm.LlmOptions.from_env(config_env()))
    with pytest.raises(AgentError, match="AGENT_OUTPUT_INVALID"):
        requester("", {}, {}, "company_info")
    with pytest.raises(AgentError):
        requester("", {}, {}, "company_info")
    report = llm.trial_report()
    assert len(calls) == 2 and report["cost_complete"]
    assert report["stop_reason"] == "invalid_response"
    assert Decimal(report["known_estimated_cost_usd"]) == Decimal("0.0001395")
    assert "secret-bad-json" not in json.dumps(report)


@pytest.mark.parametrize("code", ["organization_spend_limit_exceeded", "project_spend_limit_exceeded",
    "credit_balance_exhausted", "organization_usage_limit_exceeded"])
def test_provider_budget_errors_stop_without_retry_or_exposing_response(monkeypatch, code):
    response = httpx2.Response(429, request=httpx2.Request("POST", "https://invalid.example"))
    error = RateLimitError("secret-provider-body", response=response,
                           body={"code": code, "type": "insufficient_quota"})
    calls = fake_sdk(monkeypatch, error=error)
    requester = llm.OpenAIRequester(llm.LlmOptions.from_env(config_env()))
    with pytest.raises(AgentError) as caught:
        requester("", {}, {}, "company_info")
    assert not caught.value.retryable
    with pytest.raises(AgentError):
        requester("", {}, {}, "company_info")
    report = llm.trial_report()
    assert len(calls) == 2 and report["stop_reason"] == "provider_budget" and not report["cost_complete"]
    assert "secret-provider-body" not in json.dumps(report) + str(caught.value)


def test_timeout_is_unknown_cost_and_cannot_be_retried_in_same_trial(monkeypatch):
    request = httpx2.Request("POST", "https://invalid.example")
    calls = fake_sdk(monkeypatch, error=APITimeoutError(request=request))
    requester = llm.OpenAIRequester(llm.LlmOptions.from_env(config_env()))
    for _ in range(2):
        with pytest.raises(AgentError):
            requester("", {}, {}, "company_info")
    assert len(calls) == 2
    assert llm.trial_report()["records"][0]["estimated_cost_usd"] is None


def test_manual_stop_before_call_does_not_create_sdk():
    llm.stop_trial()
    requester = llm.OpenAIRequester(llm.LlmOptions.from_env(config_env()))
    with pytest.raises(AgentError):
        requester("", {}, {}, "company_info")
    assert llm.trial_report()["calls_started"] == 0
    assert llm.trial_report()["stop_reason"] == "manual_stop"


def test_concurrent_call_is_blocked_and_manual_stop_discards_inflight_result(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def delayed(**kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return metered_response()
    calls = fake_sdk(monkeypatch, response=delayed)
    requester = llm.OpenAIRequester(llm.LlmOptions.from_env(config_env()))
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(requester, "", {}, {}, "company_info")
        try:
            assert entered.wait(timeout=5)
            report = llm.trial_report()
            assert report["in_flight"] and report["calls_started"] == 1
            assert Decimal(report["reserved_cost_usd"]) == Decimal("0.2685")
            with pytest.raises(AgentError):
                requester("", {}, {}, "draft_sections")
            llm.stop_trial()
        finally:
            release.set()
        with pytest.raises(AgentError):
            first.result(timeout=5)
    report = llm.trial_report()
    assert len(calls) == 2 and report["cost_complete"]
    assert report["stop_reason"] == "manual_stop" and report["records"][0]["outcome"] == "discarded"


def test_legacy_validation_failure_stops_trial_after_usage_is_recorded(monkeypatch):
    # JSON 형식은 맞지만 필수 정보 구조가 잘못된 경우도 다음 유료 호출을 막는다.
    calls = fake_sdk(monkeypatch, response=metered_response(output_text='{}'))
    agent = llm.LlmAgent(llm.OpenAIRequester(llm.LlmOptions.from_env(config_env())))
    with pytest.raises(AgentError, match="AGENT_OUTPUT_INVALID"):
        agent.analyze(AnalyzeRequest("ses_test", 2, BRIEF, sources()))
    assert llm.trial_report()["cost_complete"] and llm.trial_report()["stop_reason"] == "invalid_result"
    with pytest.raises(AgentError):
        agent.analyze(AnalyzeRequest("ses_test", 2, BRIEF, sources()))
    assert len(calls) == 2


@pytest.mark.parametrize("phase", ["analyze", "draft"])
def test_postprocessing_keeps_guard_and_honors_manual_stop(monkeypatch, phase):
    def respond(**kwargs):
        payload = json.loads(kwargs["input"])
        body = extraction(payload) if phase == "analyze" else draft_response(payload)
        return metered_response(output_text=json.dumps(body, ensure_ascii=False))
    calls = fake_sdk(monkeypatch, response=respond)
    requester = llm.OpenAIRequester(llm.LlmOptions.from_env(config_env()))
    agent = llm.LlmAgent(requester)
    request = AnalyzeRequest("ses_test", 2, BRIEF, sources()) if phase == "analyze" else analyzed()[1]
    entered, release = threading.Event(), threading.Event()
    name = "_facts" if phase == "analyze" else "_pages"
    original = getattr(agent, name)
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)
    monkeypatch.setattr(agent, name, delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(getattr(agent, phase), request)
        try:
            assert entered.wait(timeout=5)
            report = llm.trial_report()
            assert not report["in_flight"] and report["operation_in_progress"] and report["cost_complete"]
            # 같은 ledger를 사용하는 새 requester도 근거 검사 완료 전에는 전송할 수 없다.
            with pytest.raises(AgentError):
                llm.OpenAIRequester(llm.LlmOptions.from_env(config_env()))("", {}, {}, "company_info")
            llm.stop_trial()
        finally:
            release.set()
        with pytest.raises(AgentError):
            first.result(timeout=5)
    assert len(calls) == 2 and not llm.trial_report()["operation_in_progress"]
    assert llm.trial_report()["stop_reason"] == "manual_stop"


def test_last_successful_result_is_returned_after_postprocessing(monkeypatch):
    def respond(**kwargs):
        return metered_response(output_text=json.dumps(extraction(json.loads(kwargs["input"]))))
    calls = fake_sdk(monkeypatch, response=respond)
    ledger = llm.TrialLedger(max_calls=1)
    agent = llm.LlmAgent(llm.OpenAIRequester(llm.LlmOptions.from_env(config_env()), ledger=ledger))
    result = agent.analyze(AnalyzeRequest("ses_test", 2, BRIEF, sources()))
    assert any(f.field_key == "company_name" and f.value == TEXTS["company_name"] for f in result.facts)
    assert ledger.snapshot()["stop_reason"] == "call_limit"
    with pytest.raises(AgentError):
        agent.analyze(AnalyzeRequest("ses_test", 2, BRIEF, sources()))
    assert len(calls) == 2


@pytest.fixture
def graph_flow(tmp_path, monkeypatch):
    settings = Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "app.sqlite3",
                        agent_mode="llm", cleanup_sweep_interval_s=0)
    model = FakeModel()
    # Job마다 실제 graph를 가진 Agent를 새로 만든다. 모델 응답만 가짜다.
    monkeypatch.setattr(ai_jobs, "get_bridge", lambda settings: llm.LlmAgent(model, settings=settings))
    with TestClient(create_app(settings)) as client:
        sid = client.post("/api/v1/sessions", json={"brief": BRIEF.model_dump()}).json()["session_id"]
        base = f"/api/v1/sessions/{sid}"
        upload = client.post(base + "/sources", files=[("files", ("fake.txt", "\n".join(TEXTS.values()).encode()))]).json()
        selected = client.patch(base + "/inputs", json={"expected_input_revision": 1,
            "selected_source_ids": [i["source_id"] for i in upload["items"]]}).json()
        rev = selected["input_revision"]
        accepted = client.post(base + "/preflights", json={"expected_input_revision": rev}).json()
        job = client.get(base + "/jobs/" + accepted["job_id"]).json()
        assert job["status"] == "succeeded", job
        pfid = job["result_ref"]["preflight_id"]
        yield SimpleNamespace(settings=settings, client=client, model=model, sid=sid, base=base, rev=rev,
                              pfid=pfid, path=settings.private_runs_dir / sid / "agent_checkpoints.sqlite3",
                              body={"preflight_id": pfid, "input_revision": rev, "confirmed": True})


def graph_request(flow, conn, *, confirm=True):
    if confirm:
        preflights.confirm(conn, flow.pfid)
    pf = preflights.get(conn, flow.sid, flow.pfid)
    return DraftRequest(flow.sid, flow.rev, BRIEF,
                        preflights.build_sources(conn, flow.sid, pf.usable_source_ids), pf)


def graph_job(flow, accepted):
    return flow.client.get(flow.base + "/jobs/" + accepted.json()["job_id"]).json()


def test_graph_wait_survives_new_app_and_contains_only_references(graph_flow, monkeypatch):
    flow = graph_flow
    attempts = []
    def record_forbidden(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("graph must not send external traces")
    monkeypatch.setattr(socket.socket, "connect", record_forbidden)
    monkeypatch.setattr(socket, "create_connection", record_forbidden)
    assert flow.path.is_file()
    assert len(flow.model.calls) == 1
    contents = flow.path.read_bytes()
    assert all(value.encode() not in contents for value in TEXTS.values())
    denied = flow.client.post(flow.base + "/drafts", json=flow.body | {"confirmed": False})
    assert denied.status_code == 422 and len(flow.model.calls) == 1
    with connect(flow.settings.db_path, immediate=True) as conn:
        with llm.DraftConfirmationGraph(flow.settings)._open(conn, flow.sid, flow.rev, create=False) as (graph, config):
            state = graph.get_state(config)
            assert state.next == ("confirm",) and len(state.interrupts) == 1
            assert state.values == {"session_id": flow.sid, "input_revision": flow.rev,
                                    "preflight_id": flow.pfid, "consumed": False, "job_id": ""}
    # 환경에 추적 설정이 있어도 외부 소켓 차단 하에서 graph가 실행된다.
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    with TestClient(create_app(flow.settings)) as reopened:
        reopened.cookies.update(dict(flow.client.cookies))
        accepted = reopened.post(flow.base + "/drafts", json=flow.body, headers={"Idempotency-Key": "graph-resume"})
        job = reopened.get(flow.base + "/jobs/" + accepted.json()["job_id"]).json()
        assert job["status"] == "succeeded", job
        assert reopened.post(flow.base + "/drafts", json=flow.body,
                             headers={"Idempotency-Key": "graph-resume"}).json() == accepted.json()
    assert [name for name, _ in flow.model.calls] == ["company_info", "draft_sections"]
    assert attempts == []


@pytest.mark.parametrize("failure", ["model", "validation", "storage"])
def test_graph_failed_draft_requires_reanalysis_and_does_not_retry_model(graph_flow, monkeypatch, failure):
    flow = graph_flow
    def fail_model(_):
        raise AgentError("AI_RATE_LIMIT", "가짜 요청 한도 오류", True)
    def fail_storage(*args, **kwargs):
        raise RuntimeError("fake storage error")
    with monkeypatch.context() as patch:
        if failure == "model":
            patch.setattr(flow.model, "draft_change", fail_model)
        elif failure == "validation":
            patch.setattr(ai_jobs, "validate_draft", lambda *args: "fake rejected output")
        else:
            patch.setattr(ai_jobs.documents, "create_initial", fail_storage)
        headers = {"Idempotency-Key": "graph-failed-draft"}
        first = flow.client.post(flow.base + "/drafts", json=flow.body, headers=headers)
        result = graph_job(flow, first)
        assert result["status"] == "failed" and not result["error"]["retryable"]
        assert "사전 점검부터" in result["error"]["message"]
        assert flow.client.post(flow.base + "/drafts", json=flow.body, headers=headers).json() == first.json()
        second = graph_job(flow, flow.client.post(flow.base + "/drafts", json=flow.body,
                                                headers={"Idempotency-Key": "graph-failed-draft-new"}))
        assert second["status"] == "failed" and second["error"]["code"] == "PREFLIGHT_NOT_CONFIRMED"
        assert len(flow.model.calls) == 2
    recheck = graph_job(flow, flow.client.post(flow.base + "/preflights", json={"expected_input_revision": flow.rev}))
    current = flow.body | {"preflight_id": recheck["result_ref"]["preflight_id"]}
    assert graph_job(flow, flow.client.post(flow.base + "/drafts", json=current))["status"] == "succeeded"


def test_server_stores_structure_from_current_brief_with_graph_confirmation(graph_flow):
    flow = graph_flow
    brief = BRIEF.model_copy(update={"emphasis": ["납기"], "direction": "customer_response"})
    revision = flow.client.patch(flow.base + "/inputs", json={"expected_input_revision": flow.rev,
                                                            "brief": brief.model_dump()}).json()["input_revision"]
    analyzed_job = graph_job(flow, flow.client.post(flow.base + "/preflights", json={"expected_input_revision": revision}))
    preflight_id = analyzed_job["result_ref"]["preflight_id"]
    generated_job = graph_job(flow, flow.client.post(flow.base + "/drafts", json={"preflight_id": preflight_id,
                                                                                "input_revision": revision, "confirmed": True}))
    assert generated_job["status"] == "succeeded", generated_job
    document = flow.client.get(flow.base + "/documents/" + generated_job["result_ref"]["document_id"]).json()["document"]
    headings = [b["content"]["text"] for p in document["pages"] for b in p["blocks"]
                if b["type"] == "heading" and b["content"]["level"] == 2 and b["fact_ids"]]
    assert headings == ["회사 개요", "납기 조건", legacy.SECTION_TITLES["products_services"]]
    assert document["target_pages"] == 6 and document["input_revision"] == revision
    assert flow.client.get(flow.base).json()["brief"] == brief.model_dump()
    assert len(flow.model.calls) == 3  # 최초 분석 + 새 설정 분석 + 초안


@pytest.mark.parametrize("reanalyze", [False, True])
def test_graph_concurrent_same_job_calls_model_once(graph_flow, reanalyze):
    flow = graph_flow
    entered, release = threading.Event(), threading.Event()
    def hold(_):
        entered.set()
        assert release.wait(10)
    flow.model.draft_change = hold
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(flow.client.post, flow.base + "/drafts", json=flow.body)
        try:
            assert entered.wait(10)
            with connect(flow.settings.db_path) as conn:
                job = jobs.find_active(conn, flow.sid, "draft", flow.rev)
            if reanalyze:
                updated = graph_job(flow, flow.client.post(flow.base + "/preflights",
                                                          json={"expected_input_revision": flow.rev}))
                assert updated["status"] == "succeeded"
                assert updated["result_ref"]["preflight_id"] != flow.pfid
            ai_jobs.run_draft_job(flow.settings, flow.sid, job.job_id, flow.rev, flow.pfid)
            with connect(flow.settings.db_path) as conn:
                assert jobs.get(conn, flow.sid, job.job_id).status == "running"
            assert len(flow.model.calls) == (3 if reanalyze else 2)
        finally:
            release.set()
        assert graph_job(flow, running.result(timeout=10))["status"] == "succeeded"
        with connect(flow.settings.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM documents WHERE session_id=?", (flow.sid,)).fetchone()[0] == 1


def test_graph_rejects_old_preflight_after_new_analysis_at_same_revision(graph_flow):
    flow = graph_flow
    new_job = graph_job(flow, flow.client.post(flow.base + "/preflights", json={"expected_input_revision": flow.rev}))
    assert new_job["status"] == "succeeded", new_job
    rejected = graph_job(flow, flow.client.post(flow.base + "/drafts", json=flow.body))
    assert rejected["status"] == "failed" and rejected["error"]["code"] == "INPUT_REVISION_CONFLICT"
    assert len(flow.model.calls) == 2
    current = flow.body | {"preflight_id": new_job["result_ref"]["preflight_id"]}
    assert graph_job(flow, flow.client.post(flow.base + "/drafts", json=current))["status"] == "succeeded"
    assert len(flow.model.calls) == 3


@pytest.mark.parametrize("failure", ["missing", "corrupt", "unconfirmed", "revision", "foreign_session"])
def test_graph_rejects_invalid_resume_without_model_call(graph_flow, failure):
    flow = graph_flow
    if failure in {"missing", "corrupt"}:
        flow.path.unlink()
        if failure == "corrupt":
            flow.path.write_bytes(b"invalid sqlite checkpoint")
        result = graph_job(flow, flow.client.post(flow.base + "/drafts", json=flow.body))
        assert result["status"] == "failed"
        if failure == "missing":
            assert not flow.path.exists()
    else:
        with connect(flow.settings.db_path, immediate=True) as conn:
            request = graph_request(flow, conn, confirm=failure != "unconfirmed")
            if failure == "unconfirmed":
                request.preflight.confirmed_at = "2026-09-28T00:00:00Z"  # 요청만 위조해도 DB 확인이 필요하다.
            elif failure == "revision":
                conn.execute("UPDATE sessions SET input_revision=input_revision+1 WHERE session_id=?", (flow.sid,))
            else:
                request.session_id = "sess_another"
            error_type = ApiError if failure == "foreign_session" else AgentError
            error_code = {"foreign_session": "RESOURCE_NOT_FOUND", "revision": "INPUT_REVISION_CONFLICT",
                          "unconfirmed": "PREFLIGHT_NOT_CONFIRMED"}[failure]
            with pytest.raises(error_type) as caught:
                llm.DraftConfirmationGraph(flow.settings).resume(conn, request, "job_invalid")
            assert caught.value.code == error_code
    assert len(flow.model.calls) == 1


@pytest.mark.parametrize("stage", ["wait", "resume"])
def test_graph_server_rollback_requires_reanalysis_without_repeating_call(graph_flow, stage):
    flow = graph_flow
    class SimulatedCrash(Exception):
        pass
    with pytest.raises(SimulatedCrash):
        with connect(flow.settings.db_path, immediate=True) as conn:
            request = graph_request(flow, conn)
            graph = llm.DraftConfirmationGraph(flow.settings)
            if stage == "wait":
                pf = request.preflight
                new_id = preflights.save(conn, flow.sid, flow.rev, pf.usable_source_ids, pf.facts, pf.issues,
                                         pf.recommendations, True)
                graph.wait(conn, flow.sid, flow.rev, new_id)
            else:
                assert graph.resume(conn, request, "job_before_crash")
            raise SimulatedCrash()
    rejected = graph_job(flow, flow.client.post(flow.base + "/drafts", json=flow.body))
    assert rejected["status"] == "failed" and not rejected["error"]["retryable"]
    assert "다시 실행" in rejected["error"]["message"] and len(flow.model.calls) == 1
    new_job = graph_job(flow, flow.client.post(flow.base + "/preflights", json={"expected_input_revision": flow.rev}))
    current = flow.body | {"preflight_id": new_job["result_ref"]["preflight_id"]}
    assert graph_job(flow, flow.client.post(flow.base + "/drafts", json=current))["status"] == "succeeded"


@pytest.mark.parametrize("end", ["close", "expire"])
def test_graph_waiting_checkpoint_deleted_on_session_end(graph_flow, end):
    flow = graph_flow
    if end == "close":
        assert flow.client.delete(flow.base).status_code == 200
    else:
        with connect(flow.settings.db_path, immediate=True) as conn:
            conn.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00Z' WHERE session_id=?", (flow.sid,))
        assert sweeper.sweep_once(flow.settings)["expired"] == 1
    assert not flow.path.parent.exists()
    assert flow.client.post(flow.base + "/drafts", json=flow.body).status_code == 410
    assert not flow.path.parent.exists() and len(flow.model.calls) == 1


def test_graph_checkpoint_delete_failure_retries_existing_cleanup_queue(graph_flow, monkeypatch):
    flow = graph_flow
    remove = cleanup._remove_tree
    monkeypatch.setattr(cleanup, "_remove_tree", lambda path: False)
    response = flow.client.delete(flow.base)
    assert response.status_code == 200 and response.json()["cleanup"] == "pending"
    assert flow.path.exists()
    assert flow.client.post(flow.base + "/drafts", json=flow.body).status_code == 410
    monkeypatch.setattr(cleanup, "_remove_tree", remove)
    assert cleanup.run_for_session(flow.settings, flow.sid)["done"] == 1
    with connect(flow.settings.db_path, immediate=True) as conn:
        assert cleanup.verify_state(conn, flow.settings, flow.sid) == "done"
    assert not flow.path.parent.exists()


@pytest.mark.parametrize("stage", ["analysis", "draft"])
@pytest.mark.parametrize("end", ["close", "expire"])
def test_graph_late_model_result_does_not_restore_deleted_checkpoint(graph_flow, stage, end):
    flow = graph_flow
    def finish_session(_):
        if end == "close":
            with connect(flow.settings.db_path, immediate=True) as conn:
                cleanup.finalize(conn, flow.settings, flow.sid, cleanup.REASON_CLOSED)
            cleanup.run_for_session(flow.settings, flow.sid)
        else:
            with connect(flow.settings.db_path, immediate=True) as conn:
                conn.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00Z' WHERE session_id=?", (flow.sid,))
            sweeper.sweep_once(flow.settings)
        assert not flow.path.parent.exists()
    if stage == "analysis":
        flow.model.extract_change = finish_session
        response = flow.client.post(flow.base + "/preflights", json={"expected_input_revision": flow.rev})
    else:
        flow.model.draft_change = finish_session
        response = flow.client.post(flow.base + "/drafts", json=flow.body)
    with connect(flow.settings.db_path) as conn:
        assert jobs.get(conn, flow.sid, response.json()["job_id"]).status == "cancelled"
        assert conn.execute("SELECT COUNT(*) FROM documents WHERE session_id=?", (flow.sid,)).fetchone()[0] == 0
    assert not flow.path.parent.exists() and len(flow.model.calls) == 2


@pytest.mark.parametrize("stop_mode", ["manual", "call_limit"])
@pytest.mark.parametrize("source_scope", ["session", "registered"])
def test_stopped_trial_preserves_existing_document_through_server(tmp_path, monkeypatch, stop_mode, source_scope):
    # T04 원문을 세션 파일 읽기 또는 mock 등록 후 Job·저장 경로로 보낸다. SDK 응답만 대체한다.
    case = D04_TRIAL_CASES["T04"]
    def respond(**kwargs):
        payload = json.loads(kwargs["input"])
        if kwargs["text"]["format"]["name"] == "company_info":
            body = {key: {"status": "not_found", "facts": []} for key in legacy.COMPANY_INFO_KEYS}
            for key, (status, items) in D04_EXPECTED["T04"].items():
                facts = []
                for value, _, _ in items:
                    unit = next(u for u in payload["source_units"] if value in u["text"])
                    facts.append({"text": value, "evidence": [{"source_id": unit["source_id"],
                                  "locator": unit["locator"], "quote": value}]})
                body[key] = {"status": status, "facts": facts}
        else:
            body = draft_response(payload)
        return metered_response(output_text=json.dumps(body, ensure_ascii=False))
    calls = fake_sdk(monkeypatch, response=respond)
    options = llm.LlmOptions.from_env(config_env() | {"OPENAI_TIMEOUT_SECONDS": "60",
                                                   "OPENAI_MAX_OUTPUT_TOKENS": "8000"})
    monkeypatch.setattr(llm.LlmOptions, "from_env", classmethod(lambda cls, env: options))
    ledger = llm.TrialLedger(max_calls=2 if stop_mode == "call_limit" else 3,
                            budget_usd=Decimal("0.729005075"))
    monkeypatch.setattr(llm, "_trial", ledger)
    settings = Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "db.sqlite3",
                        agent_mode="llm", cleanup_sweep_interval_s=0)
    with TestClient(create_app(settings)) as client:
        sid = client.post("/api/v1/sessions", json={"brief": BRIEF.model_dump()}).json()["session_id"]
        base = f"/api/v1/sessions/{sid}"
        if source_scope == "session":
            upload = client.post(base + "/sources", files=[("files", ("T04.txt", case["texts"][0].encode()))]).json()
            assert client.get(base + "/jobs/" + upload["job_id"]).json()["status"] == "succeeded"
            source = client.get(base + "/sources").json()["items"][0]
        else:
            # 등록 원문·목록·구간은 저장소 밖 pytest 임시 경로에만 만든다.
            bundle = tmp_path / "bundle"
            bundle.mkdir()
            content = case["texts"][0].encode()
            (bundle / "T04.txt").write_bytes(content)
            (bundle / "sources.json").write_text(json.dumps([{
                "source_id": "MOCK_T04", "name": "[MOCK] T04", "filename": "T04.txt",
                "path": "T04.txt", "status": "mock", "mock": True,
                "use_as_company_evidence": True, "available_in_package": True,
                "sha256": hashlib.sha256(content).hexdigest(),
            }]), encoding="utf-8")
            (bundle / "company_chunks.jsonl").write_text("\n".join(json.dumps({
                "chunk_id": f"MOCK_T04_C{line_no}", "source_id": "MOCK_T04", "text": line,
                "locator": f"TXT {line_no}행", "evidence_status": "자료에 기재됨",
            }, ensure_ascii=False) for line_no, line in enumerate(case["texts"][0].splitlines(), 1)), encoding="utf-8")
            imported = import_bundle(settings, bundle, with_mock=True)
            assert imported.added_sources == 1 and imported.added_segments == 4
            assert imported.hash_unverified == 0 and imported.errors == []
            source = client.get("/api/v1/sources").json()["items"][0]
            assert source["origin_kind"] == "mock" and source["is_mock"]
        assert source["scope"] == source_scope
        assert source["parse_status"] == "complete" and source["usable_segment_ids"]
        selected = client.patch(base + "/inputs", json={"expected_input_revision": 1,
            "selected_source_ids": [source["source_id"]]}).json()
        rev = selected["input_revision"]
        preflight_body = {"expected_input_revision": rev}
        preflight_headers = {"Idempotency-Key": "offline-server-trial-preflight"}
        pending = client.post(base + "/preflights", json=preflight_body, headers=preflight_headers).json()
        job = client.get(base + "/jobs/" + pending["job_id"]).json()
        assert job["status"] == "succeeded", job
        preflight_url = base + "/preflights/" + job["result_ref"]["preflight_id"]
        pf = client.get(preflight_url).json()
        assert pf["input_revision"] == rev and pf["can_generate"] and pf["confirmed_at"] is None
        assert pf["recommendations"]["suggested_pages"] == 1
        assert "납기 조건" in pf["recommendations"]["reason"]
        assert client.get(base).json()["brief"]["target_pages"] == 6
        assert pf["usable_source_ids"] == [source["source_id"]]
        assert client.post(base + "/preflights", json=preflight_body, headers=preflight_headers).json() == pending
        draft_body = {"preflight_id": pf["preflight_id"], "input_revision": rev, "confirmed": False}
        unconfirmed = client.post(base + "/drafts", json=draft_body)
        assert unconfirmed.status_code == 422 and unconfirmed.json()["error"]["code"] == "PREFLIGHT_NOT_CONFIRMED"
        assert client.get(preflight_url).json()["confirmed_at"] is None
        assert llm.trial_report()["calls_started"] == 1
        # 자동 테스트의 확인 조작이다. 실제 시험에서는 사용자의 답변을 기다린다.
        draft_body["confirmed"] = True
        draft_headers = {"Idempotency-Key": "offline-server-trial-draft"}
        generated = client.post(base + "/drafts", json=draft_body, headers=draft_headers).json()
        job = client.get(base + "/jobs/" + generated["job_id"]).json()
        assert job["status"] == "succeeded", job
        document_url = base + "/documents/" + job["result_ref"]["document_id"]
        saved = client.get(document_url).json()
        document = saved["document"]
        assert document["document_revision"] == 1 and document["input_revision"] == rev
        assert len(document["pages"]) == case["target_pages"]
        assert saved["validation"] is None and saved["approval"] is None
        summary = client.get(base).json()["document_summary"]
        assert summary["document_id"] == document["document_id"] and summary["document_revision"] == 1
        confirmed = client.get(preflight_url).json()
        assert confirmed["confirmed_at"] is not None and confirmed["facts"] == pf["facts"]
        facts = {f["fact_id"]: f for f in pf["facts"]}
        paragraphs = [b for p in document["pages"] for b in p["blocks"] if b["fact_ids"]]
        lead_time = next(f for f in facts.values() if f["field_key"] == "lead_time")
        assert lead_time["value"] == D04_EXPECTED["T04"]["lead_time"][1][0][0]
        assert any(b["content"].get("text") == lead_time["value"] for b in paragraphs)
        for block in paragraphs:
            assert block["evidence_refs"]
            expected_refs = [ref for fid in block["fact_ids"] for ref in facts[fid]["evidence_refs"]]
            assert block["evidence_refs"] == expected_refs
            for ref in block["evidence_refs"]:
                assert ref["source_id"] == source["source_id"] and ref["source_version"] == source["source_version"]
                assert ref["segment_id"] in source["usable_segment_ids"]
                assert ref["locator"] and ref["excerpt"] in case["texts"][0]
        assert client.post(base + "/drafts", json=draft_body, headers=draft_headers).json() == generated
        conflict = client.post(base + "/drafts", json=draft_body | {"confirmed": False}, headers=draft_headers)
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
        duplicate = client.post(base + "/drafts", json=draft_body)
        assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "DOCUMENT_EXISTS"
        assert llm.trial_report()["calls_started"] == 2
        if stop_mode == "manual":
            llm.stop_trial()
        assert llm.trial_report()["stop_reason"] == ("manual_stop" if stop_mode == "manual" else "call_limit")
        pending = client.post(base + "/preflights", json=preflight_body,
                              headers={"Idempotency-Key": "offline-server-trial-after-stop"}).json()
        job = client.get(base + "/jobs/" + pending["job_id"]).json()
        assert job["status"] == "failed" and not job["error"]["retryable"], job
        assert client.get(document_url).json() == saved
        assert client.get(preflight_url).json() == confirmed
        # 입력이 바뀌면 이전 버전·이전 점검 모두 추가 AI 호출 없이 거부한다.
        changed = client.patch(base + "/inputs", json={"expected_input_revision": rev,
            "brief": BRIEF.model_copy(update={"target_pages": 1}).model_dump()}).json()
        assert changed["input_revision"] == rev + 1
        for input_revision in (rev, changed["input_revision"]):
            stale = client.post(base + "/drafts", json=draft_body | {"input_revision": input_revision})
            assert stale.status_code == 409 and stale.json()["error"]["code"] == "INPUT_REVISION_CONFLICT"
        cookies = dict(client.cookies)
        assert len(calls) == 4 and llm.trial_report()["calls_started"] == 2
    # 같은 임시 DB를 새 앱 인스턴스로 열어 영속 저장을 확인한다. 프로세스·ledger는 유지한다.
    with TestClient(create_app(settings)) as reopened:
        reopened.cookies.update(cookies)
        assert reopened.get(document_url).json() == saved
        assert reopened.get(preflight_url).json() == confirmed
        assert len(calls) == 4 and llm.trial_report()["calls_started"] == 2


def test_existing_server_preflight_confirm_draft_storage_and_duplicate_request(tmp_path, monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(llm, "create_bridge", lambda settings: llm.LlmAgent(model))
    settings = Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "db.sqlite3",
                        agent_mode="llm", cleanup_sweep_interval_s=0)
    with TestClient(create_app(settings)) as client:
        sid = client.post("/api/v1/sessions", json={"brief": BRIEF.model_dump()}).json()["session_id"]
        base = f"/api/v1/sessions/{sid}"
        upload = client.post(base + "/sources", files=[("files", ("fake.txt", "\n".join(TEXTS.values()).encode()))])
        assert upload.status_code == 202, upload.text
        ids = [item["source_id"] for item in upload.json()["items"]]
        selected = client.patch(base + "/inputs", json={"expected_input_revision": 1, "selected_source_ids": ids})
        rev = selected.json()["input_revision"]
        job_response = client.post(base + "/preflights", json={"expected_input_revision": rev})
        job = client.get(base + "/jobs/" + job_response.json()["job_id"]).json()
        assert job["status"] == "succeeded", job
        pf = client.get(base + "/preflights/" + job["result_ref"]["preflight_id"]).json()
        body = {"preflight_id": pf["preflight_id"], "input_revision": rev, "confirmed": False}
        assert client.post(base + "/drafts", json=body).json()["error"]["code"] == "PREFLIGHT_NOT_CONFIRMED"
        assert len(model.calls) == 1
        body["confirmed"] = True
        # 초안 응답이 잘못되면 문서를 저장하지 않고 재요청할 수 있게 남긴다.
        model.draft_change = lambda result: result["draft_sections"].clear()
        bad = client.post(base + "/drafts", json=body)
        bad_job = client.get(base + "/jobs/" + bad.json()["job_id"]).json()
        assert bad_job["status"] == "failed" and bad_job["error"]["code"] == "AGENT_OUTPUT_INVALID"
        assert client.get(base).json()["document_summary"] is None
        model.draft_change = None
        headers = {"Idempotency-Key": "offline-draft-test"}
        created = client.post(base + "/drafts", json=body, headers=headers)
        assert created.status_code == 202, created.text
        job = client.get(base + "/jobs/" + created.json()["job_id"]).json()
        assert job["status"] == "succeeded", job
        did = job["result_ref"]["document_id"]
        saved = client.get(base + "/documents/" + did).json()
        assert saved["document"]["title"] == TEXTS["company_name"]
        assert saved["document"]["document_revision"] == 1
        assert saved["validation"] is None and saved["approval"] is None
        assert client.post(base + "/drafts", json=body, headers=headers).json() == created.json()
        assert len(model.calls) == 3
        # 실제 의미 검증은 미구현이다. 검증 완료·승인본으로 승격하지 않는다.
        validation = client.post(base + "/documents/" + did + "/validate",
                                 json={"expected_revision": 1, "input_revision": rev})
        validation_job = client.get(base + "/jobs/" + validation.json()["job_id"]).json()
        assert validation_job["status"] == "failed"
        assert validation_job["error"]["code"] == "SERVICE_TEMPORARY_FAILURE"
        assert client.get(base + "/documents/" + did).json() == saved
        # 분석 실패가 기존 문서를 지우거나 가짜 결과로 교체하지 않는다.
        model.extract_change = lambda info: info.pop("company_name")
        failed = client.post(base + "/preflights", json={"expected_input_revision": rev})
        fail_job = client.get(base + "/jobs/" + failed.json()["job_id"]).json()
        assert fail_job["status"] == "failed" and fail_job["error"]["code"] == "AGENT_OUTPUT_INVALID"
        assert client.get(base + "/documents/" + did).json() == saved


def test_image_only_preflight_returns_no_text_guidance_without_ai(tmp_path, monkeypatch):
    import io
    from PIL import Image
    data = io.BytesIO()
    Image.new("RGB", (2, 2)).save(data, format="PNG")
    model = FakeModel()
    monkeypatch.setattr(llm, "create_bridge", lambda settings: llm.LlmAgent(model))
    settings = Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "db.sqlite3",
                        agent_mode="llm", cleanup_sweep_interval_s=0)
    with TestClient(create_app(settings)) as client:
        sid = client.post("/api/v1/sessions", json={"brief": BRIEF.model_dump()}).json()["session_id"]
        base = f"/api/v1/sessions/{sid}"
        uploaded = client.post(base + "/sources", files=[("files", ("fake.png", data.getvalue()))]).json()
        selected = client.patch(base + "/inputs", json={"expected_input_revision": 1,
            "selected_source_ids": [i["source_id"] for i in uploaded["items"]]}).json()
        rev = selected["input_revision"]
        result = client.post(base + "/preflights", json={"expected_input_revision": rev}).json()
        job = client.get(base + "/jobs/" + result["job_id"]).json()
        assert job["status"] == "succeeded", job
        pf = client.get(base + "/preflights/" + job["result_ref"]["preflight_id"]).json()
        assert pf["can_generate"] is False and pf["usable_source_ids"] == []
        draft = client.post(base + "/drafts", json={"preflight_id": pf["preflight_id"], "input_revision": rev, "confirmed": True})
        assert draft.status_code == 422 and draft.json()["error"]["code"] == "NO_USABLE_TEXT"
        assert model.calls == []
