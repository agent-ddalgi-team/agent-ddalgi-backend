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

## 문서
AGENTS.md(작업 규칙) · plan.md · prd.md · contracts.md(API 계약 원본) · task_backend.md · task_agent.md · agent.md · task.md
