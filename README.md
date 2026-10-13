# agent-ddalgi-backend

회사 자료로 소개서 초안을 만들고 저장·편집·검증·승인·PDF/DOCX 출력을 제공하는 Python/FastAPI 백엔드와 Agent다. 프론트는 별도 저장소다.

## 주요 기능

자료 첨부·선택·파싱, 근거 기반 초안과 수정안, 문서 버전 관리, 내용·배치 검증, 승인 출력과 세션 정리를 제공한다. API 구현·프론트 연결·실제 AI 품질의 완료 범위는 [담당별 현황](docs/task.md)에서 구분한다.

## 준비·환경설정

- **Python 3.13 이상·uv**를 준비한다. 다른 PC의 `.venv`를 복사하지 않는다.
- **PDF:** Chrome/Edge 등 Chromium 브라우저가 필요하다. **DOCX 검사·승인·출력:** LibreOffice와 `EXPORT_LIBREOFFICE_PATH` 설정이 필요하다. [출력 도구 설정](docs/plan.md#backend-output-tools)
- `.env`가 없을 때만 [.env.example](.env.example)을 복사한다. 실제 키·모델·저장 경로·출력 도구는 개인 환경에 맞추고 기존 파일을 덮어쓰지 않는다.

## 설치·실행

아래 명령은 **백엔드 저장소 루트**에서 실행한다. 처음 설치하거나 의존성이 바뀌면 첫 명령으로 설치본을 맞춘다.

```powershell
uv sync --locked --group dev
uv run python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

API 문서는 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)다. 프론트는 별도로 실행한다. 기본 AI 모드는 `mock`이지만 기존 `.env`가 `AGENT_MODE=llm`이면 실제 호출을 허용한다. Windows 실제 AI 도구와 시연·예산 옵션은 [상세 실행 안내](docs/plan.md#backend-ai-run)를 따른다.

## 테스트

```powershell
$env:PYTHON_DOTENV_DISABLED = '1'
uv run --group dev python -B -m pytest -q -p no:cacheprovider
# 이 셸에서 평소 실행으로 돌아가기 전에 해제한다.
Remove-Item Env:PYTHON_DOTENV_DISABLED
```

기본 검사는 임시 DB·가상 AI를 사용하며 실제 AI 품질 평가와 구분한다. [HTTP·브라우저·출력 검사](docs/plan.md#backend-http-checks)와 [실제 검증 기록](docs/task_backend.md)을 참고한다. 프론트와 같은 별도 앱 빌드 명령은 없다.

<a id="folder-roles"></a>
## 현재 폴더 구조

```text
backend/
├─ app/                API·서비스·파서·AI 연결
│  ├─ routers/         API 입구
│  ├─ services/        저장·편집·검증·승인·출력·정리
│  ├─ parsers/         자료 읽기
│  ├─ prompts/         AI 추출·작성 지침
│  ├─ resources/       고정 스키마·기본 자료
│  └─ templates/       출력 서식·글꼴
├─ docs/               기획·요구사항·계약·담당별 작업 기록
├─ tests/              자동 검사·tests/fixtures 가상 자료
├─ scripts/            실행·적재·관리·평가 도구
│  └─ experiments/     도구 비교·품질 평가
├─ migrations/         DB 구조 변경 이력
├─ private_runs/       로컬 DB·등록 자료·세션·출력·평가 결과 (Git 제외)
├─ .venv/              설치 의존성 (로컬)
├─ __pycache__/        Python 캐시 (다른 폴더 안에도 생성)
├─ .pytest_cache/      테스트 캐시
├─ .github/            Copilot 지침 연결
├─ .git/               Git 이력
├─ README.md           일상 실행·폴더·문서 안내
├─ AGENTS.md           공통 AI 작업 규칙
├─ CLAUDE.md           AGENTS 지침 연결
└─ main.py, pyproject.toml, uv.lock, alembic.ini, .env 등 실행·설정 파일
```

같은 커밋의 Git 추적 위치는 같지만 개인 DB·캐시·설정과 VS Code 숨김/접기는 다를 수 있다. 옛 루트 prompts/contracts/fixtures/handoff에 사본을 만들지 않는다. [공통 경로](AGENTS.md#공통-경로-기준), [폴더별 상세 역할](docs/plan.md#backend-folders-storage)

<a id="data-storage"></a>
<a id="path-selection"></a>
## 자료 저장·보관

- 기본 DB는 `private_runs/app.sqlite3`다. `PRIVATE_RUNS_DIR`·`DB_PATH`로 바꾸며 상대 경로는 저장소 루트 기준이다. DB는 기록·파일 참조, 실행 저장 영역은 첨부·사진·출력 파일을 보관한다.
- `registered/`는 명시 적재한 등록 자료, `sess_…/`와 내부 `artifacts/`는 작업별 자료·출력이다. 세션 종료·만료 정리와 등록 자료 보관은 다르다. 최종 파일은 다운로드해 별도 보관한다.
- 화면 작업에서는 폴더를 직접 정하지 않는다. 저장 루트 변경·적재·평가·백업 때 지정하며 기존 DB/자료가 자동 이동되지는 않는다. [저장 위치·백업·경로 변경](docs/plan.md#backend-folders-storage)

## 중요한 주의사항

- `.env`·DB·회사 원문·가상환경은 Git으로 공유하지 않는다. 실행 중인 SQLite와 WAL/SHM, DB가 참조하는 자료를 임의 이동·삭제하지 않는다.
- `data/mock/`·`data/real/`·`data/private_runs/` 재구성은 미적용이며 등록 영역에는 실제·시연 자료가 섞여 있을 수 있다.
- 출력 도구가 없거나 검사에 실패하면 출력·승인이 제한된다. DART는 서버의 `DART_API_KEY`가 필요하며 키를 프론트에 넣지 않는다. 특허청·나라장터는 미연결이다.
- API·DB 버전의 원본은 계약과 코드다. 표본 검수를 일반 AI 품질 완료로 간주하지 않는다.

<a id="folder-lookup"></a>
## 문서 찾아가기

| 찾는 내용 | 원본 문서 |
|---|---|
| 작업 시작 규칙·공통 경로 | [AGENTS.md](AGENTS.md), [폴더 관리](AGENTS.md#folder-management), [README 관리](AGENTS.md#readme-management) |
| 기획·제품 요구사항·Agent 설계 | [plan](docs/plan.md), [prd](docs/prd.md), [agent](docs/agent.md) |
| API·자료 첨부/선택·C-05 편집 복귀·검증/승인 | [contracts](docs/contracts.md), [API 예시](docs/api_examples_v1.1.json) |
| 추출문·직접 첨부 원본·오타 정정 | [추출문](docs/contracts.md#source-text-api), [원본](docs/contracts.md#source-original-api), [정정](docs/contracts.md#source-typo-correction) |
| 현재 담당별 작업·남은 일·실제 검증 | [공통 task](docs/task.md), [백엔드 task](docs/task_backend.md), [Agent task](docs/task_agent.md) |
| 상세 실행·폴더/저장·검사·DB 관리·적재 | [plan 9절](docs/plan.md#backend-operations-guide) |
| 화면 설계·참고 아이디어 | [화면](docs/screens/README.md), [idea](docs/idea.md) |

처음 `.env`를 준비하는 방법은 [설치·환경설정 상세](docs/plan.md#backend-initial-setup), 등록 회사 자료·시연 DB 준비는 [적재 안내](docs/plan.md#backend-registered-demo), DB 변경은 [DB 관리](docs/plan.md#backend-db-maintenance)를 따른다.
