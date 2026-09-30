"""이전 Agent의 추출·본문 생성과 검사 규칙을 보존한 내부 모듈.

원본: agent-ddalgi develop e8fc52ad84157ab9da752fc905da4228db6a06c1의 backend/agent.py.
모델 호출은 request_json 함수로 전달받는다. 이 파일은 SDK·환경 설정을 읽거나 결과를 저장하지 않는다.
출처·상태·본문 검사 규칙과 원본 JSON 스키마를 재사용하며, 현재 서버 형식 변환은 agent_llm이 맡는다.
형식·인용 검사를 통과해도 문장의 의미가 검증되었다는 뜻은 아니다.
"""
from __future__ import annotations
import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROFILE_SCHEMA_PATH = ROOT / 'contracts' / 'profile.schema.json'
EXTRACT_PROMPT_PATH = ROOT / 'prompts' / 'extract.txt'

SCHEMA_VERSION = '1.0'
RequestJson = Callable[[str, dict[str, Any], dict[str, Any], str], dict[str, Any]]
# contract.md 1절의 초기 제안 상한. 넘으면 조용히 자르지 않고 멈춘다.
MAX_SOURCE_CHARS = 200_000
SOURCE_UNIT_KEYS = ('source_id', 'locator', 'text')

# 14개 키는 직접 적지 않고 공통 스키마의 company_info.required에서 가져온다.
_PROFILE_SCHEMA = json.loads(PROFILE_SCHEMA_PATH.read_text(encoding='utf-8'))
COMPANY_INFO_KEYS: tuple[str, ...] = tuple(_PROFILE_SCHEMA['properties']['company_info']['required'])


class AgentError(Exception):
    """contract.md ErrorObject의 code로 전달할 수 있는 Agent 실패."""

    def __init__(self, code: str, rule: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(f'{code} [{rule}] {message}')
        self.code = code
        self.rule = rule
        self.message = message
        # 실패 시점의 호출 정보(모델·응답 상태·토큰 수). 키 값이나 원문은 넣지 않는다.
        self.details = details


class AgentInputError(ValueError):
    """호출하는 코드가 약속과 다른 agent_input을 넘긴 경우. 맞는 공통 오류 code가 없어 새로 만들지 않았다."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f'[{rule}] {message}')
        self.rule = rule
        self.message = message


def check_agent_input(agent_input: Any) -> None:
    """agent_input이 contract.md 4절 모양인지 확인한다. 문제가 있으면 LLM 호출 전에 예외를 던진다."""
    if not isinstance(agent_input, dict):
        raise AgentInputError('input_not_object', 'agent_input은 JSON 객체(dict)여야 합니다.')
    version = agent_input.get('schema_version')
    if version != SCHEMA_VERSION:
        raise AgentInputError('schema_version', f'schema_version은 "{SCHEMA_VERSION}"이어야 합니다: {version!r}')
    hint = agent_input.get('company_name_hint')
    if hint is not None and not isinstance(hint, str):
        raise AgentInputError('company_name_hint_type', 'company_name_hint는 문자열이거나 없어야 합니다.')

    units = agent_input.get('source_units')
    if not isinstance(units, list):
        raise AgentInputError('source_units_type', 'source_units는 배열이어야 합니다.')
    if not units:
        raise AgentError('NEEDS_TEXT_SOURCE', 'source_units_empty', '분석할 자료 조각이 없습니다.')

    seen: set[tuple[str, str]] = set()
    total_chars = 0
    for i, unit in enumerate(units):
        where = f'source_units[{i}]'
        if not isinstance(unit, dict):
            raise AgentInputError('source_unit_not_object', f'{where}는 객체여야 합니다.')
        for key in SOURCE_UNIT_KEYS:
            value = unit.get(key)
            if not isinstance(value, str) or not value.strip():
                raise AgentInputError('source_unit_field', f'{where}.{key}는 비어 있지 않은 문자열이어야 합니다.')
        # 같은 자료·같은 위치가 두 번 나오면 근거 위치를 하나로 특정할 수 없다.
        pair = (unit['source_id'], unit['locator'])
        if pair in seen:
            raise AgentInputError('source_unit_duplicate', f'{where}: source_id+locator가 중복됩니다: {pair[0]} {pair[1]}')
        seen.add(pair)
        total_chars += len(unit['text'])

    if total_chars > MAX_SOURCE_CHARS:
        raise AgentError(
            'INPUT_TOO_LARGE', 'source_text_limit',
            f'자료 텍스트 합계 {total_chars:,}자가 상한 {MAX_SOURCE_CHARS:,}자를 넘습니다.',
        )


def load_extract_prompt() -> str:
    """prompts/extract.txt를 앞뒤 공백만 정리해 읽는다. 14개 항목 이름이 빠져 있으면 멈춘다."""
    if not EXTRACT_PROMPT_PATH.is_file():
        raise FileNotFoundError('추출 프롬프트 파일이 없습니다: prompts/extract.txt')
    prompt = EXTRACT_PROMPT_PATH.read_text(encoding='utf-8').strip()
    if not prompt:
        raise ValueError('추출 프롬프트 파일이 비어 있습니다: prompts/extract.txt')
    missing = [key for key in COMPANY_INFO_KEYS if key not in prompt]
    if missing:
        raise ValueError(f'추출 프롬프트에 company_info 항목 이름이 빠져 있습니다: {", ".join(missing)}')
    return prompt


# Structured Outputs(strict)에 넘길 때 남기는 JSON Schema 키워드.
# 조건 규칙(allOf/if/then)과 최소 개수·길이는 빠지므로, 응답을 받은 뒤 코드가 원래 규격으로 다시 검사해야 한다.
_MODEL_SCHEMA_KEYWORDS = {'type', 'enum', 'const', 'properties', 'required', 'additionalProperties', 'items', '$ref'}
_MODEL_SCHEMA_DEFS = ('field', 'fact', 'evidence')


def _keep_model_keywords(node: dict[str, Any]) -> dict[str, Any]:
    """스키마 조각에서 _MODEL_SCHEMA_KEYWORDS만 남긴 사본을 만든다. properties 아래의 항목 이름은 그대로 둔다."""
    kept: dict[str, Any] = {}
    for key, value in node.items():
        if key not in _MODEL_SCHEMA_KEYWORDS:
            continue
        if key == 'properties':
            kept[key] = {name: _keep_model_keywords(sub) for name, sub in value.items()}
        elif key == 'items':
            kept[key] = _keep_model_keywords(value)
        else:
            kept[key] = copy.deepcopy(value)
    return kept


def build_model_output_schema() -> dict[str, Any]:
    """profile.schema.json의 company_info 구조에서 GPT용 출력 스키마를 파생한다.

    바뀌는 점은 두 가지뿐이다: fact_id 제외(코드가 F001부터 부여), strict 모드가 받지 않는 키워드 제외.
    sources·is_mock·validation·schema_version은 company_info 밖에 있으므로 처음부터 들어가지 않는다.
    """
    defs = {name: _keep_model_keywords(_PROFILE_SCHEMA['$defs'][name]) for name in _MODEL_SCHEMA_DEFS}
    defs['fact']['properties'].pop('fact_id')
    defs['fact']['required'] = [key for key in defs['fact']['required'] if key != 'fact_id']
    # 원래 규격의 status에는 enum만 있다. 모델용 스키마에만 문자열 타입을 명시한다.
    defs['field']['properties']['status'] = {'type': 'string', **defs['field']['properties']['status']}
    company_info = _keep_model_keywords(_PROFILE_SCHEMA['properties']['company_info'])
    return {**company_info, '$defs': defs}


MODEL_SCHEMA_NAME = 'company_info'


def _source_payload(agent_input: dict[str, Any]) -> dict[str, Any]:
    """검사한 원자료만 모델 호출부로 보낸다. 호출부는 지시문과 분리해 전달한다."""
    return {
        'company_name_hint': agent_input.get('company_name_hint'),
        'source_units': [{key: unit[key] for key in SOURCE_UNIT_KEYS} for unit in agent_input['source_units']],
    }


def _request_checked(request_json: RequestJson, instructions: str, payload: dict[str, Any],
                     schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
    """주입한 호출부의 반환값도 원본처럼 JSON 객체인지 확인한다."""
    result = request_json(instructions, payload, schema, schema_name)
    if not isinstance(result, dict):
        raise AgentError('INVALID_OUTPUT', 'output_not_object', '모델 결과는 JSON 객체여야 합니다.')
    return result


FIELD_KEYS = ('status', 'facts')
MODEL_FACT_KEYS = ('text', 'evidence')
EVIDENCE_KEYS = ('source_id', 'locator', 'quote')
# status별 facts 개수 (최소, 최대). contract.md 5절과 profile.schema.json field 조건 규칙과 같다.
STATUS_FACT_COUNT: dict[str, tuple[int, int | None]] = {
    'not_found': (0, 0),
    'conflict': (2, None),
    'supported': (1, None),
    'needs_confirmation': (1, None),
}
# details에 넣는 quote는 앞부분만 남긴다. 원문 전체를 오류 정보에 싣지 않기 위해서다.
DETAIL_QUOTE_LIMIT = 80


def _invalid_output(rule: str, message: str, **details: Any) -> AgentError:
    return AgentError('INVALID_OUTPUT', rule, message, {'rule': rule, **details})


def _short(value: str) -> str:
    return value if len(value) <= DETAIL_QUOTE_LIMIT else value[:DETAIL_QUOTE_LIMIT] + '…'


def _iter_facts(company_info: dict[str, Any]):
    """14개 키 순서 → facts 순서로 (항목 이름, fact 번호, fact)를 돌려준다."""
    for key in COMPANY_INFO_KEYS:
        for i, fact in enumerate(company_info[key]['facts']):
            yield key, i, fact


def _check_field_shape(key: str, field: Any) -> None:
    if not isinstance(field, dict) or set(field) != set(FIELD_KEYS) or not isinstance(field['facts'], list):
        raise _invalid_output('field_shape', '항목은 status와 facts 배열만 가져야 합니다.', field=key)
    for i, fact in enumerate(field['facts']):
        if (not isinstance(fact, dict) or set(fact) != set(MODEL_FACT_KEYS)
                or not isinstance(fact['text'], str) or not isinstance(fact['evidence'], list)):
            raise _invalid_output('field_shape', 'fact는 text 문자열과 evidence 배열만 가져야 합니다(fact_id 포함 금지).',
                                  field=key, fact_index=i)
        for j, evidence in enumerate(fact['evidence']):
            if (not isinstance(evidence, dict) or set(evidence) != set(EVIDENCE_KEYS)
                    or not all(isinstance(evidence[name], str) for name in EVIDENCE_KEYS)):
                raise _invalid_output('field_shape', 'evidence는 source_id·locator·quote 문자열만 가져야 합니다.',
                                      field=key, fact_index=i, evidence_index=j)


def check_company_info(company_info: Any, agent_input: dict[str, Any]) -> None:
    """GPT가 준 company_info를 정해진 순서로 검사한다. 첫 실패에서 INVALID_OUTPUT으로 멈추고 아무것도 고치지 않는다.

    순서: 1 14개 키·모양 → 2 status별 facts 개수 → 3 빈 값 → 4 근거 위치 존재 → 5 quote 원문 포함.
    agent_input은 check_agent_input을 통과한 것이어야 한다. 문장 의미가 맞는지는 검사하지 않는다(사람 검토).
    """
    # 1) 14개 키가 정확히 있고, 항목·fact·evidence 모양이 맞는가
    if not isinstance(company_info, dict):
        raise _invalid_output('company_info_keys', 'company_info가 객체가 아닙니다.')
    missing = [key for key in COMPANY_INFO_KEYS if key not in company_info]
    unexpected = [key for key in company_info if key not in COMPANY_INFO_KEYS]
    if missing or unexpected:
        raise _invalid_output('company_info_keys', 'company_info 14개 키가 정확히 일치하지 않습니다.',
                              missing_keys=missing, unexpected_keys=unexpected)
    for key in COMPANY_INFO_KEYS:
        _check_field_shape(key, company_info[key])

    # 2) status가 4개 중 하나이고, status별 facts 개수 규칙을 지키는가
    for key in COMPANY_INFO_KEYS:
        status = company_info[key]['status']
        if status not in STATUS_FACT_COUNT:
            raise _invalid_output('status_value', '허용된 4개 status가 아닙니다.', field=key, actual=_short(str(status)))
        low, high = STATUS_FACT_COUNT[status]
        count = len(company_info[key]['facts'])
        if count < low or (high is not None and count > high):
            expected = f'{low}개' if high == low else f'{low}개 이상'
            raise _invalid_output('status_fact_count', f'{status}의 facts 개수 규칙을 어겼습니다.',
                                  field=key, status=status, expected=expected, actual=count)

    # 3) text / source_id / locator / quote가 비어 있지 않고, fact마다 evidence가 1개 이상인가
    for key, i, fact in _iter_facts(company_info):
        if not fact['text'].strip():
            raise _invalid_output('empty_value', 'fact의 text가 비어 있습니다.', field=key, fact_index=i, value_name='text')
        if not fact['evidence']:
            raise _invalid_output('evidence_empty', 'fact에 evidence가 없습니다.', field=key, fact_index=i)
        for j, evidence in enumerate(fact['evidence']):
            for name in EVIDENCE_KEYS:
                if not evidence[name].strip():
                    raise _invalid_output('empty_value', f'evidence의 {name}이(가) 비어 있습니다.',
                                          field=key, fact_index=i, evidence_index=j, value_name=name)

    # 4) evidence의 source_id + locator가 입력 source_units에 실제로 있는가
    source_texts = {(unit['source_id'], unit['locator']): unit['text'] for unit in agent_input['source_units']}
    for key, i, fact in _iter_facts(company_info):
        for j, evidence in enumerate(fact['evidence']):
            if (evidence['source_id'], evidence['locator']) not in source_texts:
                raise _invalid_output('evidence_location_unknown', '입력에 없는 source_id+locator입니다.',
                                      field=key, fact_index=i, evidence_index=j,
                                      source_id=_short(evidence['source_id']), locator=_short(evidence['locator']))

    # 5) quote가 그 위치의 원문 text 안에 그대로 있는가. 정규화 규칙이 아직 없어 공백·기호까지 그대로 비교한다.
    for key, i, fact in _iter_facts(company_info):
        for j, evidence in enumerate(fact['evidence']):
            if evidence['quote'] not in source_texts[(evidence['source_id'], evidence['locator'])]:
                raise _invalid_output('quote_not_in_source', 'quote가 해당 위치의 원문에 없습니다.',
                                      field=key, fact_index=i, evidence_index=j,
                                      source_id=evidence['source_id'], locator=evidence['locator'],
                                      quote=_short(evidence['quote']))


def _deduplicate_supported_facts(company_info: dict[str, Any]) -> dict[str, Any]:
    """검사를 통과한 supported의 같은 항목·text·근거 집합만 한 번 남긴다.

    의미가 같은지 추정하거나 문자열을 정규화하지 않는다. 다른 근거와 충돌·미확인
    후보는 그대로 두며, 첫 사실의 값·근거 순서와 원 응답을 보존한다. ID 부여 전에만 쓴다.
    """
    result = copy.deepcopy(company_info)
    for field in result.values():
        if field['status'] != 'supported':
            continue
        seen = set()
        distinct = []
        for fact in field['facts']:
            evidence = frozenset(tuple(ref[name] for name in EVIDENCE_KEYS) for ref in fact['evidence'])
            key = (fact['text'], evidence)
            if key not in seen:
                seen.add(key)
                distinct.append(fact)
        field['facts'] = distinct
    return result


def assign_fact_ids(company_info: dict[str, Any]) -> dict[str, Any]:
    """check_company_info를 통과한 company_info에 F001부터 fact_id를 붙인 새 객체를 돌려준다.

    번호 순서: 공통 스키마의 14개 키 순서 → 각 항목의 facts 순서. 원래 객체는 바꾸지 않는다.
    """
    numbered: dict[str, Any] = {}
    count = 0
    for key in COMPANY_INFO_KEYS:
        facts = []
        for fact in company_info[key]['facts']:
            count += 1
            facts.append({'fact_id': f'F{count:03d}', 'text': fact['text'], 'evidence': copy.deepcopy(fact['evidence'])})
        numbered[key] = {'status': company_info[key]['status'], 'facts': facts}
    return numbered


def extract_company_info(agent_input: dict[str, Any], *, request_json: RequestJson) -> dict[str, Any]:
    """허용된 source_units에서 company_info 14개 항목을 추출하고 검사한다.

    호출부는 JSON 객체만 반환한다. 원 응답 전체 검사 후 supported 완전중복만 줄이고 fact_id를 붙인다.
    실패한 값을 자동 보정하거나 mock으로 대체하지 않으며, 이 모듈은 재시도하지 않는다.
    """
    check_agent_input(agent_input)
    instructions = load_extract_prompt()
    model_output = _request_checked(request_json, instructions, _source_payload(agent_input),
                                    build_model_output_schema(), MODEL_SCHEMA_NAME)
    check_company_info(model_output, agent_input)
    return assign_fact_ids(_deduplicate_supported_facts(model_output))


# --------------------------------------------------------------------------
# 본문 초안 생성 (draft_profile) — 현재 어댑터가 Page/Block으로 변환한다
# --------------------------------------------------------------------------
# 본문 섹션의 key·제목은 직접 나열하지 않고 공통 Mock에서 가져온다.
# 이전 profile_builder와 같은 출처에서 순서·제목·안내 문구만 읽는다.
# fixture의 가짜 회사 사실을 생성 결과에 복사하지 않는다.
MOCK_PROFILE_PATH = ROOT / 'fixtures' / 'mock_profile.json'
_MOCK_SECTIONS: list[dict[str, Any]] = json.loads(
    MOCK_PROFILE_PATH.read_text(encoding='utf-8'))['draft_sections']
# 본문 섹션은 13개다. company_info의 14개 키 중 company_name만 단독 섹션이 없다.
SECTION_ORDER: tuple[str, ...] = tuple(s['key'] for s in _MOCK_SECTIONS)
SECTION_TITLES: dict[str, str] = {s['key']: s['title'] for s in _MOCK_SECTIONS}

DRAFT_PROMPT_PATH = ROOT / 'prompts' / 'draft.txt'
DRAFT_SCHEMA_NAME = 'draft_sections'
SUPPORTED_FACT_KEYS = ('field', 'fact_id', 'text')
PARAGRAPH_KEYS = ('text', 'fact_ids')
DRAFT_SECTION_KEYS = ('key', 'title', 'paragraphs')
# 누락·상충 항목의 안내 문구는 C(profile_builder)가 붙인다. 본문 생성이 같은 문구를 만들면
# 서버 처리와 중복되므로 거부한다. 문구 원본은 C 모듈이 아니라 공통 Mock에서 읽는다.
PLACEHOLDER_TEXTS: frozenset[str] = frozenset(
    paragraph['text']
    for section in _MOCK_SECTIONS
    for paragraph in section['paragraphs']
    if not paragraph['fact_ids']
)


def load_draft_prompt(*, editorial: bool = False) -> str:
    """prompts/draft.txt를 앞뒤 공백만 정리해 읽는다."""
    if not DRAFT_PROMPT_PATH.is_file():
        raise FileNotFoundError('본문 프롬프트 파일이 없습니다: prompts/draft.txt')
    prompt = DRAFT_PROMPT_PATH.read_text(encoding='utf-8').strip()
    parts = prompt.split('[editorial_v1]', 1)
    prompt = parts[1].strip() if editorial and len(parts) == 2 else parts[0].strip()
    if not prompt:
        raise ValueError('본문 프롬프트 파일이 비어 있습니다: prompts/draft.txt')
    return prompt


def check_supported_facts(supported_facts: Any) -> None:
    """C가 넘긴 supported_facts가 약속된 모양인지 확인한다(LLM 호출 전에 멈춘다).

    모양: [{"field", "fact_id", "text"}] — profile_builder.collect_supported_facts의 반환값.
    """
    if not isinstance(supported_facts, list):
        raise AgentInputError('supported_facts_type', 'supported_facts는 배열이어야 합니다.')
    seen: set[str] = set()
    for i, fact in enumerate(supported_facts):
        where = f'supported_facts[{i}]'
        if not isinstance(fact, dict) or set(fact) != set(SUPPORTED_FACT_KEYS):
            raise AgentInputError('supported_fact_shape',
                                  f'{where}는 field·fact_id·text만 가진 객체여야 합니다.')
        for key in SUPPORTED_FACT_KEYS:
            if not isinstance(fact[key], str) or not fact[key].strip():
                raise AgentInputError('supported_fact_field',
                                      f'{where}.{key}는 비어 있지 않은 문자열이어야 합니다.')
        if fact['field'] not in COMPANY_INFO_KEYS:
            raise AgentInputError('supported_fact_field_name',
                                  f'{where}.field가 company_info 항목이 아닙니다: {fact["field"]}')
        if fact['fact_id'] in seen:
            raise AgentInputError('supported_fact_duplicate',
                                  f'{where}: fact_id가 중복됩니다: {fact["fact_id"]}')
        seen.add(fact['fact_id'])


def draft_section_keys(supported_facts: list[dict[str, Any]]) -> tuple[str, ...]:
    """본문을 만들어야 하는 key를 13개 섹션 순서로 돌려준다.

    company_name은 단독 섹션이 없으므로 제외한다(C의 build_draft_sections 규칙).
    제외해도 그 사실은 다른 섹션의 fact_ids로 참조할 수 있다.
    """
    present = {fact['field'] for fact in supported_facts}
    return tuple(key for key in SECTION_ORDER if key in present)


def build_draft_output_schema(section_keys: tuple[str, ...]) -> dict[str, Any]:
    """공통 스키마의 draft_section/paragraph에서 모델용 출력 스키마를 파생한다.

    바뀌는 점: strict가 받지 않는 키워드 제외, key enum을 이번에 만들 섹션으로 한정,
    strict는 최상위가 객체여야 하므로 draft_sections로 한 번 감싼다.
    개수·길이 하한은 strict에서 빠지므로 응답을 받은 뒤 check_draft_sections가 다시 검사한다.
    """
    paragraph = _keep_model_keywords(_PROFILE_SCHEMA['$defs']['paragraph'])
    section = _keep_model_keywords(_PROFILE_SCHEMA['$defs']['draft_section'])
    section['properties']['key'] = {'type': 'string', 'enum': list(section_keys)}
    section['properties']['paragraphs'] = {'type': 'array', 'items': {'$ref': '#/$defs/paragraph'}}
    return {
        'type': 'object',
        'additionalProperties': False,
        'properties': {'draft_sections': {'type': 'array', 'items': {'$ref': '#/$defs/draft_section'}}},
        'required': ['draft_sections'],
        '$defs': {'draft_section': section, 'paragraph': paragraph},
    }


def _draft_payload(supported_facts: list[dict[str, Any]], section_keys: tuple[str, ...],
                   brief: dict[str, Any] | None) -> dict[str, Any]:
    """확인된 사실과 요청 방향을 구분한다. brief는 회사 사실의 근거가 아니다."""
    data: dict[str, Any] = {
        'supported_facts': [{key: fact[key] for key in SUPPORTED_FACT_KEYS} for fact in supported_facts],
        'sections_to_write': [{'key': key, 'title': SECTION_TITLES[key],
                               'fact_ids': [fact['fact_id'] for fact in supported_facts if fact['field'] == key]}
                              for key in section_keys],
    }
    if brief is not None:
        data['brief'] = copy.deepcopy(brief)
    return data


def _invalid_draft(rule: str, message: str, **details: Any) -> AgentError:
    return AgentError('INVALID_OUTPUT', rule, message, {'rule': rule, **details})


def check_draft_sections(sections: Any, supported_facts: list[dict[str, Any]],
                         section_keys: tuple[str, ...]) -> None:
    """GPT가 준 draft_sections를 정해진 순서로 검사한다. 첫 실패에서 멈추고 아무것도 고치지 않는다.

    순서: 1 배열·모양 → 2 key 집합 일치 → 3 빈 값·안내 문구 중복 → 4 fact_ids → 5 항목·전체 참조 누락.
    문장의 의미가 근거와 맞는지는 검사하지 않는다(사람 검토).
    """
    allowed_ids = {fact['fact_id'] for fact in supported_facts}

    # 1) 배열이고, 각 섹션·문단의 모양이 맞는가
    if not isinstance(sections, list):
        raise _invalid_draft('draft_not_array', 'draft_sections가 배열이 아닙니다.')
    for i, section in enumerate(sections):
        if not isinstance(section, dict) or set(section) != set(DRAFT_SECTION_KEYS):
            raise _invalid_draft('section_shape', '섹션은 key·title·paragraphs만 가져야 합니다.', index=i)
        if not isinstance(section['paragraphs'], list):
            raise _invalid_draft('section_shape', 'paragraphs가 배열이 아닙니다.', index=i)
        for j, paragraph in enumerate(section['paragraphs']):
            if (not isinstance(paragraph, dict) or set(paragraph) != set(PARAGRAPH_KEYS)
                    or not isinstance(paragraph['text'], str)
                    or not isinstance(paragraph['fact_ids'], list)):
                raise _invalid_draft('paragraph_shape',
                                     '문단은 text 문자열과 fact_ids 배열만 가져야 합니다.',
                                     index=i, paragraph_index=j)

    # 2) 만들어야 할 key가 정확히 한 번씩 있는가(누락·중복·초과 금지)
    keys = [section['key'] for section in sections]
    missing = [key for key in section_keys if key not in keys]
    unexpected = [key for key in keys if key not in section_keys]
    duplicated = sorted({key for key in keys if keys.count(key) > 1})
    if missing or unexpected or duplicated:
        raise _invalid_draft('section_keys', '본문 섹션 key가 기대와 다릅니다.',
                             missing_keys=missing, unexpected_keys=unexpected,
                             duplicated_keys=duplicated)

    for section in sections:
        key = section['key']
        # 3) 제목·문단이 비어 있지 않고, 서버가 붙이는 안내 문구를 본문이 만들지 않았는가
        if section['title'] != SECTION_TITLES[key]:
            raise _invalid_draft('section_title', '섹션 제목이 공통 기준과 다릅니다.',
                                 field=key, expected=SECTION_TITLES[key],
                                 actual=_short(str(section['title'])))
        if not section['paragraphs']:
            raise _invalid_draft('section_empty', '섹션에 문단이 없습니다.', field=key)
        for j, paragraph in enumerate(section['paragraphs']):
            text = paragraph['text']
            if not text.strip():
                raise _invalid_draft('empty_value', '문단의 text가 비어 있습니다.',
                                     field=key, paragraph_index=j)
            if text.strip() in PLACEHOLDER_TEXTS:
                raise _invalid_draft('placeholder_text',
                                     '서버가 붙이는 안내 문구를 본문으로 만들 수 없습니다.',
                                     field=key, paragraph_index=j, value=_short(text))

            # 4) fact_ids가 있고, 중복 없이, supported 사실만 참조하는가
            fact_ids = paragraph['fact_ids']
            if not fact_ids:
                raise _invalid_draft('fact_ids_empty', '본문 문단에 근거 fact_ids가 없습니다.',
                                     field=key, paragraph_index=j)
            if len(set(fact_ids)) != len(fact_ids):
                raise _invalid_draft('fact_ids_duplicate', '한 문단 안에서 fact_ids가 중복됩니다.',
                                     field=key, paragraph_index=j)
            unknown = [fid for fid in fact_ids
                       if not isinstance(fid, str) or fid not in allowed_ids]
            if unknown:
                raise _invalid_draft('fact_ids_unknown',
                                     'supported 사실에 없는 fact_id를 참조합니다.',
                                     field=key, paragraph_index=j,
                                     unknown=[_short(str(fid)) for fid in unknown])

        # 회사명이나 다른 항목의 ID만 붙여 해당 항목을 작성한 것으로 처리하지 않는다.
        own_ids = {fact['fact_id'] for fact in supported_facts if fact['field'] == key}
        used_ids = {fid for paragraph in section['paragraphs'] for fid in paragraph['fact_ids']}
        if not own_ids.intersection(used_ids):
            raise _invalid_draft('section_fact_missing', '항목의 사실을 참조한 문단이 없습니다.', field=key)

    # 명시적 제외는 호출 전에 적용된다. 회사명은 제목에 연결하므로 본문 필수 참조에서 제외한다.
    required_ids = {fact['fact_id'] for fact in supported_facts if fact['field'] != 'company_name'}
    used_ids = {fid for section in sections for paragraph in section['paragraphs'] for fid in paragraph['fact_ids']}
    if required_ids - used_ids:
        raise _invalid_draft('fact_ids_missing', '작성에 전달한 본문 사실의 참조가 빠졌습니다.',
                             missing_fact_ids=sorted(required_ids - used_ids))


def draft_profile(supported_facts: list[dict[str, Any]], *, request_json: RequestJson,
                  brief: dict[str, Any] | None = None,
                  section_order: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    """supported 사실만으로 본문 섹션(draft_sections)을 만들어 돌려준다. 파일은 저장하지 않는다.

    반환: [{"key", "title", "paragraphs": [{"text", "fact_ids"}]}] — supported 항목만.
    누락·상충 안내와 현재 Page/Block 조립은 호출하는 어댑터가 맡는다.
    supported 사실이 있어도 본문 섹션 대상이 없으면(company_name만 있을 때) 빈 목록을 돌려준다.
    section_order는 본문 항목의 순서만 바꾼다. 항목 제외는 호출자가 생성용 사실 목록에서 처리한다.
    실패하면 멈춘다. 입력·출력 검사를 통과하지 못한 값은 자동 수정하지 않는다.
    검사 후 같은 항목 안의 text·fact_ids 집합이 같은 문단만 첫 한 건으로 유지한다.
    주입한 호출부의 실패도 그대로 전달하며 이 모듈은 재시도하지 않는다.
    근거 ID 검사를 통과해도 문장의 의미가 맞는지는 확인되지 않는다(사람 검토 필요).
    """
    check_supported_facts(supported_facts)
    if brief is not None and not isinstance(brief, dict):
        raise AgentInputError('brief_type', 'brief는 객체(dict)이거나 없어야 합니다.')
    section_keys = draft_section_keys(supported_facts)
    if section_order is not None:
        if (not isinstance(section_order, tuple) or any(not isinstance(key, str) for key in section_order)
                or len(section_order) != len(section_keys) or set(section_order) != set(section_keys)):
            raise AgentInputError('section_order', '작성 순서는 근거 있는 본문 항목을 정확히 한 번씩 포함해야 합니다.')
        section_keys = section_order
    if not section_keys:
        return []
    instructions = load_draft_prompt()
    model_output = _request_checked(
        request_json,
        instructions,
        _draft_payload(supported_facts, section_keys, brief),
        build_draft_output_schema(section_keys),
        DRAFT_SCHEMA_NAME,
    )
    sections = model_output.get('draft_sections')
    check_draft_sections(sections, supported_facts, section_keys)
    order = {key: i for i, key in enumerate(section_keys)}
    result = []
    for section in sorted(sections, key=lambda s: order[s['key']]):
        seen, paragraphs = set(), []
        for paragraph in section['paragraphs']:
            key = (paragraph['text'], frozenset(paragraph['fact_ids']))
            if key not in seen:
                seen.add(key)
                paragraphs.append({'text': paragraph['text'], 'fact_ids': list(paragraph['fact_ids'])})
        result.append({'key': section['key'], 'title': SECTION_TITLES[section['key']], 'paragraphs': paragraphs})
    return result
