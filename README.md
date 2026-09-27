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

처음에는 다음 순서로 읽는다. 코드 변경 전에는 [AGENTS.md](AGENTS.md)의 작업 규칙을 확인한다.

| 순서 | 문서 | 알 수 있는 것 |
|---|---|---|
| 1 | [프로젝트 아이디어](docs/idea.md) | 무엇을 만들고 누가 사용하는지; 범위 축소안은 제안 |
| 2 | [전체 구조 그림](docs/site_design.png) | 화면·서버·AI·저장소의 관계와 현재 상태 |
| 3 | [개발 계획](plan.md) · [제품 요구사항](prd.md) | 확정 범위·미정 결정·사용자 흐름·완료 기준 |
| 4 | [공통 계약](contracts.md) | 데이터·API 기준; 7절은 현행 코드와의 차이 및 합의 대기 목록 |
| 5 | [백엔드 작업](task_backend.md) 또는 [Agent 작업](task_agent.md) · [Agent 설계](agent.md) | 내 담당 작업·코드 위치·남은 연결·검증할 내용 |
| 6 | [공통 연결표](task.md) | 담당자 간 연결 지점과 결과 기록 위치 |

2026-09-27에는 개발 전 문서를 정리했다. 기존 BE/AG 작업 상태와 테스트 기록은 유지했으며 실행 코드·의존성·DB는 바꾸지 않았다. 공통 계약의 확정 버전은 1.1, 데이터 schema_version은 1.0이며, 검토 메모의 제안은 합의 후 반영한다.

Stitch 화면 설계와의 연결 기준은 [prd.md 3.2절](prd.md), 화면 상태별 데이터 연결은 [contracts.md 7.5절](contracts.md)을 따른다. 추가 기능의 채택 여부는 [plan.md 4.1절](plan.md)에서 관리한다. 화면 시연·예시 응답과 실제 기능 완료는 구분한다.
