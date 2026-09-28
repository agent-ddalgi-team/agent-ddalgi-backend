# agent-ddalgi-backend
거산케미칼 회사소개서 초안 도우미 — 백엔드 + Agent (FastAPI, uv, Python 3.13)

## 시작

Python 3.13과 uv가 준비된 환경에서 저장소 최상위 폴더에서 실행한다.

### 패키지 설치
```bash
uv sync --locked
```

### 환경파일 준비
.env가 없을 때만 아래 명령을 실행한다.
이미 있다면 덮어쓰지 않고 .env.example과 필요한 항목을 비교한다.

Mac:
```bash
cp -n .env.example .env
```

Windows PowerShell:
```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

.env의 실제 값은 각자 설정하며 커밋하거나 채팅에 붙여넣지 않는다.

### 서버 실행
```bash
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

API 확인: http://127.0.0.1:8000/docs

### 테스트
```bash
uv run pytest
```

현재 AI 기능은 백엔드 작업 기록상 mock(가짜 응답)으로 확인한 상태다.
실제 LLM 연결은 Agent 작업에서 별도로 구현·검증한다.

### PDF/DOCX 출력 준비 (BE-07, D-03)
- **DOCX**는 python-docx로 만들며 추가 설치가 없다.
- **PDF**는 서버에 설치된 Chromium 계열 브라우저(Google Chrome 또는 Microsoft Edge)의 headless 인쇄로 만든다.
  Python 패키지를 추가로 설치하지 않는다. 브라우저가 없으면 PDF 생성은 `browser_not_found`로 실패한다.
  - **실행 조건**: 서버 프로세스를 root로 실행하지 않는다. Chromium은 root에서 샌드박스 때문에 시작을 거부하며, 어댑터는 `--no-sandbox`를 넣지 않는다(컨테이너도 비root 사용자로).
  - 자동 탐색: Windows는 Chrome(Program Files·LOCALAPPDATA) → Edge, Linux/Mac은 `google-chrome`·`chromium`·`microsoft-edge`.
  - 다른 위치면 `.env`에 `EXPORT_BROWSER_PATH=<실행 파일 경로>`를 지정한다. 시간 제한은 `EXPORT_RENDER_TIMEOUT_S`(기본 90초).
- 한글 서체는 레포에 동봉한 OFL 폰트(Pretendard v1.3.9, `app/templates/fonts/`)를 PDF에 임베드한다.
  DOCX는 글꼴 이름만 지정하므로(이번 구현에서 임베딩 미지원) 받는 사람 환경에 Pretendard가 없으면 다른 글꼴로 대체될 수 있다.
- 렌더 어댑터는 `app/services/export_render.py`이며 배치 검사·Export·다운로드 API는 BE-08에서 연결했다(아래).
- 도구 비교 실험은 `uv run python scripts/experiments/be07_render_candidates.py`로 재현할 수 있다(출력은 `private_runs/be07/`).
  reportlab·playwright 후보는 설치돼 있을 때만 실행되고 없으면 "미실행/설치 제약"으로 기록된다.
- 배치 검사 미리보기(쪽 PNG)는 pypdfium2로 만든다(런타임 의존성, `uv sync`에 포함).

### 배치 검사·승인·출력 흐름 (BE-08)
- `POST /documents/{did}/layout-checks`(202 Job) → `GET /jobs/{jid}` → `GET /documents/{did}`의 `layout_checks.pdf` → 미리보기 `GET /assets/{preview_asset_id}`
  → `POST /documents/{did}/approvals`(201) → `POST /exports`(202, ready면 200) → `GET /exports/{eid}/download`.
- PDF만 승인·출력이 열린다. DOCX는 파일 생성과 PDF 기준 미리보기까지이며 승인·출력은 차단된다(task_backend.md 6.15절 ㉞).
- Export는 배치 검사 때 만든 불변 산출물(`private_runs/<sid>/artifacts/`)을 그대로 내려준다. 파일이 없거나 바뀌면 자동으로 다시 만들지 않고
  재검사·재승인이 필요하다. `EXPORT_TTL_MINUTES`(기본 120)로 만료.
- 등록 사진은 `approved_for_external_use=true`일 때만 출력할 수 있다. 값을 바꾸려면 `uv run python scripts/import_registered.py --source-dir <묶음> --update-publication`
  (같은 사진만 갱신, 관련 승인·출력은 함께 무효화). 공개 API로는 바꿀 수 없다.

### 세션 종료·만료 정리 (BE-09)
- 세션은 `DELETE /sessions/{sid}` 또는 만료(무활동 `SESSION_IDLE_MINUTES` / 생성 후 `SESSION_MAX_HOURS` 중 빠른 때)로 끝난다. 끝나면 한 트랜잭션에서
  접근 차단(이후 모든 요청 410 `SESSION_EXPIRED`)·내용 제거(brief·원문 구간·사실·초안 본문·편집안·문제 문구·저장된 멱등 응답)·진행 중 Job `cancelled`·활성 Export failed를
  확정하고 `private_runs/<sid>/` 삭제를 정리 큐(`cleanup_queue`)에 넣는다. 검증·배치 검사·승인·Export·artifact 행(ID·시각·해시)과 등록 자료(`private_runs/registered/`)는 남는다.
- 만료는 두 곳에서 잡는다. 요청이 들어오면 그 자리에서 확정하고 410을 내며, 요청이 없어도 배경 sweep 스레드가 `CLEANUP_SWEEP_INTERVAL_S`(기본 60초, 0이면 없음)마다
  만료 확정·큐 처리·폴더 점검을 한다.
- 폴더 삭제가 실패하면 지수 백오프(2^n분, 최대 60분)로 재시도하고 `CLEANUP_MAX_ATTEMPTS`(기본 10) 뒤 failed로 남는다(자동 재등록 없음).
  DELETE 응답과 410 `details.cleanup`은 done(내용 제거 + 폴더 없음 + 미완료 작업 없음) 또는 pending이다.
- 운영 도구: `uv run python scripts/cleanup_sessions.py --once [--dry-run]` · `--list-failed` · `--retry-failed`(ID·건수만 출력). 서버가 떠 있으면 배경 sweep이 같은 일을 한다.
- 서버 시작 시 이전 프로세스의 정리 점유를 되찾고, 내용 제거가 안 된 closed/expired 세션(v8 이전 DB)을 한 번 정리한다. sweep 요약 로그는 INFO 수준(ID·건수만)이다.

### 시연 데이터 수용 기반 (DB v9)
- `AGENT_MODE=mock|llm`은 AI 실행 방식이다. `DEMO_MODE=false|true`는 시연 출처를 사용할 수 있는 서버 정책이며 기본은 false다. 실제 LLM·조건 문서 해석은 이번 작업에서 연결하지 않았다.
- 테스트 mock(`origin_kind=mock`, `[MOCK]`)은 여전히 `MOCK_VALUE` blocker다. 시연 자료(`origin_kind=demo`)는 별도이며 실제 자료(`real`: 입수했다는 뜻, 회사 확인 완료 뜻 아님)와 함께 선택할 수 있다.
- 시연 세션은 `POST /sessions`에 `demo: true`로 생성한다. 이후 변경할 수 없다. `GET /sources?include_demo=true`는 서버 시연 모드가 켜졌을 때만 시연 자료를 보여준다. 일반 목록은 숨기며 일반 세션에서는 선택·근거·이미지 사용이 차단된다.
- 업로드 multipart `role=instruction`은 작성 조건 첨부다. 읽기·조회는 가능하지만 회사 근거 선택·Agent 사실 입력·근거 검사·본문 이미지 렌더링에서는 제외한다. **조건의 자동 해석·Brief 반영은 아직 없다.** role 생략 또는 evidence는 기존 근거 업로드 동작을 유지한다. 새 화면은 작성 조건 첨부에 role을 명시해야 한다.
- 현재 시연 구현의 `DEMO_VALUE` warning은 개별 확인을 강제하지 않는다. 이는 최신 PRD BR-09·plan D-07의 경고 확인 의무를 완료한 상태가 아니며, 확인 기록·버전 유효성·승인 연결은 후속 작업이다. 시연 임시 사실의 별도 허용 범위는 확인이 필요하다. 근거 오류·필수 내용·수치 충돌·사진 공개 허가·MOCK blocker는 유지한다.
- 시연 출력은 `template_v1`의 하단 문구 `시연용 · 일부 내용은 임시 데이터입니다`를 사용한다. PDF 인쇄 margin box에 footer를 배치하고 검사 공간에 포함하며 DOCX도 footer를 넣는다. PDF 어느 페이지든 시연 문구가 빠지면 LAYOUT_RENDER_FAILED(reason=demo_footer_missing)로 거부하므로 해당 인쇄 기능을 지원하는 브라우저가 필요하다. 문서→배치 검사→artifact→승인→Export의 demo 식별값을 비교하고 승인된 바이트를 그대로 제공한다. DOCX 승인·출력 제한은 유지한다.
- 서버 설정 변경은 재시작으로 적용한다(동일 DB를 사용하는 프로세스는 같은 설정을 사용). 시연 모드를 끄면 유효한 시연 세션의 조회·캐시·Job 결과·출력은 403 `DEMO_MODE_DISABLED`다. 수명 연장·내용 삭제는 하지 않는다. 소유자 DELETE는 허용하고 종료·만료는 기존 410·정리 규칙이 우선한다. 재활성화 시 실패 Job/Export는 자동 재시도하지 않는다.

등록 자료는 로컬의 gitignore 경로에서만 읽는다. 실제 묶음은 파일 이동·CSV 수정 없이 루트와 JSON 폴더를 구분한다.
```bash
uv run python scripts/import_registered.py --source-dir private_runs/registered_src/real/묶음루트 --ingest-dir private_runs/registered_src/real/묶음루트/06_개발전달 --dry-run
uv run python scripts/import_registered.py --source-dir private_runs/registered_src/demo/묶음루트 --with-demo --dry-run
```
검사 후 적재하려면 동일 명령에서 `--dry-run`만 뺀다. 시연 자료는 `status=demo`, `demo=true`, 표시명·본문의 `[시연]`, 근거 상태 `시연용 임시 문장`을 사용한다. 기존 source_id의 real/mock/demo 변경은 거부한다. 후보 사진은 `photo_candidates.json.path`의 실제 바이트를 적재하며 CSV 원본과 다른 이름·형식도 가능하다. CSV 연결은 같은 경로 또는 명시적 `original_ref: {"폴더": "…", "파일명": "…"}`만 사용한다. 사진 공개 허가 null/false 차단은 그대로다.

`verify_bundle.py`는 기존 fixture 전용이다. 실제 구조 호환성은 `tests/test_registered_import.py`의 가짜 묶음으로 검증한다. 실제 시연 묶음 작성·실자료 로컬 적재·실제 AI 생성 품질은 아직 완료하지 않았다. 회귀 테스트는 `uv run pytest tests/test_demo.py tests/test_registered_import.py`로 실행한다.

## 문서

처음에는 다음 순서로 읽는다. 코드 변경 전에는 [AGENTS.md](AGENTS.md)의 작업 규칙을 확인한다.

| 순서 | 문서 | 알 수 있는 것 |
|---|---|---|
| 1 | [프로젝트 아이디어](docs/idea.md) | 무엇을 만들고 누가 사용하는지; 범위 축소안은 제안 |
| 2 | [전체 구조 그림](docs/site_design.png) | 화면·서버·AI·저장소의 관계와 현재 상태 |
| 3 | [개발 계획](plan.md) · [제품 요구사항](prd.md) | 확정 범위·미정 결정·사용자 흐름·완료 기준 |
| 4 | [공통 계약](contracts.md) | 데이터·API 기준; 7절은 현행 코드와의 차이 및 합의 대기 목록 |
| 5 | [백엔드 작업](task_backend.md) 또는 [Agent 작업](task_agent.md) · [Agent 설계](agent.md) | 내 담당 작업·코드 위치·남은 연결·검증할 내용 |
| 6 | [공통 연결표](task.md) | 담당자 간 연결 지점과 결과 기록 위치 |

2026-09-27에는 개발 전 문서를 정리했다. 당시 기존 BE/AG 작업 상태와 테스트 기록을 유지했고 실행 코드·의존성·DB는 바꾸지 않았다. 현재 공통 계약은 1.2, 데이터 schema_version은 1.0이다. 2026-09-28의 계약 1.2는 재점검 충돌로 인한 기존 승인 무효화·승인 재전송 차단을 반영한다. 상세는 [contracts.md](contracts.md), 기존 경로를 유지한 예시는 [API 예시](handoff/api_examples_v1.1.json)를 따른다. 프론트 사본 갱신은 미확인이며 검토 메모의 다른 제안은 합의 후 반영한다.

Stitch 화면 설계와의 연결 기준은 [prd.md 3~5절](prd.md), 화면 상태별 데이터 연결은 [contracts.md 7.5절](contracts.md)을 따른다. 추가 기능의 채택 여부는 [plan.md 4.1절](plan.md)에서 관리한다. 화면 시연·예시 응답과 실제 기능 완료는 구분한다.
