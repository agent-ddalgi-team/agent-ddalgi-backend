# agent-ddalgi-backend

회사 자료로 소개서 초안을 만들고 저장·편집·검증·승인·PDF/DOCX 출력을 제공하는 Python/FastAPI 백엔드와 Agent다. 프론트는 별도 저장소다.

## 평소 실행

아래 명령은 **백엔드 저장소 루트**에서 실행한다. 자신의 기존 `.env`·자료·DB 설정을 사용한다. 의존성 변경을 받은 경우 먼저 `uv sync --locked --group dev`로 설치본을 맞춘다.

```powershell
uv run python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

API 문서: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs). 기본 모드는 `mock`이며 실제 AI는 `AGENT_MODE=llm`과 키/모델 설정이 필요하다. `.env`에 llm이 지정돼 있으면 위 명령도 실제 호출을 허용한다. Windows 실제 AI 실행은 기존 도구를 사용할 수 있다.

```powershell
.\scripts\run_llm.ps1 -ContentReview -TextProposals -CheckOnly
# 설정 확인 후 같은 명령에서 -CheckOnly를 빼면 서버를 시작한다.
```

`-CheckOnly`는 AI 호출·DB 초기화·서버 시작 없이 설정만 확인한다. 시연 자료를 사용할 때만 `-Demo`를 추가한다. 기본 `runtime`과 예산 제한 `interactive`는 다르며, [상세 실행 안내](docs/plan.md#backend-ai-run)의 옵션을 따른다.

- **PDF:** 서버에 Chrome/Edge 등 Chromium 브라우저가 필요하다. 다른 설치 위치는 `EXPORT_BROWSER_PATH`로 지정한다.
- **DOCX 실제 검사·승인·출력:** LibreOffice와 `EXPORT_LIBREOFFICE_PATH` 설정이 필요하다. 엔진이 없거나 검사에 실패하면 승인이 차단된다.
- 프론트는 해당 저장소에서 따로 실행한다. 백엔드 기본 주소는 `http://127.0.0.1:8000`이다. [출력 도구 상세](docs/plan.md#backend-output-tools)

## 테스트

```powershell
$env:PYTHON_DOTENV_DISABLED = '1'
uv run --group dev python -B -m pytest -q -p no:cacheprovider
# 이 셸에서 다시 평소 실행할 때는 아래 값을 해제한다.
Remove-Item Env:PYTHON_DOTENV_DISABLED
```

기본 검사는 임시 DB·가상 AI로 동작·버전·접근·실패 복구를 확인한다. 실제 AI 품질 시험과 구분한다. [HTTP·브라우저·출력 검사](docs/plan.md#backend-http-checks), [실제 검증 결과](docs/task_backend.md)를 참고한다.

<a id="folder-roles"></a>
## 현재 폴더 구조

일반 문서·프롬프트·고정 자료의 이동이 반영된 **현재 저장소 구조**다. 같은 커밋을 받으면 Git 추적 파일의 위치도 같다. 개인 PC의 캐시·DB·설정과 VS Code의 숨김/접기 표시는 다를 수 있다.

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

옛 루트 `prompts/`·`contracts/`·`fixtures/`·`handoff/`에 사본을 다시 만들지 않는다. 실제 테스트용 `tests/fixtures/`는 유지한다. [공통 경로 기준](AGENTS.md#공통-경로-기준), [폴더별 상세 역할](docs/plan.md#backend-folders-storage)

<a id="data-storage"></a>
## 자료 저장·보관

- 저장 루트는 `PRIVATE_RUNS_DIR`, DB는 `DB_PATH`로 정한다. 기본은 `private_runs/app.sqlite3`이며 상대 경로는 저장소 루트 기준이다. 개인 설정·시연 실행 옵션이 있으면 그 값을 사용한다.
- DB에는 세션·추출문/사실·문서/버전·검증/승인 상태와 파일 참조 등이 저장된다. 실제 첨부·사진·출력 파일은 저장 루트의 파일 폴더에 저장된다.
- `<PRIVATE_RUNS_DIR>/registered/`는 명시적으로 적재한 등록 자료, `sess_…/`는 세션 첨부·사진·체크포인트와 `artifacts/` 출력·미리보기다. 세션 데이터는 종료/만료 시 정리되고 등록 자료는 세션 종료만으로 삭제되지 않는다.
- 최종 PDF/DOCX를 오래 보관하려면 다운로드해 팀 보관 위치에 둔다. `.env`·DB·회사 원문·가상환경은 Git으로 공유하지 않는다. DB·등록 자료·평가 기록의 백업은 별도 절차가 필요하다.
- `data/mock/`, `data/real/`, `data/private_runs/` 재구성은 **아직 적용하지 않았다.** 현재 `registered/`에는 실제·시연 자료가 함께 있을 수 있다.

<a id="path-selection"></a>
화면에서 작업할 때 세션 폴더를 직접 지정할 필요는 없다. 저장 루트 변경·등록 적재·평가·백업 때만 경로를 지정한다. 설정 변경이 기존 자료를 자동 이동하지 않으며 DB가 참조하는 파일·SQLite WAL/SHM을 임의로 이동/삭제하면 안 된다. [상세 저장·경로 안내](docs/plan.md#backend-folders-storage)

<a id="folder-lookup"></a>
## 문서 찾아가기

| 찾는 내용 | 원본 문서 |
|---|---|
| 작업 시작 규칙·공통 경로 | [AGENTS.md](AGENTS.md) |
| 기획·제품 요구사항·Agent 설계 | [plan](docs/plan.md), [prd](docs/prd.md), [agent](docs/agent.md) |
| API·자료 첨부/선택·C-05 편집 복귀·검증/승인 | [contracts](docs/contracts.md), [API 예시](docs/api_examples_v1.1.json) |
| 추출문·직접 첨부 원본·오타 정정 | [추출문](docs/contracts.md#source-text-api), [원본](docs/contracts.md#source-original-api), [정정](docs/contracts.md#source-typo-correction) |
| 현재 담당별 작업·남은 일·실제 검증 | [공통 task](docs/task.md), [백엔드 task](docs/task_backend.md), [Agent task](docs/task_agent.md) |
| 상세 실행·폴더/저장·검사·DB 관리·적재 | [plan 9절](docs/plan.md#backend-operations-guide) |
| 화면 설계·참고 아이디어 | [화면](docs/screens/README.md), [idea](docs/idea.md) |

API의 현재 계약은1.13, DB는v11이다. 최신 모델/버전의 원본은 계약·코드를 따른다. 백엔드 API 구현과 별도 프론트 화면 연결, 표본 검수와 일반 품질 완료를 구분한다. 이 README가 담당별 완료 상태를 대신하지 않는다.

<details>
<summary>처음 설치하거나 재설치할 때</summary>

Python 3.13 이상과 uv를 준비하고 저장소 루트에서 의존성을 설치한다. 다른 PC의 `.venv`를 복사하지 않는다.

```powershell
uv sync --locked --group dev
# .env가 없을 때만 예시를 복사한다.
if (!(Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
```

macOS/Linux에서는 `.env`가 없을 때 `cp -n .env.example .env`로 준비한다. 실제 키·모델·저장 경로·출력 도구는 개인 환경에 맞추고 `.env`는 커밋하지 않는다. 등록 회사 자료·시연 DB는 별도 전달/적재가 필요하며 Git pull로 생기지 않는다.

DART 기업 검색/공개자료는 서버의 `DART_API_KEY` 설정이 필요하며 프론트에 키를 넣지 않는다. 특허청·나라장터는 현재 미연결이다. [등록·시연·적재](docs/plan.md#backend-registered-demo), [DB 관리](docs/plan.md#backend-db-maintenance)

</details>
