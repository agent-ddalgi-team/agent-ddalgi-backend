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
| BE-05 | BE-01, BE-02 | 문서/페이지/블록 저장·버전·수정안 적용·취소·복원 API. 출처 ID 유지, 예상 버전 검사, 중복 적용은 1회만, 트랜잭션 실패 시 원본 보존. | DONE (2026-09-25, 6.5절 · Proposal은 mock, 검증은 BE-06 미연결) |
| BE-06 | BE-05, AG-07 | 문제 해결 상태·검증 버전·승인 조건 강제. 부분 검증에서 기존 문제 유지, 필수 누락 제외 불가. 배치 검사는 BE-08 연결 전 계약 예시로만 확인했다고 표시. | DONE (2026-09-26, 6.7절 · AI 검증은 mock, **배치 검사는 BE-08 연결 전 계약 예시로만 확인**) |
| BE-07 | BE-01, AG-04 | PDF/DOCX 생성 도구·템플릿 검증 후 D-03/D-06 기록. 한글·사진·1쪽/다쪽·DOCX 본문 편집성을 실제 파일로 비교. 특정 도구를 필수로 전제하지 않음. | DONE (2026-09-26, 6.8절 · 렌더 어댑터·템플릿 v0·D-03/D-06 기록. AG-04 미완이라 mock 초안·fixture 문서로 검증. LayoutCheck Job·Export API·다운로드·승인 연결은 BE-08) |
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
| QA-11 | 오래된 수정안·중복 재개 | 백엔드 | 충돌 처리, 중복 반영 없음 | PASS (2026-09-25) — `tests/test_be05.py`: 문서가 바뀐 뒤 옛 편집안 apply → 409 PROPOSAL_STALE + DB status=stale 유지(`test_stale_proposal_rejected_and_stays_stale`, `test_stale_by_input_change_set_during_apply`); 같은 키 재전송은 최초 응답·revision 재증가 없음, 키 없이 재전송 409, 스레드 3개 동시 apply는 1승 2패(`test_concurrent_apply_only_one_wins`); 진행 중 Job 재사용은 BE-04 |
| QA-14 | 순서 변경과 사실 문장 변경 | 백엔드 + Agent | 검사 범위 구분, 둘 다 승인 무효화 | PASS (2026-09-26, 백엔드 몫·mock Agent) — 순서만 변경: 블록 지문 집합이 같아 Agent 호출 생략(`agent_called=false`, 호출 카운터로 확인)·기존 blocker 유지·재사용 기록. 문장 변경: 바뀐 블록만 Agent 검사, 나머지 재사용. 둘 다 Approval invalidated(`test_approval_invalidated_after_changes[patch,move]`). 실제 AI 의미 검증의 재사용 판단은 AG-07 |
| QA-16 | 승인 API 우회 | 백엔드 | 미해결 필수/배치 문제 시 서버 차단 | PASS (2026-09-26, 배치는 테스트 행 기준) — `tests/test_be06.py::test_approval_conditions_each_fail` 12경로(버전·입력·타 문서/옛 검증·blocker·필수 결핍·배치 없음/형식/해시/실패/옛 버전·미확인) + mock 문서 MOCK_VALUE 차단. 실서버: 정상 문서도 LayoutCheck 행이 없어 422 LAYOUT_NOT_READY(BE-08 전 정상 차단). 실제 배치 검사 실행은 미연결 |
| QA-17 | 승인 후 변경 | 백엔드 | 옛 승인으로 최신본 출력 불가 | 부분 PASS (2026-09-26) — PATCH·순서 변경·편집안 적용·restore·입력 변경 5경로에서 Approval invalidated, 문서 조회·summary에서 승인 사라짐, 멱등 재전송이 되살리지 못함(`test_approval_invalidated_after_changes`). "옛 승인으로 출력 불가"의 출력 API 부분은 BE-08 |
| QA-18 | PDF/DOCX 파일 열기 | 백엔드 + 프론트 | 유효한 파일·한글·사진, DOCX 본문 편집 가능 | 부분 PASS (2026-09-26, 백엔드 몫·가상 문서 4종) — PDF: pypdf 쪽수·텍스트 추출·폰트(Pretendard 서브셋 임베드) + pypdfium2 PNG 전 페이지 육안(한글·가로/세로/배너 사진·깨진 이미지 상자·사진 자리 상자·넘침 흐름). DOCX: python-docx 재열기 구조 + 로컬 Word 16.0.20326 COM 실측(쪽수 1/10/7/4, 보호 없음, 텍스트·그림 삽입 가능) + Word SaveAs(PDF)→PNG 육안(구조 동일, Pretendard 미설치라 한글 바탕·라틴 Cambria로 대체 표시). 실제 다운로드 경로·프론트 몫은 BE-08/BE-10 |
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
| 2026-09-25 | BE-05 | 브랜치 feat/be-05-document-edit · services/{doc_ops,proposals,refs}.py, routers/proposals.py(신규), routers/documents.py(PATCH·restore·proposals), db.py(v4), agent_bridge.py(propose)·agent_mock.py, tests/test_be05.py | `uv run pytest` 92/92(BE-05 41개). 실서버(가짜 자료): 초안 rev1 → PATCH rev2 → Proposal(text) → apply rev3 → 중복 apply 409 → restore rev4. 개발 DB v3→v4 마이그레이션 확인. 상세 6.5절 | **Agent에게 전달**: 6.5-3 `propose()` 규격. 프론트: 편집 8종·Proposal·restore API(6.5-1), validation은 계속 null. 팀: 계약 확인 ⑭~⑳ |
| 2026-09-26 | 등록 자료 적재(BE-02 후속) | 브랜치 feat/registered-import · scripts/import_registered.py, app/services/{registered,locators}.py, app/routers/registered_sources.py(신규), db.py(v5), tests/test_registered_import.py, tests/fixtures/ddalgi_mock_bundle_v1(가상 묶음, 한 겹으로 정리)·mock_upload_pdf(업로드 시연 PDF) | `uv run pytest` 118/118(신규 26). CLI: mock 묶음 --with-mock 없이 0건, 있으면 자료 8·구간 24·이미지 6(기대값 일치), 재적재 0건, dry-run 무기록. 실서버 GET /sources 8건. **실제 적재: 미확인(파일 미배치)**. 상세 6.6절 | 프론트: GET /sources + SourceOut 추가 필드(㉑). 팀: 계약 확인 ⑪ 제안값 기록, ㉑. Agent: 등록 구간 text에 [MOCK] 라벨 유지 |
| 2026-09-26 | BE-06 | 브랜치 feat/be-06-validation-approval · services/{validation,issues,approvals,layout_checks}.py, routers/{validations,issues,approvals}.py(신규), db.py(v6), agent_bridge.py(validate)·agent_mock.py, ai_jobs.py, documents.py(status 계산), sessions.py(입력 변경 시 승인 무효화), tests/test_be06.py, .gitattributes | `uv run pytest` 165/165(신규 46 = 40 + Codex 리뷰 회귀 6). 실서버(가짜 자료): validate→Job→issues→resolve→approvals — 정상 문서는 passed·ready_for_approval 후 **422 LAYOUT_NOT_READY(BE-08 전 정상 차단)**, mock 등록 문서는 MOCK_VALUE 12건·failed·acknowledge 거부·422 VALIDATION_NOT_PASSED. 개발 DB v5→v6, 배치·승인 행 심지 않음. 상세 6.7절 | **Agent에게 전달**: 6.7-4 `validate()` 규격. 프론트: validate/issues/resolve/approvals API·Document.status 계산 규칙. 팀: 계약 확인 ㉒~㉛ |
| 2026-09-26 | BE-07 | 브랜치 feat/be-07-export-tools · services/export_render.py, templates/export/company_intro_v0.html, templates/fonts/(Pretendard v1.3.9 TTF 2·OFL·SOURCE.md)(신규), services/layout_checks.py(㉖ 확정·해시 공유), config.py(EXPORT_BROWSER_PATH·EXPORT_RENDER_TIMEOUT_S), tests/test_be07.py, scripts/experiments/be07_render_candidates.py, pyproject.toml(jinja2 명시·pypdfium2 dev)·uv.lock, README, .env.example, .gitattributes, plan.md(D-03/D-06·비교표), task.md | `uv run pytest` 196/196(신규 31). 후보 비교: Chrome CLI·Playwright·reportlab 실제 생성, LibreOffice·번들 Chromium 미실행. 가상 문서 4종 PDF/DOCX 생성 + PNG 육안 + 로컬 Word 실측(쪽수·편집성·글꼴 대체·PDF 변환). 실서버 API 연결 없음(BE-08). 상세 6.8절 | 프론트(FE-07): D-06 협의 항목 6건(plan.md). Agent(AG-04): 초안에 image_placeholder가 남으면 배치 검사 finding·승인 불가(㉚·㉜). 팀: 계약 확인 ㉖ 확정값, ㉜~㉞ |

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

### 6.5 BE-05 구현 결과 (2026-09-25)

**6.5-1 실제 동작하는 API** (`/api/v1`, 소유자 쿠키. 멱등 재전송도 소유자·세션·문서 접근 검사를 먼저 통과해야 최초 응답을 돌려줌 — 기존 preflights/inputs 라우트도 같은 순서로 수정)

| API | 동작 | 검사·오류 |
|---|---|---|
| PATCH /documents/{did} | operations 8종을 **하나의 트랜잭션**으로 적용 → 새 revision(origin=user_edit). 하나라도 실패하면 무변경 | expected_revision 불일치 409 DOCUMENT_REVISION_CONFLICT · 문서 input_revision < 세션 409 INPUT_REVISION_CONFLICT · 연산 실패 422 INVALID_OPERATION(⑭, details.index/op/reason) · 삽입 블록의 segment/asset/fact가 세션에 없음 422 |
| POST /documents/{did}/proposals | 202 Job(kind=propose) → Agent `propose()` → Proposal 저장(proposed). **문서 불변** | 대상 블록 없음 422 · Job 도중 문서/입력이 바뀌면 `stale`로 저장(Job은 succeeded, result_ref.status) |
| GET /proposals/{pid} | Proposal(changes·rationale·candidates·status·applied_revision) | 계약에 없는 경로(⑯) |
| POST /proposals/{pid}/apply | `BEGIN IMMEDIATE` 한 트랜잭션: 상태 확인 → 기준 버전 확인 → 연산 적용 → 새 revision(origin=proposal_apply, source_ref=pid) → applied → 멱등 응답 저장 | 기준이 바뀜 → **stale로 저장·커밋한 뒤** 409 PROPOSAL_STALE · applied/rejected/stale 409 PROPOSAL_STALE(details.status ⑱) · 후보 미선택 422 CANDIDATE_REQUIRED · 동시 apply는 1건만 성공 |
| POST /proposals/{pid}/reject | rejected, 문서 무변화. 멱등 | applied면 409 |
| POST /documents/{did}/restore | 과거 revision 내용 → 새 revision(origin=restore, source_ref=원본 번호). 승인·검증 상태 부활 없음(status는 draft/review_required만), input_revision은 현재 세션 값 | 없는 버전 404 · 현재 버전 복원 422 · 참조 자료가 지금 없음 422 RESTORE_REFERENCE_INVALID(⑲) |

연산 규칙(`app/services/doc_ops.py`, 순수 함수·DB 무관): 없는 대상·자기 뒤 이동·다른 페이지의 after_block_id·중복 ID·type별 content 모양 위반·마지막 페이지 삭제 거부. `replace_block_content`는 content만 바꾸고 `block_id·type·fact_ids·evidence_refs` 보존. 편집 뒤 status: review_required 유지, 그 외 draft(BE-06 전 임시). 모든 새 revision에서 `documents.on_revision_created` 훅이 그 이전 기준의 proposed 편집안을 stale로 — **Approval 무효화는 BE-06에서 이 훅에 붙임**.

**6.5-2 DB v4** — `proposals` 테이블, `document_revisions.origin/source_ref`. `documents`/`document_revisions` 구조는 BE-04 그대로(새 revision = 행 추가 + `current_revision`을 `WHERE current_revision=expected`로 갱신 → 낙관적 잠금).

**6.5-3 Agent에게 전달 — `propose()` 규격** (`app/agent_bridge.py`, analyze/draft와 같은 방식·sync/async 호환·AGENT_MODE=llm 미구현 시 Job failed)

```python
def propose(self, request: ProposeRequest) -> ProposeResult | Awaitable[ProposeResult]

ProposeRequest(session_id, input_revision, brief, sources: list[SourceIn],
               document: Document,            # 기준 문서(현재 revision)
               target_block_ids: list[str],   # 사용자가 선택한 영역
               instruction: str, kind: "text"|"structure"|"image")
ProposeResult(changes: list[Operation],       # contracts.md 연산 8종. 대상 블록 안에서만(삽입은 대상 바로 뒤·같은 페이지)
              rationale: str,                 # 사람이 읽을 이유. 근거 없는 새 주장 금지
              candidates: list[Candidate] | None)  # kind=image: Candidate(candidate_id, label, changes). 선택 전 문서 불변
```
- 서버 검사(`ai_jobs.validate_propose_ops`): 선택 영역 밖 블록을 건드리면 거부, kind=text/image에서 페이지 연산 거부, dry-run 적용 통과·참조(segment/asset/fact) 세션 내 존재. 위반 시 저장하지 않고 Job failed AGENT_OUTPUT_INVALID.
- 지원하지 않는 요청은 **빈 changes로 성공 처리하지 말고** `AgentError(code)`: mock은 structure → UNSUPPORTED_PROPOSAL, text+비텍스트 블록 → UNSUPPORTED_PROPOSAL, image+asset 없음 → NO_IMAGE_CANDIDATES(⑳).
- mock(`app/agent_mock.py`)의 text: 대상 블록마다 replace_block_content로 "(정리) "+공백 정돈, 60자 초과 축약 표시. image: 세션 asset마다 후보 1개(placeholder는 delete+insert(image), image는 replace). 실제 회사 단어 없음(테스트로 고정).
- task_agent.md는 수정하지 않았다.

**6.5-4 확인** — `uv run pytest` 92/92(BE-05 41개: 연산 8종 각각, 실패 8종 422·무변경, 배치 원자성, 세션 밖 참조 거부, 버전·입력 충돌, PATCH 멱등, 동시 PATCH 4건 1승, Proposal 생성 시 문서 불변, apply 후 비선택 영역·출처 보존, 중복 apply(같은 키/키 없음/다른 키), 동시 apply 3건 1승, stale 거부+DB 유지(훅·입력 변경 두 경로), reject 무변화·멱등, 생성 검증, mock 미지원 3종 failed, 이미지 후보·선택, placeholder 교체, 선택 영역 밖 연산 거부, 생성 중 문서 변경 → stale 저장, restore 4건, 참조 삭제 시 복원 거부, 멱등보다 접근 검사 우선(404/410), 타 소유자, v3→v4, mock 실제 단어 없음). 실서버(가짜 자료): rev1 → PATCH rev2 → Proposal → apply rev3 → restore rev4, validation null 유지. 개발 DB v3→v4 마이그레이션 확인.

**실서버 apply 재전송 5가지 경우 (2026-09-25, 가짜 자료. 처음 흐름 확인 때의 "중복 apply 409"는 아래 ④ 경로였음 — 키 없는 재요청)**

| # | 요청 | 응답 | 이후 proposal.status / applied_revision / document.revision |
|---|---|---|---|
| ① | 최초 apply, `Idempotency-Key: K1`, `{expected_revision: 2}` | 200 `{document_revision: 3, status: draft}` | applied / 3 / 3 |
| ② | **같은 키 K1·같은 본문** 재전송 | 200, **①과 바이트 단위로 동일한 본문**(revision 3). 재증가 없음 | applied / 3 / 3 (변화 없음) |
| ③ | 같은 키 K1·**다른 본문**(selected_candidate_id 추가) | 409 `IDEMPOTENCY_KEY_CONFLICT` | applied / 3 / 3 (변화 없음) |
| ④ | **키 없음**·같은 본문 | 409 `PROPOSAL_STALE`, details `{status: "applied", applied_revision: 3}` | applied / 3 / 3 (**stale로 바뀌지 않음**) |
| ⑤ | **다른 키 K2**·같은 본문 | 409 `PROPOSAL_STALE`, details `{status: "applied", applied_revision: 3}` | applied / 3 / 3 (변화 없음) |

정리: 같은 키·같은 본문만 최초 성공 응답을 그대로 돌려주고, 그 외 재요청은 어느 경우에도 revision을 올리거나 applied 상태를 stale로 바꾸지 않는다(④⑤의 오류 코드 `PROPOSAL_STALE`은 상태값이 아니라 응답 코드이며 details.status로 실제 상태 `applied`를 알려줌 — 계약 확인 ⑱).

**6.5-5 하지 않은 것** — Validation·Issue 해결·Approval·승인 무효화 실체(BE-06, `validation`은 계속 null — 검증 완료로 포장하지 않음), 실제 AI·프롬프트·LangGraph·task_agent.md, 등록 자료 적재 스크립트(다음 작업), 문서 안 그림 교체 외 Asset 관리, "입력 변경 후 기존 문서 영향 검사"(지금은 문서 input_revision이 뒤처지면 편집을 409로 막기만 함 — BE-06/AG 협의).

### 6.6 등록 자료 적재 (2026-09-26)

**6.6-1 CLI** — `uv run python scripts/import_registered.py --source-dir <묶음 루트> [--ingest-dir <JSON 폴더>] [--with-mock] [--dry-run]`. 출력은 건수 요약 JSON뿐(원문·경로·해시 미출력). mock 묶음: `--source-dir tests/fixtures/ddalgi_mock_bundle_v1/ingest --with-mock`. 실제 묶음: `--source-dir private_runs/registered_src/real/<루트>`(JSON이 06_개발전달에 있으면 `--ingest-dir`).

**6.6-2 규칙(구현)** — status=ready만 적재, mock은 --with-mock일 때만(없으면 skipped.mock), missing_original·received_by_team_not_in_package·planning_reference·content_confirmed_original_missing은 건너뛰고 건수, 모르는 status는 오류. sha256: path 파일 있으면 바이트 비교(불일치 HASH_MISMATCH → 전체 중단), sha256_note 있으면 검증 생략·hash_verified=false·사유 보존, null은 중복 판정에 안 씀(같은 ID 재적재는 검증된 해시가 서로 다를 때만 SOURCE_ID_CONFLICT). 경로 이탈 PATH_OUTSIDE_BUNDLE, 없는 파일 PATH_NOT_FOUND/IMAGE_NOT_FOUND, chunk의 UNKNOWN_SOURCE_ID·DUPLICATE_CHUNK_ID·UNKNOWN_EVIDENCE_STATUS·INVALID/AMBIGUOUS_LOCATOR·CHUNK_TEXT_MISMATCH(TXT 원본 행 대조), mock 표시 불일치 MOCK_MARKER_CONFLICT. 하나라도 오류면 아무것도 적재하지 않음(한 트랜잭션). 이미지는 CSV·candidates의 정식 경로만(images/ 사본 제외), candidates에 없는 CSV 행은 적재하지 않음, source_id 없는 사진은 `REGISTERED_IMAGES` 묶음 자료에 연결. use_as_company_evidence=false는 적재·목록 표시하되 선택(404)·사전 점검·근거 검사·refs에서 제외. document_date는 문자열 그대로("2017"), date_from_filename 별도. [MOCK] 라벨은 text·excerpt에 보존하고 mock Agent가 "라벨: 값"을 찾을 때만 접두어를 벗김. quality/의 missing_image·path_escape는 README v1.1대로 CSV 폴더/파일명 기준으로 테스트.

**6.6-3 DB v5** — sources(origin_group, document_date, document_date_verified, date_from_filename, extraction_method, use_as_company_evidence, hash_verified, hash_note, is_mock, imported_at, note), segments(chunk_id, evidence_status, document_date, extraction_method), assets(photo_id, caption_candidate, selected_as_candidate, approved_for_external_use, photo_locator_json), registered_imports(적재 이력: 루트 폴더명 해시·건수). ALTER 가드 마이그레이션, 기존 개발 DB v4→v5 확인.

**6.6-4 세션 연결** — `GET /api/v1/sources[?kind=]`(쿠키만, 세션 무관). PATCH inputs로 등록 ID 선택 가능(근거 제외 자료는 404), 사전 점검·초안·편집 참조 검사가 등록 구간·사진을 인정, `GET /sessions/{sid}/assets/{id}`로 등록 사진 조회(모든 소유자), 세션 삭제는 등록 자료·구간·사진·파일을 건드리지 않음(테스트로 확인). 세션 자료 목록에는 등록 자료가 섞이지 않음.

**6.6-5 확인** — `uv run pytest` 118/118(신규 26: 기본 적재·기대값 8/24/6·재적재 0·dry-run·실제 형식(status 다양·sha256_note·null·source_id 없는 사진)·ingestion_cases 10건 전부(오류 시 무적재)·locator 케이스 8+팀 패턴 5·GET /sources·선택/사전 점검/근거/asset/세션 삭제 보존·근거 제외 refs·업로드 PDF 2건(MOCK01 텍스트 segment, MOCK08 partial+IMAGE_ONLY)). CLI 실행: 플래그 없이 0건 → dry-run 8/24/6 무기록 → 적재 8/24/6 → 재실행 0건. 실서버 GET /sources 8건. verify_bundle.py PASS와 적재기 검사 결과 불일치 없음. **실제 적재: 미확인(파일 미배치)** — private_runs/registered_src/real/ 비어 있음.

**6.6-6 요청** — 팀(계약): ⑪ 제안값 "백엔드 CLI 적재"(plan.md 4절 기록) · ㉑ SourceOut에 `document_date`·`use_as_company_evidence`·`is_mock` 추가(백엔드 제안; chunk 단위 evidence_status는 내부 보존만) · 등록 사진의 공개 허가(approved_for_external_use)는 적재 시 null 보존, 출력(BE-08)에서 어떻게 막을지. Agent: 등록 구간은 `SegmentIn.text`에 [MOCK] 라벨이 그대로 들어감(실제 자료는 없음).

### 6.7 BE-06 검증·문제 해결·승인 (2026-09-26)

**6.7-1 API** (`/api/v1`, 멱등 규칙은 BE-05 방식: 접근 검사 → 같은 키·같은 본문 재사용 → 다른 본문 409)

| API | 동작 | 검사·오류 |
|---|---|---|
| POST /documents/{did}/validate | 202 Job(kind=validate, 중복 판정 키 `문서@문서버전@입력버전`). 서버 일반 검사 → 바뀐 블록이 있으면 Agent validate → Issue 기록 → Validation 저장 | expected_revision 409 · 입력 버전 불일치 409 · Job 도중 문서/입력 변경 → 결과 폐기·failed(㉔) |
| GET /documents/{did}/issues | 현재 문서의 Issue 전부(open·resolved·excluded·acknowledged) + 현재 검증 ID | 계약에 없는 경로(㉒) |
| POST /issues/{iid}/resolve | `{expected_revision, resolution:{action, reason}, evidence_refs}`. 서버가 조치와 현재 내용을 대조한 뒤에만 상태 변경. resolution에 action·by(쿠키 소유자)·at(서버)·reason·evidence_refs·당시 버전 기록. 처리 후 Validation 상태를 문서 전체로 재합산 | acknowledged: warning만(422 RESOLUTION_NOT_ALLOWED) · excluded: 대상 블록·fact 참조·자료 선택·근거 사용이 모두 사라졌을 때만(422 ISSUE_STILL_PRESENT), REQUIRED_MISSING·MOCK_VALUE 불가 · resolved: 서버 Issue는 즉시 재검사해 사라졌을 때만, agent Issue는 validate로만(422 REVALIDATION_REQUIRED) |
| GET /documents/{did} | `validation`·`approval`을 **현재 문서·입력 버전**의 실제 저장 결과로. 없으면 null. 과거 승인·검증은 돌려주지 않음 | |
| POST /documents/{did}/approvals | 승인 7조건을 `BEGIN IMMEDIATE` 한 트랜잭션에서 검사·생성·멱등 저장. 같은 버전·형식의 active 승인은 1건 | ② 409 DOCUMENT_REVISION_CONFLICT ③ 409 INPUT_REVISION_CONFLICT / 422 PREFLIGHT_NOT_CONFIRMED ④ 422 VALIDATION_NOT_PASSED(not_current/superseded/open_blockers) ⑤ 422 UNRESOLVED_REQUIRED ⑥ 422 LAYOUT_NOT_READY(reason: not_found/문서·버전·입력·형식·status·template_version·render_options_hash·asset_manifest_hash 불일치) ⑦ 422 APPROVAL_NOT_CONFIRMED |

**6.7-2 서버 일반 검사(AI와 분리)** — `REQUIRED_MISSING`(blocker): 회사명, 그리고 주요 사업/공정(company_summary·business_areas·processes·products_services·technology 중 하나)의 supported 사실을 참조하는 블록이 있고 **그 블록 본문에 사실 값이 실제로 들어 있어야** 함(정규화 부분 문자열 또는 2자 이상 토큰 60%). fact_ids만 남기고 본문을 바꾸면 실패. `UNSUPPORTED_CLAIM`(blocker, ㉛): fact_ids 없는 사실 주장 — paragraph/list는 안내 문구·연결 문장이 아니면 전부, heading·image 캡션은 라벨이 아니면 검사(종류만으로 제외하지 않음). 확인 클릭 불가, 주장 삭제 또는 근거 연결 후 재검증으로만 해결. `PLACEHOLDER_TEXT`(warning). `VALUE_CONFLICT`(blocker): 사전 점검 충돌을 현재 블록이 참조하는 사실에만 연결. `EVIDENCE_INVALID`(blocker). `MOCK_VALUE`(blocker): 블록 텍스트·근거 원문·근거/사실 원출처 `is_mock`·이미지 `asset.source_id` 원출처 중 하나라도 mock(메시지에 사유 표기). excluded·acknowledged 불가, 원인 제거 후 재검증으로만 resolved.
연결 문장 규칙(㉛): "다음은/아래는/이어서/이 장에서는/이 페이지에서는/본 자료는/여기서는/다음 페이지에서는"으로 시작 + "입니다/합니다/살펴봅니다/소개합니다/안내합니다/정리합니다"로 끝 + 숫자·%·주장 키워드 없음 + 40자 이하. 라벨 규칙(heading·캡션): 절 번호(`2.`, `… 2`)를 뺀 뒤 20자 이하 + 숫자·% 없음 + 주장 키워드(인증·납기·최고·1위·보장·특허·ISO·최대·최소·이상·이하·년·개·톤·㎡·억·만·명·위·국내·세계·최초·유일·품질·정밀) 없음. 애매하면 주장으로 본다.

**6.7-3 부분 재검증** — 블록 지문 = sha256(type·content·fact_ids·evidence_refs·근거 원문). 마지막 유효 검증(같은 문서·같은 입력 버전·낮은 revision 중 최신)과 서버가 비교해 바뀐 블록만 Agent에 넘김. 지문 집합이 같으면(순서만 변경) Agent 미호출. 서버 검사는 결정적이라 매번 전체 실행. Issue는 identity_key(**origin**+scope+code+대상 ID)로 한 행을 갱신해 해결 기록 보존 — 서버 Issue와 Agent Issue는 같은 code·대상이어도 다른 행이라 Agent 결과가 서버 행을 갱신하거나 severity를 낮출 수 없음. resolved/excluded였던 Issue가 다시 검출되면(원인이 돌아옴, 예: 삭제한 블록을 restore) open으로 되돌리고 이전 resolution은 `resolution_history`에(previous_status 포함); acknowledged는 관련 내용·근거·입력 지문(anchor)이 바뀌었을 때만 재확인. 이번에 검출되지 않은 open Issue는 **그 검사가 그 범위를 실제로 다시 봤을 때만** 서버가 resolved(by=server)로 닫음 — 서버 검사는 매번 전체, Agent 검사는 이번에 넘긴 블록에 한정, block_ids가 없는 문서 전체 Agent Issue는 전체 검사였을 때만(재실행하지 않은 검사의 Issue는 보존). "최신" 조회(Validation·Approval·Preflight·Job·문서 요약·Issue 순서)는 created_at에 **rowid 보조 정렬**(초 단위 동일 시각·ID 문자열 순서에 의존하지 않음). Validation.status = open blocker → failed / open warning → needs_review / 없음 → passed. **Document.status는 저장하지 않고 읽을 때 `validation.compute_document_status()` 한 함수로 계산**(문서 조회·세션 summary 공용): 현재 rev·세션 최신 input의 active 승인 → approved / 최신 검증 passed → ready_for_approval(배치 검사는 승인 시 확인 ㉕) / failed·needs_review → review_required / 없음 → draft. `document_revisions.status`는 캐시.

**6.7-4 Agent에게 전달 — `validate()` 규격** (`app/agent_bridge.py`, sync/async 호환, llm 미구현 시 Job failed·mock 대체 없음)
```python
def validate(self, request: ValidateRequest) -> ValidateResult | Awaitable[ValidateResult]
ValidateRequest(session_id, input_revision, brief, sources, document: Document, preflight: PreflightOut,
                changed_block_ids: list[str],   # 마지막 유효 검증 이후 바뀐 블록(전체 검사면 전부)
                server_issues: list[Issue])     # 서버 일반 검사 결과(참고). 삭제·완화 대상이 아님
ValidateResult(issues: list[Issue], notes: str)  # scope=content, block_ids는 changed_block_ids 안, fact/source는 요청 안의 ID만. status/resolution은 서버가 정함
```
서버 검사: 범위 밖 ID·바뀌지 않은 블록·status 지정 → 저장 안 함(AGENT_OUTPUT_INVALID). mock: 바뀐 블록에 최상급·보장 표현("최고","1위","보장","최상","유일")이 있으면 `UNVERIFIED_SUPERLATIVE` warning. `MockAgent.validate_calls`로 호출 횟수 확인 가능. task_agent.md 미수정.

**6.7-5 확인** — `uv run pytest` 165/165(BE-06 46개 = 40 + Codex 리뷰 회귀 6: `test_review1_resolved_issue_reopens_when_cause_returns`(MOCK_VALUE→삭제→resolved→restore→open·failed), `test_review2_agent_cannot_downgrade_server_issue`(Agent가 MOCK_VALUE/warning 반환해도 서버 blocker 유지·별도 행), `test_review2_legacy_issue_keys_migrate_without_duplicates`(옛 key 행 있는 DB 재시작·재검증 시 중복 없음·해결 이력 유지), `test_review3a_document_level_agent_blocker_survives_move_only`(block_ids=[] Agent blocker가 순서 변경 후에도 open·failed), `test_review3b_partial_agent_recheck_preserves_uncovered_issues`(일부 블록만 재검사 시 검사 안 한 영역·문서 전체 Issue 보존), `test_review4_latest_pick_uses_insert_order_not_id_order`(같은 created_at·사전순 작은 ID라도 나중 저장 행이 최신 — Validation·Approval). v5→v6·보존·재실행, 정리 후 passed·ready_for_approval, UNSUPPORTED_CLAIM(주장·연결 문장·라벨·제목 주장·캡션 주장), 필수 내용(본문 실제 확인·관련 사실 종류 인정·fact_ids만 남기면 실패), mock 묶음 문서 MOCK_VALUE·이미지 원출처·excluded/acknowledged 거부·승인 차단, 라벨 제거 후 근거로 차단, warning acknowledged·멱등·409, excluded 실제 제거 전/후, agent Issue REVALIDATION_REQUIRED, 부분 재검증(blocker 유지·순서 변경 Agent 미호출·바뀐 블록만), 선행 없음/입력 변경 재사용 금지, 해결 기록 재확인·이력, Job 중복 분리·도중 변경 폐기, llm 미구현 failed, 승인 성공·상태·멱등 3경로·Approval 1건, 조건 실패 12경로, 자산 해시 변경, 무효화 5경로, 접근 우선 404/410, **TestClient 기반 동시 ASGI 요청(스레드 3개) → 응답 정확히 3개·작업 스레드 예외 없음·같은 approval_id·Approval 1건** + DB 잠금 경합 재현 테스트(실서버 네트워크 동시 요청은 미실시), restore 승인 미부활, 실제 회사 단어 없음). 실서버(가짜 자료, 개발 DB v5→v6): 정상 문서 validate→passed(agent_called=true)→ready_for_approval→approvals **422 LAYOUT_NOT_READY(layout_check_not_found)** — BE-08 전 정상 차단. mock 등록 문서: MOCK_VALUE 12건·failed·review_required, blocker acknowledge 422 RESOLUTION_NOT_ALLOWED, approvals 422 VALIDATION_NOT_PASSED(open_blockers). 개발 DB에 layout_checks·approvals 행 0(심지 않음). 승인 성공·무효화는 격리 테스트 DB의 가상 LayoutCheck 행으로만 확인.

**6.7-6 제한·하지 않은 것** — 실제 AI 의미 검증(AG-07)·실제 배치 렌더링·LayoutCheck 실행/Job/API(BE-08)·PDF/DOCX 출력(BE-07/08). **입력 변경 후 영향 검사·기존 문서를 최신 input_revision에 연결하는 흐름 미지원**(㉙): 자료 선택 변경 뒤에는 validate·편집·승인 모두 409 INPUT_REVISION_CONFLICT — 자료 제외 후 재검증 같은 확인은 완료 아님. 근거 보완 편집(기존 블록에 fact_ids·evidence_refs를 붙이는 연산)은 PATCH 연산 8종에 없어 미지원 — UNSUPPORTED_CLAIM은 지금은 주장 삭제 후 재검증으로만 해결(㉛ 제한). image_placeholder 잔존은 LayoutCheck 몫(㉚). `.gitattributes` 추가: `tests/fixtures/** -text` — autocrlf 환경에서 fixture TXT가 CRLF로 바뀌어 sha256이 어긋나던 문제 수정(mock 묶음 적재 HASH_MISMATCH 재현·해결).

**6.7-7 계약 확인 (임시 구현, contracts.md 원본 미수정)** — ㉒ `GET /documents/{did}/issues` · ㉓ Validation에 `checks`·`agent_called`·`checked_block_ids`·`reused_block_ids`·`base_validation_id` · ㉔ 검증 중 버전 변경 시 결과 폐기·Job failed(DOCUMENT_REVISION_CONFLICT/INPUT_REVISION_CONFLICT) · ㉕ `ready_for_approval` = 검증 passed(배치 검사 전) · ㉖ `template_version="template_v0"`·render_options_hash 서버 상수(BE-07에서 확정 → 6.8-7) · ㉗ resolve 본문 모양과 코드 `RESOLUTION_NOT_ALLOWED`·`ISSUE_STILL_PRESENT`·`REVALIDATION_REQUIRED` · ㉘ 승인 코드 `VALIDATION_NOT_PASSED`·`APPROVAL_NOT_CONFIRMED`, Approval.`invalidated_reason` · ㉙ 입력 변경 후 영향 검사·재연결(미구현) · ㉚ image_placeholder 잔존 = LayoutCheck · ㉛ UNSUPPORTED_CLAIM 정책(근거 없는 사실 주장 = blocker, 확인 클릭 불가, 연결 문장·라벨 규칙, 삭제 또는 근거 보완 후 재검증 — 근거 보완 편집 경로는 미지원) · Issue에 `origin` 필드 추가(server/agent/preflight) — identity_key에도 origin이 포함되며, 이 형식 변경 전에 만들어진 개발 DB v6 행은 시작 시 `migrate_legacy_issue_keys`가 한 번 변환(재실행 안전).

**6.5-6 계약 확인 (임시 구현, contracts.md 원본 미수정)** — ⑭ 연산 실패 코드 없음 → `422 INVALID_OPERATION` · ⑮ insert_block/insert_page의 ID는 클라이언트가 주고 서버가 유일성 검사(서버 발급은 안 함) · ⑯ Proposal 조회 경로 없음 → `GET /sessions/{sid}/proposals/{pid}` · ⑰ Proposal에 `rationale`·`candidates`·`applied_revision` 필드 추가(계약은 changes만; selected_candidate_id가 계약에 있어 candidates 필요) · ⑱ applied/rejected/stale에 apply → `409 PROPOSAL_STALE` + details.status(별도 코드 제안) · ⑲ restore 참조 무효 → `422 RESTORE_REFERENCE_INVALID` · ⑳ mock 미지원 요청 Job 코드 `UNSUPPORTED_PROPOSAL`/`NO_IMAGE_CANDIDATES`, 후보 미선택 `422 CANDIDATE_REQUIRED`(④⑬과 함께 정리). 문서 input_revision이 세션보다 뒤처졌을 때의 편집 처리(현재 409 INPUT_REVISION_CONFLICT)도 계약 3절 "영향 검사"와 맞춰 확정 필요.

### 6.8 BE-07 PDF/DOCX 출력 도구·템플릿 검증 (2026-09-26)

**6.8-1 결정(D-03)과 비교** — 비교표는 plan.md 4절(D-03 비교표). 같은 가상 문서 4종(D1 fixture 1쪽 · D2 fixture 10쪽 · D3 스트레스 4쪽 · D4 mock 업로드 PDF+PNG를 실제 API 흐름(업로드→선택→사전 점검→mock 초안)으로 만든 4쪽)을 `scripts/experiments/be07_render_candidates.py`로 후보별 생성(출력 `private_runs/be07/`, 커밋 안 함). 채택: **DOCX=python-docx, PDF=HTML 템플릿+시스템 Chromium 계열 브라우저(Chrome/Edge) headless 인쇄**. 이유: 한글 어절 단위 줄바꿈·`object-fit` crop·CSS 템플릿(프론트와 D-06 협의 가능)·측정과 인쇄가 같은 엔진, 서버 런타임 Python 의존성 0. 시간(이 PC, cold): Chrome CLI 8.4~12.3초(1회 실행에 인쇄+DOM 측정), Playwright warm 2.2~3.0초(패키지 37MB·상주 브라우저 필요 → 보류), reportlab 0.4~0.7초(문자 단위 줄바꿈으로 라틴 단어가 중간에 끊김 → 대체 후보). LibreOffice(미설치)·Playwright 번들 Chromium(미다운로드)은 미실행/설치 제약. 시간 측정 조건: Chrome CLI 값은 HTML 생성·pypdf 구조 검사까지 포함한 어댑터 전체 시간이고 Playwright 값은 HTML 생성 뒤 브라우저 구간만이라 두 후보의 차이는 다소 과장돼 있다(같은 엔진이라 출력은 동일). 실험 중 이 PC의 Chrome이 사용자 기본 프로필을 열지 않도록 어댑터는 항상 임시 `--user-data-dir`을 쓴다.

**6.8-2 렌더 어댑터 규격 — `app/services/export_render.py` (BE-08이 그대로 씀)**
```python
build_snapshot(conn, settings, session_id, document) -> RenderSnapshot   # 유일한 DB·파일 접근. image 블록 asset을 확인해 바이트 고정
render(snapshot, fmt: "pdf"|"docx", out_dir, settings=None) -> RenderResult   # 호출자 옵션 없음. 실패는 RenderError(code)
RenderSnapshot(document_id, document_revision, input_revision, title, target_pages, pages, assets{asset_id: SnapshotAsset}, content_hash)
SnapshotAsset(asset_id, content_hash, mime_type, width, height, data: bytes|None, ok, reason)  # reason: not_found/not_accessible/not_ready/missing/hash_mismatch/decode_failed
RenderResult(format, file_path, actual_pages|None, checks[LayoutCheckRecord], findings[Finding], layout_ok, template_version, render_options_hash, asset_manifest_hash, renderer, elapsed_ms, details)
LayoutCheckRecord(check_key: overflow|broken_image|placeholder_remaining, required, result: ok|finding|not_checked, block_ids, page_ids, reason)
Finding(kind, page_id, block_id|None, message, details)   # overflow: excess_mm·height_mm / broken_image: asset_id·reason / placeholder_remaining: description
RenderError.code: unsupported_format / browser_not_found / render_timeout / render_failed / save_failed / template_missing
```
- `REQUIRED_CHECKS`: pdf·docx 모두 3종 required. `layout_ok` = required 검사가 전부 `ok`. required가 `not_checked`면 `layout_ok=False`(빈 findings를 통과로 해석하지 않음). DOCX overflow는 `not_checked(reason=docx_no_layout_engine)`·`actual_pages=None`.
- 스냅샷: asset 행 조회는 승인 검사와 같은 `assets.content_hash`(행 없으면 "")로 manifest를 계산하고, 접근 규칙은 `assets.get_ready`와 같다(이 세션의 session asset 또는 registered, deleted 아님, ready). 파일 sha256이 content_hash와 다르면 `hash_mismatch`, Pillow **전체 픽셀 디코딩**(`img.load()`) 실패는 `decode_failed`(헤더만 정상인 잘린 JPEG 포함). 스냅샷은 Document의 Page/Block/content를 깊은 복사하므로 원본을 바꿔도 스냅샷·해시·출력이 유지된다. 렌더는 스냅샷 바이트만 쓴다(HTML data URI, DOCX BytesIO). 등록 사진의 공개 허가(`approved_for_external_use` null)는 6.6-6대로 BE-08 몫.
- PDF: Jinja2 autoescape 템플릿 → 단일 HTML(폰트·이미지 data URI, CSP `default-src 'none'`, 외부 리소스 없음) → 브라우저 1회 실행(`--headless=new … --virtual-time-budget=10000 --dump-dom --print-to-pdf`, 임시 `--user-data-dir`, 시간 제한 `EXPORT_RENDER_TIMEOUT_S` 기본 90초, 초과 시 프로세스 트리 종료) → 발행 전에 pypdf로 쪽수·폰트 구조 검사(읽지 못하면 render_failed, 파일 미발행). 측정 JSON(#be07-measure)은 load·fonts.ready 뒤에 기록되며, 모양·page_id 집합·block_id를 스냅샷과 대조한 뒤에만 쓴다(문서 값으로 위조 불가). 못 읽으면 overflow=not_checked(measure_failed), 대조 실패면 measure_invalid. 삽입 이미지는 EXIF Orientation을 먼저 반영(돌린 사본에는 태그 미기록)하고 긴 변 1600px(`IMAGE_MAX_PX`)를 넘을 때만 같은 형식으로 축소한다(HTML·`--dump-dom` 출력·PDF/DOCX 용량 상한). 방향·크기가 그대로면 검증한 바이트를 그대로 넣는다. 스냅샷 바이트·content_hash는 어느 경우에도 그대로이며 같은 입력·같은 Pillow 버전이면 삽입 바이트의 sha256이 같다. 샌드박스를 끄는 인자(`--no-sandbox`)는 넣지 않는다 — **실행 조건**: Chromium은 root로 실행되면 샌드박스 때문에 시작을 거부하므로 서버(컨테이너 포함)는 비root 사용자로 실행한다. root로 돌리면 PDF는 `render_failed`로 실패한다(사용자 결정 2026-09-27). 브라우저 탐색: `EXPORT_BROWSER_PATH` → Chrome → Edge(Windows) / google-chrome·chromium·microsoft-edge(POSIX). `renderer`에 `chrome/153.0.8010.54` 형식으로 기록(해시 밖).
- DOCX: A4·여백 15mm, Normal/Title/Heading 1~3/Caption/List Bullet 스타일에 ascii·hAnsi·eastAsia·cs 글꼴 이름 지정(테마 글꼴 속성 제거), 논리 Page마다 쪽 나눔, 이미지 contain(180×120mm), 캡션, 사진 자리·깨진 이미지는 1×1 표 상자. 고유 이름의 임시 파일에 저장해 다시 열어 문단·이미지·표 수만 센 뒤 발행(같은 문서 동시 생성 안전). 본문 구성 중 예외(lxml XML 비호환 문자, python-docx 이미지 오류)는 RenderError(render_failed)로만 알리며 U+FFFE/U+FFFF·서로게이트·C0 제어 문자는 미리 제거한다. 스냅샷 asset은 PNG/JPEG만 ok(그 외 형식은 unsupported_format → broken_image). 폰트 임베딩은 이번 구현(python-docx)에서 미지원.
- 출력 식별값은 `layout_checks.py`에서만 정의(6.8-7). 템플릿(줄바꿈 정규화)·폰트·DOCX 배치 상수(`DOCX_LAYOUT_CONSTANTS`: 색·상자 간격 포함)·PDF 렌더 상수(`PDF_RENDER_CONSTANTS`: 창 크기·가상 시간)·이미지 상한의 sha256을 `template_fingerprint()`로 묶고 `tests/test_be07.py::test_template_fingerprint_pinned_per_version`이 `TEMPLATE_VERSION`별 고정값과 비교한다 — 바꾸면 버전을 올려야 통과.
- 옛 코드 재사용: `_set_korean_font`의 eastAsia 지정 아이디어(Title/Heading은 테마 글꼴이 우선이라 확장), 저장 후 0바이트 검사, 출력 폴더 한정·`RenderError(code, message)` 규칙. PDF·page/block 구조·이미지 배치·측정은 신규.

**6.8-3 생성 파일 요약** (`private_runs/be07/`, 커밋 안 함, 실제 회사 단어 없음 확인)

| 문서 | 논리 쪽 | PDF actual_pages | PDF checks/findings | layout_ok(PDF / DOCX) | DOCX 구조(문단/이미지/표) | Word 실측 쪽수 |
|---|---|---|---|---|---|---|
| D1 fixture 1쪽 (`chrome_cli/doc_mock_1pages_rev3.pdf`, `docx/doc_mock_1pages_rev3.docx`) | 1 | 1 | 3종 ok | True / False(overflow not_checked) | 8/1/0 | 1 |
| D2 fixture 10쪽 | 10 | 10 | 3종 ok | True / False | 44/2/0 | 10 |
| D3 스트레스 | 4 | 8 | overflow 2(s_p2 긴 문단 +792.5mm, s_p3 사진 3장 +22.4mm) · broken_image 2(decode_failed, not_found) · placeholder 1 | False / False | 27/3/3 | 7 |
| D4 실제 흐름(mock PDF+PNG) | 4 | 4 | 3종 ok · 스냅샷 manifest == `layout_checks.asset_manifest_hash` | True / False | 17/1/0 | 4 |

PDF 폰트: 모든 문서 `Pretendard-Regular`/`Pretendard-Bold` 서브셋 임베드. 스트레스 문서의 `<script>alert(1)</script>`는 PDF·DOCX 모두 문자 그대로. Word 실측 쪽수는 Chrome PDF와 다를 수 있다(D3: Word 7 / Chrome 8) — DOCX 쪽 나눔은 열람 프로그램·글꼴에 따라 달라진다는 prd.md 7절 그대로이며 어댑터는 DOCX `actual_pages`를 None으로 둔다.

**6.8-4 화면 검사** — PDF: pypdfium2로 전 페이지 PNG(`private_runs/be07/png/`) 육안 확인. 한글 서체(Pretendard) 정상, 제목/본문/목록, 가로 사진 폭 맞춤·세로 사진 높이 120mm 제한·배너 crop(중앙 잘림), 깨진 이미지·없는 asset은 붉은 점선 상자에 asset_id·사유, 사진 자리는 회색 점선 상자, 긴 문단은 잘리지 않고 다음 쪽으로 흐름, 페이지 라벨(n / 제목) 우상단. DOCX: 로컬 Word 16.0.20326(COM, PowerShell 스크립트는 스크래치, 커밋 안 함) 실측 — 쪽수 1/10/7/4, ProtectionType 없음, 문단 글꼴 이름 Pretendard, **Word 글꼴 목록에 Pretendard 없음 → 한글 바탕(Batang)·라틴 Cambria로 대체 표시**(SaveAs PDF의 폰트 목록으로 확인), 텍스트·그림 삽입 가능(저장 안 함). Word SaveAs(PDF)→pypdfium2 PNG(`private_runs/be07/png_word/`) 육안: 페이지 라벨·제목·목록·이미지 크기·상자·쪽 나눔이 PDF와 같은 구조로 표시. Word `ExportAsFixedFormat` COM 호출은 응답이 없어 `SaveAs(…, 17)`로 대체. 이 실측은 개발 PC 확인이며 서버 기능이 아니다(어댑터 DOCX `actual_pages`는 None 유지).

**6.8-5 확인** — `uv run pytest` 196/196(BE-07 31개: 스냅샷 격리(원본 변경 후 스냅샷·해시·출력 유지)·잘린 JPEG decode_failed→broken_image·EXIF 방향(상한 이하/초과) 반영과 원본 바이트·해시 보존·삽입 바이트 sha256 결정성·샌드박스 인자 없음 + 식별값 공유·어댑터가 layout_checks 현재 값을 읽음·manifest 순수 함수 호환·템플릿 지문(줄바꿈 무관)·render 인자 목록 고정·스냅샷 manifest==DB(해시 변경 추적)·접근 규칙(not_accessible/not_ready/missing/not_found)·스냅샷 바이트만으로 렌더(파일·DB 삭제 후)·asset_from_bytes 검증/형식 제한·이미지 축소 상한·DOCX 구조/스타일 글꼴/여백/속성/임시 파일 없음·DOCX findings·XML 비호환 문자·본문 실패/저장 실패 시 부분 파일 없음·같은 문서 동시 생성·HTML 이스케이프/외부 리소스 없음/fonts.ready·측정 JSON 위조/불일치 거부·PDF 쪽수/폰트/텍스트/fonts_ready/임시 파일 정리·PDF 넘침/깨진 이미지/자리/이미지 수·measure_failed/measure_invalid → not_checked·시간 초과/비정상 종료 시 부분 파일 없음·형식/브라우저 없음 오류·required not_checked 규칙·브라우저 인자 지문·실제 회사 단어 없음). PDF 테스트 5개는 브라우저가 없는 환경에서 skip으로 표시된다(통과 아님). 코드 리뷰(4관점 적대적 검토 27건 → 반박 3건 제외 24건 반영: unescape 제거·측정 검증·폰트 로드 후 측정·줄바꿈 지문·XML 비호환 문자·고유 임시 파일·형식 제한·발행 전 구조 검사·root 샌드박스·프로세스 트리 종료·이미지 축소·지문 범위·테스트 보강). 실험 스크립트 4문서 × 후보 실행 결과 `private_runs/be07/results.json`. **미실행**: LibreOffice 변환, Playwright 번들 Chromium, Linux/Mac 브라우저 탐색 경로(코드만), 실서버 API 연결·다운로드(BE-08).

**6.8-6 제한·하지 않은 것** — LayoutCheck Job/API·미리보기 응답·Export API·다운로드·승인 연결(BE-08). **DOCX는 배치 엔진이 없어 overflow가 required·not_checked → `layout_ok=False`**: 현재 규칙으로는 DOCX 자동 배치 검사가 통과할 수 없고 승인도 열리지 않는다. 실측 도구(Word/LibreOffice) 연결이나 정책 결정은 BE-08 미해결 항목이며, PRD의 쪽수 차이 허용은 검사 면제 근거로 쓰지 않는다. DOCX 폰트 임베딩은 이번 구현에서 미지원(받는 사람 환경에 Pretendard가 없으면 대체 글꼴 — 이 PC의 Word는 바탕/Cambria). DOCX `fit=crop`은 contain으로 대체. 브라우저 종류·버전은 해시에 넣지 않으므로 검사와 출력 사이에 브라우저가 바뀌면 렌더가 미세하게 달라질 수 있다 — 검사/출력 간 렌더러 변경 정책은 BE-08 필요(㉝). PDF 생성은 서버에 Chrome/Edge가 있어야 하고 비root 사용자로 실행해야 하며 문서당 8~12초(cold). PDF 메타데이터의 /Creator·/Producer에 HeadlessChrome·Skia 문자열(브라우저·OS 정보)이 남는다(제거는 BE-08에서 필요 시 pypdf로). 큰 사진은 삽입 시 1600px로 축소되므로 원본 해상도 그대로의 출력이 필요하면 상한을 올리고 TEMPLATE_VERSION을 올려야 한다. 실험 스크립트의 reportlab·Playwright 후보 코드는 커밋하되 패키지는 설치하지 않는다(미설치면 "미실행"으로 기록). 등록 사진 공개 허가 차단은 BE-08.

**6.8-7 계약 확인 (임시 구현, contracts.md 원본 미수정)** — ㉖ **확정**: `TEMPLATE_VERSION="template_v0"`, `DEFAULT_RENDER_OPTIONS={"template_key":"company_intro","page_size":"A4","margin_mm":15,"font_family":"Pretendard","font_version":"1.3.9","base_font_pt":10.5}`, `RENDER_OPTIONS_HASH=sha256(json.dumps(옵션, sort_keys=True))[:16]="34673880fdef80f9"`, `asset_manifest_hash=sha256(json.dumps(sorted((asset_id, content_hash)…)))[:16]`(image 블록 순서·중복 포함, 행 없으면 ""). 버전 규칙: 템플릿·폰트·DOCX 배치 상수 변경 = TEMPLATE_VERSION 상향(가드 테스트), 옵션 값 변경 = 해시 자동 변경, `render()`에 호출자 옵션 없음. · ㉜ 배치 검사 결과 구조(백엔드 제안): `checks[{check_key, required, result: ok/finding/not_checked, block_ids, page_ids, reason}]` + `findings` + `layout_ok`; Issue 코드 제안 `LAYOUT_OVERFLOW`·`BROKEN_IMAGE`·`PLACEHOLDER_REMAINING`(scope=layout, BE-08에서 Issue 행 생성). 기존 Validation ㉓ `checks[CheckRecord]`(kind server/agent, result ok/issue/skipped)와는 다른 구조. · ㉝ 렌더러(브라우저) 종류·버전은 `RenderResult.renderer`에만 기록하고 식별값에 넣지 않음 — 검사/출력 간 렌더러 변경 처리 정책은 BE-08에서 정함. · ㉞ DOCX overflow는 required·not_checked로 두어 자동 배치 검사 통과 불가 — 실측 연결 또는 정책 결정은 BE-08 미해결.


## 7. 첫 요청

```text
이번 역할은 백엔드 담당이고 작업은 BE-01야.
AGENTS.md와 이 task 파일의 읽기 순서를 따라 관련 문서를 확인해줘.
내 작업 범위에서 현재 코드·기존 변경을 확인하고 구현·검증해줘.
다른 담당자에게 필요한 결과는 요청 항목으로 남겨줘.
상세 작업 상태와 실제 결과는 내 역할의 task 파일에 기록해줘.
공통 task.md 2절에 연결된 항목은 내 담당 상태·날짜·참조 위치도
함께 갱신하고, 커밋할 때 같은 커밋에 포함해줘.
다른 담당의 상태는 임의로 변경하지 마.
```
