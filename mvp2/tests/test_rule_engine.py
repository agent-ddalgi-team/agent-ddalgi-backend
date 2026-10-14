"""Offline rule-engine tests; the supplied four-case JSON is read-only."""

import copy
import hashlib
import itertools
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from mvp2.agent import rule_engine
from mvp2.agent.rule_engine import evaluate


MVP2 = Path(__file__).resolve().parents[1]
ROOT = MVP2.parent
CASES = json.loads((MVP2 / "agent_test_cases.json").read_text(encoding="utf-8-sig"))["cases"]
SAMPLES = json.loads((MVP2 / "agent_call_samples.json").read_text(encoding="utf-8-sig"))


def request(index=0):
    return copy.deepcopy(CASES[index]["input"])


def item(data, code):
    return next(entry for entry in data["items"] if entry["item_code"] == code)


def valid(data):
    result = evaluate(data)
    assert result["run_status"] == "완료", result
    metrics = result["metrics"]
    assert 0 <= metrics["E"] <= metrics["P"] <= 100
    assert metrics["E"] <= metrics["R"] <= 100
    return result


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_supplied_cases(case):
    result = valid(case["input"])
    expected = case["expected"]
    assert result["filter_state"] == expected["filter_state"]
    assert result["classification"] == expected["classification"]
    assert result["metrics"] == expected["metrics"]  # E/F/T/P/S/C/R and counts.
    assert result["flags"] == expected["flags"]
    assert result["blocking"] == expected["blocking"]
    for source in case["input"]["items"]:
        if source["state"] == "미확보":
            result_item = item(result, source["item_code"])
            assert all(result_item[key] is None for key in ("score", "match_rate", "certainty", "decay"))
    assert result["metrics"]["P"] == sum(entry["weight"] for entry in result["items"] if entry["score"] is not None)


def test_zero_is_not_missing_and_changes_research_to_watch():
    data = request(1)
    for code in ("A4", "A5"):
        item(data, code)["state"] = "신호 없음"
    result = valid(data)
    assert result["metrics"]["E"] == result["metrics"]["R"] == 56
    assert result["metrics"]["P"] == 100
    assert result["classification"]["rule"] == 10
    assert all(item(result, code)["score"] == 0 for code in ("A4", "A5"))


@pytest.mark.parametrize("index", range(4))
def test_deterministic_no_mutation_and_no_io(index, monkeypatch):
    data = request(index)
    original = copy.deepcopy(data)

    def forbidden(*args, **kwargs):
        raise AssertionError("evaluate must not perform I/O")

    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    first = evaluate(data)
    second = evaluate(data)
    assert first == second
    assert data == original
    # Mutating a returned value must not change subsequent evaluations.
    first["items"][0]["score"] = -1
    first["versions"]["rule_version"] = "changed"
    assert evaluate(data) == second
    assert evaluate(dict(reversed(list(data.items())))) == second


def test_response_shape_hash_empty_llm_fields_and_snapshot_identity():
    result = valid(SAMPLES["call_B_evaluate"]["request"])
    assert set(result) == set(SAMPLES["call_B_evaluate"]["response"]) | {"notes"}
    for key in ("items", "requirement_checks"):
        assert set(result[key][0]) == set(SAMPLES["call_B_evaluate"]["response"][key][0])
    assert result["versions"]["rule_hash"] == "sha256:" + hashlib.sha256(
        (MVP2 / "agent" / "rules-v0.15.json").read_bytes()).hexdigest()
    assert result["questions"] == {"internal": [], "customer": [], "generation": None}
    assert all(value is None for value in result["next_action"].values())
    assert result["explanation"] is result["created_at"] is None
    assert all(check["checked_at"] is None for check in result["requirement_checks"])
    changed = request()
    changed["base_date"] = "2026-10-15"
    assert evaluate(changed)["eval_id"] != result["eval_id"]


def test_invalid_link_matches_error_sample_shape():
    data = request()
    data["requirements"][2]["link_id"] = "LNK-does-not-exist"
    result = evaluate(data)
    assert set(result) == set(SAMPLES["call_B_error"]["response"])
    assert result["run_status"] == "오류"
    assert result["error"]["code"] == "INPUT_INVALID"
    assert result["error"]["field"] == "requirements[2].link_id"
    assert result["classification"] is None
    assert result["ranked"] is False


@pytest.mark.parametrize("state,match,expected_rule", [("검수 대기", None, 2), ("검수 완료", "불일치", 1)])
def test_filter_precedes_high_scores_and_other_scope_checks_survive(state, match, expected_rule):
    data = request(3)
    data["requirements"][0]["capability_check"].update(status=state, match=match)
    result = valid(data)
    assert result["metrics"]["E"] == 77
    assert result["classification"]["rule"] == expected_rule
    assert result["ranked"] is False
    assert result["blocking"] and result["blocking"][0].startswith("R1 ")
    assert result["requirement_checks"][1]["result"] == "통과"


def test_exclusion_precedes_pending_and_scores_do_not_short_circuit():
    data = request(2)
    data["requirements"][1]["capability_check"]["match"] = "불일치"
    result = valid(data)
    assert result["classification"]["rule"] == 1
    assert len(result["blocking"]) == 2
    assert result["metrics"]["E"] == 74
    assert len(result["requirement_checks"]) == 3


@pytest.mark.parametrize("level", ["우대", "비요구", "미확인"])
def test_nonmandatory_and_unknown_requirements_do_not_block(level):
    data = request(3)
    data["requirements"].append({"req_id": "R-extra", "text": "추가 조건", "req_level": level,
                                 "scope_kind": "opportunity", "link_id": None, "stage_required": None,
                                 "customer_evidence_state": "미확인" if level == "미확인" else "확인",
                                 "capability_check": {"status": "검수 대기", "match": None}})
    result = valid(data)
    assert result["filter_state"] == "통과"
    assert result["blocking"] == []
    assert result["classification"]["rule"] == 5


def test_unknown_stage_and_relation_block_but_explicit_future_stage_does_not():
    data = request(3)
    data["requirements"][0]["stage_required"] = None
    assert valid(data)["classification"]["rule"] == 2
    data["requirements"][0]["stage_required"] = "샘플 단계"
    data["requirements"][0]["capability_check"]["status"] = "검수 대기"
    assert valid(data)["filter_state"] == "통과"
    data["relation_status"] = "관계 미확인"
    assert valid(data)["classification"]["rule"] == 2


@pytest.mark.parametrize("age,decay", [(0, 1), (30, 1), (31, .5), (90, .5), (91, 0), (-1, 0)])
@pytest.mark.parametrize("case_index,code", [(0, "B5"), (0, "B6"), (0, "B8"), (1, "A6")])
def test_general_event_decay_boundaries(case_index, code, age, decay):
    data = request(case_index)
    entry = item(data, code)
    entry.update(state="확인", match_rate=1, signal_status="진행 중",
                 event_date=(date.fromisoformat(data["base_date"]) - timedelta(days=age)).isoformat())
    result = valid(data)
    assert item(result, code)["decay"] == decay


@pytest.mark.parametrize("code", ["B5", "B6", "B8"])
@pytest.mark.parametrize("state", ["확인", "강한 추정", "약한 추정"])
def test_missing_general_event_date_is_invalid_not_silently_missing(code, state):
    data = request()
    entry = item(data, code)
    entry.update(state=state, match_rate=1, signal_status="진행 중")
    entry.pop("event_date", None)
    result = evaluate(data)
    assert result["run_status"] == "오류"
    assert result["error"]["code"] == "INPUT_INVALID"
    assert result["error"]["field"].endswith(".event_date")


def test_fit_items_do_not_decay_and_half_up_rounds_each_item_before_sum():
    data = request(2)
    for entry in data["items"][:4]:
        entry["event_date"] = "1990-01-01"
    result = valid(data)
    assert all(entry["decay"] == 1 for entry in result["items"][:4])
    assert item(result, "B4")["score"] == 7  # 10 * .65 = 6.5, never banker's 6.
    assert result["metrics"]["F"] == 62
    assert item(valid(request(3)), "A3")["score"] == 8  # 15 * .5 = 7.5.


@pytest.mark.parametrize("status,deadline,expected_score", [
    ("진행 중", "2026-10-15", 7), ("진행 중", "2026-10-14", 7),
    ("진행 중", "2026-10-13", 0), ("마감", "2026-10-15", 0),
    ("취소", "2026-10-15", 0), ("미확인", "2026-10-15", None),
    ("진행 중", None, None), (None, "2026-10-15", None),
])
def test_notice_overrides_general_decay_and_unknown_excludes_weight(status, deadline, expected_score):
    data = request()
    entry = item(data, "B7")
    entry.update(state="확인", match_rate=1, event_date="2025-01-01", deadline=deadline)
    if status is not None:
        entry["signal_status"] = status
    result = valid(data)
    assert item(result, "B7")["score"] == expected_score
    assert result["metrics"]["P"] == (83 if expected_score is None else 90)


@pytest.mark.parametrize("status,score", [("진행 중", 5), ("마감", 0), ("완료", 0), ("취소", 0), ("미확인", None), (None, None)])
def test_hiring_requires_active_status(status, score):
    data = request()
    entry = item(data, "B8")
    entry.update(state="확인", match_rate=1, event_date="2026-10-01")
    if status is not None:
        entry["signal_status"] = status
    assert item(valid(data), "B8")["score"] == score


@pytest.mark.parametrize("status,decay", [("취소", 0), ("예정", 0), ("미확인", .5), ("진행 중", .5)])
def test_event_status_does_not_extend_old_event(status, decay):
    data = request(1)
    item(data, "A6")["signal_status"] = status
    assert item(valid(data), "A6")["decay"] == decay


@pytest.mark.parametrize("code,key,days,expected_rate,expected_decay", [
    ("A5", "expected_date", 60, 1, 1), ("A5", "expected_date", -60, 1, 1),
    ("A5", "expected_date", 61, .5, 1), ("A5", "expected_date", -120, .5, 1),
    ("A5", "expected_date", 121, 0, 1), ("A5", "expected_date", -121, 0, 0),
    ("A7", "scheduled_date", -30, 1, 1), ("A7", "scheduled_date", 0, 1, 1),
    ("A7", "scheduled_date", 30, 1, 1), ("A7", "scheduled_date", 31, .5, 1),
    ("A7", "scheduled_date", 90, .5, 1), ("A7", "scheduled_date", 91, 0, 1),
    ("A7", "scheduled_date", -31, 0, 0),
])
def test_schedule_stages_without_confirmed_match(code, key, days, expected_rate, expected_decay):
    data = request(1)
    entry = item(data, code)
    entry.update(state="확인", **{key: (date.fromisoformat(data["base_date"]) + timedelta(days=days)).isoformat()})
    entry.pop("match_rate", None)
    result = valid(data)
    assert item(result, code)["match_rate"] == expected_rate
    assert item(result, code)["decay"] == expected_decay


@pytest.mark.parametrize("status", ["완료", "취소"])
def test_closed_schedule_scores_zero_but_preserves_human_match(status):
    data = request(1)
    item(data, "A7").update(signal_status=status, scheduled_date="2026-11-02")
    result = valid(data)
    assert item(result, "A7")["match_rate"] == 1
    assert item(result, "A7")["score"] == 0
    assert any("A7" in note and "match_rate" in note for note in result["notes"])


def test_human_match_preserved_warning_and_computed_decay_authoritative():
    data = request(1)
    item(data, "A7").update(scheduled_date="2026-12-01", decay=.5)
    result = valid(data)
    assert item(result, "A7")["match_rate"] == 1  # computed stage is .5.
    assert item(result, "A7")["decay"] == 1
    assert len(result["notes"]) == 2
    assert "match_rate" in result["notes"][0] and "decay" in result["notes"][1]


def test_schedule_event_date_never_substitutes_for_schedule_or_extends_it():
    data = request(1)
    item(data, "A7")["event_date"] = "1990-01-01"
    assert item(valid(data), "A7")["score"] == 10  # confirmed match only, no scheduled_date.
    item(data, "A7")["scheduled_date"] = "2026-09-01"
    assert item(valid(data), "A7")["score"] == 0


def test_all_missing_and_operations_pending_priorities():
    data = request()
    for entry in data["items"]:
        entry["state"] = "미확보"
    result = valid(data)
    assert result["metrics"]["E"] == result["metrics"]["P"] == 0
    assert result["metrics"]["R"] == 100
    assert result["metrics"]["S"] is None
    assert result["classification"]["rule"] == 4
    data["ops_ready"] = False
    assert valid(data)["classification"]["rule"] == 3


def test_missing_capability_or_allowed_history_prevents_contact():
    data = request(3)
    data["history"] = []
    result = valid(data)
    assert result["flags"]["core_evidence"] == "재확인"
    assert result["classification"]["rule"] == 9
    data = request()
    data["requirements"] = []
    assert valid(data)["classification"]["rule"] == 9


def test_all_timing_conditions_and_unexpired_deadline_required():
    for key in ("direct_action_signal", "deadline_confirmed", "ready_confirmed"):
        data = request(3)
        data["timing_requirements"][key] = False
        assert valid(data)["classification"]["rule"] == 6
    data = request(3)
    data["timing_requirements"]["deadline"] = "2026-10-13"
    result = valid(data)
    assert result["classification"]["rule"] == 6
    assert result["sort_keys"]["deadline"] is None


def test_each_research_requirement_and_recheck_sort_key():
    for key in ("evidence_grade_ok", "question_set", "path_and_owner", "can_change_class", "time_capacity"):
        data = request(1)
        data["research_requirements"][key] = False
        result = valid(data)
        assert result["classification"]["rule"] == 9
        assert result["sort_keys"]["recheck_date"] == "2026-11-02"


def test_thresholds_and_weights_come_from_configuration(monkeypatch):
    rules = json.loads(rule_engine._RULE_BYTES)
    rules["thresholds"]["contact"] = 78
    rules["profiles"]["A"][0]["weight"] = 24
    monkeypatch.setattr(rule_engine, "_RULE_BYTES", json.dumps(rules).encode())
    result = valid(request(3))
    assert result["classification"]["rule"] == 7
    assert result["metrics"]["E"] == 76
    assert item(result, "A1")["weight"] == 24


def test_integer_item_sums_not_display_ratios_drive_branch():
    data = request(2)
    data["requirements"][0]["req_level"] = "우대"
    item(data, "B6")["event_date"] = "2026-10-01"
    # E=76 after rounding individual 10.05 and 6.5; unrounded total is 75.55.
    result = valid(data)
    assert result["metrics"]["E"] == 76
    assert result["classification"]["rule"] == 6


@pytest.mark.parametrize("E,P,R,core,timing,research,expected", [
    (75, 80, 95, True, True, True, 5),
    (75, 80, 95, True, False, True, 6),
    (74, 80, 94, True, True, True, 7),
    (60, 70, 90, True, False, True, 7),
    (60, 69, 91, True, False, True, 8),
    (59, 70, 89, True, False, True, 8),
    (75, 80, 95, False, True, True, 8),
    (20, 60, 60, False, False, False, 9),
    (20, 100, 59, False, False, True, 10),
    (20, 100, 40, False, False, True, 10),
    (20, 100, 39, False, False, True, 11),
])
def test_classification_threshold_boundaries(E, P, R, core, timing, research, expected):
    rules = json.loads(rule_engine._RULE_BYTES)
    metrics = {"E": E, "P": P, "R": R, "S": 100, "C": 100}
    flags = {"ops_ready": True, "core_evidence": "충족" if core else "재확인",
             "timing_priority": timing, "research_ready": "모두 충족" if research else None}
    result = rule_engine._classify("통과", metrics, flags, rules)
    assert result["rule"] == expected


def test_invariants_across_both_profiles_and_all_certainty_combinations():
    states = ("확인", "강한 추정", "약한 추정", "신호 없음", "미확보")
    for index in (0, 3):
        for combo in itertools.product(states, repeat=3):
            data = request(index)
            for entry, state in zip(data["items"], combo):
                entry["state"] = state
            valid(data)


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(ops_ready="true"),
    lambda data: data.update(profile="C"),
    lambda data: data.update(rule_version="rules-unknown"),
    lambda data: data.update(base_date="2026-02-30"),
    lambda data: data["items"].pop(),
    lambda data: data["items"].append(copy.deepcopy(data["items"][0])),
    lambda data: data["items"][0].update(match_rate=True),
    lambda data: data["items"][0].update(match_rate=.123),
    lambda data: data["items"][0].update(state="unknown"),
    lambda data: data["items"][0].update(match_rate=float("nan")),
    lambda data: data["requirements"][1].update(link_id=None),
])
def test_invalid_inputs_return_structured_errors(mutation):
    data = request()
    mutation(data)
    result = evaluate(data)
    assert result["run_status"] == "오류"
    assert result["error"]["code"] == "INPUT_INVALID"
    assert result["ranked"] is False and result["classification"] is None


def run_cli(path):
    return subprocess.run([sys.executable, "-B", "-m", "mvp2.agent.cli", str(path)],
                          cwd=ROOT, capture_output=True, encoding="utf-8", check=False)


def test_cli_fixture_bundle():
    result = run_cli(MVP2 / "agent_test_cases.json")
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert len(output["results"]) == 4
    for original, actual in zip(CASES, output["results"]):
        assert actual["case_id"] == original["case_id"]
        assert actual["response"] == evaluate(original["input"])


def test_cli_raw_request_and_invalid_reference(tmp_path):
    path = tmp_path / "case.json"
    data = request()
    path.write_text(json.dumps(data), encoding="utf-8")
    result = run_cli(path)
    assert result.returncode == 0
    assert json.loads(result.stdout) == evaluate(data)
    data["requirements"][1]["link_id"] = "missing"
    path.write_text(json.dumps({"input": data}), encoding="utf-8")
    result = run_cli(path)
    assert result.returncode == 1
    assert json.loads(result.stdout)["error"]["code"] == "INPUT_INVALID"


@pytest.mark.parametrize("text", ['{"profile":"A", "profile":"B"}', '{"number":NaN}', '{'])
def test_cli_rejects_invalid_json(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text, encoding="utf-8")
    result = run_cli(path)
    assert result.returncode == 2
    assert not result.stdout
    assert "사례 JSON을 읽을 수 없습니다" in result.stderr
