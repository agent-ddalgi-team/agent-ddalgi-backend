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
- 렌더 어댑터는 `app/services/export_render.py`이며 Export API·다운로드는 BE-08에서 연결한다.
- 도구 비교 실험은 `uv run python scripts/experiments/be07_render_candidates.py`로 재현할 수 있다(출력은 `private_runs/be07/`).
  reportlab·playwright 후보는 설치돼 있을 때만 실행되고 없으면 "미실행/설치 제약"으로 기록된다.
- PDF 페이지를 그림으로 확인하려면 dev 의존성 pypdfium2를 쓴다(`uv sync`에 포함).

## 문서
AGENTS.md(작업 규칙) · plan.md · prd.md · contracts.md(API 계약 원본) · task_backend.md · task_agent.md · agent.md · task.md
