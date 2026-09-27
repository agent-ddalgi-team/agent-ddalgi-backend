"""AG-01~04 첫 연결: 가짜 회사·가짜 모델 응답만 사용한다.

실행: PYTHON_DOTENV_DISABLED=1 .venv/bin/python -B -m pytest tests/test_agent_llm.py
테스트 중 외부 소켓 연결과 실제 SDK 클라이언트 생성을 금지한다.
실제 모델의 사실 판단·표현 품질·출력 배치는 이 테스트의 통과 범위가 아니다.
"""
from __future__ import annotations

import copy
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
from app.models import Brief, PreflightOut
from app.services.ai_jobs import validate_analyze, validate_draft

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
    def delayed(*args):
        entered.set()
        assert release.wait(timeout=5)
        return original(*args)
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


def test_stopped_trial_preserves_existing_document_through_server(tmp_path, monkeypatch):
    def respond(**kwargs):
        payload = json.loads(kwargs["input"])
        body = extraction(payload) if kwargs["text"]["format"]["name"] == "company_info" else draft_response(payload)
        return metered_response(output_text=json.dumps(body, ensure_ascii=False))
    calls = fake_sdk(monkeypatch, response=respond)
    options = llm.LlmOptions.from_env(config_env())
    monkeypatch.setattr(llm.LlmOptions, "from_env", classmethod(lambda cls, env: options))
    settings = Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "db.sqlite3",
                        agent_mode="llm", cleanup_sweep_interval_s=0)
    with TestClient(create_app(settings)) as client:
        sid = client.post("/api/v1/sessions", json={"brief": BRIEF.model_dump()}).json()["session_id"]
        base = f"/api/v1/sessions/{sid}"
        upload = client.post(base + "/sources", files=[("files", ("fake.txt", "\n".join(TEXTS.values()).encode()))]).json()
        selected = client.patch(base + "/inputs", json={"expected_input_revision": 1,
            "selected_source_ids": [item["source_id"] for item in upload["items"]]}).json()
        rev = selected["input_revision"]
        pending = client.post(base + "/preflights", json={"expected_input_revision": rev}).json()
        job = client.get(base + "/jobs/" + pending["job_id"]).json()
        assert job["status"] == "succeeded", job
        generated = client.post(base + "/drafts", json={"preflight_id": job["result_ref"]["preflight_id"],
            "input_revision": rev, "confirmed": True}).json()
        job = client.get(base + "/jobs/" + generated["job_id"]).json()
        assert job["status"] == "succeeded", job
        document_url = base + "/documents/" + job["result_ref"]["document_id"]
        saved = client.get(document_url).json()
        assert llm.trial_report()["calls_started"] == 2
        llm.stop_trial()
        pending = client.post(base + "/preflights", json={"expected_input_revision": rev}).json()
        job = client.get(base + "/jobs/" + pending["job_id"]).json()
        assert job["status"] == "failed" and not job["error"]["retryable"], job
        assert client.get(document_url).json() == saved
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
