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
| BE-02 | BE-01 | 세션 생성·소유·만료, 첨부 임시 저장과 제한 설정. D-01/D-02 기록. 서로 다른 두 세션의 자료 접근 차단 확인. | DONE (2026-09-25, 6.2절 · 파일 읽기는 BE-03) |
| BE-03 | BE-02 | 형식별 읽기·구간 위치·complete/partial/failed·사진 asset 제공. 손상/암호/스캔 파일 점검, 지원표 기록. 불확실 수치를 완료로 표시하지 않음. | DONE (2026-09-25, 6.3절 · OCR 없음, 문서 안 그림 추출 없음) |
| BE-04 | BE-01, BE-02 | AI 작업 실행/상태/사용자 응답 API와 내부 함수 연결 지점 구현. 먼저 계약 예시로 확인하고 AG-03에서 실제 연결. 오래된 입력·중복 재개·만료 요청 처리. | DONE (2026-09-25, 6.4절 · mock으로만 확인, 실제 AI 연결은 AG-03) |
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
| QA-02 | 세션 A/B 분리 | 백엔드 | 다른 세션 자료·사진·검색 접근 불가 | 부분 PASS (2026-09-25) — `tests/test_be02.py::test_other_owner_cannot_see_session`: 다른 소유자가 세션·자료 목록·작업 조회·삭제 시 404, 쿠키 없으면 401. 사진(asset)·검색은 아직 없어 미검증(BE-03 이후 재실행) |
| QA-03 | 미지원·손상·암호 파일 | 백엔드 | 원인·보완·제외, 다른 자료 보존 | PASS (2026-09-25) — `tests/test_be03.py`: 미지원 .hwp 415, 암호 PDF failed+ENCRYPTED, 손상 DOCX/PDF/PNG failed+FILE_CORRUPT, 확장자-내용 불일치 failed, 빈 TXT failed+NO_USABLE_TEXT. 같은 업로드의 다른 파일은 정상 읽힘(`test_failed_file_does_not_block_others`). 각 경고에 action(보완·제외 안내) 포함 |
| QA-04 | 불확실한 표·수치 | 백엔드 + Agent | partial, 확인 구간만 사용 | 부분 PASS (2026-09-25, 백엔드 몫) — 스캔 PDF·글자 없는 쪽은 partial+IMAGE_ONLY, usable_segment_ids에 글자 있는 구간만. 표 셀은 locator로 위치 보존. "수치가 불확실하다"는 판단은 Agent(AG-02) 몫 → 미실행 |
| QA-07 | 자료/목적 변경 후 옛 점검 사용 | 백엔드 | 이전 확인 무효화 | PASS (2026-09-25) — `tests/test_be04.py`: 입력 변경 후 옛 preflight로 drafts 요청 → 409 INPUT_REVISION_CONFLICT(`test_draft_requires_confirmation_and_matching_revision`); 점검 Job 도중 입력이 바뀌면 결과 폐기·failed(`test_preflight_job_fails_if_input_changed_while_running`) |
| QA-11 | 오래된 수정안·중복 재개 | 백엔드 | 충돌 처리, 중복 반영 없음 | 부분 PASS (2026-09-25) — 같은 입력 버전의 진행 중 preflight/draft Job은 새로 만들지 않고 같은 Job 반환, Idempotency-Key 재전송은 최초 응답. 수정안(Proposal) 부분은 BE-05 후 |
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
| 2026-09-25 | BE-02 | 브랜치 feat/be-02-session · app/(신규 15파일), tests/test_be02.py, main.py, pyproject.toml(pytest dev), .env.example, plan.md 4절 | `uv run pytest` 17/17 PASS. 실서버(uvicorn main:app)에서 curl로 세션 생성→쿠키 조회 200→쿠키 없이 401→TXT 업로드 202→삭제 200 확인. 상세 6.2절 | 프론트: API 6개 실제 동작(6.2-1), 쿠키 필요(credentials 포함 호출). Agent: 없음. 팀: 계약 확인 ⑥⑦(6.2-3) |
| 2026-09-25 | BE-03 | 브랜치 feat/be-03-parsers · app/parsers/(신규 5), app/services/reading.py·assets.py, app/routers/assets.py, app/db.py(v2), tests/test_be03.py, tests/fixtures/(옛 가짜 TXT 4), plan.md D-01 | `uv run pytest` 36/36. 실서버 가짜 PPTX 20슬라이드→segment 44, 가짜 스캔 PDF→partial+IMAGE_ONLY. BE-02 리뷰 3건 반영(6.3-0). 상세 6.3절 | 프론트: Source.asset_ids·warnings 형식, 읽기 결과는 폴링 후 GET sources. Agent(AG-02): 구간 입력 형태(6.3-5). 팀: 계약 확인 ⑨⑩⑪ |
| 2026-09-25 | BE-04 | 브랜치 feat/be-04-ai-jobs · app/agent_bridge.py·agent_mock.py(신규), services/{preflights,documents,ai_jobs}.py, routers/{preflights,drafts,documents}.py, db.py(v3), tests/test_be04.py | `uv run pytest` 51/51. 실서버(가짜 자료): 업로드→선택→사전 점검 Job→확인→초안 Job→Document rev.1→세션 요약. 개발 DB v2→v3 마이그레이션 확인. 상세 6.4절 | **Agent에게 전달**: 6.4-3 함수 서명(app/agent_bridge.py) — AG-03에서 같은 서명으로 llm 구현. 프론트: preflights/drafts/documents API 실제 동작(mock). 팀: 계약 확인 ⑫⑬ |

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

### 6.2 BE-02 구현 결과 (2026-09-25)

**6.2-1 실제 동작하는 API** (`/api/v1`, 소유자 쿠키 `ddalgi_owner` 필요 — 프론트는 `credentials: 'include'`로 호출)

| API | 동작 | 비고 |
|---|---|---|
| POST /sessions | Session 생성(201), 첫 호출에 HttpOnly 쿠키 발급 | Idempotency-Key 지원 |
| GET /sessions/{sid} | 조회. 활동으로 치지 않음(만료 연장 없음) | document_summary는 BE-05까지 null |
| DELETE /sessions/{sid} | closed + `private_runs/<sid>/` 삭제. 멱등 | 삭제 실패 시 `cleanup: pending` |
| PATCH /sessions/{sid}/inputs | expected_input_revision 검사(409), brief·selected_source_ids 갱신, input_revision+1 | 세션에 없는 source_id → 404 |
| POST /sessions/{sid}/sources | multipart `files`(+`kind`), 검사 후 저장, Job(kind=read, queued) 발급(202) | **읽기는 하지 않음** → parse_status=queued 고정(BE-03) |
| GET /sessions/{sid}/sources | 세션 첨부 목록 | usable_segment_ids·warnings는 BE-03까지 빈 값 |
| DELETE /sessions/{sid}/sources/{source_id}?expected_input_revision= | 세션 첨부 삭제. 선택 중이던 자료면 input_revision+1 | |
| GET /sessions/{sid}/jobs/{jid} | 작업 조회. 활동으로 치지 않음 | progress 형식은 계약 확인 ② 대기 |

접근 규칙: 쿠키 없음 → 401 UNAUTHORIZED / 남의 세션·없는 자원 → 404 RESOURCE_NOT_FOUND(존재 여부 숨김) / 닫힘·만료 → 410 SESSION_EXPIRED(details.status로 구분).

**6.2-2 코드 위치** — `app/config.py`(설정·D-01/D-02 값), `app/db.py`(SQLite 4테이블), `app/models.py`(Pydantic, contracts.md 필드명), `app/errors.py`(오류 봉투·request_id), `app/access.py`(소유자 쿠키), `app/services/{sessions,sources,jobs,idempotency}.py`, `app/routers/{sessions,sources,jobs}.py`, `tests/test_be02.py`(17개). 실행 `uv run uvicorn main:app --host 127.0.0.1 --port 8000`, 시험 `uv run pytest`.

**6.2-3 계약 확인 추가 요청** — ⑥ 요청 형식 오류(Pydantic 검증 실패)에 쓸 코드가 4절 표에 없어 `400 INVALID_REQUEST`로 임시 배정. ⑦ 같은 Idempotency-Key에 다른 본문일 때 "409"만 있고 코드가 없어 `IDEMPOTENCY_KEY_CONFLICT`로 임시 배정. ⑧ 세션당 파일 개수 초과를 `413 FILE_TOO_LARGE`로 냈는데 별도 코드가 나은지.

**6.2-4 하지 않은 것** — 파일 내용 읽기·Segment(BE-03), 등록 자료 `GET /sources`(적재 방식 미정), 로그인(익명 브라우저 소유자), 만료 세션의 배경 정리 작업(만료는 접근 시점에 판정만 함; 정리 스케줄은 BE-09), 브라우저 실제 클릭 확인(프론트 FE 연동 시).

### 6.3 BE-03 구현 결과 (2026-09-25)

**6.3-0 BE-02 리뷰 반영** — (1) `sources.session_id` nullable + `CHECK(scope='session' ⇔ session_id NOT NULL)`. 등록 자료 테이블을 따로 두지 않음: contracts.md의 Source는 scope로 구분되는 한 객체라 응답 모델·segments/assets FK가 하나면 되고, 분리하면 파서·조회 코드가 두 테이블을 다뤄야 함. (2) `stored_path`는 `private_runs` 기준 상대경로(`<session_id>/<source_id>.ext`). (3) `PRAGMA user_version` 마이그레이션 v1→v2: sources 재생성·행 복사·절대경로→상대경로 변환(`test_v1_db_migrates_to_v2_keeping_rows`).

**6.3-1 지원표** (D-01)

| 형식 | 파서 | segment 단위 · locator | 특수 처리 |
|---|---|---|---|
| TXT/MD | 옛 parsers.py 이관 | 행 `{"line_start":n,"line_end":n}` (빈 행은 건너뛰되 번호 유지, MD 기호 보존) | UTF-8 strict 실패→failed UNSUPPORTED_ENCODING, 빈 파일→failed NO_USABLE_TEXT |
| PDF | pypdf | 페이지 `{"page":n}` | 암호→failed ENCRYPTED. **글자 0자 쪽→IMAGE_ONLY 경고(쪽 목록), 전체가 0자면 partial**(실패 아님). 일부만 0자→partial |
| DOCX | python-docx | 문단 `{"paragraph":n}`, 표 셀 `{"table":t,"row":r,"col":c}` (병합 셀 1회) | 안 열림(손상·암호)→failed FILE_CORRUPT. 글자 없음: 그림 있으면 partial+IMAGE_ONLY, 없으면 failed |
| PPTX | python-pptx | 텍스트 상자 `{"slide":s,"shape":n}`, 표 셀 `{…,"row":r,"col":c}`, 그룹 안 `{…,"child":m}` | 이미지만 있는 슬라이드→IMAGE_ONLY(슬라이드 목록)+partial. 노트는 읽지 않음 |
| JPG/PNG | Pillow | segment 없음. Asset 1개(width/height/hash, status=ready) | complete지만 text_available=false. 확장자-실제 형식 불일치→failed |
| 공통 | — | 자료당 100,000자 초과→뒤 구간 버리고 partial+TEXT_LIMIT(자르지 않고 표시) | 파서 예외→failed PARSE_ERROR, 다른 파일은 계속 |

**6.3-2 동작** — 업로드 202 후 FastAPI BackgroundTasks로 읽기 Job(kind=read) 실행. 진행 `{"stage":"reading","message":"i/n"}` → `succeeded` + `result_ref {type:"sources", source_ids}`. 파일별 결과는 `GET sources`의 parse_status/warnings/usable_segment_ids/asset_ids. 서버 재시작 시 남은 queued/running Job은 failed(SERVICE_TEMPORARY_FAILURE), 읽던 자료는 failed+INTERRUPTED. `GET /sessions/{sid}/assets/{asset_id}`로 이미지 바이트(소유·세션 확인, `Cache-Control: private, no-store`). 자료 삭제·세션 종료 시 segments 삭제, assets deleted_at.

**6.3-3 확인** — `uv run pytest` 36/36 PASS(BE-03 19개: 형식 7종, 스캔 partial, 암호·손상·빈 파일, locator, 글자 상한, 실패 파일 격리, asset 조회·타 소유자 404·삭제 후 404, 재시작 정리, 마이그레이션). 실서버(uvicorn): 가짜 20슬라이드 PPTX → 202 queued → 폴링 succeeded → segment 44개(상자 40+표 셀 4), 가짜 스캔 PDF → partial+IMAGE_ONLY.

**실제 파일 확인 (2026-09-25, 로컬만·레포 미포함)** — 회사소개서 PPTX(20260921, 9.9MB) 실서버 업로드 → 2.4초 succeeded → complete, 20/20 슬라이드에서 세그먼트 163개(표 셀 52, 그룹 도형 16), 경고 없음. 카탈로그 PDF: 원본(282MB, 8쪽, 폰트 없음·추출 글자 0)에 파서 직접 실행 → partial, IMAGE_ONLY pages 1~8. 원본은 D-01 10MB 초과라 서버 업로드 불가 → 쪽별 스캔 이미지를 축소해 이미지 전용 PDF(0.54MB, 8쪽)로 다시 묶은 축소본을 실서버 업로드 → 0.4초 succeeded → partial, text_available=false, IMAGE_ONLY pages 1~8, 메시지 "이미지만 있음, 글자를 읽지 못함". (Drive의 "축소본 1.4MB"는 존재하지 않아 로컬에서 새로 만듦.) 확인 후 세션 DELETE로 private_runs 사본 삭제.

**6.3-4 하지 않은 것** — OCR(prd 2절 MVP 제외), PDF/DOCX/PPTX 안 그림 추출(다음 단계), 등록 자료 `GET /sources`·적재(요청 항목), Segment 조회 API(계약에 없음, Agent는 `services/sources.segments_for_source`로 내부 사용), PPTX 노트.

**6.3-5 요청** — 팀(계약): ⑨ `Source.asset_ids` 필드 추가 제안(화면이 자료→이미지를 잇는 방법이 계약에 없음) ⑩ 프론트가 구간 텍스트를 볼 API가 필요한지(자료 분석 전 "읽을 수 있는 구간·출처 위치" 표시용) ⑪ 등록 자료 `GET /sources` 적재 방식(누가·어떻게 등록하는지). Agent(AG-02): 구간 입력 형태는 `{segment_id, source_id, source_version, locator, text}` — 옛 `source_units{source_id, locator "N행", text}`와 다르므로 어댑터 필요.

### 6.4 BE-04 구현 결과 (2026-09-25)

**6.4-1 실제 동작하는 API** (mock Agent 기준, `/api/v1`)

| API | 동작 | 검사·오류 |
|---|---|---|
| POST /sessions/{sid}/preflights | 202 Job(kind=preflight) → 백그라운드 분석 → Preflight 저장. 같은 입력 버전의 진행 중 Job이 있으면 그 Job 반환 | expected_input_revision ≠ 현재 → 409 INPUT_REVISION_CONFLICT. Job 도중 입력이 바뀌면 결과 폐기·failed |
| GET /sessions/{sid}/preflights/{pid} | Preflight(facts 14·issues·recommendations·can_generate·confirmed_at) | 계약 확인 ⑤ 백엔드 제안 경로 |
| POST /sessions/{sid}/drafts | preflight_id·input_revision·confirmed 검사 → confirmed_at 기록 → 202 Job(kind=draft) → Document rev.1 | confirmed=false 422 PREFLIGHT_NOT_CONFIRMED · can_generate=false 422 NO_USABLE_TEXT · 버전 불일치 409 · 문서 이미 있음 409 DOCUMENT_EXISTS(⑫) |
| GET /sessions/{sid}/documents/{did} | 현재 버전 Document + validation/approval(BE-06까지 null) | |
| GET /sessions/{sid} | document_summary 채움 | |

can_generate = 텍스트 근거가 있는 선택 자료 ≥ 1(사용자 확인은 별도). Document.status는 사전 점검에 open blocker가 있으면 review_required, 없으면 draft(검증 BE-06 전 임시 규칙).

**6.4-2 AI 결과 서버 검사** (AGENTS.md 4절) — segment_id·source_version이 선택 자료에 실제로 있는지, image 블록의 asset_id가 선택 자료 것인지, 블록 fact_ids가 이번 Preflight에 있는지, missing 사실에 값·근거가 없는지, 근거 없는 문단이 없는지(허용 문구 "추가 확인 필요"/"자료에서 확인되지 않음" 제외). 하나라도 어기면 저장하지 않고 Job failed AGENT_OUTPUT_INVALID(⑬). 자동 보정 없음. AGENT_MODE=llm인데 구현이 없으면 failed SERVICE_TEMPORARY_FAILURE — mock으로 대체하지 않음.

**6.4-3 Agent에게 전달 — 연결 지점 함수 서명** (`app/agent_bridge.py`, AG-03에서 같은 서명으로 `app/agent_llm.py`의 `create_bridge(settings) -> AgentBridge` 구현)

```python
class AgentBridge(Protocol):
    def analyze(self, request: AnalyzeRequest) -> AnalyzeResult | Awaitable[AnalyzeResult]   # 역할 ① 자료 분석·기획
    def draft(self, request: DraftRequest) -> DraftResult | Awaitable[DraftResult]           # 역할 ② 초안 작성

AnalyzeRequest(session_id, input_revision, brief: Brief, sources: list[SourceIn])
SourceIn(source_id, source_version, kind, name, parse_status, segments: list[SegmentIn], asset_ids: list[str])
SegmentIn(segment_id, locator: dict, text)                   # 글자를 읽은 구간. 이미지 자료는 segments=[]
AnalyzeResult(facts: list[Fact], issues: list[Issue], recommendations: Recommendations)   # contracts.md 2절 모델
DraftRequest(session_id, input_revision, brief, sources, preflight: PreflightOut)          # 사용자가 확인한 점검
DraftResult(title: str, pages: list[Page])                   # Block에 fact_ids·evidence_refs, image는 asset_id
AgentError(code, message, retryable)                         # 실패 통지. code는 contracts.md 오류 코드
```
- 동기·async 구현 모두 가능(실행기가 awaitable이면 이벤트 루프에서 돌림). 백그라운드 스레드에서 호출되므로 자체 루프를 만들지 않아도 됨.
- 결과에는 서버가 넘긴 segment_id·asset_id·fact_id만 쓸 수 있다(6.4-2 검사). 요청 밖 자료·URL·경로를 만들지 않는다.
- mock(`app/agent_mock.py`)은 "라벨: 값" 패턴만 읽는 가짜 구현. `field_key`는 옛 14개 키 이름을 그대로 써서 D-05 변환표에 맞추기 쉽게 함. 고정 문구는 전부 가짜("예시 회사"), 실제 회사 정보 없음(`test_mock_never_contains_real_company_terms`).
- task_agent.md는 백엔드가 수정하지 않았다. 이 절을 Agent 담당에게 알린다.

**6.4-4 확인** — `uv run pytest` 51/51(BE-04 15개: 사실/문제/can_generate, 충돌·필수 누락, 텍스트 없음 422, 오래된 입력 409, 중복 Job 반환, 실행 중 입력 변경 폐기, 없는 segment/asset 거부, AgentError 매핑, llm 미연결 failed, 동기 구현 호환, 미확인 422, 문서 rev.1·근거·asset·요약, blocker→review_required, 타 소유자 404, mock 실제 정보 없음). 실서버(가짜 자료) 전체 흐름 성공, 개발 DB v2→v3 마이그레이션 확인.

**6.4-5 하지 않은 것** — 실제 LLM 호출·LangGraph·waiting_user 재개(AG-03), D-05 변환표(Agent 공동), 문서 편집·버전 증가·Proposal(BE-05), Validation/Issue 해결(BE-06), 사전 점검 재실행 시 기존 문서 영향 검사(계약 3절 "새 자료 추가 시 덮어쓰지 않음" — BE-05/06).

**6.4-6 요청** — 팀(계약): ⑫ 세션에 문서가 이미 있을 때 drafts 재요청 처리(현재 409 DOCUMENT_EXISTS 임시 코드; 계약 3절 "전체 재생성으로 덮어쓰지 않음"과 맞춤) ⑬ AI 결과 검사 실패 코드(AGENT_OUTPUT_INVALID 임시, 계약 확인 ④와 같은 건). 프론트: `handoff/api_examples_v1.1.json`의 문서 조회 예시에 있는 validation 객체는 BE-06까지 null.

## 7. 첫 요청

```text
이번 역할은 백엔드 담당이고 작업은 BE-01야.
AGENTS.md와 이 task 파일의 읽기 순서를 따라 관련 문서를 확인해줘.
내 작업 범위에서 현재 코드·기존 변경을 확인하고 구현·검증해줘.
다른 담당자에게 필요한 결과는 요청 항목으로 남겨줘.
작업 상태와 실제 결과는 이 파일에만 기록하고 다른 담당의 작업을 대신 완료 처리하지 마.
```
