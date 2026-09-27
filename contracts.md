# 공통 데이터·API 계약

기준일: 2026-09-27 · 문서 v1.3 · contract_version: 1.1 · 데이터 schema_version: 1.0

개발 전 대조 메모는 7절에서 관리한다. 문서 v1.2에서 경고 승인 정책(PRD BR-08/09, plan D-07)을 구체화했고, v1.3에서 로그인 MVP 제외 결정(plan D-08)을 반영했다. 두 정책은 2026-09-27 사용자 확정 사항이다. 필드·상태값·API 형식과 계약/스키마 버전은 유지한다. 7절은 확정된 제품 정책과 아직 정할 연결 규격을 구분한 목록이며, 코드·예시·프론트 사본의 반영 완료를 뜻하지 않는다.

원본 위치는 백엔드 레포의 contracts.md이며 프론트 레포에는 동일 버전의 사본을 둔다. 원본 변경 주 담당은 백엔드이고 AI 입출력은 Agent, 화면 영향은 프론트 담당과 협의한다. 사본만 독자 수정하지 않는다.

이 문서는 프론트·백엔드·Agent의 데이터 교환 기준이다. 기존 API와 필드가 이미 있다면 백엔드 BE-01에서 연결표를 만들고 호환 어댑터를 우선한다. 아래 이름은 현재 코드에 구현되어 있다는 뜻이 아니다.

## 1. 공통 규칙

- JSON 필드는 snake_case, ID는 불투명 문자열, 시간은 UTC ISO 8601을 사용한다.
- 사실값이 없으면 null 또는 확인 필요 상태를 사용한다. 빈 문자열을 확정 사실로 취급하지 않는다.
- 파일, 근거, 페이지, 블록, 문제 ID는 이동·표시 순서 변경 후에도 유지한다.
- 화면 표시명과 내부 상태 코드는 구분한다.
- `input_revision`: 자료 선택·내용·목적·강조·페이지 설정의 버전.
- `document_revision`: 저장 문서의 버전. 초안 최초 저장은 1, 변경할 때마다 증가한다.
- `schema_version`: 데이터 형식 버전으로 시작값은 `1.0`이다.
- 화면에 전달할 파일은 접근 제어된 asset 참조로 제공한다. 내부 파일 경로나 임의 외부 URL을 AI가 만들어 넣지 않는다.

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

로그인은 현재 MVP에서 제외한다(plan D-08). 로그인 없이도 세션 식별자 자체를 권한으로 믿지 않고, 서버의 소유자/보안 쿠키 등 검증된 접근 컨텍스트와 함께 검사한다. 세션 간 격리는 필수이며 배경 폴링만으로 만료를 무한 연장하지 않는다. 경고 확인 사용자와 승인 주체는 해당 세션에 접근 가능한 작업 사용자를 뜻하며 별도 회원계정을 요구하지 않는다.

### Brief — 작성 요청

| 필드 | 타입 | 의미 |
|---|---|---|
| purpose | string | 사용 목적 |
| emphasis | string[] | 강조하고 싶은 내용 |
| direction | balanced / quality_process / customer_response | A/B/C 작성 방향 |
| target_pages | 1 / 4 / 6 / 8 / 10 | 기본 목표 쪽수 |
| photo_preference | none / balanced / many | 사진 비중; 많아도 자료 없는 사진을 만들지 않음 |

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
| warnings | object[] | 위치·원인·권장 조치 |
| expires_at | string 또는 null | 세션 자료 만료 시간 |

이미지 표시가 가능해도 `text_available=false`일 수 있다. 지원하는 이미지 파일 자체의 정상 처리는 complete가 가능하지만, 그 안의 글자를 읽었다는 의미는 아니다. 파일 제외는 선택 목록에서 해제하는 것이며 등록 자료 원본 삭제와 다르다.

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
- 서버가 검증 결과와 승인 상태로 status를 계산한다. 클라이언트가 임의로 approved를 설정할 수 없다.
- Page: `page_id`, `title`, `layout_key`, `blocks`.
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

- recommendations는 제안 페이지 수와 이유, 필요한 사진/인증/보완자료를 담는다.
- can_generate는 읽기·최소 텍스트 근거 등 생성 조건을 뜻한다. 사용자 확인은 별도로 필요하다.
- 필수 내용 누락과 읽기 실패를 동일하게 처리하지 않는다. 읽을 근거가 있고 필수 내용 일부가 빠진 경우 검토용 초안은 가능하다.

### Issue — 확인할 내용

필수: `issue_id`, `scope`, `code`, `severity`, `status`, `message`, `source_ids`, `fact_ids`, `block_ids`, `resolution`.

- scope: source / content / layout.
- severity: blocker / warning / info.
- status: open / resolved / excluded / acknowledged.
- resolution: 조치 종류, 사용자, 시간, 사유, 관련 근거, 확인 당시 문서/입력 버전.
- excluded는 선택 주장이나 자료를 실제 문서/선택에서 제외했을 때만 가능하다. 필수 내용의 결핍에는 사용할 수 없다.
- 사실·수치·필수 내용의 정확성 또는 출력 파일의 정상 이용에 영향을 주는 문제는 blocker다. 사진 부족·표현 반복처럼 정확성과 출력 이용에 영향 없는 문제는 warning으로 분류한다. 사용자 확인을 이유로 blocker를 warning으로 낮추지 않는다.
- acknowledged는 위 기준의 warning을 사용자가 확인했을 때만 사용한다. blocker를 확인 클릭만으로 통과시키지 않는다. 사진 부족을 확인해도 깨진 사진·남은 사진 자리의 blocker는 유지한다.
- 확인 기록은 resolution의 사용자·시간·관련 근거·문서/입력 버전과 문제·관련 내용에 연결한다. 관련 문장·사진·근거가 바뀌면 재확인이 필요하다. 무관한 변경의 확인 기록을 재사용할 때도 새 버전의 검증 결과에 유효성을 연결한다.
- 해결 여부는 서버가 조치와 현재 내용의 일치를 확인해 기록한다. AI가 자체 승인하지 않는다.

### Proposal — AI 편집안

필수: `proposal_id`, `document_id`, `base_document_revision`, `base_input_revision`, `target_block_ids`, `kind`, `changes`, `status`.

- kind: text / structure / image.
- status: proposed / applied / rejected / stale.
- changes는 허용된 블록 수정·삽입·삭제·이동 연산과 근거를 담는다. 이미지 후보 선택 전에는 문서를 바꾸지 않는다.
- 기준 문서 또는 입력 버전이 달라지면 stale로 바꾸고 적용을 거부한다.
- 적용은 원자적으로 한 번만 실행하며 새 문서 버전을 만든다.

### Validation / LayoutCheck / Approval / Export

| 객체 | 필수 내용 |
|---|---|
| Validation | validation_id, document_id, document_revision, input_revision, status: pending/passed/needs_review/failed, issue_ids |
| LayoutCheck | layout_check_id, document_revision, input_revision, format, template_version, render_options_hash, asset_manifest_hash, status, actual_pages 또는 null, issue_ids |
| Approval | approval_id, document_id, document_revision, input_revision, format, validation_id, layout_check_id, template_version, render_options_hash, asset_manifest_hash, approved_at, approved_by, status: active/invalidated |
| Export | export_id, approval_id, format, status: queued/generating/ready/failed, artifact_id 또는 null, expires_at, error 또는 null |

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

출력 API도 Approval이 active이며 최신 문서/입력 버전과 일치하는지 확인한다. 승인 후 문서가 바뀌면 옛 파일을 최신 승인본처럼 내려주지 않는다. 변경 전에 내려받은 파일은 사용자의 로컬 파일로 남는다.

## 4. API 초안

기본 경로는 `/api/v1`이다. 모든 세션 자원은 소유·만료 검사를 거친다. 본문 예시는 필수 핵심 필드만 표시하며 백엔드 BE-01에서 서버 모델과 OpenAPI로 구체화한다.

| Method / 경로 | 요청 핵심 | 결과 |
|---|---|---|
| POST /sessions | 초기 Brief | Session |
| GET /sessions/{sid} | 없음 | 상태·최신 입력·문서 요약 |
| DELETE /sessions/{sid} | 없음 | 접근 즉시 차단, 멱등 정리 시작; 삭제 완료 여부 |
| GET /sources | 검색/종류 필터 | 접근 가능한 등록 자료; 현재 세션 업로드 목록과 분리 |
| POST /sessions/{sid}/sources | multipart 파일 | Source 목록; 비동기 읽기 상태 |
| GET /sessions/{sid}/sources | 없음 | 세션 첨부·읽기 상태 |
| DELETE /sessions/{sid}/sources/{source_id} | 현재 input_revision | 세션 첨부만 삭제; 연결 내용 영향 검사 |
| PATCH /sessions/{sid}/inputs | expected_input_revision, Brief, selected_source_ids | 새 입력 버전, 사전 확인 무효화 |
| POST /sessions/{sid}/preflights | expected_input_revision | Job; 완료 시 Preflight |
| POST /sessions/{sid}/drafts | preflight_id, input_revision, confirmed: true | 최신 사전 확인 기록, 초안 생성 Job |
| GET /sessions/{sid}/documents/{did} | 없음 | Document, 검사/승인 상태 |
| PATCH /sessions/{sid}/documents/{did} | expected_revision, operations | 새 Document; 필요한 검증 Job |
| POST /sessions/{sid}/documents/{did}/proposals | expected_revision, input_revision, target_block_ids, instruction, kind | Proposal 생성 Job |
| POST /sessions/{sid}/proposals/{pid}/apply | expected_revision, selected_candidate_id 필요 시 | 새 Document; 검증 Job |
| POST /sessions/{sid}/proposals/{pid}/reject | 없음 | rejected; 문서 변화 없음 |
| POST /sessions/{sid}/documents/{did}/restore | expected_revision, restore_from_revision | 이전 내용의 새 버전, 재검증 |
| POST /sessions/{sid}/issues/{iid}/resolve | expected_revision 필요 시, resolution, evidence_refs | 조치 결과, 관련 재검증 |
| POST /sessions/{sid}/documents/{did}/validate | expected_revision, input_revision | Validation Job |
| POST /sessions/{sid}/documents/{did}/layout-checks | expected_revision, format | LayoutCheck Job 및 미리보기 |
| POST /sessions/{sid}/documents/{did}/approvals | expected_revision, input_revision, format, validation_id, layout_check_id, confirmed: true | Approval |
| POST /sessions/{sid}/exports | approval_id, format | Export 및 Job; 같은 승인 결과 재사용 가능 |
| GET /sessions/{sid}/jobs/{jid} | 없음 | Job 상태·진행·결과 참조 |
| GET /sessions/{sid}/assets/{asset_id} | 없음 | 접근 확인 후 이미지/원본/미리보기 바이트 |
| GET /sessions/{sid}/exports/{eid}/download | 없음 | 승인·만료 재확인 후 실제 파일 |

source 업로드만으로 자동 선택하지 않는 UI를 택하면 사용자가 선택할 때 input_revision을 갱신한다. 단, 선택된 기존 원자료를 수정·삭제하거나 문서가 참조하는 자산을 바꾸면 즉시 영향 상태를 갱신한다.

### 비동기·중복 요청

- 오래 걸리는 읽기/AI/배치/출력 작업은 202와 job_id를 반환한다. Job.status: queued / running / waiting_user / succeeded / failed / cancelled.
- 프론트는 진행 상태를 조회한다. 초기 구현은 폴링 가능; SSE는 필수 요구가 아니다.
- 기다리는 사용자 입력을 처리하는 API는 서버가 검증한 행동만 내부 LangGraph 재개 값으로 변환한다. 임의 노드명·thread_id 실행 API를 공개하지 않는다.
- 상태를 바꾸는 POST/PATCH는 `Idempotency-Key`를 받아 요청자·세션·경로와 함께 중복을 식별한다. 같은 키에 다른 본문은 409다.
- 같은 적용 요청의 재전송은 최초 결과를 돌려주고 문서 버전을 다시 늘리지 않는다.
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
| 401/403 | UNAUTHORIZED / FORBIDDEN | 접근 검증; 다른 세션 존재 여부를 불필요하게 노출하지 않음 |
| 404 | RESOURCE_NOT_FOUND | 접근 가능한 범위에서 자원 없음 |
| 409 | DOCUMENT_REVISION_CONFLICT / INPUT_REVISION_CONFLICT / PROPOSAL_STALE | 최신 상태 조회 후 재요청 |
| 410 | SESSION_EXPIRED / ARTIFACT_EXPIRED | 만료 안내; 기존 요청을 자동 복원하지 않음 |
| 413/415 | FILE_TOO_LARGE / UNSUPPORTED_FILE_TYPE | 업로드 전후 제한 안내 |
| 422 | NO_USABLE_TEXT / PREFLIGHT_NOT_CONFIRMED / UNRESOLVED_REQUIRED / LAYOUT_NOT_READY | 보완할 위치와 행동 표시 |
| 429/503 | AI_RATE_LIMIT / SERVICE_TEMPORARY_FAILURE | 제한된 재시도·대기 안내 |
| 500 | EXPORT_FAILED / INTERNAL_ERROR | 기존 문서 보존; 안전한 오류 메시지 |

업로드 후 파서 실패는 Source.parse_status=failed와 원인으로도 표현한다. 모든 파일 실패를 HTTP 500으로 묶지 않는다.

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

아래는 `app/models.py`와 라우터의 현행 구현이다. 이 표를 기준으로 채택 여부·빈 값·오류·예시를 확인한 뒤 2절과 4절의 규격에 함께 반영한다.

| ID | 우선순위 | 현행 코드에서 확인한 내용 | 합의·반영할 것 |
|---|---|---|---|
| C-01 | P0 | `SourceOut.asset_ids: list[str]`가 있고, ready 이미지 ID와 `GET /sessions/{sid}/assets/{asset_id}`로 사진을 연결함 | 기존 목록 방식의 계약 반영. 별도 목록 API가 필요한지 확인. 원본 PDF/DOCX·추출문 미리보기는 이미지 조회와 구분 |
| C-02 | P0 | `Candidate(candidate_id, label, changes)`, `ProposalOut.rationale`, `candidates`, `applied_revision`이 있음 | 후보 없음의 null/빈 목록 규칙, 선택 적용·취소·재요청과 실제 응답 필드 정의 |
| C-03 | P0 | `JobOut`에 kind/status/progress/result_ref/error/시각이 있음. progress는 stage/message이며 백분율 없음. Preflight·Proposal 단독 GET 구현 | 작업 종류별 result_ref와 완료 결과 조회 방식, 진행률을 모를 때의 표시. `GET /sessions/{sid}/preflights/{pid}`, `GET /sessions/{sid}/proposals/{pid}`를 API 표에 반영할지 확정 |
| C-04 | P0 | `SourceWarning(locator, code, message, action)`, `Recommendations(suggested_pages, reason, needed: list[str])`가 있음 | 기존 구조로 화면을 연결할지 확인. 보완자료 종류를 새 고정 분류로 확장하는 경우 별도 합의 |
| C-07 | P0 | 추가 필드·GET·임시 오류 코드가 작업 기록과 코드에 있으나 본문·예시 반영이 일부 지연됨 | 유지/변경할 항목을 정하고 모델·계약·예시·프론트 사본을 함께 맞춤 |

`Source.asset_ids`, 경고, 추천, 후보 구조를 다시 만드는 작업부터 시작하지 않는다. 등록 자료 `GET /sources`, CLI 적재, 선택 자료의 Agent 전달은 구현되어 있다. `services/preflights.build_sources()`는 선택한 등록 자료와 현재 세션 첨부 중 사용 가능한 근거 자료를 준비하며, 작성 조건 첨부와 다른 세션 자료는 제외한다.

- 문서 조회의 `validation`·`approval`은 현재 문서·입력 버전의 검증 결과와 유효한 승인 기록이 있으면 실제 값을 반환한다. 해당 결과가 없을 때만 `null`이다.
- PDF 배치 검사 → 미리보기 → 승인 → Export → 다운로드는 구현되어 있으며, 가짜 자료를 사용한 실서버 검증 기록이 있다(task_backend.md 6.15·6.17절). 실제 AI·프론트 연결과 실제 회사 자료의 생성 품질·PDF 전체 흐름 검증은 남아 있다. 현재 Mac의 PDF 시간 초과는 기존 Windows 성공 기록과 구분한다(task_backend.md 6.18절).
- BE-09의 세션 종료·만료, 서버 저장 내용 제거, 폴더 삭제와 정리 재시도는 구현되어 있다(task_backend.md 6.16절). 실제 LangGraph 체크포인트 정리 연결은 후속 작업이다.

### 7.2 기존 원칙을 실행 절차로 연결할 항목

| ID | 우선순위 | 현재 상태 | 합의·구현할 것 |
|---|---|---|---|
| C-05 | P0 | 기존 문서의 입력이 오래되면 편집·제안 요청을 409로 차단. 문서가 있으면 초안 재생성은 `DOCUMENT_EXISTS`. 편집 보존 원칙은 3절에 있음 | 기존 문서용 재점검 확인 → 영향 확인 → 수정안 적용 또는 현 내용 유지 근거 → 최신 입력 연결 → 재검증의 API·중간 상태·문서 버전 증가·재시도 규칙 |
| C-06 | P0 | 서버 작업의 시작·최종 저장 시 세션 상태·실제 만료 시각과 관련 버전을 재검사함. 기준이 바뀐 결과는 폐기하거나 Proposal을 stale 처리하고, 종료·만료 후 결과 저장을 차단함 | 이 가드를 실제 LLM·LangGraph 실행과 연결해 확인. 그래프 대기·재개와 체크포인트 정리 범위 합의 |
| C-08 | P1 | `validate` 규격·mock 검증 Job·결과 저장·승인 연결 구현. 문서·입력 버전 검사와 최종 사용자 승인도 수행함. 허용 warning의 확인자·시각·버전 기록과 관련 변경 후 재확인 기반은 있으나, 확인 의무를 승인 조건으로 강제하는 D-07 연결은 미완료 | D-07 제품 원칙을 유지하면서 경고 확인 대상·기록 규격·변경 후 재확인 조건과 `DEMO_VALUE` 적용 범위를 먼저 합의한 뒤 백엔드 후속 작업. 기존 기록·재확인 처리를 재사용하고 needs_review의 남은 문제·확인 유효성을 승인과 연결. 실제 LLM 의미 검증은 Agent 후속 작업 |
| C-09 | P1 | PDF 배치·미리보기·승인·Export·다운로드와 형식·버전 일치 검사 구현. DOCX 파일 생성과 PDF 기준 미리보기는 있으나 DOCX의 `actual_pages=null`·`overflow=not_checked`로 승인·Export·다운로드는 차단됨 | DOCX 배치 검증 방법과 승인 보장 범위를 별도 합의한 뒤 후속 구현. 기존 PDF 검사·미리보기·승인/출력 규격은 프론트와 맞추며 PDF 검사만으로 DOCX 검증 완료로 표시하지 않음 |
| C-10 | P1 | 보완 메모가 근거·수명 정책에 등장하지만 전용 API/모델은 없음 | 지원 여부부터 결정. 지원하면 원자료 버전·근거 위치·수명 연결, 미지원이면 텍스트 파일 첨부로 안내 |
| C-11 | P1 | 직접 삽입 ID는 클라이언트가 전달하고 서버가 중복 검사. 초안 ID는 Agent 결과에 포함. 페이지 구조 제안의 범위가 불명확함 | ID 발급 책임과 블록/페이지 구조 편집 범위. 빈 페이지·페이지 단독 요청 표현도 합의 후 모델·검사와 맞춤 |

C-05는 기존 문서를 새 초안으로 덮어쓰는 기능이 아니다. 사용자 흐름은 [prd.md BR-05](prd.md), Agent 내부 연결의 현황과 미합의 부분은 [agent.md](agent.md)를 따른다. 현재 되돌리기는 참조 존재를 확인한 뒤 과거 내용을 최신 입력 버전에 연결하므로, 복원만으로 새 자료의 영향 검사가 완료되었다고 판단하지 않도록 C-05와 함께 정리한다.

### 7.3 오류·예시·표시에서 확인할 차이

- `Brief.company_name_hint`는 현재 필드가 없다. 추가한다면 회사명 사실 근거로 인정할지, 원문 비교용 힌트로만 쓸지부터 정한다.
- 파일 개수 초과도 현재 `413 FILE_TOO_LARGE`를 사용한다. 안내 문구는 개수 제한을 설명하므로 코드 구분 필요성을 확인한다. 추출 글자 제한은 `partial + TEXT_LIMIT` 경고이며 업로드 크기 초과와 다르다.
- `INVALID_REQUEST`, `IDEMPOTENCY_KEY_CONFLICT`, `AGENT_OUTPUT_INVALID`, `INVALID_OPERATION`, `DOCUMENT_EXISTS`, `RESTORE_REFERENCE_INVALID`, `CANDIDATE_REQUIRED`, `UNSUPPORTED_PROPOSAL`, `NO_IMAGE_CANDIDATES` 등 현행 코드를 C-07에서 정리한다.
- 적용/거절/오래된 수정안의 재적용은 현재 `PROPOSAL_STALE + details.status`로 구분한다. 같은 멱등 키·같은 본문의 재전송은 기존 성공 결과를 반환한다. 코드 통합 여부와 사용자 안내를 함께 결정한다.
- `handoff/api_examples_v1.1.json`에는 계약용 가상 예시와 가짜 자료로 받은 실서버 응답이 함께 있다. 각 `_note`의 작성·검증 범위를 따른다. DB v9의 기존 예시는 형식 참고이며 전체를 재실행한 응답은 아니다. 등록 자료·검증·PDF 출력은 구현된 기능이지만 실제 회사 자료의 품질 검증을 뜻하지 않는다. 이동된 작업일지 절 번호만 이번에 정정하며 응답값·필드·상태는 바꾸지 않는다.
- 프론트가 현재 적용 중인 파일 제한·만료 설정을 어느 응답에서 읽을지도 정한다. 제한값 자체는 plan.md D-01/D-02에서 관리한다.

### 7.4 반영 순서와 담당

1. 백엔드가 현행 모델·경로·오류·예시 연결표를 확인하고 Agent·프론트와 C-01~C-07을 맞춘다.
2. 해당 후속 기능 착수 전에 C-08~C-11의 남은 항목을 확정한다. 특히 새 경고 확인 의무와 `DEMO_VALUE` 적용 범위는 기존 승인 동작과 구분해 합의한다. 도구·보관 기간 등 제품 결정은 [plan.md 4.1절](plan.md)에 기록한다.
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
| E05 편집 중 자료 추가 | 선택 변경 시 `input_revision` 증가; 현재 입력이 오래된 문서는 편집 차단 | 업로드와 자료 선택을 구분한다. 편집 보존·재점검·사용자 확인·문장/사진/근거 영향 확인·선택 적용 또는 유지·최신 입력 연결은 C-05의 후속 구현이다. 배너만으로 완료 처리하지 않는다. |
| E06 수정안 기준 버전 충돌 | `Proposal.status=stale`, `base_document_revision`, `base_input_revision`; `PROPOSAL_STALE` 등 | 오래된 제안의 적용을 막고 최신 문서에서 재요청하도록 안내한다. 현재 stale 기록을 실제 삭제로 해석하지 않는다. 늦은 결과·만료는 C-06, 오류 구분은 C-07에 연결한다. |
| E07 필수 문제 미해결 | `Issue.severity/status/resolution`, Validation·LayoutCheck·Approval, 3절 승인 조건 | 현재 서버는 미해결 blocker·버전·검사 상태와 최종 사용자 승인을 검사한다. D-07의 경고 확인 의무를 승인과 연결하는 작업은 C-08 합의 후 진행한다. 확인으로 blocker를 낮추지 않는 원칙과 기존 확인·관련 변경 후 재확인 기반을 유지한다. |
| E08 출력 실패 | `Export.status/error`, 승인 스냅샷·형식·버전·재사용 규칙 | PDF는 유효한 승인·세션을 확인하고 출력만 재시도하는 경로까지 구현되어 있다. AI 초안을 다시 생성하지 않는다. 가짜 자료 실서버 검증 범위이며 DOCX 승인·Export·다운로드는 C-09 후속 작업이다. |
| E09 세션 만료 예고 | `Session.expires_at`; 현재 무활동 120분과 생성 후 24시간 중 빠른 때 | 서버 만료 시각으로 남은 시간을 표시한다. 10분 전 알림·명시적 연장은 합의 대기다. 현재 연장 전용 API는 없고 GET·폴링은 활동으로 세지 않는다. |
| E10 세션 종료·만료 | `Session.status`, `410 SESSION_EXPIRED`, 종료·정리 상태 | BE-09 서버 범위의 내용 제거·폴더 삭제·재시도가 구현되어 있다. 접근 차단과 `cleanup=done/pending`을 구분해 정리 중인 상태를 삭제 완료로 표시하지 않는다. 등록 원본과 내용 제거 후 감사용 메타 기록은 보존하며 실제 LangGraph 체크포인트 정리 연결은 후속이다(C-06). |

**화면 표시에서 재사용할 정보**

- 출처는 `EvidenceRef.source_id/source_version/segment_id/locator/excerpt`를 사용한다. 원자료 ID는 작성 결과의 `document_id`와 구분하고 PDF 쪽·PPTX 슬라이드·DOCX 문단/표·TXT 줄 등 실제 위치를 표시한다. 원문/추출문 조회 경로는 C-01에서 별도 합의한다.
- 근거 상태는 `Fact.status`와 관련 Issue로 보여준다. 현재 모델에는 수정안의 신뢰도 점수나 ‘신뢰도 높음’ 판정이 없다. 근거 있음이 외부 현실의 진위를 보증하지 않는다.
- 페이지 카드는 기존 Document의 제목·본문 일부·사진 포함 여부로 구성할 수 있다. 수치 확인은 연결된 Fact·Issue·검사 결과를 사용하며 화면 보기 전환만으로 검증 완료 상태를 만들지 않는다.
- A4 비율의 편집 화면과 선택 형식의 배치 검사·출력 미리보기(C-09)를 구분한다. PDF와 DOCX의 쪽수·배치 일치를 보장하지 않는다.

명시적 세션 연장, 별도 AI 페이지 요약, 문장별 출처, 별도 결재자 절차의 채택 여부는 [plan.md 4.1절](plan.md)에서 정한다. 채택 전 새 필드·API를 추가하지 않는다. 연장 목적으로 입력 수정 API를 호출하거나 화면 전환마다 AI 요약을 생성하지 않는다. 이번 문서 개정은 기존 필드·상태값·API 형식과 계약/스키마 버전을 바꾸지 않는다. 확인 요청·응답이나 상태 연결을 변경할 때는 실제 모델·예시·계약 버전의 영향을 함께 검토한다. 프론트 계약 사본에는 이번 정책 설명의 동기화가 필요하며 아직 확인하지 않았다.
