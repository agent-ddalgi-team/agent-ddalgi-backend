"""Deterministic call-B evaluator; no persistence, clock, network or model calls.

The configuration bytes are snapshotted on import. Restart/reload after changing
the versioned configuration. evaluate() only uses that snapshot and its request.
TODO: Compare F.2/F.3/F.4/F.7-F.10 with the original in mvp2/docs/ when available;
the 2026-10-14 user clarification governs until then.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


_RULE_BYTES = Path(__file__).with_name("rules-v0.15.json").read_bytes()
_RULE_HASH = "sha256:" + hashlib.sha256(_RULE_BYTES).hexdigest()
CODE_VERSION = "engine-0.1.0"


class _Invalid(ValueError):
    def __init__(self, field: str, message: str):
        self.field = field
        super().__init__(message)


def _require(condition: bool, field: str, message: str) -> None:
    if not condition:
        raise _Invalid(field, message)


def _string(value, field: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), field, "빈 값이 아닌 문자열이 필요합니다.")
    return value


def _date(value, field: str) -> date:
    _string(value, field)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise _Invalid(field, "YYYY-MM-DD 날짜가 필요합니다.") from None
    _require(parsed.isoformat() == value, field, "YYYY-MM-DD 날짜가 필요합니다.")
    return parsed


def _number(value, field: str) -> Decimal:
    _require(type(value) in (int, float), field, "유한한 숫자가 필요합니다.")
    result = Decimal(str(value))
    _require(result.is_finite() and 0 <= result <= 1, field, "0~1 범위의 숫자가 필요합니다.")
    return result


def _objects(value, field: str) -> list:
    _require(isinstance(value, list), field, "배열이 필요합니다.")
    for index, entry in enumerate(value):
        _require(isinstance(entry, dict), f"{field}[{index}]", "객체가 필요합니다.")
    return value


def _enum(value, choices, field: str) -> None:
    _require(isinstance(value, str) and value in choices, field, "허용된 코드값이 아닙니다.")


def _unique(entries: list, key: str, field: str) -> None:
    seen = set()
    for index, entry in enumerate(entries):
        value = _string(entry.get(key), f"{field}[{index}].{key}")
        _require(value not in seen, f"{field}[{index}].{key}", "중복 ID입니다.")
        seen.add(value)


def _validate(request: dict, rules: dict) -> tuple[date, dict, dict]:
    for key in ("opp_id", "customer_id", "batch_id", "profile_reason", "capdb_version"):
        _string(request.get(key), key)
    _enum(request.get("profile"), rules["profiles"], "profile")
    _require(request.get("rule_version") == rules["rule_version"], "rule_version", "지원하지 않는 규칙 버전입니다.")
    for key in ("relation_status", "data_kind"):
        _enum(request.get(key), rules["enums"][key], key)
    _require(type(request.get("ops_ready")) is bool, "ops_ready", "불리언이 필요합니다.")
    base_date = _date(request.get("base_date"), "base_date")
    links = _objects(request.get("process_links"), "process_links")
    _unique(links, "link_id", "process_links")
    for index, link in enumerate(links):
        for key in ("process", "material"):
            _string(link.get(key), f"process_links[{index}].{key}")
    link_map = {link["link_id"]: link for link in links}

    requirements = _objects(request.get("requirements"), "requirements")
    _unique(requirements, "req_id", "requirements")
    for index, req in enumerate(requirements):
        prefix = f"requirements[{index}]"
        _string(req.get("text"), prefix + ".text")
        for key in ("req_level", "scope_kind"):
            _enum(req.get(key), rules["enums"][key], prefix + "." + key)
        link_id = req.get("link_id")
        if link_id is not None or req["scope_kind"] == "process_link":
            _require(isinstance(link_id, str) and link_id in link_map,
                     prefix + ".link_id", "요구의 link_id가 process_links에 없습니다.")
        if req["scope_kind"] == "opportunity":
            _require(link_id is None, prefix + ".link_id", "기회 전체 요구의 link_id는 null이어야 합니다.")
        if req.get("stage_required") is not None:
            _string(req["stage_required"], prefix + ".stage_required")
        _enum(req.get("customer_evidence_state"), (*rules["certainty"], "미확인"),
              prefix + ".customer_evidence_state")
        cap = req.get("capability_check")
        if cap is not None:
            _require(isinstance(cap, dict), prefix + ".capability_check", "객체 또는 null이 필요합니다.")
            _string(cap.get("status"), prefix + ".capability_check.status")
            _require(cap.get("match") in (None, "일치", "불일치", "미확인"),
                     prefix + ".capability_check.match", "일치·불일치·미확인 또는 null이 필요합니다.")
            if cap.get("note") is not None:
                _string(cap["note"], prefix + ".capability_check.note")
            if cap.get("checked_at") is not None:
                _date(cap["checked_at"], prefix + ".capability_check.checked_at")

    items = _objects(request.get("items"), "items")
    _unique(items, "item_code", "items")
    definitions = {item["item_code"]: item for item in rules["profiles"][request["profile"]]}
    for index, item in enumerate(items):
        prefix = f"items[{index}]"
        _enum(item["item_code"], definitions, prefix + ".item_code")
        _enum(item.get("state"), rules["certainty"], prefix + ".state")
        definition = definitions[item["item_code"]]
        if item.get("match_rate") is not None:
            rate = _number(item["match_rate"], prefix + ".match_rate")
            # Zero-evidence states may explicitly use zero, regardless of item stages.
            permitted = definition["match_rates"]
            if rules["certainty"][item["state"]] == 0:
                permitted = [*permitted, rules["zero_state_match_rate"], 0]
            _require(rate in permitted, prefix + ".match_rate", "해당 항목의 일치도 단계가 아닙니다.")
        if item.get("decay") is not None:
            _number(item["decay"], prefix + ".decay")
        for key in ("event_date", "expected_date", "scheduled_date", "deadline"):
            if item.get(key) is not None:
                _date(item[key], prefix + "." + key)
        if "signal_status" in item:
            _enum(item["signal_status"], rules["signal_statuses"], prefix + ".signal_status")
        if item.get("note") is not None:
            _string(item["note"], prefix + ".note")
        evidence_ids = item.get("evidence_ids")
        _require(isinstance(evidence_ids, list) and all(isinstance(x, str) and x.strip() for x in evidence_ids),
                 prefix + ".evidence_ids", "근거 ID 문자열 배열이 필요합니다.")
        certainty = rules["certainty"][item["state"]]
        if certainty and definition["time_policy"] in ("event", "hiring"):
            # User clarification: a positive general signal without an actual event
            # date is an input error, not a silently inferred date or a zero score.
            _date(item.get("event_date"), prefix + ".event_date")
    _require(len(items) == len(definitions), "items", "프로필의 모든 항목을 한 번씩 제공해야 합니다.")

    timing = request.get("timing_requirements")
    _require(isinstance(timing, dict), "timing_requirements", "객체가 필요합니다.")
    for key in rules["timing_fields"]:
        _require(type(timing.get(key)) is bool, "timing_requirements." + key, "불리언이 필요합니다.")
    if timing.get("deadline") is not None:
        _date(timing["deadline"], "timing_requirements.deadline")
    research = request.get("research_requirements")
    if research is not None:
        _require(isinstance(research, dict), "research_requirements", "객체 또는 null이 필요합니다.")
        for key in rules["research_fields"]:
            _require(type(research.get(key)) is bool, "research_requirements." + key, "불리언이 필요합니다.")
        if research.get("recheck_date") is not None:
            _date(research["recheck_date"], "research_requirements.recheck_date")
    history = _objects(request.get("history", []), "history")
    for index, entry in enumerate(history):
        prefix = f"history[{index}]"
        _require(type(entry.get("allowed")) is bool, prefix + ".allowed", "불리언이 필요합니다.")
        _string(entry.get("apply_scope"), prefix + ".apply_scope")
        _string(entry.get("summary"), prefix + ".summary")
        _date(entry.get("date"), prefix + ".date")
    return base_date, link_map, {item["item_code"]: item for item in items}


def _temporal(item: dict, definition: dict, base_date: date, rules: dict, notes: list) -> tuple:
    """Return effective state, authoritative match rate and calculated decay."""
    code, state = item["item_code"], item["state"]
    rate = Decimal(str(item["match_rate"])) if item.get("match_rate") is not None else None
    full, zero = rules["decay"]["full"], rules["decay"]["zero"]
    decay, calculated_rate = full, None
    status, policy = item.get("signal_status"), definition["time_policy"]
    if state == "미확보":
        return state, None, None
    if rules["certainty"][state] == 0:
        return state, rate if rate is not None else rules["zero_state_match_rate"], full

    # TODO(F.2/F.4): Verify optional status/date names and deadline-day inclusion
    # against the original and BE contract. event_date is never a collection date
    # or an A5/A7 schedule fallback. Human-confirmed match_rate is never replaced.
    if policy in ("reorder", "appointment"):
        schedule = rules["schedule"][policy]
        value = item.get(schedule["date_field"])
        if value is not None:
            days = (date.fromisoformat(value) - base_date).days
            interval = abs(days) if schedule["absolute"] else days
            calculated_rate = next((band["match_rate"] for band in schedule["bands"]
                                    if band["min_days"] <= interval <= band["max_days"]), schedule["otherwise"])
            if days < -schedule["overdue_days"]:
                decay = zero
        if status in rules["closed_statuses"]:
            calculated_rate, decay = zero, zero
        elif status == "미확인":
            state = "미확보"
    elif policy == "notice":
        deadline = item.get("deadline")
        if status in rules["closed_statuses"] or (deadline and date.fromisoformat(deadline) < base_date):
            decay = zero
        elif status == "예정":
            decay = zero
        elif status != "진행 중" or deadline is None:
            state = "미확보"
        else:
            decay = full  # Active notices override the general 90-day decay rule.
    elif policy in ("event", "hiring"):
        age = (base_date - date.fromisoformat(item["event_date"])).days
        decay = next((band["factor"] for band in rules["decay"]["event_bands"]
                      if age <= band["max_days"]), rules["decay"]["after_bands"])
        if age < 0 or status == "예정":
            decay = zero
            notes.append(f"{code}: 미래 예정 사건에는 일반 시점 가점을 부여하지 않습니다.")
        if status == "취소" or (policy == "hiring" and status in rules["closed_statuses"]):
            decay = zero
        elif policy == "hiring" and status not in ("진행 중", "예정"):
            state = "미확보"
    if calculated_rate is not None:
        if rate is None:
            rate = calculated_rate
        elif rate != calculated_rate:
            notes.append(f"{code}: match_rate 입력 {rate}와 날짜/상태 계산 {calculated_rate}가 다릅니다. 확정 입력을 유지합니다.")
    if state == "미확보":
        notes.append(f"{code}: 일정 또는 공고 상태·마감이 미확인되어 미확보로 처리합니다.")
        return state, None, None
    if item.get("decay") is not None and Decimal(str(item["decay"])) != decay:
        notes.append(f"{code}: decay 입력 {item['decay']}와 계산 {decay}가 다릅니다. 계산값을 사용합니다.")
    _require(rate is not None, f"items.{code}.match_rate", "확정 일치도 또는 계산 가능한 일정 날짜가 필요합니다.")
    return state, rate, decay


def _round(value: Decimal, quantum: str) -> Decimal:
    return value.quantize(Decimal(quantum), rounding=ROUND_HALF_UP)


def _score_items(item_map: dict, profile: str, base_date: date, rules: dict, notes: list) -> list:
    result = []
    for definition in rules["profiles"][profile]:
        code = definition["item_code"]
        item = item_map[code]
        state, rate, decay = _temporal(item, definition, base_date, rules, notes)
        certainty = rules["certainty"][state]
        score = None if certainty is None else int(_round(
            Decimal(definition["weight"]) * Decimal(rate) * Decimal(certainty) * Decimal(decay),
            rules["rounding"]["score_quantum"]))
        result.append({key: definition[key] for key in ("item_code", "name", "weight", "axis")} | {
            "state": state, "match_rate": float(rate) if rate is not None else None,
            "certainty": float(certainty) if certainty is not None else None,
            "decay": float(decay) if decay is not None else None,
            "score": score, "evidence_note": item.get("note", ""),
        })
    return result


def _metrics(items: list, rules: dict) -> dict:
    judged = [item for item in items if item["score"] is not None]
    F = sum(item["score"] for item in judged if item["axis"] == "F")
    T = sum(item["score"] for item in judged if item["axis"] == "T")
    E, P = F + T, sum(item["weight"] for item in judged)
    R = E + sum(item["weight"] for item in items if item["score"] is None)
    for item in judged:
        if 0 < item["certainty"] < 1:
            confirmed = Decimal(item["weight"]) * Decimal(str(item["match_rate"])) * Decimal(str(item["decay"]))
            R += int(_round(confirmed, rules["rounding"]["score_quantum"])) - item["score"]
    display = lambda numerator, denominator: float(_round(
        Decimal(numerator) * rules["percent_scale"] / denominator, rules["rounding"]["display_quantum"]))
    return {"E": E, "F": F, "T": T, "P": P, "S": display(E, P) if P else None,
            "C": display(len(judged), len(items)), "R": R,
            "direct_confirmed_weight": sum(item["weight"] for item in judged if item["state"] == "확인"),
            "judged_items": len(judged), "total_items": len(items)}


def _filters(request: dict, links: dict, rules: dict) -> tuple[str, list, list]:
    checks, blocking, gates = [], [], []
    if request["relation_status"] == "관계 미확인":
        gates.append("미확인")
        blocking.append("관계 미확인 · 필터 확인 대기")
    # TODO(F.3): Reconcile stage enums and per-process exclusion aggregation with
    # the original. All scopes retain their checks; one exclusion never deletes
    # another process's scores/checks. Unknown stage text is conservatively pending.
    for req in request["requirements"]:
        cap = req.get("capability_check") or {}
        level, stage = req["req_level"], req.get("stage_required")
        unknown_requirement = level == "미확인" or req["customer_evidence_state"] != "확인"
        reviewed = cap.get("status") == "검수 완료"
        stage_unknown = stage != "현재 단계" and stage not in rules["future_stages"]
        kind, result = None, "통과"
        if level == "비요구":
            reason = "비요구 조건으로 필터·가점에 반영하지 않습니다."
        elif unknown_requirement:
            result, kind, reason = "미확인", "요구 미확인", "고객 요구의 수준·근거 확인이 필요합니다."
        elif not reviewed or cap.get("match") not in ("일치", "불일치"):
            result, kind, reason = "미확인", "충족 미확인", "거산 보유·범위·상태 검수 전"
        elif cap["match"] == "불일치":
            result, reason = "제외", "검수된 역량과 요구가 불일치합니다."
        else:
            reason = "검수된 역량과 요구가 일치합니다."
        gate = None
        if level == "필수" and not unknown_requirement:
            if stage_unknown:
                gate = "미확인"
                if reviewed and cap.get("match") in ("일치", "불일치"):
                    result, kind, reason = "미확인", "충족 미확인", "필수 요구의 필요 단계 확인 필요"
            elif stage == "현재 단계" and result != "통과":
                gate = result
        if gate:
            gates.append(gate)
            label = req["text"].removesuffix(" 보유")
            suffix = "거산 보유·범위·상태 검수 전" if not reviewed else reason
            blocking.append(f"{req['req_id']} {label} · 필수 · {kind or result} ({suffix})")
        scope = "기회 전체" if req["scope_kind"] == "opportunity" else "공정: " + links[req["link_id"]]["process"]
        checks.append({"req_id": req["req_id"], "result": result, "result_kind": kind,
                       "scope": scope, "stage": stage or "—",
                       "reason": reason + (" " + cap["note"] if cap.get("note") else ""),
                       "checked_at": cap.get("checked_at") if reviewed else None})
    state = "제외" if "제외" in gates else "미확인" if "미확인" in gates else "통과"
    return state, checks, blocking


def _flags(request: dict, items: list, rules: dict, base_date: date) -> dict:
    core = rules["core"]
    eligible = {item["item_code"] for item in items
                if item["state"] in core["accepted_states"] and item["match_rate"] and item["score"]}
    # TODO(F.8): Existing input has no separate capability proof per item. Require
    # reviewed matching process requirements for every selected process, instead
    # of mining free-text notes or inventing a company capability confirmation.
    reviewed_links = {req["link_id"] for req in request["requirements"]
                      if req["scope_kind"] == "process_link" and req["req_level"] != "비요구"
                      and (req.get("capability_check") or {}).get("status") == "검수 완료"
                      and (req.get("capability_check") or {}).get("match") == "일치"}
    links = {link["link_id"] for link in request["process_links"]}
    capability = bool(links) and links <= reviewed_links
    customer = set(core[request["profile"] + "_required_items"]) <= eligible
    if request["profile"] == "A":
        history_ok = any(entry["allowed"] and entry["apply_scope"] == "opportunity"
                         and date.fromisoformat(entry["date"]) <= base_date for entry in request.get("history", []))
        customer = customer and history_ok and bool(set(core["A_reason_items"]) & eligible)
    core_ok = customer and capability
    timing = request["timing_requirements"]
    deadline = timing.get("deadline")
    timing_ok = all(timing[key] for key in rules["timing_fields"]) and bool(deadline) and date.fromisoformat(deadline) >= base_date
    research = request.get("research_requirements")
    research_state = None if research is None else (
        "모두 충족" if all(research[key] for key in rules["research_fields"]) else "일부 미충족")
    return {"ops_ready": request["ops_ready"], "core_evidence": "충족" if core_ok else "재확인",
            "core_reason": "고객 연결 근거 확인/강한 추정 + 거산 근거 검수 완료" if core_ok else "고객 연결 근거·허용 이력 또는 거산 근거 검수 재확인 필요",
            "timing_priority": bool(timing_ok), "research_ready": research_state}


def _classify(filter_state: str, metrics: dict, flags: dict, rules: dict) -> dict:
    E, P, R = (metrics[key] for key in ("E", "P", "R"))
    thresholds = rules["thresholds"]
    # F.7/F.9 clarification: E/P are integer item-score sums; S/C are display only.
    if filter_state == "제외":
        rule = 1
    elif filter_state == "미확인":
        rule = 2
    elif not flags["ops_ready"]:
        rule = 3
    elif P == 0:
        rule = 4
    elif E >= thresholds["contact"] and flags["core_evidence"] == "충족":
        rule = 5 if flags["timing_priority"] else 6
    elif E >= thresholds["review_score"] and P >= thresholds["review_coverage"] and flags["core_evidence"] == "충족":
        rule = 7
    elif R >= thresholds["research_potential"]:
        rule = 8 if flags["research_ready"] == "모두 충족" else 9
    elif R >= thresholds["watch_potential"]:
        rule = 10
    else:
        rule = 11
    return dict(rules["classification"][str(rule)])


def evaluate(request: dict) -> dict:
    """Evaluate a JSON request without modifying it or performing any I/O.

    notes extends the sample response with requested date/status warnings.
    eval_id identifies the input + rules + code snapshot, not a database row.
    created_at remains null: only BE can supply an actual persistence timestamp.
    Missing checked_at is likewise never inferred from unrelated evidence dates.
    """
    rules = json.loads(_RULE_BYTES, parse_float=Decimal)
    metadata = request if isinstance(request, dict) else {}
    versions = {"rule_version": rules["rule_version"], "rule_hash": _RULE_HASH,
                "code_version": CODE_VERSION, "capdb_version": metadata.get("capdb_version")}
    identity = {"eval_id": None, "opp_id": metadata.get("opp_id"), "batch_id": metadata.get("batch_id")}
    try:
        _require(isinstance(request, dict), "request", "요청 객체가 필요합니다.")
        try:
            canonical = json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (ValueError, TypeError, RecursionError):
            raise _Invalid("request", "유한한 숫자와 JSON 호환 값만 사용할 수 있습니다.") from None
        identity["eval_id"] = "EVAL-" + hashlib.sha256(
            (CODE_VERSION + "\n" + _RULE_HASH + "\n" + canonical).encode("utf-8")).hexdigest()
        base_date, links, item_map = _validate(request, rules)
        filter_state, checks, blocking = _filters(request, links, rules)
        notes = []
        items = _score_items(item_map, request["profile"], base_date, rules, notes)
        metrics = _metrics(items, rules)
        flags = _flags(request, items, rules, base_date)
        classification = _classify(filter_state, metrics, flags, rules)
        deadline = request["timing_requirements"].get("deadline")
        valid_deadline = deadline if (deadline and request["timing_requirements"]["deadline_confirmed"]
                                      and date.fromisoformat(deadline) >= base_date) else None
        research = request.get("research_requirements") or {}
        return identity | {
            "run_status": "완료", "profile": request["profile"], "base_date": request["base_date"],
            "data_kind": request["data_kind"], "filter_state": filter_state, "filter_reasons": list(blocking),
            "requirement_checks": checks, "items": items, "metrics": metrics, "flags": flags,
            "classification": classification, "ranked": classification["group"] == "contact",
            # Single-request evaluation returns the sample's sort components.
            # Caller groups by profile/classification; opp_id breaks remaining ties.
            "sort_keys": {"deadline": valid_deadline, **{key: metrics[key] for key in ("E", "F", "direct_confirmed_weight", "P")},
                          "recheck_date": research.get("recheck_date")},
            "blocking": blocking, "questions": {"internal": [], "customer": [], "generation": None},
            "next_action": {"text": None, "purpose": None, "owner": None, "deadline": None},
            "explanation": None, "versions": versions, "created_at": None, "notes": notes,
        }
    except _Invalid as error:
        return identity | {"run_status": "오류", "error": {"code": "INPUT_INVALID", "field": error.field,
                           "message": "입력 검증 실패 — " + str(error)}, "classification": None,
                           "ranked": False, "versions": versions, "data_kind": metadata.get("data_kind")}
