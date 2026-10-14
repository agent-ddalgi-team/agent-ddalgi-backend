# 에이전트(AI) 설계 초안 — 2026-10-14

기준 문서: 「거산케미칼 AI 영업지원 2차 기획서 v0.15」. 이 문서는 구현용 요약이며, 원문(특히 5장·부록 F)과 다르면 **원문이 우선**한다. 모든 기업명·인증·수치는 가상이다.

함께 쓰는 파일
- `agent_call_samples.json` — 백엔드↔에이전트 호출 샘플(추출 1, 평가 1, 오류 1)과 코드표
- `agent_test_cases.json` — 규칙 엔진 테스트 사례 4건 (`input` → `expected`)

완료 기준: 백엔드가 어떤 정보로 에이전트를 호출하는지 알고, PM이 어떤 결과를 정상으로 볼지 판단할 수 있다.

---

## 1. 처리 흐름 (6단계)

| 단계 | 담당 | 하는 일 | 자료가 없거나 실패하면 |
|---|---|---|---|
| ① 입력 받기 | BE | 기회 ID, 프로필, 기준일, 요구 목록, 근거·검수 상태, 자료 허용 여부, 실제/가상 | 필수 입력 누락 → `run_status=오류`, `error.code=INPUT_INVALID`. 순위 제외 |
| ② 근거 후보 추출 (호출 A) | AI · LLM | 허용된 원문에서 제품·소재·성능·날짜·상태 후보, 요구 후보 추출. 출력은 `suggested_*` (후보) | 허용 안 된 자료는 보내지 않고 `not_sent`에 기록, 해당 항목 미확보 |
| ③ 사람 확인 | 팀 · 기업 검수 | 후보를 확인/강한 추정/약한 추정/미확보로 확정. 거산 역량은 검수 완료 값만 | 확인 안 되면 추정 또는 미확보. 거산 역량은 추정으로 채우지 않음 (F.6) |
| ④ 규칙 판정 (호출 B) | AI · 규칙 엔진 | 필터 → 항목 점수 → E·P·R → 11단계 분류 → 프로필 안 정렬. 결정적 | 미확보는 `score=null` 유지. 필수 미확인이면 필터 확인 대기. 점수로 상쇄 안 함 |
| ⑤ 질문·다음 행동 초안 | AI · LLM | 미확보·추정·미확인 요구를 질문으로 (거산 내부 / 고객). 접촉 목적 4종 중 하나 | LLM 실패 → 판정은 반환, `questions.generation=실패` |
| ⑥ 결과 반환 | BE 저장 · FE 표시 | 평가 ID·버전·기준일과 함께 저장. 의견·승인은 이 평가 ID에 연결 | 재평가는 새 평가 ID. 이전 평가·의견·승인 보존, 예전 승인으로 자동 실행 안 함 (4.4.4) |

호출 A와 B를 나누는 이유: 둘 사이에 사람 확인이 있고, A는 비결정적(LLM)·B는 결정적(규칙 엔진)이어야 하기 때문(6.1). **팀 결정 필요.**

## 2. 호출 계약 요약

필드명은 기획서 4.4.5의 테이블 필드를 우선 사용(`opp_id, eval_id, batch_id, profile, base_date, rule_version, rule_hash, code_version, run_status, filter_state, item_code, score, req_id, req_level, scope_kind, link_id`). 없는 것은 제안.

### 호출 A · `POST /agent/extract` (LLM)
- 요청: `opp_id, customer_id, profile, base_date, data_kind, sources[]{evidence_id, source_grade(A~D), source_ref, external_ai_allowed, event_date, text}, targets[]`
- 응답: `extract_id, model, prompt_version, needs_confirmation=true, candidates[]{item_code, fact, evidence_id, relation(직접/간접), suggested_state, suggested_match, event_date}, requirement_candidates[]{text, req_level_hint, scope_hint, process_hint, evidence_id}, question_hints[], not_sent[]`
- `external_ai_allowed`가 허용/공개가 아닌 자료는 보내지 않는다 (6.2).

### 호출 B · `POST /agent/evaluate` (규칙 엔진, 결정적)
- 요청(확정 입력 스냅샷): `opp_id, customer_id, batch_id, profile(B|A), profile_reason, relation_status, base_date, rule_version, capdb_version, data_kind, ops_ready, process_links[]{link_id, process, material}, items[]{item_code, state, match_rate, event_date, evidence_ids[]}, requirements[]{req_id, text, req_level, scope_kind(opportunity|process_link), link_id, stage_required, customer_evidence_state, capability_check{status, match}}, timing_requirements{direct_action_signal, deadline_confirmed, ready_confirmed, deadline}, research_requirements{evidence_grade_ok, question_set, path_and_owner, can_change_class, time_capacity, recheck_date} | null, history[]`
- 응답: `eval_id, run_status, filter_state(통과|미확인|제외), filter_reasons[], requirement_checks[]{req_id, result, result_kind(요구 미확인|충족 미확인|null), reason}, items[]{item_code, weight, axis(F|T), state, match_rate, certainty, decay, score(null 허용)}, metrics{E, F, T, P, S, C, R, direct_confirmed_weight}, flags{core_evidence(충족|재확인), timing_priority, research_ready}, classification{rule, code, label, group}, ranked, sort_keys, blocking[], questions{internal[], customer[], generation}, next_action{text, purpose, owner, deadline}, versions{rule_version, rule_hash, code_version, capdb_version}`
- 오류: `run_status=오류, error{code, field, message}, classification=null, ranked=false`

## 3. 규칙 요약 (구현용 · 부록 F)

### 3.1 항목과 배점
신규 기회 프로필 B (F.1) — 적합 70 + 시점 30

| code | 항목 | 배점 | 축 | 일치도 단계 |
|---|---|---:|---|---|
| B1 | 표면처리가 필요한 제품·부품 생산 근거 | 25 | F | 해당 1.0 |
| B2 | 소재와 보유 공정 일치 | 20 | F | 정확히 일치 1.0 / 일부 가능 0.5 |
| B3 | 유사 산업·부품 납품실적 | 15 | F | 동일 부품 1.0 / 동일 산업 0.67 / 인접 산업 0.33 |
| B4 | 처리 목적·성능 태그 일치 | 10 | F | 2개 이상 1.0 / 1개 0.5 |
| B5 | 공장 증설·신규 생산라인 | 10 | T | 직접 1.0 / 간접 0.5 |
| B6 | 신규 수주·제품 출시·사업 확대 | 8 | T | 직접 1.0 / 간접 0.5 |
| B7 | 입찰·협력사 모집·구매 공고 | 7 | T | 직접 1.0 / 간접 0.5 |
| B8 | 관련 직무 채용 | 5 | T | 직접 1.0 / 간접 0.5 |

재접촉 프로필 A (F.2)

| code | 항목 | 배점 | 축 | 일치도 단계 |
|---|---|---:|---|---|
| A1 | 과거 거래 공정·품목과 현재 역량 일치 | 25 | F | 동일 공정·동일 품목 1.0 / 동일 공정·유사 품목 0.6 / 일부 공정 0.3 |
| A2 | 과거 수주·샘플 승인 이력 | 20 | F | 반복 수주 1.0 / 1회 수주 0.7 / 샘플 승인만 0.4 |
| A3 | 과거 요구조건(품질·납기·인증) 충족 | 15 | F | 모두 1.0 / 일부 0.5 |
| A4 | 실주·거래중단 사유의 해소 | 10 | F | 사유 없음 또는 해소 확인 1.0 / 미해소 0 (사유 불명은 미확보) |
| A5 | 예상 재발주 시점 도래 | 10 | T | 예상 시점과 절대 차이 0~60일 1.0 / 61~120일 0.5 / 121일 이상 0 |
| A6 | 신규 수주·증설·관련 채용 신호 | 10 | T | 직접 1.0 / 간접 0.5 (F.4 감쇠 적용) |
| A7 | 후속 연락 약속·샘플 일정·보류 해제 | 10 | T | 0~30일 이내 예정 또는 1~30일 경과 1.0 / 31~90일 이내 예정 0.5 / 그 외 0. 30일 넘긴 약속·완료·취소는 0 |

### 3.2 항목 점수
```
항목 점수 = 배점 × 일치도 × 확실도 × 감쇠계수
확실도: 확인 1.0 / 강한 추정 0.65 / 약한 추정 0.25 / 불일치·가점 없음·신호 없음 0 / 미확보 → 계산 안 함 (score=null)
감쇠: 적합도(F) 항목 1. 일반 외부 사건은 발생일 기준 0~30일 1 / 31~90일 0.5 / 91일 이상 0 (초기 가설).
      공고·재접촉 일정(A5·A7)의 예외는 F.4·F.2 원문을 따른다. 확인일을 발생일로 바꾸지 않는다.
반올림: 소수 첫째 자리에서 반올림, 0.5는 올림(round half up). 합계는 정수 항목점수의 합.
```

### 3.3 합계 지표 (F.7)
```
F = 적합 항목 점수 합 (최대 70) · T = 시점 항목 점수 합 (최대 30) · E = F + T
P = 판정 완료 항목의 원래 배점 합 (미확보 배점 제외)
S = E / P × 100 (P=0이면 null, 화면 "산정 전")      ← 표시용. 분기·정렬에 쓰지 않음
C = 판정 완료 항목 수 / 전체 항목 수 × 100           ← 추정도 완료에 포함
W = P
R = E + Σ(미확보 항목 배점) + Σ(추정 항목을 확실도 1.0으로 재계산한 점수 − 현재 점수)
    (추정 항목의 일치도·감쇠 유지, 같은 방식으로 반올림한 뒤 차이를 더함)
불변식: 0 ≤ E ≤ P ≤ 100, E ≤ R ≤ 100. 축 전체가 미확보면 그 축은 화면에 "산정 전".
```

### 3.4 필터 (5.2.1 · F.3) — 점수보다 먼저
| 상황 | filter / requirement result |
|---|---|
| 현재 단계 필수 요구 + 충족 미확인 (거산 검수 전) | `미확인` · result_kind `충족 미확인` → 규칙 2 |
| 현재 단계 필수 요구 + 검수된 일치 | `통과` |
| 현재 단계 필수 요구 + 불일치 | `제외` · 해당 범위만 (기업/기회/공정 연결). 같은 기회의 다른 연결은 계속 평가 |
| 요구 자체 미확인 | result_kind `요구 미확인` → 질문. 필터 영향 없음 |
| 우대 / 비요구 | 가점·필터 없음. 일치 범위만 제안 근거로 표시 |
| 필수 요구의 필요 단계 불명확 | 대기 (규칙 2) |
| 관계 미확인 | 필터 확인 대기. 접촉 확정 금지 |
"조건부 통과" 상태는 만들지 않는다. 공정별 요구는 `link_id` 필수.

### 3.5 핵심 근거 (F.8)
- B: 제품·부품 근거(B1)와 소재/공정 연결 근거(B2)가 각각 확인 또는 강한 추정 + 거산 공정·소재 근거 검수 완료
- A: 과거 거래·견적·상담 근거 확인 + 현재 품목·사유 확인 또는 강한 추정 + 거산 역량 검수 완료
- 핵심 묶음에 약한 추정·미확보뿐이면 `핵심 근거 재확인` → 접촉 분기(5~7) 통과 안 함 → R·조사 요건으로 처리

### 3.6 시점 우선 3요건 (F.8) — 셋 다 필요
① 기회와 직접 연결된 행동 신호(모집·구매 요청·합의된 후속 연락) 직접 확인 ② 유효성과 기한·실행기간 확인 ③ 기한 내 대응 가능하다는 담당자 확인. 일반 증설·채용 기사나 높은 T만으로는 불충족.

### 3.7 조사 5요건 (F.10) — 모두 충족해야 규칙 8
관련 A~C등급 근거 1건 이상 / 구체 질문 묶음 / 허용된 실제 확인 경로·담당자 / 분류를 바꿀 수 있는 질문 / 합의한 조사시간 안의 실행 여력. 하나라도 부족하면 규칙 9(조건부 대기)와 재개 조건 기록.

### 3.8 실행 분기 (F.9) — 위에서 처음 해당하는 하나
| 규칙 | 조건 | code | 표시 문구 |
|---:|---|---|---|
| 1 | 적용 범위의 하드필터 제외 | EXCLUDED | 해당 범위 제외 |
| 2 | 적용 범위의 하드필터 미확인 | FILTER_PENDING | 필터 확인 대기 |
| 3 | 운영 전제·핵심 근거 운영 정의 미확정 | OPS_PENDING | 운영 검증 대기 |
| 4 | P = 0 | INITIAL_PENDING | 초기 평가 대기 |
| 5 | E ≥ 75 + 핵심 근거 충족 + 시점 우선 요건 충족 | CONTACT_TIMING | 시점 우선 접촉 후보 |
| 6 | E ≥ 75 + 핵심 근거 충족 | CONTACT_PLANNED | 계획형 접촉 후보 |
| 7 | E ≥ 60 및 P ≥ 70 + 핵심 근거 충족 | CONTACT_REVIEW | 담당자 검토 후 접촉 후보 |
| 8 | P > 0, R ≥ 60 + 조사 요건 모두 충족 | RESEARCH_READY | 추가조사 검토 대기 (F.9) ※ 부록 H는 "추가조사 대기" — 통일 필요 |
| 9 | P > 0, R ≥ 60 + 조사 요건 일부 미충족 | RESEARCH_CONDITIONAL | 조건부 대기 |
| 10 | 40 ≤ R < 60 | WATCH | 관찰 |
| 11 | R < 40 | LOW_PRIORITY | 후순위 |
분기에는 반올림 전 E·P를 쓴다. 75·60·70·40은 검증 전 가설이며 설정 파일에 둔다. `run_status=오류`는 분류 없음(순위 제외). 영문 code는 제안.

### 3.9 정렬 (F.10) — 프로필별 목록 안에서만
- 접촉 목록 순서: 시점 우선 → 계획형 → 검토 후 접촉
- 시점 우선: 유효 접촉 기한 ↑ → E ↓ → F ↓ → 직접 확인 배점 ↓ → P ↓ → opp_id ↑
- 계획형: E ↓ → F ↓ → 직접 확인 배점 ↓ → P ↓ → opp_id ↑
- 검토 후 접촉: E ↓ → F ↓ → 직접 확인 배점 ↓ → P ↓ → 유효 접촉 기한 ↑ → opp_id ↑
- 추가조사 목록 기본 표시: 재확인 예정일 ↑ (미기재 마지막) → opp_id ↑
- 직접 확인 배점 = 상태가 `확인`인 항목의 원래 배점 합. S는 정렬 키가 아니다. 서로 다른 프로필을 한 줄 순위로 섞지 않는다.

## 4. 코드표
`agent_call_samples.json`의 `code_tables` 참조. 항목 상태 7종(미확보만 null), 필터·요구 판정 3종, 요구 수준 4종, 적용 범위 2종, 검수 4종, 관계 3종, 의견 3종, 접촉 목적 4종, 승인 4종, 결과 5종, 실행 상태 3종.

## 5. 테스트 사례 (`agent_test_cases.json`)
| 사례 | 유형 | 기대 결과 | 흔한 오류(must_not) |
|---|---|---|---|
| TC-01 OPP-002 | 정상 · 신규(B) · 기존 고객의 새 프로젝트 | 필터 통과 · 규칙 7 · E 64 P 83 R 81 | 미확보를 0점으로(P 100) / 기존 고객이라 A 적용 / 우대 미확인을 필터 대기로 |
| TC-02 OPP-007 | 정보 부족 · 재접촉(A) · 두 항목 미확보 | 필터 통과 · 규칙 8 · E 56 P 80 R 76 | 미확보를 0점으로(R 56 관찰) / 조사 요건 없이 규칙 8 / 예정일을 발생일로 감쇠 |
| TC-03 OPP-001 | 필수조건 충족 미확인 · 점수는 높음 | 필터 미확인 · 규칙 2 · E 74 P 95 R 82 | 높은 E로 필터 상쇄 / "조건부 통과" / 요구 미확인을 대기 사유로 |
| TC-04 OPP-008 | 경계 · 시점 우선 3요건 충족 | 필터 통과 · 규칙 5 · E 77 P 90 R 87 | 증설·채용 기사만으로 시점 우선 / 기한을 기사 날짜로 / 1회 수주를 1.0으로 |

PM이 "정상"으로 볼 기준(제안): filter_state·classification 일치, E·P·R 일치, 미확보 score null, blocking이 요구 ID를 가리킴, 질문에 추정·미확보 항목 포함. 질문 문장의 표현 차이는 정상 범위.

## 6. 데이터 현황
있음: 공정 8종 명칭(아연도금·아노다이징·무전해니켈·크롬·전해연마·인산염피막·크로메이트·부동태), 판단 규칙(부록 F).
없음·확인 필요: 9번째 공정, 공정별 사양·시험·실적(검수), 인증 원본·범위·상태, 비식별 납품실적, 공개정보 소스 선정(3개 이내), 거래·상담 이력 사용 허용, LLM·임베딩 구성과 비용. 없는 것은 가상값 + `data_kind=virtual`로 진행.

## 7. 팀 결정 필요
1. 근거 상태(확인/강한 추정/약한 추정)를 누가 확정하나 — 호출 A의 `suggested_state`를 사람이 확정(제안) vs 출처 등급·직접/간접으로 자동 매핑
2. 호출 A/B 분리 여부 (제안: 분리)
3. 재접촉으로 등록됐지만 허용 이력이 없는 기회의 분류 — 이 문서는 "핵심 근거 재확인 → R 분기 → 조건부 대기"로 읽음. 원문은 "확인 대기로 남긴다"(5.1)
4. 추출 품질 기준 — 소량 정답셋 제안 (9.2)
5. 규칙 8 표시 문구 통일 (F.9 vs 부록 H), 영문 code 확정

## 8. 다음 작업
1. 규칙 엔진 뼈대 `evaluate(request) -> response` 순수 함수. 완료 기준: TC-01~04 pytest 통과, 같은 입력 두 번 → 같은 응답
2. 배점·기준점·확실도·감쇠를 `rules-v0.15` 설정 파일로 분리, 파일 sha256을 `rule_hash`로 응답에 포함
3. 호출 A 프롬프트 초안 + `not_sent` 동작 확인
