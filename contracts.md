# 공통 데이터·API 계약

기준일: 2026-09-23 · 문서 v1.1 · contract_version: 1.1 · 데이터 schema_version: 1.0

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

세션 식별자 자체를 권한으로 믿지 않는다. 서버의 소유자/보안 쿠키 등 검증된 접근 컨텍스트와 함께 검사한다. 로그인 제품 범위는 기존 저장소에 맞추되 세션 간 격리는 필수다. 배경 폴링만으로 만료를 무한 연장하지 않는다.

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
- acknowledged는 허용된 warning에만 사용한다. blocker를 확인 클릭만으로 통과시키지 않는다.
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
4. 해당 문서/입력 버전의 Validation이 완료되어 있고 미해결 blocker가 없다.
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
- 단순화를 위해 MVP의 작업 문서·체크포인트는 등록 자료만 사용해도 세션 보관을 기본으로 제안한다. 장기 보관은 별도 제품 결정이다.
- 종료/만료는 즉시 접근을 막고 임시 바이트를 삭제한다. 삭제 작업 실패는 재시도 목록으로 관리하며 종료 성공과 바이트 삭제 완료를 구분한다.
- 브라우저 닫힘 이벤트만 믿지 않는다. 서버 만료 검사와 정리 작업이 필요하다.
- 로그는 ID·오류 코드·시간·토큰 수 중심이다. 원문, 전체 프롬프트, 문서 본문을 영구 로그/외부 추적에 자동 저장하지 않는다.
- LangGraph 체크포인트나 모델 제공자의 보관 정책이 이 원칙을 자동으로 충족한다고 가정하지 않는다. 외부 제공자 설정·별도 보관 범위를 확인하고 설명한다.
- `thread_id`를 지우는 것만으로 모든 체크포인트/첨부가 삭제되었다고 판단하지 않는다. 실제 저장소별 정리 결과를 확인한다.

만료 시간과 파일 제한의 최종 값은 백엔드 레포 plan.md의 D-01/D-02를 따른다. 프론트는 서버가 반환하는 제한·만료 상태를 표시한다.

## 6. 두 레포의 계약 동기화

원본과 사본은 동일한 문서 버전과 내용으로 유지한다. API 변경 시 원본·실제 서버 모델·예시 응답을 먼저 맞추고, 프론트 사본·타입·호출부를 갱신한다. 이 문서의 contract_version과 문서 JSON의 schema_version은 서로 다른 개념이다.

레포 분리로 API 경로를 임의 변경하지 않는다. 실행 환경별 API 기본 주소, 필요한 인증 전달, 실제 origin이 다를 때 CORS 설정을 함께 확인한다. 프론트에 모델 API 키를 넣지 않는다.
