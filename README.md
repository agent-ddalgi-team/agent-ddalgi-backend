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
- PDF만 승인·출력이 열린다. DOCX는 파일 생성과 PDF 기준 미리보기까지이며 승인·출력은 차단된다(task_backend.md 6.9절 ㉞).
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

## 문서
AGENTS.md(작업 규칙) · plan.md · prd.md · contracts.md(API 계약 원본) · task_backend.md · task_agent.md · agent.md · task.md
