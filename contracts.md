# 공통 데이터·API 계약

기준일: 2026-09-30 · 문서 v1.17 · contract_version: 1.7 · 데이터 schema_version: 1.0

**계약 1.7 — 근거 선별·구성·디자인(2026-09-30):** LlmAgent의 기본 초안 경로는 사진 유무·쪽수와 무관하게 `editorial_v1` 계획을 사용한다. 기존 문서는 선택 필드의 기본값으로 읽는다. DB 테이블 변경 없이 문서 revision JSON에 계획과 디자인을 저장한다. 프론트 사본은 미갱신이며 아래 추가 필드·연산 처리가 필요하다. 이전 1.6 이하 기록은 당시 결과다.

- **Brief 추가:** `audience`(최대 300자, 기본 고객·협력사), `usage_context`(최대 500자, 기본 빈 문자열), `tone`(`plain|formal|concise`), `target_company`(선택, 원문 속 대상 식별 단서), `required_fields`(기존 회사정보 14개 key 중 중복 없는 배열), `brand_color`(선택, `#RRGGBB`). 회사명 단서는 근거를 생성하지 않으며 확인된 회사명과 다르면 생성 거부다. 필수 항목의 근거 부재는 보완 요청과 승인 blocker로 처리한다. 세션 생성/입력 수정의 멱등 해시는 새 필드가 기본값일 때 기존 요청과 동일하게 유지하며 실제 선호가 달라지면 구분한다.
- **Page.design 추가:** nullable `{palette: neutral|ocean|forest|clay, density: comfortable|compact, typography: editorial|restrained, brand_color: null|#RRGGBB}`. 외부 폰트·임의 CSS/HTML은 허용하지 않는다. 브랜드 색상은 선·강조에 사용하고 본문 대비는 고정된 안전한 색상으로 유지한다. 글꼴은 동봉 Pretendard, 밀도는 간격만 바꾸며 글자 축소로 넘침을 숨기지 않는다.
- **레이아웃:** 기존 5종에 `cover_text`, `fact_sheet`, `timeline`, `certification_summary`를 추가한다. 저장된 과거 자유 문자열은 읽되 미등록 값은 text로 렌더링한다. 새 디자인 변경 연산은 허용 목록만 받는다. 단계·연혁은 원문 순서/날짜와 의미 검증이 필요하다. 신규 table/차트 블록은 추가하지 않았다.
- **Document.editorial 추가:** nullable 생성 감사 기록. `prompt_version=editorial_v1`, `input_revision`, `basis_document_revision=1`, `selections[{fact_id, disposition: required|optional|excluded|review, reason}]`, `requested_pages`, `generated_pages`, `page_count_reason`, `supplement_requests`, `unextracted_segment_ids`. 모든 Fact를 한 번 분류한다. optional도 이번 본문에 포함하기로 선택한 사실이다. 수정 후에도 원래 생성 기록을 보존하므로 현재 문서의 검증/승인 상태로 표시하면 안 된다. 현재 반영 위치는 현재 블록의 fact_ids로 찾아야 하며, 자료 변경 후에는 기록의 input_revision과 최신 입력을 구분한다. 미추출 구간 표시는 해당 구간에 Fact 참조가 없다는 진단이지 모든 사실 누락을 판정한 결과가 아니다.
- **주장 단위:** 새 초안의 lead와 각 point는 독립 paragraph 블록이다. 각 블록에 fact_ids와 전체 evidence_refs(자료 버전·구간·원문 위치·발췌)를 연결한다. 목록 항목을 하나의 배열로 합쳐 근거를 잃지 않는다. 기존 다항목 list는 계속 읽고 편집하지만 과거 근거를 항목별로 복원했다고 표시하지 않는다. 사실 없는 단순 제목은 빈 참조를 허용한다.
- **수정 연산 추가:** `{"op":"set_page_design","page_id":"p1","layout_key":"fact_sheet","design":{"palette":"ocean","density":"comfortable","typography":"editorial","brand_color":null}}`. 기존 PATCH와 Proposal의 operations/changes에서 처리한다. `kind=structure` 제안의 instruction은 이번 구현에서 `카드형|텍스트형|여유롭게|촘촘하게`를 지원한다. 문구·근거는 변경하지 않으며 적용 전 문서를 보존한다. 요청 외 페이지는 거부한다. 적용하면 revision을 올려 기존 승인·배치 검사를 무효화한다. 색상/간격 변경은 기존 내용 검증 재사용이 가능하지만 단계/연혁으로의 배치 변경이나 해당 페이지의 블록 순서 변경은 내용 검증도 다시 수행한다.
- **검증·출력:** `template_v4`. 세로/가로 넘침·블록 겹침·논리/물리 쪽수 불일치를 검사하며 실패를 통과로 바꾸지 않는다. 초안·수정 저장 후 원문 의미 검증과 실제 PDF 배치 검사를 거쳐 승인한다. 출력/재다운로드는 저장된 artifact를 사용하며 AI 초안을 재호출하지 않는다. DOCX 승인·출력 제한은 유지한다.
- **프론트 동작:** S01의 독자/용도/문체·필수 항목·회사명 단서·브랜드 색상 입력, S02의 생성 계획/실제 쪽수 이유·보완/제외 사유 패널과 블록별 원문 이동, Page.design/추가 layout 표시, 디자인 제안 전후 비교와 명시적 적용, S03의 배치 재검사·승인 무효화 표시가 필요하다. 내부 검토 기록은 고객용 본문에 삽입하지 않는다. 로고 전용 선택 UI, 표·차트 편집 UI, 자동 넘침 재작성은 이번 계약에서 추가하지 않는다.
- **접근 한계:** 익명 소유자 쿠키·선택 자료 검사는 세션 보호다. 공용 등록 자료에는 tenant/company ACL이 없으므로 비공개 회사 자료를 여러 기업에 공용 등록하는 운영은 지원하지 않는다. 현재 안전한 시연 범위는 공개 허용 공통 자료와 각 소유자의 세션 첨부다. 기업 서비스에는 인증된 사용자/기업·등록 자료 ACL·기업별 저장/검색 범위 설계가 별도로 필요하다.

**계약 1.6 — 자료 변경 후 편집 복귀(C-05, 2026-09-30):** 기존 문서의 재점검 확인·영향 조회·선택 적용 API 3개를 추가한다. 적용 전 편집 내용을 보존하고, 명시적 적용 시 새 문서 버전과 현재 입력/점검을 연결하며 전체 내용 검증을 예약한다. 같은 입력의 과거 버전만 바로 복원할 수 있다. DB v11의 기존 영향/확인 테이블을 사용하며 스키마 변경은 없다. 프론트 계약 사본·타입·자료 변경 배너와 확인/적용 화면은 별도 갱신이 필요하다.

**초안의 사실 참조·구성 보완(2026-09-30, 문서 v1.15):** 내부 작성 입력 `sections_to_write`에 항목별 `fact_ids`를 함께 전달한다. 각 항목은 자기 항목의 사실을 하나 이상 참조해야 하며, 작성에 전달한 회사명 외 모든 supported ID가 본문에서 사용되어야 한다. 명시적 항목 제외는 전달 전에 적용한다. 누락은 기존 `AGENT_OUTPUT_INVALID`로 거부하고 자동 재호출하지 않는다. 이는 ID 누락 검사이며 실제 문장의 의미·조건 보존을 보장하지 않는다. 원 응답 전체 검사 후 같은 항목의 text·fact_ids 집합이 모두 같은 문단만 첫 한 건으로 유지한다. 부족·확인 안내는 기존 제목과 정확한 안내 문구·빈 근거를 유지한 채 본문 뒤 한 묶음으로 구성한다. 공개 필드·상태·API·DB와 계약/데이터 버전은 유지한다. 프론트에서 목표 쪽수와 실제 `pages` 개수를 구분해 표시하는지 확인이 필요하며, 프론트 코드·계약 사본은 이번에 갱신하지 않았다.

**추출 결과의 완전중복 정리(2026-09-30, 문서 v1.14):** 새 분석에서 원 응답의 형식·상태·모든 근거를 검사한 뒤, 같은 항목의 supported 사실 중 text와 `(source_id, locator, quote)` 근거 집합이 모두 같은 중복만 ID 부여 전에 한 건으로 유지한다. 첫 사실의 문자열·근거 순서를 보존하며 서로 다른 근거·조건·시점 표현과 conflict/needs_confirmation 후보는 합치지 않는다. 실패한 값을 자동 보정하거나 상태를 바꾸지 않는다. 기존 점검·문서의 ID와 저장 내용은 변경하지 않는다. 공개 필드·상태·API·DB와 계약/데이터 버전은 유지한다. 프론트 코드 변경은 필요하지 않으며 이 문서 설명의 사본 동기화는 아직 하지 않았다.

**내부 내용 검증 전송 형식(2026-09-29, 문서 v1.13):** `content_review` 원문 구간에 요청 내 `unit_id`를 붙이고, 각 finding의 `evidence`는 해당 번호의 정수 배열로 반환한다. 서버가 선택 원문 전체를 복원하고 기존 출처·원문·검사 범위를 확인한다. 이전 내부 requester의 인용 객체도 기존의 엄격한 원문 검사로 처리하지만 실제 모델 생성 스키마는 번호만 허용한다. 공개 Issue·Validation JSON, DB, 승인 조건과 contract_version은 유지한다. 검증 전송 JSON 한도는 `OPENAI_REVIEW_MAX_INPUT_CHARS`로 별도 설정하며 미지정 시 기존 시험 입력 한도를 따른다. 프론트 코드 변경은 필요하지 않으며 이번 설명 갱신은 프론트 계약 사본에 복사하지 않았다.

개발 전 대조 메모는 7절에서 관리한다. 문서 v1.2에서 경고 승인 정책(PRD BR-08/09, plan D-07)을 구체화했고, v1.3에서 로그인 MVP 제외 결정(plan D-08)을 반영했다. 두 정책은 2026-09-27 사용자 확정 사항이다. 필드·상태값·API 형식과 계약/스키마 버전은 유지한다. 7절은 확정된 제품 정책과 아직 정할 연결 규격을 구분한 목록이며, 코드·예시·프론트 사본의 반영 완료를 뜻하지 않는다.

원본 위치는 백엔드 레포의 contracts.md이며 프론트 레포에는 동일 버전의 사본을 둔다. 원본 변경 주 담당은 백엔드이고 AI 입출력은 Agent, 화면 영향은 프론트 담당과 협의한다. 사본만 독자 수정하지 않는다.

**내부 추출 전송 형식(2026-09-29, 문서 v1.12):** 실제 `company_info` 호출에서 각 원문 구간에 요청 내 `unit_id`를 부여하고 모델의 evidence는 `{"unit_id": 1}`처럼 그 번호만 반환한다. 서버가 해당 구간의 원문 전체를 정확히 quote로 연결하고 기존 source_id·locator·버전을 복원한다. 번호는 이번 요청의 선택 자료 구간만 허용하며 bool/실수/문자열·범위 밖 ID·임의 quote 필드는 거부한다. fact의 text·상태·충돌 후보를 자동 보정하거나 삭제하지 않고 기존 원문·형식 검사와 후속 의미 검증을 유지한다. 이는 인용문 재작성 오류를 없애는 내부 전송 변경이다. 공개 Preflight/Fact/EvidenceRef JSON과 DB 스키마는 그대로이며 프론트는 같은 API를 사용한다. 인용 범위는 모델이 재작성한 짧은 발췌 대신 선택된 원문 구간 전체다.

**계약 1.2(2026-09-28):** 사용자가 요청한 AG-04 연결 후속으로, 같은 입력의 재점검에서 충돌이 발견되면 기존 승인을 무효화하고 승인 요청 재전송도 현재 승인 상태를 확인한다. `invalidated_reason`의 `preflight_conflict`, 기존 `APPROVAL_NOT_ACTIVE`·`REVALIDATION_REQUIRED` 오류가 적용되는 경로를 아래에 반영했다. 모델 필드·상태값·API 경로·문서 schema_version은 그대로이며 승인 재전송의 응답 동작 변경에 맞춰 계약 버전을 올렸다. 기존 [API 예시](handoff/api_examples_v1.1.json)는 파일 경로를 유지하고 내부 contract_version과 관련 예시를 1.2로 갱신한다. 프론트 사본·오류 표시 갱신은 필요하며 아직 확인하지 않았다.

이 문서는 프론트·백엔드·Agent의 데이터 교환 기준이다. 기존 API와 필드가 이미 있다면 백엔드 BE-01에서 연결표를 만들고 호환 어댑터를 우선한다. 아래 이름은 현재 코드에 구현되어 있다는 뜻이 아니다.

**계약 1.3(2026-09-28, 개발 순서 7단계):** 기존 JSON API 25개와 파일 응답 2개의 실제 요청·응답 형식을 정리했다. 버전 숫자·명시적 동의·빈 입력 변경 요청의 검증을 강화하며 잘못된 입력은 `400 INVALID_REQUEST`다. 아래의 Source·Job·후보 필드, 조회 API, 문서 변경 결과를 현행 형식으로 채택하고 OpenAPI·예시를 맞춘다. 정상 응답의 필드명과 저장 문서 `schema_version=1.0`은 유지한다. 현재 프론트의 구형 `/api/profiles` 호출·타입 및 계약 사본은 이번 백엔드 작업에서 갱신하지 않았으므로 연결 전에 갱신해야 한다. C-05 영향 검토, D-07 경고 승인 강제, DOCX 승인·출력 완료를 뜻하지 않는다.

**계약 1.4(2026-09-28, 개발 순서 8단계 S01 보완):** 업로드 재전송·동시 요청과 점검/초안의 진행 중 Job 재사용을 보완한다. 같은 파일이라도 자료 종류/역할이 달라지면 같은 키를 재사용할 수 없다. 기존 키/응답과 API 경로·필드·DB 구조는 유지한다. 아래 재전송 규칙과 handoff 예시를 갱신하며 프론트 사본 반영은 후속이다.

**S01 프론트 연결(2026-09-28, 문서 v1.8):** 위 1.2~1.4 도입 당시의 프론트 미반영 기록 이후, 프론트 첫 화면을 `/api/v1`의 세션·자료 첨부/선택/삭제·점검·확인·초안 조회에 연결했다. 동일한 계약 원본/예시 사본과 S01 TypeScript 타입을 반영하고 임시 v10 DB·mock AI·실제 브라우저로 확인했다. 구형 `/api/profiles` 모듈은 보존하되 첫 화면에서 호출하지 않는다. 편집·승인·출력 화면, 실제 AI 품질·실자료 적재는 후속이다. 서버 필드·경로·동작 규격은 바꾸지 않아 contract_version=1.4를 유지한다.

**S02/S03 프론트 연결(2026-09-28, 문서 v1.9):** 직접 문구/페이지/블록 편집·저장, 기존 자료의 사진 교체, AI 수정안 비교·명시 적용/거절, 내용 검증·PDF 배치 미리보기·최종 승인·출력/다운로드를 기존 API에 연결한다. 요청은 편집 시작 문서 버전을 사용하며 충돌 때 로컬 편집을 유지한다. 미저장 변경·검사 실패·미확인 경고는 화면에서 승인을 막고, 검사/문서가 바뀌거나 새로고침하면 최종 동의를 다시 받는다. 화면은 `Validation.status=passed`이고 현재 PDF 검사와 미해결 문제가 없는 경우만 승인한다. 이는 미구현 D-07 서버 강제를 대체하지 않는다. 실제 LLM 수정안·검증, C-05 자료 변경 복귀, DOCX 승인/출력, 저장본 이력 복원 UI는 후속이다. API/계약 버전은 1.4로 유지한다.

**계약 1.5(2026-09-29, D-07 백엔드):** 개별 경고 확인과 승인 검사를 연결한다. `acknowledged` 요청에는 기존 문서 버전·사유에 `input_revision`, `validation_id`가 필수이며, 완료된 최신 검증과 일치해야 한다. 확인 기록을 `confirmations(kind=warning_ack)`에 저장하고 승인·승인 재전송·출력/다운로드에서 유효성을 검사한다. 미확인 경고는 `422 WARNING_ACKNOWLEDGEMENT_REQUIRED`로 거부한다. 원문·입력·관련 블록·경고 설명/심각도가 바뀌면 재확인하고, 무관한 변경은 재검증 후 원 확인자/시각을 보존한 연결 기록을 남긴다. 프론트 계약 사본·확인 버튼/요청/오류 표시는 아직 갱신하지 않았다. 이전 절의 D-07 미구현 표기는 당시 상태다. 데이터 schema_version은 1.0, DB는 v11을 유지한다.

## 1. 공통 규칙

- JSON 필드는 snake_case, ID는 불투명 문자열, 시간은 UTC ISO 8601을 사용한다.
- 사실값이 없으면 null 또는 확인 필요 상태를 사용한다. 빈 문자열을 확정 사실로 취급하지 않는다.
- 파일, 근거, 페이지, 블록, 문제 ID는 이동·표시 순서 변경 후에도 유지한다.
- 화면 표시명과 내부 상태 코드는 구분한다.
- `input_revision`: 자료 선택·내용·목적·강조·페이지 설정의 버전.
- `document_revision`: 저장 문서의 버전. 초안 최초 저장은 1, 변경할 때마다 증가한다.
- `schema_version`: 데이터 형식 버전으로 시작값은 `1.0`이다.
- 화면에 전달할 파일은 접근 제어된 asset 참조로 제공한다. 내부 파일 경로나 임의 외부 URL을 AI가 만들어 넣지 않는다.

### 요청 형식 검사

- JSON의 `expected_input_revision`, `expected_revision`, `input_revision`, `restore_from_revision`은 1 이상 9,223,372,036,854,775,807 이하의 **정수**다. 문자열 `"1"`, 소수 `1.0`, boolean `true`를 버전으로 자동 변환하지 않는다. 값은 형식에 맞지만 현재 버전과 다르면 기존 `409` 충돌 응답이다. 자료 삭제의 query 버전은 URL 문자열 숫자를 받아 같은 범위를 검사한다.
- `confirmed`와 세션 생성의 `demo`는 실제 JSON boolean만 허용한다. `"true"`, `"yes"`, `1`은 거부한다. `confirmed:false`는 형식에 맞지만 확인 미완료이므로 기존 업무 검사에서 `422`로 거부한다.
- `Brief.purpose`, 수정 요청 `instruction`, 문제 해결 `reason`, 페이지 변경 `title`은 공백만으로 구성할 수 없다. 정상 문장의 앞뒤 공백을 임의로 제거하지 않는다. 목표 쪽수는 실제 정수 1/4/6/8/10이다.
- `PATCH inputs`는 `brief` 또는 `selected_source_ids` 중 하나 이상의 non-null 값을 포함한다. 생략/null은 그 필드 유지, `selected_source_ids:[]`는 선택 전체 해제다. 동일한 값을 명시해서 다시 보내는 것은 허용한다. 선택 자료와 대상 블록 ID 목록에는 공백 ID나 중복 ID를 넣지 않는다. ID 접두사·UUID 형식은 강제하지 않는다.
- 정의되지 않은 JSON 요청 필드는 거부한다. `kind`는 자료 업로드와 등록 목록에서 같은 5종 값을 사용한다. 형식 오류는 DB 변경/Job 접수 전에 거부하고 받은 원문·잘못된 입력값을 오류 본문에 되돌려주지 않는다. 의미·권한·자료 참조·승인 조건은 이후 업무 검사다.

## 2. 주요 객체

### Session — 작업 세션

| 필드 | 타입 | 의미 |
|---|---|---|
| session_id | string | 서버가 발급하는 세션 ID |
| status | active / closed / expired | 사용 상태 |
| input_revision | integer | 최신 작성 입력 버전 |
| created_at / last_activity_at / expires_at | string | 생성·사용·만료 시간 |
| brief | Brief | 목적·분량·강조 설정 |
| selected_source_ids | string[] | 실제 선택한 자료 |
| demo | boolean | 시연 세션 구분. 생성 요청에서 생략하면 false; 서버의 시연 허용 설정도 충족해야 함 |
| document_summary | object 또는 null | 현재 문서가 있으면 demo, document_id, document_revision, status; 없으면 null |

로그인은 현재 MVP에서 제외한다(plan D-08). 로그인 없이도 세션 식별자 자체를 권한으로 믿지 않고, 서버의 소유자/보안 쿠키 등 검증된 접근 컨텍스트와 함께 검사한다. 세션 간 격리는 필수이며 배경 폴링만으로 만료를 무한 연장하지 않는다. 경고 확인 사용자와 승인 주체는 해당 세션에 접근 가능한 작업 사용자를 뜻하며 별도 회원계정을 요구하지 않는다.

### Brief — 작성 요청

| 필드 | 타입 | 의미 |
|---|---|---|
| purpose | string | 사용 목적 |
| emphasis | string[] | 강조하고 싶은 내용 |
| direction | balanced / quality_process / customer_response | A/B/C 작성 방향 |
| target_pages | 1 / 4 / 6 / 8 / 10 | 기본 목표 쪽수 |
| photo_preference | none / balanced / many | 사진 비중; 많아도 자료 없는 사진을 만들지 않음 |
| audience / usage_context | string / string | 독자(기본 처음 회사를 접하는 고객·협력사) / 사용 상황(기본 빈 문자열) |
| tone | plain / formal / concise | 문체; 기본 plain |
| target_company | string 또는 null | 원문에서 대상 회사를 고르는 단서; 사실 근거가 아님 |
| required_fields | string[] | 반드시 포함할 기존 회사정보 key; 근거 부재 시 보완 요청 |
| brand_color | #RRGGBB 또는 null | 제공된 브랜드 강조색 |

### Source — 원자료

| 필드 | 타입 | 의미 |
|---|---|---|
| source_id / source_version | string / integer | 원자료와 버전 |
| scope | registered / session | 등록 자료 / 이번 세션 자료 |
| session_id | string 또는 null | session 자료의 소유 세션 |
| name / mime_type / size_bytes | string / string / integer | 파일 정보 |
| kind | company / interview / certificate / photo / other | 자료 종류 |
| parse_status | queued / reading / complete / partial / failed | 읽기 결과 |
| text_available / image_available | boolean | 텍스트 근거 및 표시 이미지 사용 가능 여부 |
| usable_segment_ids | string[] | 근거로 사용할 수 있는 확인된 구간 |
| asset_ids | string[] | ready 상태의 자료 이미지 ID. 없으면 빈 목록; 이미지 조회 API에 연결 |
| warnings | object[] | 위치·원인·권장 조치 |
| expires_at | string 또는 null | 세션 자료 만료 시간 |
| role / origin_kind | evidence/instruction / real/mock/demo | 근거 자료와 작성 조건 첨부, 실자료와 시연 출처 구분 |
| document_date | string 또는 null | 자료 날짜 메타. 연도만 있는 경우도 있으므로 ISO 날짜로 강제하지 않음 |
| use_as_company_evidence / is_mock | boolean / boolean | 회사 근거 사용 가능 여부와 테스트 자료 구분 |

이미지 표시가 가능해도 `text_available=false`일 수 있다. 지원하는 이미지 파일 자체의 정상 처리는 complete가 가능하지만, 그 안의 글자를 읽었다는 의미는 아니다. 파일 제외는 선택 목록에서 해제하는 것이며 등록 자료 원본 삭제와 다르다.

`warnings[]`의 항목은 `code`, `message`, nullable `locator`(위치 객체), nullable `action`(권장 조치 문자열)이다. 원문/추출문 조회 API와 이미지 조회 API는 다르며 원문/추출문 조회는 아직 구현되지 않았다. 목록 응답은 `{items: Source[]}`, 업로드 접수 응답은 `{job_id, items: Source[]}`다. 업로드만으로 선택 목록에 자동 추가하지 않는다.

### Asset — 화면과 출력에 사용하는 이미지

필수: `asset_id`, `source_id`, `source_version`, `scope`, `session_id`, `origin`, `mime_type`, `width`, `height`, `content_hash`, `status`, `expires_at`.

- origin: source_image / user_upload / generated_illustration. 마지막 종류는 선택 기능이 활성화된 경우만 사용한다.
- status: processing / ready / failed. ready 자산만 문서 image 블록에 넣는다.
- scope와 session_id는 원자료의 보관·접근 범위를 따른다. 원본 위치와 캡션 후보는 별도로 기록할 수 있다.
- 화면은 asset_id를 서버 조회 주소에 연결한다. AI가 URL을 만들거나 다른 세션의 asset_id를 적용하지 못한다.
- 이미지 파일을 원문에서 추출한 경우에도 원자료 버전을 유지한다. 설명용 생성 이미지는 실제 회사의 시설·제품 증거로 분류하지 않는다.

### EvidenceRef — 근거 위치

필수: `source_id`, `source_version`, `segment_id`, `locator`, `excerpt`.

- locator는 PDF 페이지, PPTX 슬라이드, DOCX 문단/표 셀, TXT 줄 범위, 사용자 추가 메모 위치 등을 표현하는 객체다.
- excerpt는 화면에서 비교할 짧은 원문이며 자료 전체를 중복 보관하지 않는다.
- 파일 내용/버전이 바뀌면 기존 위치가 아직 유효한지 재검사한다.
- 업로드 및 사용자 보완 메모의 근거도 session 범위를 따른다.

### Fact — 사실/주장

필수: `fact_id`, `field_key`, `value`, `status`, `evidence_refs`.

| status | 의미 |
|---|---|
| supported | 선택한 원문이 해당 주장을 뒷받침함; 외부 현실의 진위 보증은 아님 |
| needs_confirmation | 근거·조건이 불충분하거나 사람이 확인할 부분이 있음 |
| conflict | 둘 이상의 자료가 서로 다름 |
| missing | 필요한 값이 없음 |

`conditions`에 수치 적용 조건을 담고, `alternatives`에 충돌 값과 각 근거를 담을 수 있다. 기존 14개 등 상세 회사정보 필드는 읽기 전에 축소·삭제하지 않는다. 기존 상태가 다르면 변환표를 만든다.

### Document — 편집 원본

필수: `schema_version`, `document_id`, `session_id`, `document_revision`, `input_revision`, `title`, `target_pages`, `pages`, `status`.

- status: `draft`, `review_required`, `ready_for_approval`, `approved`.
- `target_pages`는 사용자 목표이며 `pages` 개수와 같음을 보장하지 않는다. 새 editorial 초안은 사실과 조건을 담을 수 있는 목표 이하의 분량으로 구성하고 `editorial.page_count_reason`에 이유를 기록한다. 부족·확인 안내는 본문을 채우는 대신 내부 기록에 남긴다. 실제 PDF/DOCX 출력 배치는 별도 검사한다.
- 선택 `editorial`은 위 계약 1.7의 생성 감사 기록이다. 과거 문서는 null이며 편집 후 현재 검증 결과로 해석하지 않는다.
- 서버가 검증 결과와 승인 상태로 status를 계산한다. 클라이언트가 임의로 approved를 설정할 수 없다.
- Page: `page_id`, `title`, `layout_key`, `blocks`, 선택 `design`(기본 null, 위 계약 1.7의 토큰).
- Block 공통: `block_id`, `type`, `content`, `fact_ids`, `evidence_refs`.
- 블록은 배열 순서로 배치하고 ID는 순서와 분리한다.

| Block.type | content |
|---|---|
| heading | text, level |
| paragraph | text |
| list | items: string[] |
| image | asset_id, alt, caption, fit: contain/crop |
| image_placeholder | description |

AI가 임의 URL이나 실제로 없는 asset_id를 생성하면 저장 전에 거부한다. `image_placeholder`는 출력용 실제 이미지가 아니다. 사용자는 승인 전 업로드하거나 해당 자리를 제거해야 한다.

문서 수정 operations는 다음 허용 목록으로 시작한다. 배열 전체를 하나의 트랜잭션으로 적용하고, 한 연산이라도 실패하면 변경하지 않는다.

| op | 필수 값 |
|---|---|
| replace_block_content | block_id, content |
| insert_block | page_id, after_block_id 또는 null, block |
| delete_block | block_id |
| move_block | block_id, target_page_id, after_block_id 또는 null |
| insert_page | after_page_id 또는 null, page |
| rename_page | page_id, title |
| move_page | page_id, after_page_id 또는 null |
| delete_page | page_id |
| set_page_design | page_id, layout_key, design(계약 1.7의 허용 토큰) |

after 값이 null이면 맨 앞이다. 존재하지 않는 대상, 자신 뒤로 이동, 다른 문서의 ID 참조는 거부한다. 사용자 직접 수정에서 근거 연결이 유효한지는 서버가 확인하며, 화면이 전달한 fact_ids만으로 검증 완료 처리하지 않는다.

예시는 형식 설명용 가상 데이터다.

```json
{
  "schema_version": "1.0",
  "document_id": "doc_demo",
  "session_id": "sess_demo",
  "document_revision": 1,
  "input_revision": 3,
  "title": "예시 회사 소개",
  "target_pages": 1,
  "status": "draft",
  "pages": [
    {
      "page_id": "page_01",
      "title": "회사 소개",
      "layout_key": "text_photo",
      "blocks": [
        {
          "block_id": "block_01",
          "type": "paragraph",
          "content": {"text": "예시 회사는 금속 표면처리 공정을 소개합니다."},
          "fact_ids": ["fact_demo_01"],
          "evidence_refs": [
            {
              "source_id": "src_demo",
              "source_version": 1,
              "segment_id": "seg_01",
              "locator": {"line_start": 1, "line_end": 2},
              "excerpt": "예시 회사 / 주요 사업: 금속 표면처리"
            }
          ]
        }
      ]
    }
  ]
}
```

### Preflight — 사전 점검

필수: `preflight_id`, `session_id`, `input_revision`, `usable_source_ids`, `facts`, `issues`, `recommendations`, `can_generate`, `confirmed_at`.

- recommendations는 `{suggested_pages: 1/4/6/8/10, reason: string, needed: string[]}`다. 필요한 사진/인증/보완자료 설명이 없으면 needed는 빈 목록이다. 새 고정 분류 코드는 추가하지 않는다.
- can_generate는 읽기·최소 텍스트 근거 등 생성 조건을 뜻한다. 사용자 확인은 별도로 필요하다.
- 필수 내용 누락과 읽기 실패를 동일하게 처리하지 않는다. 읽을 근거가 있고 필수 내용 일부가 빠진 경우 검토용 초안은 가능하다.

### Issue — 확인할 내용

필수: `issue_id`, `scope`, `code`, `severity`, `status`, `message`, `source_ids`, `fact_ids`, `block_ids`, `resolution`.

- scope: source / content / layout.
- severity: blocker / warning / info.
- status: open / resolved / excluded / acknowledged.
- resolution: 조치 종류, 사용자, 시간, 사유, 관련 근거, 확인 당시 문서/입력 버전. 경고 확인에는 원 `validation_id`, `anchor_fingerprint`, 재검증에 연결한 `validated_document_revision`, `validated_validation_id`를 추가한다. 원 확인자·시각·버전은 재사용 시 덮어쓰지 않는다.
- excluded는 선택 주장이나 자료를 실제 문서/선택에서 제외했을 때만 가능하다. 필수 내용의 결핍에는 사용할 수 없다.
- 사실·수치·필수 내용의 정확성 또는 출력 파일의 정상 이용에 영향을 주는 문제는 blocker다. 사진 부족·표현 반복처럼 정확성과 출력 이용에 영향 없는 문제는 warning으로 분류한다. 사용자 확인을 이유로 blocker를 warning으로 낮추지 않는다.
- acknowledged는 위 기준의 warning을 사용자가 확인했을 때만 사용한다. blocker를 확인 클릭만으로 통과시키지 않는다. 사진 부족을 확인해도 깨진 사진·남은 사진 자리의 blocker는 유지한다.
- 확인 기록은 resolution의 사용자·시간·관련 근거·문서/입력 버전과 문제·관련 내용에 연결한다. 관련 문장·사진·근거가 바뀌면 재확인이 필요하다. 무관한 변경의 확인 기록을 재사용할 때도 새 버전의 검증 결과에 유효성을 연결한다.
- 현재 확인 허용 코드는 서버의 `PLACEHOLDER_TEXT`(선택 항목 안내 문구), Agent의 `REPETITION`/`PHOTO_SHORTAGE`다. 명시적 시연 세션에서 서버가 만든 `DEMO_VALUE` warning에도 개별 확인을 요구한다. 일반 세션의 DEMO_VALUE, MOCK_VALUE, 근거·사실 오류, 미지원 코드와 모든 blocker/배치 문제는 확인으로 넘길 수 없다. 필수 내용·깨진 이미지·남은 이미지 자리는 별도 blocker로 유지된다. 시연 자료의 실자료 사용을 허용하지 않는다.
- 해결 여부는 서버가 조치와 현재 내용의 일치를 확인해 기록한다. AI가 자체 승인하지 않는다.

### ImpactReview — 자료 변경 영향 확인

DB v11에서 제공한다. v9 요청은 `409 IMPACT_HISTORY_UNAVAILABLE`로 거부하며 기존 DB를 자동 변경하지 않는다.

- 생성 요청: `{expected_revision, input_revision, preflight_id, confirmed: true}`. 현재 문서가 이전 입력 또는 이전 점검에 연결되어 있어야 한다. 최신 입력의 가장 최근 점검만 허용하며 같은 입력의 점검 Job이 진행 중이면 기다린다. 확인 시각과 검토 결과를 저장하며 문서는 변경하지 않는다.
- 응답: `review_id`, `document_id`, `document_revision`(검토 기준), `from_input_revision`, `to_input_revision`, `preflight_id`, `status`, `items`, `fact_rebindings`, `created_at`, nullable `completed_at`.
- 상태: `pending`(적용 대기), `applied`(적용 이력), `stale`(자료·문서·점검 또는 선택 자료의 내용/허용 범위가 달라져 재검토 필요). 적용 이력은 후속 편집 후에도 `applied`로 남지만 당시 확인의 현재 유효성은 별도다.
- 항목: `{block_id: string|null, code, message, requires_change}`. 코드는 전체 입력 변경 `INPUT_CHANGED`, 안전한 사실 ID 연결 `FACT_REBOUND`, 사실 변경/모호함 `FACT_REVIEW_REQUIRED`, 근거 제외 `EVIDENCE_REMOVED`, 사진 제외 `PHOTO_REMOVED`다. 목록은 ID·사실·근거·사진 범위 검사이며 문장 의미 검증 결과가 아니다.
- `fact_rebindings`는 이전 ID→현재 ID 대응이다. 이전/신규 모두 supported이고 항목·값·조건·근거 집합의 자료/버전/구간/위치/인용이 같으며 후보가 유일할 때만 제공한다. 같은 ID나 같은 값만으로 연결하지 않는다.
- 적용 요청: `{expected_revision, input_revision, keep_reason, operations?: Operation[], reference_updates?: [{block_id, fact_ids, evidence_refs}]}`. `keep_reason`은 비어 있지 않은 유지 사유다. 두 배열의 기본값은 빈 배열이며 참조 수정 블록 ID·각 fact_id는 중복할 수 없다. 자동 사실 ID 연결 → 선택한 연산 → 명시적 참조 수정 순서로 사본에 적용한다. 일반 `replace_block_content`는 계속 참조를 보존한다.
- 연결되지 않은 이전 사실이 있는 블록은 삭제하거나 `reference_updates`로 명시적으로 검토해야 한다. 남은 참조는 현재 선택 자료 및 최신 점검의 supported 사실만 허용한다. 인용문·구간 소속·위치도 검사한다. 근거 없는 문구를 남기려고 참조를 비워도 내용 검증과 필수 blocker가 유지된다. 유지 사유는 근거/승인 확인을 대신하지 않는다.
- 성공은 `200 DocumentChangeOut`이며 `validation_job_id`가 있다. 문서 버전을 정확히 1회 증가시키고 현재 입력·확인한 점검에 연결한다. 페이지/블록 ID·본문·사진·순서는 명시적으로 수정한 부분 외에는 유지한다. 영향 확인, 새 문서, 확인 이력, 검증 Job은 한 트랜잭션으로 저장한다. 검증 전에는 `review_required`이며 실제 검증 결과는 Job과 문서 조회로 확인한다.
- 같은 Idempotency-Key/본문은 최초 응답을 반환하며 문서·Job을 중복 생성하지 않는다. 먼저 소유자·세션·문서 접근을 검사한다. 다른 키로 이미 적용한 검토를 재적용하거나 변경된 기준으로 적용하면 409다. 입력·문서·최신 점검 및 선택 자료 스냅샷을 적용 직전에 다시 확인한다. 생성 응답 재전송은 당시 결과이므로 현재 상태는 GET으로 조회한다.
- 적용 후 전체 재검증과 형식별 배치·최종 승인이 다시 필요하다. 이전 승인·경고 확인을 복원하지 않으며 미해결 문제는 실제 재검증으로 해결될 때까지 유지한다. 검증 실패 시 기존 validate API로 재시도하며 초안을 다시 생성하지 않는다. 적용 이후의 검증에도 현재 선택 범위·최신 supported 사실 검사를 유지한다.
- C-05 검토 시작 이후에는 같은 입력을 다시 점검해도 기존 승인과 영향 확인의 유효성을 해제한다. 첫 검토 생성 때도 기존 승인을 무효화한다. 검증 기록은 `checks[].check_key=preflight:<id>`로 사용한 점검에 연결하며 다른 점검의 결과는 현재 검증이나 부분 재사용 기준으로 반환하지 않는다. 최신 점검을 위 API에서 확인·적용한 뒤 전체 검증한다. 확인만 하고 적용하지 않은 문서는 `409 IMPACT_REVIEW_REQUIRED`로 승인을 차단한다. 충돌 없는 재점검의 승인 무효화 사유는 `preflight_changed`다.

### Proposal — AI 편집안

필수: `proposal_id`, `document_id`, `base_document_revision`, `base_input_revision`, `target_block_ids`, `kind`, `changes`, `status`.

- kind: text / structure / image.
- status: proposed / applied / rejected / stale.
- changes는 허용된 블록 수정·삽입·삭제·이동 연산과 근거를 담는다. 이미지 후보 선택 전에는 문서를 바꾸지 않는다.
- 기준 문서 또는 입력 버전이 달라지면 stale로 바꾸고 적용을 거부한다.
- 적용은 원자적으로 한 번만 실행하며 새 문서 버전을 만든다.

현행 응답에는 `instruction`, `rationale`, nullable `candidates`, nullable `applied_revision`, `created_at`, `updated_at`도 포함한다. 후보 항목은 `{candidate_id, label, changes: Operation[]}`다. `candidates=null` 또는 빈 목록은 선택 가능한 후보 없음이며 UI가 가짜 후보를 만들지 않는다. 이미지 후보가 있으면 적용 요청의 `selected_candidate_id`로 선택한다. 후보 개수는 3개로 고정하지 않는다. `applied_revision`은 적용 전 null이며 적용되면 저장된 새 문서 버전이다.

### Validation / LayoutCheck / Approval / Export

| 객체 | 필수 내용 |
|---|---|
| Validation | validation_id, document_id, document_revision, input_revision, status: pending/passed/needs_review/failed, issue_ids |
| LayoutCheck | layout_check_id, document_revision, input_revision, format, template_version, render_options_hash, asset_manifest_hash, status, actual_pages 또는 null, issue_ids |
| Approval | approval_id, document_id, document_revision, input_revision, format, validation_id, layout_check_id, template_version, render_options_hash, asset_manifest_hash, approved_at, approved_by, status: active/invalidated |
| Export | export_id, approval_id, format, status: queued/generating/ready/failed, artifact_id 또는 null, expires_at, error 또는 null |

**사진 후보·실제 이미지 검증 연결(2026-09-29, 문서 v1.11):** 기존 `kind=image`/`Candidate.changes`/`selected_candidate_id` 계약을 사용한다. 단일 블록 후보는 선택 자료 사진의 목록이며 AI 적합성 순위가 아니다. 텍스트 뒤에 사진을 삽입하거나 image/image_placeholder를 같은 위치에서 교체한다. 새 사진은 새 블록 ID·기본 설명으로 시작하고 이전 사실·근거를 이어받지 않는다. 적용 전에는 문서가 바뀌지 않는다.

LLM 내용 검증은 선택 범위·접근 권한·해시를 확인한 실제 PNG/JPEG 바이트와 caption/alt를 전달한다. 최대 20장/사진당 5MiB·1,600만 화소/합계 20MiB다. 미선택·누락·변조·손상·한도 초과는 호출 전에 실패하며 일부 사진을 빼고 통과시키지 않는다. 사진 블록은 매 검증에서 다시 확인한다. 모델의 모든 대상 블록/이미지 검사 ID를 확인하고 검증 중 사진이 바뀌면 결과를 저장하지 않는다. `IMAGE_MISMATCH`(설명 불일치), `IMAGE_UNVERIFIABLE`(시각 확인 불가)는 content blocker이며 확인 클릭으로 해소할 수 없다. 등록 사진 공개 허가·최종 승인 기준은 유지한다.

HTTP 필드·상태·DB 스키마는 그대로여서 contract_version 1.5/schema_version 1.0을 유지한다. 내부 ValidateRequest.images와 이미지 요청 함수의 images 키워드만 확장했다. 바이트를 DB/로그/브라우저 저장소에 추가 보관하지 않는다. 프론트 Candidate.changes 타입·선택 적용·사진 설명 편집을 맞췄다. 현재 C 경로 프론트에는 contracts.md 사본이 없으며 사본 동기화 완료라고 간주하지 않는다.

부분 재검증을 하더라도 Validation의 최종 상태는 현재 문서 전체의 미해결 문제를 합산한다. 변경되지 않은 영역의 blocker가 사라져서는 안 된다. 새 문서 버전의 검사 결과에 재사용한 검사와 새로 수행한 검사를 연결한다.

검증이 끝나고 허용된 warning만 남으면, 유효한 사용자 확인 기록과 나머지 승인 조건을 충족한 경우 승인할 수 있다. `needs_review`라는 상태 이름만으로 승인 가능 여부를 결정하지 않는다. 검사 중·실패·오래된 결과는 승인할 수 없으며, 상태 조합과 확인 요청의 상세 연결은 C-08에서 정한다.

자산 목록과 원문 참조는 승인 당시 버전을 고정한다. 같은 이름의 파일로 교체해도 승인 이미지가 조용히 바뀌어서는 안 된다. 세션 종료 후 임시 자산이 삭제되면 해당 출력물의 서버 재다운로드도 만료된다.

## 3. 상태 변경과 승인 규칙

| 사용자 행동 | 변경 | 재확인 범위 |
|---|---|---|
| 선택 자료 추가/정정/제외 또는 목적 변경 | input_revision 증가, 사전 확인 해제, 관련 제안과 승인 무효화 | 자료 점검과 영향받는 내용 |
| AI 편집 요청 | Proposal만 생성 | 아직 문서·승인 변화 없음 |
| AI 수정안 적용 / 본문 직접 수정 | document_revision 증가, 승인 무효화 | 변경된 주장과 관련 근거, 배치 |
| 블록/페이지 순서만 변경 | document_revision 증가, 승인 무효화 | 필수 구조·배치; 동일 문장의 기존 사실 확인 재사용 가능 |
| 사진 교체/제외 | document_revision 증가, 승인 무효화 | 사진·캡션 일치, 배치 |
| 되돌리기 | 이전 내용을 새 document_revision으로 저장 | 현재 자료와의 정합성 확인; 과거 승인을 부활시키지 않음 |
| 출력 형식 변경 | 문서 버전 유지, 해당 형식 배치 확인과 승인 필요 | 내용이 같으면 사실 질문 재실행 불필요 |

새 자료를 추가해도 사용자의 기존 편집 내용을 전체 재생성으로 덮어쓰지 않는다. 현재 문서에 대한 영향 검사를 하고, 수정 제안 또는 현 내용 유지의 근거를 확인한 뒤 최신 입력 버전에 연결한다.

승인 API는 다음을 모두 검사한다.

1. 세션이 유효하고 요청자가 해당 문서에 접근 가능하다.
2. 저장되지 않은 변경이 없도록 요청 버전과 서버 최신 버전이 일치한다.
3. 문서의 input_revision이 최신이고 사전 확인 및 영향 검사가 완료되었다.
4. 해당 문서/입력 버전의 Validation이 완료되어 있고 미해결 blocker가 없다. 남은 warning에는 현재 관련 내용에 유효한 사용자 확인 기록이 있으며, 확인을 이유로 blocker를 낮추지 않았다.
5. 회사명·주요 사업/공정 등 필수 내용이 실제 블록에 있다.
6. 해당 형식의 배치 검사에서 넘침·깨진 이미지·남은 사진 자리가 해결되었다.
7. 사용자가 해당 내용을 최종 승인했다.

내용 재검증에서 미해결 blocker/경고가 생기면 기존 승인을 `validation_changed`로 무효화한다. 경고 확인은 최종 승인을 대신하지 않는다. 확인 후 사용자가 새로 승인해야 한다. 같은 입력에서 경고 내용·근거가 달라졌거나 확인 기록이 없으면 캐시된 승인과 기존 다운로드도 거부한다.

출력 API도 Approval이 active이며 최신 문서/입력 버전과 일치하는지 확인한다. 승인 후 문서가 바뀌면 옛 파일을 최신 승인본처럼 내려주지 않는다. 변경 전에 내려받은 파일은 사용자의 로컬 파일로 남는다.

같은 `input_revision`의 재점검에서 미해결 `VALUE_CONFLICT` 또는 `Fact.status=conflict`가 발견되면, 결과 저장과 같은 트랜잭션에서 현재 입력에 연결된 문서의 충돌 Issue를 추가·재개하고 기존 승인을 `invalidated`로 바꾼다. 사유는 `preflight_conflict`다. 최신 Validation이 있으면 미해결 문제를 다시 합산하여 `failed`로 표시하고 문서·세션 요약은 `review_required`, 현재 승인 조회는 `null`을 반환한다. 새 의미 검증을 실행한 것으로 기록하지 않으며 다른 미해결 문제·문서 본문·문서 버전은 보존한다. 점검 결과 저장에 실패하면 무효화도 함께 롤백한다.

충돌 없는 재점검은 기존 충돌 Issue를 자동 해결하거나 취소된 승인을 복구하지 않는다. 사전 충돌의 해결·제외 요청은 원인이 남으면 `ISSUE_STILL_PRESENT`, 원인이 사라져도 별도 문서 검증이 필요하면 `REVALIDATION_REQUIRED`로 거부한다. 문서 재검증에서 문제가 해결된 뒤 사용자가 새 승인 요청을 해야 한다. 검증 중 같은 입력의 새 점검이 저장되면 이전 점검에 기반한 늦은 결과를 폐기하며 Job은 재시도 가능한 `SERVICE_TEMPORARY_FAILURE`와 재검증 안내를 반환한다.

무효 승인으로 출력 생성·재전송·다운로드를 요청하면 기존 승인 가드가 `409 APPROVAL_NOT_ACTIVE`로 거부한다. 대기 중인 출력과 재시작 복구도 발행 직전에 같은 검사를 수행한다. 이미 실패로 확정된 출력의 다운로드는 `EXPORT_NOT_READY`와 저장된 실패 사유를 반환할 수 있다. 이미 내려받은 파일이나 승인 검사 후 시작된 전송을 회수하는 기능은 제공하지 않는다.

## 4. API 초안

기본 경로는 `/api/v1`이다. 모든 세션 자원은 소유·만료 검사를 거친다. 본문 예시는 필수 핵심 필드만 표시하며 백엔드 BE-01에서 서버 모델과 OpenAPI로 구체화한다.

| Method / 경로 | 요청 핵심 | 결과 |
|---|---|---|
| POST /sessions | `{brief: Brief, demo?: boolean}` | 201 Session, 소유자 쿠키 |
| GET /sessions/{sid} | 없음 | 상태·최신 입력·문서 요약 |
| DELETE /sessions/{sid} | 없음 | 접근 즉시 차단, 멱등 정리 시작; 삭제 완료 여부 |
| GET /sources | `kind?`, `include_demo?` query; 텍스트 검색 파라미터 없음 | `{items: Source[]}`; 소유자 쿠키 필요, 시연 포함은 서버 설정 조건 |
| POST /sessions/{sid}/sources | multipart `files`, `kind?`, `role?` | 202 `{job_id, items: Source[]}`; 비동기 읽기 상태 |
| GET /sessions/{sid}/sources | 없음 | 세션 첨부·읽기 상태 |
| DELETE /sessions/{sid}/sources/{source_id} | query `expected_input_revision` | `{source_id, deleted, input_revision}`; 세션 첨부만 삭제 |
| PATCH /sessions/{sid}/inputs | expected_input_revision, brief?/selected_source_ids? 중 하나 이상 | `{session_id, input_revision, selected_source_ids, preflight_invalidated}` |
| POST /sessions/{sid}/preflights | expected_input_revision | Job; 완료 시 Preflight |
| GET /sessions/{sid}/preflights/{pid} | 없음 | 저장된 Preflight; 현재 입력 버전인지 함께 확인 |
| POST /sessions/{sid}/drafts | preflight_id, input_revision, confirmed: true | 최신 사전 확인 기록, 초안 생성 Job |
| GET /sessions/{sid}/documents/{did} | 없음 | Document, 검사/승인 상태 |
| PATCH /sessions/{sid}/documents/{did} | expected_revision, operations | DocumentChangeOut; 전체 문서는 별도 GET |
| POST /sessions/{sid}/documents/{did}/impact-reviews | expected_revision, input_revision, preflight_id, confirmed: true | 201 ImpactReview; 문서 불변 |
| GET /sessions/{sid}/documents/{did}/impact-reviews/{rid} | 없음 | ImpactReview와 현재 적용 가능 상태 |
| POST /sessions/{sid}/documents/{did}/impact-reviews/{rid}/apply | expected_revision, input_revision, keep_reason, operations?, reference_updates? | DocumentChangeOut; 새 버전·전체 검증 Job |
| POST /sessions/{sid}/documents/{did}/proposals | expected_revision, input_revision, target_block_ids, instruction, kind | Proposal 생성 Job |
| GET /sessions/{sid}/proposals/{pid} | 없음 | Proposal·후보·현재 상태 |
| POST /sessions/{sid}/proposals/{pid}/apply | expected_revision, selected_candidate_id 필요 시 | DocumentChangeOut; 전체 문서는 별도 GET |
| POST /sessions/{sid}/proposals/{pid}/reject | 없음 | rejected; 문서 변화 없음 |
| POST /sessions/{sid}/documents/{did}/restore | expected_revision, restore_from_revision | DocumentChangeOut; 현재 입력과 같은 입력의 이전 내용을 새 버전으로 저장 |
| GET /sessions/{sid}/documents/{did}/issues | 없음 | `{document_id, document_revision, validation_id, issues}` |
| POST /sessions/{sid}/issues/{iid}/resolve | expected_revision, resolution, evidence_refs?, input_revision?, validation_id? (acknowledged는 뒤 두 필드 필수) | `{issue, validation, document_status}` |
| POST /sessions/{sid}/documents/{did}/validate | expected_revision, input_revision | Validation Job |
| POST /sessions/{sid}/documents/{did}/layout-checks | expected_revision, format | LayoutCheck Job 및 미리보기 |
| POST /sessions/{sid}/documents/{did}/approvals | expected_revision, input_revision, format, validation_id, layout_check_id, confirmed: true | Approval |
| POST /sessions/{sid}/exports | approval_id, format | `{export, job_id}`; 신규/진행 중 202, ready 재사용 200 |
| GET /sessions/{sid}/jobs/{jid} | 없음 | Job 상태·진행·결과 참조 |
| GET /sessions/{sid}/assets/{asset_id} | 없음 | 접근 확인 후 JPEG/PNG 자료 이미지 또는 출력 미리보기 바이트 |
| GET /sessions/{sid}/exports/{eid}/download | 없음 | 승인·만료 재확인 후 실제 파일 |

source 업로드만으로 자동 선택하지 않는 UI를 택하면 사용자가 선택할 때 input_revision을 갱신한다. 단, 선택된 기존 원자료를 수정·삭제하거나 문서가 참조하는 자산을 바꾸면 즉시 영향 상태를 갱신한다.

`DocumentChangeOut`은 `{document_id, document_revision, input_revision, status, validation_job_id}`이며 전체 문서 본문이 아니다. `validation_job_id`는 C-05 영향 적용에서 예약한 전체 검증 Job ID다. 일반 직접 수정/Proposal 적용/복원에서는 null이며 필요하면 validate API를 호출한다. 문서 GET은 `{demo, document, validation, approval, layout_checks}`이며 `layout_checks`는 `{pdf: LayoutCheck|null, docx: LayoutCheck|null}`다. `document_summary.status`, 문서 변경의 `status`, `document_status`는 Document의 4종 상태를 공유한다. 다운로드 성공은 PDF 또는 DOCX 바이트이며 JSON 오류는 아래 공통 봉투다.

### Job — 진행 상태와 결과 조회

Job 조회는 `job_id`, `kind`, `status`, `progress`, `result_ref`, `error`, `created_at`, `updated_at`을 반환한다. `progress={stage: string, message: string|null}`이며 백분율은 없다. 접수 응답 `JobAccepted`는 `{job_id, status: queued/running, kind, session_id, created_at}`다. Source 업로드와 Export는 위 표의 별도 접수 형식을 사용한다.

| kind | 성공한 Job의 result_ref | 이후 조회 |
|---|---|---|
| read | `{type:"sources", source_ids:string[]}` | 세션 Source 목록 |
| preflight | `{type:"preflight", preflight_id}` | Preflight GET |
| draft | `{type:"document", document_id, document_revision}` | Document GET |
| propose | `{type:"proposal", proposal_id, status:proposed/stale}` | Proposal GET; stale은 적용 불가 |
| validate | `{type:"validation", validation_id, status:passed/needs_review/failed}` | Document·Issue 목록 GET |
| layout_check | `{layout_check_id, format, status:passed/failed, layout_ok, publication_policy_ok, actual_pages, artifact_id, preview_asset_ids, preview_basis:"pdf", warnings, demo?}` | Document GET의 형식별 검사, 이미지 GET |
| export | `{export_id, artifact_id, format}` | Export 다운로드 |

진행·실패 시 `result_ref`는 null일 수 있다. layout_check/export에는 기존에 `type` 필드가 없으므로 새로 추가하지 않는다. `actual_pages`는 DOCX에서 null일 수 있다. Job의 `succeeded`는 작업 실행이 끝났다는 뜻이며 검증/배치 결과의 `status=failed`도 가능하다. `error`는 없으면 null, 있으면 `{code, message, retryable, details, request_id}`이며 비동기 Job의 `request_id`는 null일 수 있다.

문서 v1.4(2026-09-28)는 최초 초안의 LangGraph 내부 연결·정리 결과를 C-06/E10에 반영한다. API 형식과 계약/스키마 버전은 유지한다. 초안 확인을 소비한 뒤 생성·검사·저장에 실패하면 `retryable=false`와 재점검 안내를 반환하므로 프론트는 기존 오류 필드를 표시해야 한다. 프론트 사본 갱신은 확인하지 않았다.

### 비동기·중복 요청

- 오래 걸리는 읽기/AI/배치/출력 작업은 202와 job_id를 반환한다. Job.status: queued / running / waiting_user / succeeded / failed / cancelled.
- 프론트는 진행 상태를 조회한다. 초기 구현은 폴링 가능; SSE는 필수 요구가 아니다.
- 기다리는 사용자 입력을 처리하는 API는 서버가 검증한 행동만 내부 LangGraph 재개 값으로 변환한다. 임의 노드명·thread_id 실행 API를 공개하지 않는다.
- 상태를 바꾸는 POST/PATCH는 `Idempotency-Key`를 받아 요청자·세션·경로와 함께 중복을 식별한다. 같은 키에 다른 본문은 409다.
- 업로드의 같은 요청은 파일 순서·표시 파일명·내용과 서버가 결정한 kind/role/MIME이 같은 경우다. kind 생략 시 사진은 photo, 나머지는 other이며 role 생략은 evidence다. 생략값과 같은 유효값을 명시한 요청은 동일하게 취급한다. 기존 저장 해시와 응답의 메타정보를 함께 검사하므로 과거 업로드 키도 재사용할 수 있다.
- 업로드 재전송은 이미 채워진 세션 파일 개수에 다시 더하지 않고 최초 202를 반환한다. 새로운 업로드는 저장 직전에 현재 개수를 검사하며, 동시에 들어온 동일 키 요청도 파일·읽기 Job을 한 번만 만든다. 파일/DB 저장 실패 시 이번 요청의 행과 파일을 정리하고 기존 파일·선택 상태를 보존한다. 프로세스 강제 종료나 파일 삭제 권한 오류까지 원자성을 보장하지 않으며 남은 파일은 세션 정리 대상이다.
- 사전 점검/초안의 같은 입력 작업이 진행 중이면 새 키도 기존 Job을 가리키는 202로 접수·기록한다. 그 키의 재전송은 작업이 완료/실패한 뒤에도 최초 202를 반환한다. 최신 결과는 해당 Job GET으로 확인하며 재전송 자체가 작업을 재실행하지 않는다. 종료/만료·접근 제한은 기존 정책대로 우선 검사한다.
- 실패한 작업을 새로 실행할 때는 새 키로 요청한다. 실제 AI가 이미 소비한 초안 확인은 재사용하지 않으며, 새 사전 점검 결과를 확인한 뒤 생성한다. 문서가 이미 있으면 `DOCUMENT_EXISTS`로 편집 화면을 안내한다.
- 같은 적용 요청의 재전송은 최초 결과를 돌려주고 문서 버전을 다시 늘리지 않는다.
- 승인 요청은 같은 키·같은 본문이어도 저장된 승인이 현재 `active`일 때만 최초 성공 응답을 반환한다. 무효화·소실된 승인은 `409 APPROVAL_NOT_ACTIVE`이며 같은 키로 새 승인을 만들지 않는다. 같은 키·다른 본문은 기존 `409 IDEMPOTENCY_KEY_CONFLICT`를 유지한다.
- Export 재사용 키는 승인 ID, 형식, 템플릿 버전, 렌더 설정, 자산 목록을 포함한다. 만료된 결과는 반환하지 않는다.

### 오류 응답

```json
{
  "error": {
    "code": "DOCUMENT_REVISION_CONFLICT",
    "message": "문서가 변경되었습니다. 최신 문서에서 다시 요청해 주세요.",
    "retryable": false,
    "details": {"expected_revision": 2, "current_revision": 3},
    "request_id": "req_demo"
  }
}
```

| HTTP | 대표 코드 | 처리 |
|---|---|---|
| 400 | INVALID_REQUEST | 본문/쿼리 형식 수정. details.fields에 필드 위치, 전체 본문 조건이면 body; 입력값 자체는 미반환 |
| 401/403 | UNAUTHORIZED / FORBIDDEN | 접근 검증; 다른 세션 존재 여부를 불필요하게 노출하지 않음 |
| 404 | RESOURCE_NOT_FOUND | 접근 가능한 범위에서 자원 없음 |
| 405 | METHOD_NOT_ALLOWED | 요청 메서드 확인. 허용 메서드는 Allow 헤더에 유지 |
| 409 | DOCUMENT_REVISION_CONFLICT / INPUT_REVISION_CONFLICT / PROPOSAL_STALE | 최신 상태 조회 후 재요청 |
| 409 | PREFLIGHT_STALE / IMPACT_REVIEW_STALE | 최신 점검 완료 후 결과 확인·영향 검토부터 다시 진행 |
| 409 | IMPACT_REVIEW_REQUIRED | 최신 점검의 영향 검토를 명시적으로 적용한 뒤 다시 검증·승인 |
| 409 | IMPACT_HISTORY_UNAVAILABLE | DB v11의 이력 저장이 필요; 기존 DB 이전 절차 확인 |
| 410 | SESSION_EXPIRED / ARTIFACT_EXPIRED | 만료 안내; 기존 요청을 자동 복원하지 않음 |
| 413/415 | FILE_TOO_LARGE / UNSUPPORTED_FILE_TYPE | 업로드 전후 제한 안내 |
| 422 | NO_USABLE_TEXT / PREFLIGHT_NOT_CONFIRMED / UNRESOLVED_REQUIRED / LAYOUT_NOT_READY | 보완할 위치와 행동 표시 |
| 422 | IMPACT_REFERENCE_INVALID | 선택한 자료·최신 사실에 맞게 참조/사진 수정 또는 블록 삭제; 유지 사유만으로 통과 불가 |
| 429/503 | AI_RATE_LIMIT / SERVICE_TEMPORARY_FAILURE | 제한된 재시도·대기 안내 |
| 500 | EXPORT_FAILED / INTERNAL_ERROR | 기존 문서 보존; 안전한 오류 메시지 |

업로드 후 파서 실패는 Source.parse_status=failed와 원인으로도 표현한다. 모든 파일 실패를 HTTP 500으로 묶지 않는다.

HTTP 오류의 `request_id`는 필수 문자열이고 `X-Request-Id` 헤더와 연결한다. 알 수 없는 API 경로·지원하지 않는 메서드·multipart 형식 오류도 공통 봉투로 반환하며 내부 예외 내용은 숨긴다. OpenAPI에는 `400` 형식 오류와 `422` 업무 조건 오류를 구분해서 표시한다. 기본 FastAPI의 `422 {detail:[...]}`를 사용하는 계약이 아니다. 공통 응답 표는 가능한 오류 형식이며 모든 경로가 모든 상태 코드를 발생시킨다는 의미는 아니다.

## 5. 세션 전용 데이터 수명

- 등록 자료 원본은 세션 종료로 삭제하지 않는다.
- 세션 업로드, 추출 텍스트, 미리보기 이미지, 임시 임베딩, 사용자 보완 메모, 파생 사실, 초안, 수정안, 검증문, 임시 출력 파일은 세션에 종속한다.
- PRD BR-03에 따라 작업 문서·체크포인트는 등록 자료만 사용해도 해당 세션 수명을 따라 정리한다. 장기 보관·다음 날 재편집의 추가 지원은 별도 제품 결정이다.
- 종료/만료는 즉시 접근을 막고 임시 바이트를 삭제한다. 삭제 작업 실패는 재시도 목록으로 관리하며 종료 성공과 바이트 삭제 완료를 구분한다.
- 브라우저 닫힘 이벤트만 믿지 않는다. 서버 만료 검사와 정리 작업이 필요하다.
- 로그는 ID·오류 코드·시간·토큰 수 중심이다. 원문, 전체 프롬프트, 문서 본문을 영구 로그/외부 추적에 자동 저장하지 않는다.
- LangGraph 체크포인트나 모델 제공자의 보관 정책이 이 원칙을 자동으로 충족한다고 가정하지 않는다. 외부 제공자 설정·별도 보관 범위를 확인하고 설명한다.
- `thread_id`를 지우는 것만으로 모든 체크포인트/첨부가 삭제되었다고 판단하지 않는다. 실제 저장소별 정리 결과를 확인한다.

만료 시간과 파일 제한의 최종 값은 백엔드 레포 plan.md의 D-01/D-02를 따른다. 프론트는 서버가 반환하는 제한·만료 상태를 표시한다.

## 6. 두 레포의 계약 동기화

원본과 사본은 동일한 문서 버전과 내용으로 유지한다. API 변경 시 원본·실제 서버 모델·예시 응답을 먼저 맞추고, 프론트 사본·타입·호출부를 갱신한다. 이 문서의 contract_version과 문서 JSON의 schema_version은 서로 다른 개념이다.

레포 분리로 API 경로를 임의 변경하지 않는다. 실행 환경별 API 기본 주소, 필요한 인증 전달, 실제 origin이 다를 때 CORS 설정을 함께 확인한다. 프론트에 모델 API 키를 넣지 않는다.

## 7. 개발 전 대조 및 합의 대기 (2026-09-27)

검토안 r2의 대조 목록에 develop 병합 후 코드와 담당자 검증 기록을 반영했다. 이번 정정에서는 서버 실행·테스트를 새로 수행하지 않았으며, 기존 결과의 환경과 한계는 task_backend.md 6.12~6.18절을 따른다. P0는 프론트·백엔드·Agent 연결 전에 맞출 항목, P1은 해당 후속 기능 착수 전까지 정할 항목이다. 구현된 기능과 미합의 규격을 구분해서 작업한다.

### 7.1 코드에 이미 있는 연결

아래는 `app/models.py`와 라우터의 현행 구현이다. 계약 1.3에서 C-01~C-04의 기존 필드·조회 형식과 C-07의 공통 오류/OpenAPI를 본문에 반영했다. 프론트 사본·타입·호출부 연결과 미구현 API는 후속이다.

| ID | 우선순위 | 현행 코드에서 확인한 내용 | 합의·반영할 것 |
|---|---|---|---|
| C-01 | P0 | Source.asset_ids와 이미지 GET | 기존 목록 형식은 계약 1.3 반영. 원본/추출문 미리보기 API는 미구현·별도 후속 |
| C-02 | P0 | Proposal의 rationale/candidates/applied_revision과 선택 적용 | 기존 후보·빈 값 형식은 계약 1.3 반영. 실제 AI 후보/프론트 비교 화면은 후속 |
| C-03 | P0 | Job.progress와 7종 result_ref, Preflight/Proposal GET | 현행 필드·결과 조회 방식을 계약 1.3/OpenAPI에 반영. 백분율은 추가하지 않음 |
| C-04 | P0 | SourceWarning와 Recommendations | 현행 위치·문자열 보완 목록을 계약 1.3에 반영. 새 고정 보완 분류는 별도 결정 |
| C-07 | P0 | 27개 API 경로·JSON/파일 응답·공통 오류 | Pydantic·계약·예시 정리. 프론트 사본/타입/호출부 동기화는 미실행, 추가 기능 계약은 해당 작업에서 보완 |

`Source.asset_ids`, 경고, 추천, 후보 구조를 다시 만드는 작업부터 시작하지 않는다. 등록 자료 `GET /sources`, CLI 적재, 선택 자료의 Agent 전달은 구현되어 있다. `services/preflights.build_sources()`는 선택한 등록 자료와 현재 세션 첨부 중 사용 가능한 근거 자료를 준비하며, 작성 조건 첨부와 다른 세션 자료는 제외한다.

- 문서 조회의 `validation`·`approval`은 현재 문서·입력 버전의 검증 결과와 유효한 승인 기록이 있으면 실제 값을 반환한다. 해당 결과가 없을 때만 `null`이다.
- PDF 배치 검사 → 미리보기 → 승인 → Export → 다운로드는 구현되어 있으며, 가짜 자료를 사용한 실서버 검증 기록이 있다(task_backend.md 6.15·6.17절). 실제 AI·프론트 연결과 실제 회사 자료의 생성 품질·PDF 전체 흐름 검증은 남아 있다. 현재 Mac의 PDF 시간 초과는 기존 Windows 성공 기록과 구분한다(task_backend.md 6.18절).
- BE-09의 세션 종료·만료, 서버 저장 내용 제거, 폴더 삭제와 정리 재시도는 구현되어 있다(task_backend.md 6.16절). AG-03 최초 초안의 참조 체크포인트를 세션 폴더에 연결해 같은 삭제 경로로 확인했다(task_agent.md 6.22절). 실제 모델·운영 환경과 편집 단계 그래프는 후속이다.

### 7.2 기존 원칙을 실행 절차로 연결할 항목

| ID | 우선순위 | 현재 상태 | 합의·구현할 것 |
|---|---|---|---|
| C-05 | P0 | 계약 1.6: DB v11에서 재점검 확인·영향 조회·선택 수정/유지 사유·최신 입력 연결·전체 재검증 API 구현. 다른 입력 복원 우회 차단 | 프론트 계약 사본/타입/화면 연결, 실제 모델의 변경 자료 의미 검증과 사용자 통합 확인. 자동 의미 수정안 생성은 별도 |
| C-06 | P0 | 서버 시작·최종 저장 가드 유지. 최초 초안의 LangGraph 대기·재개도 세션·입력 버전·현재 preflight·DB의 사용자 확인을 검사하고 소비한 확인의 중복 호출을 거부함. 세션 폴더 체크포인트 삭제 연결 검사 완료(task_agent.md 6.22절) | 실제 모델을 붙인 그래프·편집 단계·프로세스 장애 복구 확인. 별도 SQLite 커밋 사이 장애는 재점검 필요(plan.md 4.7절) |
| C-08 | P1 | 계약 1.5에서 D-07 서버 확인 기록·최신 검증/버전 검사·승인/출력 차단 구현. 기존 정확성 blocker 유지 | 프론트 확인 UI·계약 사본 연결, 실제 Agent/화면 통합 검증은 후속. 시연 warning도 명시 확인하며 일반 문서의 정확성 기준은 완화하지 않음 |
| C-09 | P1 | PDF 배치·미리보기·승인·Export·다운로드와 형식·버전 일치 검사 구현. DOCX 파일 생성과 PDF 기준 미리보기는 있으나 DOCX의 `actual_pages=null`·`overflow=not_checked`로 승인·Export·다운로드는 차단됨 | DOCX 배치 검증 방법과 승인 보장 범위를 별도 합의한 뒤 후속 구현. 기존 PDF 검사·미리보기·승인/출력 규격은 프론트와 맞추며 PDF 검사만으로 DOCX 검증 완료로 표시하지 않음 |
| C-10 | P1 | 보완 메모가 근거·수명 정책에 등장하지만 전용 API/모델은 없음 | 지원 여부부터 결정. 지원하면 원자료 버전·근거 위치·수명 연결, 미지원이면 텍스트 파일 첨부로 안내 |
| C-11 | P1 | 직접 삽입 ID는 클라이언트가 전달하고 서버가 중복 검사. 초안 ID는 Agent 결과에 포함. 페이지 구조 제안의 범위가 불명확함 | ID 발급 책임과 블록/페이지 구조 편집 범위. 빈 페이지·페이지 단독 요청 표현도 합의 후 모델·검사와 맞춤 |

C-05의 사용자 흐름은 [prd.md BR-05](prd.md)를 따른다. 현재 서버의 영향 목록은 참조 변화의 검사이며 문장 의미 판정은 적용 후 기존 내용 검증에서 수행한다. 다른 입력 기준의 과거 내용은 복원으로 현재 입력에 연결할 수 없다. Agent의 자동 의미 수정안·편집 그래프는 별도 후속이다.

### 7.3 오류·예시·표시에서 확인할 차이

- `Brief.company_name_hint`는 현재 필드가 없다. 추가한다면 회사명 사실 근거로 인정할지, 원문 비교용 힌트로만 쓸지부터 정한다.
- 파일 개수 초과도 현재 `413 FILE_TOO_LARGE`를 사용한다. 안내 문구는 개수 제한을 설명하므로 코드 구분 필요성을 확인한다. 추출 글자 제한은 `partial + TEXT_LIMIT` 경고이며 업로드 크기 초과와 다르다.
- `INVALID_REQUEST`, `IDEMPOTENCY_KEY_CONFLICT`, `AGENT_OUTPUT_INVALID`, `INVALID_OPERATION`, `DOCUMENT_EXISTS`, `RESTORE_REFERENCE_INVALID`, `CANDIDATE_REQUIRED`, `UNSUPPORTED_PROPOSAL`, `NO_IMAGE_CANDIDATES` 등 현행 코드를 C-07에서 정리한다.
- 적용/거절/오래된 수정안의 재적용은 현재 `PROPOSAL_STALE + details.status`로 구분한다. 같은 멱등 키·같은 본문의 재전송은 기존 성공 결과를 반환한다. 코드 통합 여부와 사용자 안내를 함께 결정한다.
- `handoff/api_examples_v1.1.json`에는 계약용 가상 예시와 가짜 자료로 받은 실서버 응답이 함께 있다. 각 `_note`의 작성·검증 범위를 따른다. DB v9의 기존 예시는 형식 참고이며 전체를 재실행한 응답은 아니다. 등록 자료·검증·PDF 출력은 구현된 기능이지만 실제 회사 자료의 품질 검증을 뜻하지 않는다. 이동된 작업일지 절 번호만 이번에 정정하며 응답값·필드·상태는 바꾸지 않는다.
- 프론트가 현재 적용 중인 파일 제한·만료 설정을 어느 응답에서 읽을지도 정한다. 제한값 자체는 plan.md D-01/D-02에서 관리한다.

### 7.4 반영 순서와 담당

1. 백엔드가 현행 모델·경로·오류·예시 연결표를 확인하고 Agent·프론트와 C-01~C-07을 맞춘다.
2. 해당 후속 기능 착수 전에 C-08~C-11의 남은 항목을 확정한다. D-07의 현행 요청·저장·승인 규격은 계약 1.5를 따른다. 시연 범위를 실제 문서로 확대하는 결정은 별도 합의가 필요하다. 도구·보관 기간 등 제품 결정은 [plan.md 4.1절](plan.md)에 기록한다.
3. 합의한 내용을 1~6절·실제 모델·응답 예시에 반영하고 contract_version 변경과 schema_version 변경을 각각 판단한다.
4. 프론트 계약 사본·타입·호출부를 갱신하고 실제 연결을 확인한다. 이번 정정에서는 문서 현황과 API 예시의 작업일지 참조만 수정했다. 프론트 사본·API 응답 형식·코드는 수정하지 않았다.

문서 준비 기록은 [task_backend.md 6.6절](task_backend.md), [task_agent.md 6.1절](task_agent.md)을 따른다. 병합된 구현·검증 기록은 task_backend.md 6.12~6.17절, 현재 Mac 확인과 남은 일은 6.18절에서 확인한다. 기존 BE/AG 상태와 QA 결과는 이번 문서 정정으로 바꾸지 않는다.

### 7.5 Stitch 화면 상태와 현재 계약 연결

2026-09-27 제공된 화면 PRD v2.5를 바탕으로 연결한 참고표이며, 이후 확정한 경고 정책은 이 저장소의 최신 PRD를 따른다. E01~E10은 화면 식별자이며 서버 오류 코드나 새 API가 아니다. 실제 프론트 화면과 서버 연결은 확인하지 않았다. E04는 요청 대기·결과 확인, E06은 기준 버전 충돌로 구분한다.

| 화면 상태 | 현재 데이터·동작 연결 | 화면 표시 및 남은 합의 |
|---|---|---|
| E01 읽을 텍스트 없음 | `Source.text_available`, `Preflight.can_generate`, `NO_USABLE_TEXT` | 텍스트 근거가 없으면 생성하지 않는다. 사진 표시와 텍스트 근거를 구분한다. 생성 가능 판정만으로 사용자 확인이 완료되지는 않는다. |
| E02 일부만 읽음 | `Source.parse_status=partial`, `warnings[].locator/code/message/action`, `usable_segment_ids` | 읽지 못한 위치·보완 방법을 표시한다. `partial`만으로 생성 여부를 정하지 않고 최신 Preflight를 따른다. 스캔 이미지는 `IMAGE_ONLY` 경고와 함께 표시한다(C-04). |
| E03 사진 후보 선택·부족 | `Source.asset_ids`, `Proposal.candidates`, `selected_candidate_id`; `CANDIDATE_REQUIRED`, `NO_IMAGE_CANDIDATES` | 실제 있는 후보만 표시하며 3개를 보장하지 않는다. 후보가 없으면 첨부 또는 글 중심 구성을 안내한다. 사용자 선택·적용과 빈 후보·오류 표현은 C-01/C-02/C-07에서 맞춘다. |
| E04 AI 수정안 대기·확인 | `Job.status/progress/result_ref`, Proposal 조회 및 `changes/rationale/candidates` | AI 제안은 적용 전 문서를 자동 변경하지 않는다. 사용자가 직접 편집하면 오래된 제안은 E06으로 처리한다. 현재 진행 정보는 stage/message이며 백분율은 없다. 결과 조회·적용·취소·재요청은 C-02/C-03을 따른다. |
| E05 편집 중 자료 추가 | 선택 변경 시 `input_revision` 증가; 현재 입력이 오래된 문서는 편집 차단 | 업로드와 자료 선택을 구분한다. 계약 1.6의 재점검 확인→영향 조회→선택 수정/유지 사유→적용→검증 Job 조회에 프론트를 연결한다. 화면 연결은 미완료다. |
| E06 수정안 기준 버전 충돌 | `Proposal.status=stale`, `base_document_revision`, `base_input_revision`; `PROPOSAL_STALE` 등 | 오래된 제안의 적용을 막고 최신 문서에서 재요청하도록 안내한다. 현재 stale 기록을 실제 삭제로 해석하지 않는다. 늦은 결과·만료는 C-06, 오류 구분은 C-07에 연결한다. |
| E07 필수 문제 미해결 | `Issue.severity/status/resolution`, Validation·LayoutCheck·Approval, 3절 승인 조건 | D-07 서버는 미확인/무효 경고 확인을 차단한다. 허용 경고 확인 후에도 필수 문제·배치·최종 동의를 별도로 검사한다. 프론트 연결은 후속이다. |
| E08 출력 실패 | `Export.status/error`, 승인 스냅샷·형식·버전·재사용 규칙 | PDF는 유효한 승인·세션을 확인하고 출력만 재시도하는 경로까지 구현되어 있다. AI 초안을 다시 생성하지 않는다. 가짜 자료 실서버 검증 범위이며 DOCX 승인·Export·다운로드는 C-09 후속 작업이다. |
| E09 세션 만료 예고 | `Session.expires_at`; 현재 무활동 120분과 생성 후 24시간 중 빠른 때 | 서버 만료 시각으로 남은 시간을 표시한다. 10분 전 알림·명시적 연장은 합의 대기다. 현재 연장 전용 API는 없고 GET·폴링은 활동으로 세지 않는다. |
| E10 세션 종료·만료 | `Session.status`, `410 SESSION_EXPIRED`, 종료·정리 상태 | BE-09 내용 제거·폴더 삭제·재시도에 최초 초안의 LangGraph 체크포인트를 연결했다(C-06). 접근 차단과 `cleanup=done/pending`을 구분한다. 등록 원본과 내용 제거 후 감사용 메타 기록은 보존하며 편집 단계 그래프·실제 운영 검증은 후속이다. |

**화면 표시에서 재사용할 정보**

- 출처는 `EvidenceRef.source_id/source_version/segment_id/locator/excerpt`를 사용한다. 원자료 ID는 작성 결과의 `document_id`와 구분하고 PDF 쪽·PPTX 슬라이드·DOCX 문단/표·TXT 줄 등 실제 위치를 표시한다. 원문/추출문 조회 경로는 C-01에서 별도 합의한다.
- 근거 상태는 `Fact.status`와 관련 Issue로 보여준다. 현재 모델에는 수정안의 신뢰도 점수나 ‘신뢰도 높음’ 판정이 없다. 근거 있음이 외부 현실의 진위를 보증하지 않는다.
- 페이지 카드는 기존 Document의 제목·본문 일부·사진 포함 여부로 구성할 수 있다. 수치 확인은 연결된 Fact·Issue·검사 결과를 사용하며 화면 보기 전환만으로 검증 완료 상태를 만들지 않는다.
- A4 비율의 편집 화면과 선택 형식의 배치 검사·출력 미리보기(C-09)를 구분한다. PDF와 DOCX의 쪽수·배치 일치를 보장하지 않는다.

명시적 세션 연장, 별도 AI 페이지 요약, 문장별 출처, 별도 결재자 절차의 채택 여부는 [plan.md 4.1절](plan.md)에서 정한다. 채택 전 새 필드·API를 추가하지 않는다. 연장 목적으로 입력 수정 API를 호출하거나 화면 전환마다 AI 요약을 생성하지 않는다. 이 화면 연결표를 처음 정리할 당시에는 기존 필드·상태값·API 형식과 계약/스키마 버전을 유지했다. 이후 승인 재전송 동작의 변경은 상단의 계약 1.2와 3~4절을 따른다. 확인 요청·응답이나 상태 연결을 변경할 때는 실제 모델·예시·계약 버전의 영향을 함께 검토한다. 프론트 계약 사본에는 최신 설명의 동기화가 필요하며 아직 확인하지 않았다.
