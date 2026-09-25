# 백엔드 담당 전용 작업 목록

기준일: 2026-09-23 · v1.2 · 이 파일의 작업 상태·결과 기록 담당: 백엔드 담당

범위: 파일·세션·파서·API·문서/버전 저장·승인·PDF/DOCX 출력. Agent 상세 작업은 [task_agent.md](task_agent.md)에 있다.

## 1. AI가 읽을 파일

[AGENTS.md](AGENTS.md) → [plan.md](plan.md) → [prd.md](prd.md) → [contracts.md](contracts.md) → 이 파일. AI 함수 연결이 필요한 경우에만 [agent.md](agent.md)의 해당 부분을 확인한다.

공통 [task.md](task.md)는 연결 지점과 검수 위치를 확인할 때 읽는다. 다른 담당자의 상세 작업은 필요한 입력/결과를 확인할 때만 참조한다. 타 담당의 작업 상태를 임의로 바꾸거나 해당 작업까지 자기 구현 범위로 해석하지 않는다.

## 2. 내 작업

| 작업 | 선행 | 구현할 것·완료 조건 | 상태 |
|---|---|---|---|
| BE-01 | 없음 | 기존 코드·브랜치·실행 명령·공통 타입·API 확인. Agent와 실제 파일별 담당 경계 기록. contracts.md와 기존 모델 연결, 프론트에 예시 응답 제공. | DONE (2026-09-25, 6.1절 · 예시는 계약 예시이며 실제 응답 아님) |
| BE-02 | BE-01 | 세션 생성·소유·만료, 첨부 임시 저장과 제한 설정. D-01/D-02 기록. 서로 다른 두 세션의 자료 접근 차단 확인. | TODO |
| BE-03 | BE-02 | 형식별 읽기·구간 위치·complete/partial/failed·사진 asset 제공. 손상/암호/스캔 파일 점검, 지원표 기록. 불확실 수치를 완료로 표시하지 않음. | TODO |
| BE-04 | BE-01, BE-02 | AI 작업 실행/상태/사용자 응답 API와 내부 함수 연결 지점 구현. 먼저 계약 예시로 확인하고 AG-03에서 실제 연결. 오래된 입력·중복 재개·만료 요청 처리. | TODO |
| BE-05 | BE-01, BE-02 | 문서/페이지/블록 저장·버전·수정안 적용·취소·복원 API. 출처 ID 유지, 예상 버전 검사, 중복 적용은 1회만, 트랜잭션 실패 시 원본 보존. | TODO |
| BE-06 | BE-05, AG-07 | 문제 해결 상태·검증 버전·승인 조건 강제. 부분 검증에서 기존 문제 유지, 필수 누락 제외 불가. 배치 검사는 BE-08 연결 전 계약 예시로만 확인했다고 표시. | TODO |
| BE-07 | BE-01, AG-04 | PDF/DOCX 생성 도구·템플릿 검증 후 D-03/D-06 기록. 한글·사진·1쪽/다쪽·DOCX 본문 편집성을 실제 파일로 비교. 특정 도구를 필수로 전제하지 않음. | TODO |
| BE-08 | BE-06, BE-07 | 실제 배치 미리보기·검사·승인 스냅샷 출력·다운로드. 실패 재시도/같은 파일 재다운로드에 AI 재생성 없음. 실제 결과로 승인 검사 교체. | TODO |
| BE-09 | BE-02, BE-04, BE-08, AG-03 | 종료/만료 시 원본·파생문·초안·사진·체크포인트·출력 캐시 접근 차단 및 정리. 삭제 실패 재시도, 기록/외부 추적 보관 범위 확인. | TODO |
| BE-10 | BE-08, BE-09, AG-08, 프론트 FE-08 | 실제 프론트와 세 단계·오류·승인·출력 연결 확인. 모든 QA 항목의 개발 책임자가 결과 기록. 실행 안내·미정 결정·계약 버전·남은 제한 갱신. | TODO |

기존 작업 ID·선행 조건·상태를 유지했다. 현재 구현 여부는 미확인이며 기존 파일의 TODO를 완료로 바꾸지 않았다. 상태는 TODO / IN_PROGRESS / DONE / BLOCKED로 관리한다. 예시 데이터로만 확인한 경우 실제 연동 완료와 구분한다.

## 3. 상대 담당에게 전달할 것

- Agent에게: 접근 확인된 선택 자료, 읽기 구간과 원문 위치, 사용 가능한 사진 자산, 목적·입력/문서 버전.
- 프론트에게: 같은 계약 버전, API 예시와 실제 구현 상태, 이미지/다운로드 경로, 승인·오류·만료 상태.
- 공유 파일의 변경 범위와 연결 확인 방법.

## 4. 상대 담당에게 받아야 할 것

- Agent의 Facts/Preflight/Document/Proposal/Validation과 실제 평가 결과.
- 프론트의 실제 API 연결 결과(FE-08) 및 UI 검수 근거.
- 서로 다른 상태/필드가 있으면 contracts.md 원본을 먼저 맞춘다.

선행 결과가 없으면 요청/응답 계약 예시로 가능한 부분을 먼저 진행할 수 있다. 실제 연결이 필요한 완료 조건은 검증 후에만 통과시킨다. 부족한 입력과 요청 대상·작업 ID를 자기 진행 기록에 적는다.

## 5. 내가 결과를 기록할 필수 검수

아래 항목은 이 파일이 결과 기록의 원본이다. 다른 담당과 함께 확인하는 항목도 기록은 한 곳에 모은다. 수정/테스트가 다른 역할에 걸치면 그 담당의 확인 근거를 함께 남긴다. 확인·보조 담당자는 추가 육안 확인만 맡고 필수 검증의 단독 책임자가 되지 않는다.

| ID | 검수 내용 | 확인 참여 | 기대 결과 | 결과 |
|---|---|---|---|---|
| QA-02 | 세션 A/B 분리 | 백엔드 | 다른 세션 자료·사진·검색 접근 불가 | 미실행 |
| QA-03 | 미지원·손상·암호 파일 | 백엔드 | 원인·보완·제외, 다른 자료 보존 | 미실행 |
| QA-04 | 불확실한 표·수치 | 백엔드 + Agent | partial, 확인 구간만 사용 | 미실행 |
| QA-07 | 자료/목적 변경 후 옛 점검 사용 | 백엔드 | 이전 확인 무효화 | 미실행 |
| QA-11 | 오래된 수정안·중복 재개 | 백엔드 | 충돌 처리, 중복 반영 없음 | 미실행 |
| QA-14 | 순서 변경과 사실 문장 변경 | 백엔드 + Agent | 검사 범위 구분, 둘 다 승인 무효화 | 미실행 |
| QA-16 | 승인 API 우회 | 백엔드 | 미해결 필수/배치 문제 시 서버 차단 | 미실행 |
| QA-17 | 승인 후 변경 | 백엔드 | 옛 승인으로 최신본 출력 불가 | 미실행 |
| QA-18 | PDF/DOCX 파일 열기 | 백엔드 + 프론트 | 유효한 파일·한글·사진, DOCX 본문 편집 가능 | 미실행 |
| QA-19 | 일시 실패·재다운로드 | 백엔드 | 제한 재시도, 출력 재시도에 AI 생성 없음 | 미실행 |
| QA-20 | 세션 종료/만료·삭제 | 백엔드 | 임시 데이터/체크포인트 정리, 등록 자료 유지 | 미실행 |

다른 검수의 결과 위치는 공통 task.md의 인덱스를 따른다. 같은 상태를 여러 파일에 중복 기록하지 않는다. 검수 결과에는 사용한 버전·자료·명령 또는 클릭·실제 관찰을 남긴다.

## 6. 진행·전달 기록

| 날짜 | 작업 | 파일/커밋 | 실제 확인·결과 | 상대에게 전달/요청할 내용 |
|---|---|---|---|---|
| 2026-09-25 | BE-01 | 브랜치 feat/be-01-inventory · task_backend.md, task.md (코드 변경 없음) | 옛 레포(C:\vscode\Backend_old, 커밋 e8fc52a) 56개 파일 읽음. 파일별 담당·이관 판단, API 연결표, 데이터 항목 목록 작성 → 6.1절. 프론트 예시 응답은 미완료(후속) | Agent: 6.1-5 요청 6건. 팀: 계약 확인 ①~④, 자료사용 허용기록 이관 |
| 2026-09-25 | BE-01 | 브랜치 feat/be-01-examples · handoff/api_examples_v1.1.json(신규), task_backend.md, task.md | 프론트 전달용 계약 예시 22개 작성(Session·Source·Job·Preflight·Document·Export·오류 8종). JSON 파싱 확인만 했고 실제 서버 응답 아님. BE-01 DONE | 프론트: handoff/api_examples_v1.1.json + contracts.md v1.1 전달. 팀: 계약 확인 ⑤ 추가(6.1-5) |

자료·시스템의 진위를 추정하여 정상 처리하지 않는다. 설명용 이미지 생성·OCR·장기 보관은 별도 범위가 정해지기 전 기본 작업에 추가하지 않는다.

### 6.1 BE-01 확인 결과 (2026-09-25)

옛 레포 `C:\vscode\Backend_old`(= `agent-ddalgi` fork, develop e8fc52a) 기준. 코드는 옮기지 않았고 읽기만 했다. 이관은 별도 PR에서 하며, 옮기기 전 옛 CLAUDE.md 7절(실행 의존성·문서 참조·유일 정보) 확인을 적용한다.

**확인한 사실**

1. `database.py`·`schema.sql`은 옛 레포에 없다. 옛 코드는 DB 없이 메모리 dict(`_jobs`) + `private_runs/<job_id>/` 파일로 동작한다. DB 설계 참고 자료는 없다.
2. 옛 구조는 "업로드 1회 = 작업 1건 = 결과 1개"다. 세션·input_revision·document_revision·수정안·승인 개념이 없어 `main.py`는 파일째 못 가져오고 패턴만 참고한다.
3. 옛 담당 표기 A=Agent, C=백엔드, D=검사·문서출력. 새 팀에 D가 없으므로 D 파일은 AGENTS.md 2절에 따라 백엔드로 잡았다(`validators.py`의 근거-원문 대조는 AG-07과 경계가 닿아 Agent 확인 요청).
4. 옛 `.env.example`의 `OPENAI_MODEL`을 `agent.py`가 필수로 읽는데 새 `.env.example`에 없다.
5. 실행 확인: 옛 코드는 Python 3.12.10 + pip 기준(`docs/backend_readme.md`). 새 레포(uv, 3.13.5)에서는 실행하지 않았다(미확인).

**6.1-1 파일별 담당·이관 판단** (BE=백엔드, AG=Agent, 공통 / 가져옴·참고만·버림·AG 판단)

| 파일 | 역할 | 담당 | 판단 | 이유 |
|---|---|---|---|---|
| backend/main.py | FastAPI 앱, /api/profiles 3개, 업로드 제한(3개·10MB·40k자), 메모리 job, mock/llm 분기, CORS | BE | 참고만 | 새 API 구조가 다름. 재사용 패턴: `_read_limited`(스트리밍 크기 검사), `_write_json`(원자적 쓰기), 오류 봉투, `_check_document_file`, 스레드풀 실행 |
| backend/parsers.py | TXT/MD → 행 단위 source_units(S001, "N행"), UTF-8 strict | BE | 가져옴 | BE-03 출발점. 행 번호가 EvidenceRef.locator TXT 규칙과 일치 |
| backend/profile_builder.py | company_info → 13섹션 조립, 질문 생성, 스키마 검사 | BE | 참고만 | 옛 ProfileResult 전용. "supported만 본문·보충 금지" 규칙은 BE-04/05에서 유지 |
| backend/validators.py | fact_id 참조 검사, (source_id, locator) 원문 존재·quote 포함 검사 | BE (AG 확인) | 참고만→일부 가져옴 | 근거-원문 대조 로직은 새 EvidenceRef 검증에 필요 |
| backend/document_generator.py | ProfileResult → MD/DOCX(python-docx, 맑은고딕) | BE | 참고만 | BE-07 D-03 비교 자료. 폰트 설정·0바이트 검사·폴더 한정 규칙 재사용 |
| backend/mock_agent.py, __init__.py | Mock 로더, 빈 파일 | BE | 버림 | 옛 스키마 전용 |
| backend/agent.py | OpenAI Responses + Structured Outputs 추출·본문 생성·응답 검사·fact_id 부여 | AG | AG 판단 | BE-04 연결 형태: 입력 agent_input{schema_version, company_name_hint, source_units[]} → company_info, 예외 AgentError(code)/AgentInputError |
| prompts/extract.txt, draft.txt | 추출·본문 프롬프트 | AG | AG 판단 | 수정 금지 |
| contracts/profile.schema.json | 결과 JSON Schema v1.0(14필드·4상태·evidence·13섹션) | 공통 | 가져옴(보존, 수정 금지) | contracts.md Fact절 "기존 14개 필드 축소 금지, 변환표 작성" → D-05 원본 |
| contracts/contract.md, day2_addendum.md | 옛 규격 v1.0·보완안 | 공통 | 참고만 | contracts.md v1.1이 대체 |
| fixtures/mock_source_a.txt, mock_source_b.txt, day2_variant_source_a.txt, day2_variant_source_b.txt | 가짜 회사 자료 TXT | 공통 | 가져옴 | BE-02/03 시험 데이터. 실제 기업 자료 아님 |
| fixtures/*.json(6개), day2_테스트데이터_사용법.md | 옛 스키마 Mock·입력 예시 | 공통 | 참고만 | 새 Document/Preflight 예시 작성 시 값 참고 |
| handoff/* (5개) | 옛 API 실제 응답·전달 메모 | BE | 버림 | 새 예시 응답은 후속에서 생성 |
| docs/day1.md, day2.md | 날짜별 기록 | BE | 버림 | 옛 레포에 남김 |
| docs/backend_readme.md | 옛 설명·검증 명령 목록 | BE | 참고만 | 스크립트 색인 |
| docs/자료사용_허용기록.md | 실제 기업 자료 외부 LLM 입력 허용 = 미확인(R10) | 공통 | 참고만→정책 이관 요청 | 새 레포 문서에 이 상태가 없음 |
| scripts/check_backend.py, check_upload_storage.py, check_document_api.py, check_llm_mode.py, check_llm_pipeline.py, check_profile_builder.py, make_agent_input.py, test_validators.py, test_document.py | 백엔드·D 검증 스크립트 | BE | 참고만 | 옛 API 전용. TestClient 예외 주입·동시성 시험 패턴 참고 |
| scripts/test_agent.py, check_model_schema.py, check_company_info_rules.py, check_openai_extract.py, check_draft_rules.py, test_openai_connection.py | Agent 시험 | AG | AG 판단 | |
| validate_fixtures.py | 옛 fixture 검증 | 공통 | 버림 | |
| requirements.txt, requirements-dev.txt | openai 3.14.0, jsonschema 4.26.0, python-docx 1.2.0, httpx 0.28.1 등 | BE | 참고만 | 이관 시 `uv add`. fastapi·dotenv·httpx·multipart는 lock에 있음 |
| .env.example | ANTHROPIC_API_KEY, OPENAI_API_KEY, AGENT_MODE, OPENAI_MODEL, FRONTEND_DIR | BE | 참고만 | OPENAI_MODEL 추가 필요 |
| CLAUDE.md(옛), README.md, .gitignore | 파일 관리 규칙·안내 | 공통 | 참고만/버림 | 7절 "옮기기 전 3가지 확인"은 이관 PR에 적용 |

집계: 가져옴 6 · 참고만 22 · 버림 14 · AG 판단 9 · 정책 이관 검토 1.

**6.1-2 API 연결표** (contracts.md 4절 ↔ 옛 /api/profiles 3개). 대체 가능=옛 코드 위 어댑터 / 새로 / 계약 확인=원본 수정 검토 요청

| 새 API | 옛 대응 | 구분 | 비고 |
|---|---|---|---|
| POST /sessions | 없음 | 새로 | |
| GET /sessions/{sid} | GET job 봉투 패턴 | 새로 | |
| DELETE /sessions/{sid} | 없음 | 새로 | 옛 코드에 정리 로직 없음 |
| GET /sources | 없음 | 새로 | 등록 자료 저장소 필요 |
| POST /sessions/{sid}/sources | POST /api/profiles 업로드 부분 | 대체 가능 | 수신·크기 검사·S001 부여·저장·parsers 호출. 분석 자동 시작 제거, 응답 Source[] |
| GET /sessions/{sid}/sources | extraction.json 기록 | 대체 가능(부분) | |
| DELETE .../sources/{source_id} | 없음 | 새로 | |
| PATCH /sessions/{sid}/inputs | company_name_hint Form | 새로 + 계약 확인 ① | Brief에 company_name_hint 없음(Agent 프롬프트가 사용) |
| POST .../preflights | analyzing(extract_company_info) | 새로(함수 재사용 후보) | D-05 변환: 옛 status는 항목 단위, 새는 Fact 단위. not_found→missing, quote→excerpt, "N행"→locator{line_start,line_end}, segment_id·source_version 신규 |
| POST .../drafts | drafting(draft_profile+profile_builder) | 새로 | 13섹션 → Document(page/block) 변환 |
| GET .../documents/{did} | 없음 | 새로 | |
| PATCH .../documents/{did} | 없음 | 새로 | |
| proposals 생성·apply·reject | 없음 | 새로 | |
| restore | 없음 | 새로 | |
| issues/{iid}/resolve | needs_confirmation 질문 생성 | 새로(질문 규칙 참고) | |
| documents/{did}/validate | validating(validators) | 새로(대조 함수 재사용 후보) | 옛은 이진, 새는 Issue 목록·부분 재검증 |
| layout-checks | 없음 | 새로 | |
| approvals | 없음 | 새로 | |
| POST /sessions/{sid}/exports | POST /api/profiles/{job_id}/document | 대체 가능(동기→비동기) | render_document + _check_document_file 재사용 |
| GET .../jobs/{jid} | GET /api/profiles/{job_id} | 대체 가능 + 계약 확인 ② | 상태 변환 queued→queued, extracting/analyzing/drafting/validating→running, ready→succeeded, error→failed. "진행" 필드 형식 미정 |
| GET .../assets/{asset_id} | 없음 | 새로 | |
| GET .../exports/{eid}/download | document 응답(FileResponse) | 대체 가능(부분) | 승인·만료 재확인 추가 |

집계: 대체 가능 5 · 새로 19 · 계약 확인 4건.

오류 변환 (옛 {code,stage,message,retryable} → 새 {code,message,retryable,details,request_id}; stage→details.stage): UNSUPPORTED_FILE→UNSUPPORTED_FILE_TYPE · INPUT_TOO_LARGE→FILE_TOO_LARGE(글자수 초과는 계약 확인 ③) · NEEDS_TEXT_SOURCE→NO_USABLE_TEXT · JOB_NOT_FOUND→RESOURCE_NOT_FOUND · PERMISSION_REQUIRED→FORBIDDEN · LLM_TIMEOUT→SERVICE_TEMPORARY_FAILURE · INVALID_OUTPUT→대응 없음(계약 확인 ④: AI 결과 형식 오류 코드) · DOCUMENT_FAILED→EXPORT_FAILED · BUSY→불필요(Idempotency-Key로 대체).

**6.1-3 저장할 데이터 항목** (스키마 아님. 범위: 영구 / 세션=종료·만료 시 정리)

| 항목 | 핵심 필드 | 범위 | 옛 대응 |
|---|---|---|---|
| 접근 컨텍스트 | 소유자·보안 쿠키(세션 ID만으로 권한 판단 금지) | 영구(로그인 범위 미정) | 없음 |
| Session | session_id, status, input_revision, created/last_activity/expires_at, selected_source_ids | 세션 | `_jobs` dict |
| Brief | purpose, emphasis[], direction, target_pages, photo_preference (+company_name_hint 확인①) | 세션 | company_name_hint |
| Source 메타 | source_id, source_version, scope, session_id, name, mime_type, size_bytes, kind, parse_status, text/image_available, usable_segment_ids, warnings, expires_at, content_hash | registered=영구 / session=세션 | stored_files, extraction.json |
| Source 원본 바이트 | 서버 내부 저장명 | scope 따름 | private_runs/<job>/S001.txt |
| Segment | segment_id, source_id, source_version, locator, text | scope 따름 | source_units |
| Asset 메타+바이트 | asset_id, source_id/version, scope, session_id, origin, mime_type, width, height, content_hash, status, expires_at | scope 따름 | 없음 |
| Fact | fact_id, field_key, value, status, evidence_refs[], conditions, alternatives | 세션 | company_info.<key>.facts[] |
| EvidenceRef | source_id, source_version, segment_id, locator, excerpt | Fact/Block 안 | evidence{source_id, locator, quote} |
| Preflight | preflight_id, session_id, input_revision, usable_source_ids, facts, issues, recommendations, can_generate, confirmed_at | 세션 | company_info.json |
| Document(버전별 보존) | schema_version, document_id, session_id, document_revision, input_revision, title, target_pages, pages, status | 세션 | result.json(단일) |
| Proposal | proposal_id, document_id, base_document_revision, base_input_revision, target_block_ids, kind, changes, status | 세션 | 없음 |
| Issue | issue_id, scope, code, severity, status, message, source_ids, fact_ids, block_ids, resolution | 세션 | needs_confirmation[] |
| Validation | validation_id, document_id, document_revision, input_revision, status, issue_ids | 세션 | validation 플래그 |
| LayoutCheck | layout_check_id, document_revision, input_revision, format, template_version, render_options_hash, asset_manifest_hash, status, actual_pages, issue_ids | 세션 | 없음 |
| Approval | approval_id, document_id, document_revision, input_revision, format, validation_id, layout_check_id, template_version, render_options_hash, asset_manifest_hash, approved_at, approved_by, status | 세션 | 없음 |
| Export+파일 | export_id, approval_id, format, status, artifact_id, expires_at, error; 바이트 | 세션 | documents/*.docx |
| Job | job_id, session_id, kind, status, 진행 정보(확인②), result 참조, error, 시간 | 세션 | `_jobs[job_id]` |
| Idempotency 기록 | key + 요청자·세션·경로 + 본문 해시 + 최초 응답 | 세션 | 없음(BUSY) |
| 정리 재시도 목록 | 대상, 실패 사유, 횟수 | 영구(운영) | 없음 |
| 실행 기록(로그성) | 모드, LLM 호출 여부, 토큰 수, 오류 코드, 시간(원문·프롬프트 제외) | 로그 | run_meta.json |
| LangGraph 체크포인트 | thread_id ↔ session/job, 저장 위치 | 세션(AG-03 협의) | 없음 |

BE-02에서 먼저 필요한 것: 접근 컨텍스트, Session, Brief, Source 메타·바이트, Segment, Job, Idempotency 기록.

**6.1-4 BE-01 완료 조건 처리 결과**

- 프론트 예시 응답: `handoff/api_examples_v1.1.json`(2026-09-25). 손으로 쓴 계약 예시 22개이며 실제 서버 응답이 아니다. 미결 사항은 파일 안 `pending_decisions`·`_note`에 표시. 실제 구현에서 달라지면 이 파일을 먼저 갱신한다.
- 옛 코드 새 환경 실행 확인: 미실행(uv, Python 3.13). BE-01 완료 조건이 아니므로 이관 PR(parsers.py 등)에서 확인한다.

**6.1-5 상대에게 요청**

- Agent (AG-01): (a) AG 판단 9개 파일(agent.py, prompts 2, 스크립트 6)의 이관 여부 결정 (b) validators.py의 근거-원문 대조를 백엔드가 가져가도 되는지(AG-07 경계) (c) 계약 확인 ① Brief의 company_name_hint (d) 계약 확인 ④ AI 결과 형식 오류 코드 (e) D-05 변환표 공동 작성(옛 항목 단위 status → Fact 단위) (f) .env.example에 OPENAI_MODEL 추가 시점.
- 팀(계약 원본): 계약 확인 ② Job 진행 필드 형식, ③ 추출 글자수 초과 코드, ⑤ Preflight 단독 조회 경로(`GET /sessions/{sid}/preflights/{pid}`)가 4절 표에 없음 — Job result_ref만으로 도달할지 경로를 추가할지. `docs/자료사용_허용기록.md`(R10 미확인)를 새 레포 어느 문서에 둘지.

## 7. 첫 요청

```text
이번 역할은 백엔드 담당이고 작업은 BE-01야.
AGENTS.md와 이 task 파일의 읽기 순서를 따라 관련 문서를 확인해줘.
내 작업 범위에서 현재 코드·기존 변경을 확인하고 구현·검증해줘.
다른 담당자에게 필요한 결과는 요청 항목으로 남겨줘.
작업 상태와 실제 결과는 이 파일에만 기록하고 다른 담당의 작업을 대신 완료 처리하지 마.
```
