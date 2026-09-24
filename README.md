# agent-ddalgi-backend
거산케미칼 회사소개서 초안 도우미 — 백엔드 + Agent (FastAPI, uv, Python 3.13)

## 시작
uv sync
Copy-Item .env.example .env        # 값은 각자 채움, 커밋 금지
uv run uvicorn main:app --host 127.0.0.1 --port 8000   # http://127.0.0.1:8000/docs

## 문서
AGENTS.md(작업 규칙) · plan.md · prd.md · contracts.md(API 계약 원본) · task_backend.md · task_agent.md · agent.md · task.md
