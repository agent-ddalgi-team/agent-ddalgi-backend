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

Windows의 예산 제한 시연(`interactive`)은 아래처럼 실행한다. `PRIVATE_RUNS_DIR`과 `DB_PATH`는 사용하려는 시연 폴더/DB를 먼저 지정한다. 실제 `.env`는 수정하지 않는다.

```powershell
$env:OPENAI_EXECUTION_MODE = 'interactive'
.\scripts\run_llm.ps1 -Demo -ContentReview -TextProposals -RequestTimeoutSeconds 180 -MaxInputChars 40000 -MaxReviewInputChars 400000 -MaxOutputTokens 32000 -MaxRetries 0 -CheckOnly
# 설정 확인 뒤 같은 명령에서 -CheckOnly를 빼면 서버를 시작한다.
```

`-CheckOnly`는 API 호출·DB 초기화·서버 시작 없이 유효 설정을 확인한다. 실행 스크립트는 요청 옵션과 `OPENAI_TRIAL_*` 내부 상한을 함께 맞춘다. `trial`/`interactive`는 기존 모델·재시도 0·모드별 범위·예산 검사를 유지하며, 범위를 벗어나면 시작을 거부한다. 일반 `runtime`의 확대 범위는 유지한다. 직접 uvicorn으로 실행할 때는 `.env.example`의 대응 설정을 함께 지정해야 한다.

### 현재 PC의 시연 실행·인계 (2026-10-06)

백엔드 작업본은 `C:\backend`의 `test` 브랜치다. 기존 시연 자료를 이어서 사용하려면 아래 폴더와 DB를 지정한다. 서버 재시작은 DB를 재생성하지 않는다. 경로의 DB가 없으면 실행을 멈추고 복원 여부를 확인한다.

```powershell
Set-Location C:\backend
$env:PRIVATE_RUNS_DIR = 'C:\backend\private_runs\demo_preview_20260929_131826'
$env:DB_PATH = Join-Path $env:PRIVATE_RUNS_DIR 'app.sqlite3'
if (-not (Test-Path -LiteralPath $env:DB_PATH -PathType Leaf)) { throw '기존 시연 DB가 없습니다.' }
$env:EXPORT_LIBREOFFICE_PATH = 'C:\Program Files\LibreOffice\program\soffice.com'
$env:OPENAI_EXECUTION_MODE = 'runtime'
.\scripts\run_llm.ps1 -Demo -ContentReview -TextProposals -CheckOnly
# 설정 확인 후 서버 시작 (8000 포트의 기존 서버가 종료된 상태에서 실행)
.\scripts\run_llm.ps1 -Demo -ContentReview -TextProposals
```

스크립트 기본값은 입력 200,000자·내용 검증 **400,000자**·출력 64,000토큰·요청 제한 180초·재시도 2회다. 검증 한도를 별도 옵션으로 낮출 필요가 없다. 이 실행은 실제 AI 호출을 허용하며 `.env`의 모델·키를 그대로 사용한다. `-CheckOnly` 자체는 AI 호출이나 서버 시작을 하지 않는다.

프론트 작업본은 `C:\frontend`다. 해당 폴더에서 `npm run dev -- --host 127.0.0.1`로 실행한 뒤 [백엔드 연동 화면](http://localhost:5173/)을 연다. `?preview=1`은 가상 자료 미리보기다. 백엔드 API 문서는 [8000/docs](http://127.0.0.1:8000/docs)에서 확인한다. 이미 서버가 켜져 있으면 중복 실행하지 않는다.

현재 시연 DB에는 고객 제안용 8쪽(revision 7)과 품질 심사용 9쪽(revision 3)의 PDF·LibreOffice DOCX 승인/출력 결과가 있다. 세션·출력 만료와 종료 정리 정책은 계속 적용되므로 DB 보존만으로 승인 파일을 영구 재다운로드할 수 있는 것은 아니다. DOCX 배치 검사는 LibreOffice 기준이며 Word에서는 글꼴·쪽 나눔이 달라질 수 있다.

현재 API 계약은 1.9, 데이터 형식은 1.0, DB는 v11, 출력 템플릿은 `template_v10`이다. 서버 기능 및 두 시연 표본 검수는 완료했다. 전체 회귀 1,807 passed/10 skipped와 이후 사진 분할 관련 10 passed는 각각의 검사 기록이다. 다른 목적·업종의 AI 품질, 제외 ISO 자료의 회사 확인, 별도 프론트 작업 공유는 남아 있다. 완료 범위·제한·검사 근거는 [백엔드 인계 기록](task_backend.md#backend-handoff-20261006)을 따른다.

### 테스트
```bash
uv run pytest
```

백엔드 통합 검사는 mock(가짜 응답)으로 실행한다. 실제 LLM 분석·초안과 AG-07 원문 의미 검증의 구현/별도 시험 기록은 `task_agent.md`를 따른다. 기본 유료 호출 한도에는 content_review가 포함되지 않으므로 의미 검증 구현과 일반 서버의 호출 허용을 구분한다. 기본 통합 검사와 별도로 실제 AI·실자료 시연 검수를 수행했으며 범위와 결과는 [백엔드 작업표](task_backend.md)에 기록한다.

### API 요청·응답 형식 (Pydantic, 계약 1.9)

Pydantic 모델은 **화면이 보내는 값과 서버가 돌려주는 값의 형식**을 검사한다. `app/models.py`에 선언하며, DB 테이블을 정의하는 `app/orm_models.py`와 역할이 다르다. 예를 들어 입력 버전은 숫자 `2`이고 문자열 `"2"`가 아니며, 사용자 확인은 `true`이고 문자열 `"true"`가 아니다. 형식이 맞아도 세션 소유자·최신 버전·근거·승인 조건 검사는 별도로 통과해야 한다.

- 버전은 1 이상의 정수, 목표 쪽수는 1/4/6/8/10, 필수 문장은 공백만 입력할 수 없다. 선택 자료·대상 블록 ID는 중복 없이 보낸다.
- 작성 조건/선택 변경은 `expected_input_revision`과 함께 `brief` 또는 `selected_source_ids`를 보낸다. 빈 목록 `[]`은 전체 선택 해제이고, 변경할 필드가 없는 요청은 거부한다.
- 형식 오류는 `400 INVALID_REQUEST`, 최신 버전 불일치는 `409`, 명시적 확인 누락 등 업무 조건 실패는 `422`다. 모두 `{error:{code,message,retryable,details,request_id}}`이며 입력 원문을 오류 본문에 돌려주지 않는다.
- `/docs`와 `/openapi.json`에 30개 API의 모델·오류·파일 응답을 표시한다. 구조가 있는 JSON 응답 28개와 이미지/출력 파일 응답 2개가 있다. Export는 새 작업 202와 준비된 결과 재사용 200을 구분한다.
- 긴 작업은 접수 후 Job을 조회한다. `result_ref`는 작업 종류별 결과 ID이며 Preflight·Document·Proposal 등 결과를 별도 GET으로 읽는다. `succeeded`인 검사 Job도 검사 결과 자체는 failed일 수 있다.

현재 형식과 예시는 [contracts.md](contracts.md), [API 예시](handoff/api_examples_v1.1.json)를 따른다. 예시 파일명은 기존 참조를 위해 유지하고 내부 계약 버전은 1.9이다. 기존 프론트 S01~S03 연결 기록은 [백엔드 작업표](task_backend.md)를 따른다. C-05 가상 화면 연결·실제 AI 보완 자료 복귀 및 DOCX 승인·출력 표본 검수는 완료했다. 별도 프론트 계약 사본 공유와 일반 품질 평가는 후속이다.

### 자료 변경 후 기존 편집으로 복귀 (C-05)

선택 자료·작성 조건을 바꾸면 기존 문서의 편집을 보존하고 다음 순서로 복귀한다. 경로 앞에는 `/api/v1/sessions/{sid}`를 붙인다.

1. `POST /preflights`로 최신 입력을 점검하고 결과를 조회한다.
2. 결과를 확인한 사용자가 `POST /documents/{did}/impact-reviews`에 현재 문서·입력 버전, `preflight_id`, `confirmed: true`를 보낸다. 응답의 영향 목록은 `GET /documents/{did}/impact-reviews/{rid}`로 다시 조회할 수 있다.
3. 변경할 편집 연산 `operations`, 필요한 `reference_updates`, 필수 유지 사유 `keep_reason`을 `POST /documents/{did}/impact-reviews/{rid}/apply`로 보낸다. 정확히 같은 supported 사실·근거는 최신 Fact ID로 연결하고, 제외되거나 달라진 근거는 명시적으로 수정하거나 해당 블록을 삭제해야 한다.
4. 응답의 `validation_job_id`로 전체 내용 검증 결과를 조회한 뒤 편집을 계속한다. 승인·PDF 배치는 새 문서 기준으로 다시 확인한다.

DB v11의 기존 `impact_reviews`·`confirmations`를 사용한다. v9 DB는 자동 이전하지 않으며 C-05 요청에 `409 IMPACT_HISTORY_UNAVAILABLE`를 반환한다. 프론트 복귀 화면은 가상 자료로 연결 검사했고, 실제 AI로 보완 자료 추가 후 기존 편집 보존·근거 연결·재검증 표본을 확인했다. 수치 변경·자료 제외와 다른 업종의 일반 품질 평가는 별도다. 상세 규칙은 [공통 계약](contracts.md), 결과는 [C-05 작업 기록](task_backend.md#c05-edit-return-20260930)을 따른다.

실제 사용 DB에 자료를 넣지 않고, 임시 DB와 mock 자료로 형식·S01 흐름을 확인하려면 다음 검사를 실행한다.

```powershell
uv run pytest -q tests/test_api_contract.py
```

### 자료 첨부부터 초안까지 요청하는 순서

1. `POST /api/v1/sessions`로 작업 세션을 만들고 응답의 소유자 쿠키를 유지한다.
2. `POST /sessions/{sid}/sources`로 파일을 첨부한다. Job을 조회해 읽기 결과를 확인하고 `GET /sessions/{sid}/sources`에서 자료 ID를 얻는다.
3. `PATCH /sessions/{sid}/inputs`로 자료 ID를 선택한다. 첨부와 선택은 별개이며 응답의 새 `input_revision`을 다음 요청에 사용한다.
4. `POST /sessions/{sid}/preflights`에 최신 입력 버전을 보낸다. Job이 성공하면 결과의 `preflight_id`로 점검 내용을 조회한다.
5. 사용자가 결과를 확인한 뒤 `POST /sessions/{sid}/drafts`에 `preflight_id`, `input_revision`, `confirmed: true`를 보낸다. Job 성공 후 결과의 문서 ID로 초안을 조회한다. 2~5의 주소 앞에는 공통 `/api/v1`을 붙인다.

`Idempotency-Key`는 **한 번의 요청을 구분하는 번호**다. 통신 오류로 같은 요청을 다시 보낼 때는 같은 키를 써야 파일이나 작업이 중복 생성되지 않는다. 본문·파일 종류·역할을 바꾸면 새 키를 사용한다. 같은 키의 202 응답은 접수 당시 기록이므로 최신 성공/실패는 Job GET으로 확인한다. 이미 실패한 AI 작업을 다시 실행할 때는 새 키를 사용하고, 소비된 초안 확인은 새 점검·사용자 확인을 거친다. 저장 중 오류가 나면 이번 업로드의 파일/DB 행을 정리하며 기존 선택과 파일을 유지한다.

위 흐름은 임시 DB·가짜 자료로 검사할 수 있어 실제 DB 적재를 먼저 할 필요가 없다.

```powershell
uv run pytest -q tests/test_orm_workflow.py tests/test_be04.py
```

### 실제 HTTP와 화면 연결 검사 (자료 적재 불필요)

아래 명령은 별도 임시 DB와 가짜 AI 응답으로 서버를 띄워 검사하고, 끝나면 서버·테스트 자료를 정리한다. `.env`와 현재 DB를 사용하지 않으며 API 키가 필요 없다. 첫 명령은 실제 HTTP 요청으로 쿠키·전체 S01 흐름·중복 요청·접근 차단·종료를 확인한다.

```powershell
.\.venv\Scripts\python.exe -X utf8 -B scripts/check_s01_http.py

# 프론트 의존성 설치/빌드 후: 실제 Chrome/Edge 버튼·Vite 프록시까지 확인
.\.venv\Scripts\python.exe -X utf8 -B scripts/check_s01_http.py --frontend C:\frontend
```

화면 검사는 Node 24와 설치된 Chrome/Edge를 사용한다. 다른 브라우저 실행 파일은 `S01_BROWSER_PATH`로 지정한다. 기존 브라우저 프로필 대신 임시 프로필을 사용하고, 예시 화면은 프론트의 무시되는 `dist/s01-check.png`에 저장한다. 응답 유실 뒤 업로드 재시도·새로고침 복원·동의 전 생성 차단·입력 충돌·이미지만 선택한 경우·첨부 삭제·만료 복구를 포함한다. 기본 명령은 S01까지만 검사한다. 편집·승인·실제 PDF 다운로드까지 검사하려면 아래처럼 실행한다.

```powershell
.\.venv\Scripts\python.exe -X utf8 -B scripts/check_s01_http.py --publication --frontend C:\frontend --timeout 120
```

`--publication`은 설치된 Chrome/Edge를 PDF 렌더러로 사용한다. macOS의 `/Applications` Chrome·Edge도 자동 탐색하며 별도 위치는 `--browser-path`로 지정한다. 실제 HTTP로 PDF 바이트·반복 다운로드·수정 후 승인 무효화를 확인한다. `--frontend`를 함께 쓰면 편집/AI 제안 비교·적용·거절을 화면에서 확인하고, 미리보기는 `dist/publication-check.png`, 편집 화면은 `dist/s02-publication-check.png`에 저장한다. AI는 계속 mock이며 실제 AI 품질이나 DOCX 승인·출력 확인을 뜻하지 않는다.

사진 포함 HTTP 검사는 다음처럼 실행한다(Mac/Linux; Windows에서는 위 Python 실행 경로 사용).

```bash
.venv/bin/python -B scripts/check_s01_http.py --publication --photos --timeout 120
```

`--photos`는 가상 PNG 1장을 텍스트와 함께 업로드해 사진 원본 바이트·소유자별 접근 차단·편집 후 사진 보존·실제 PDF 이미지의 픽셀 일치·각 쪽 PNG 미리보기를 확인한다. 검사·승인·출력의 파일 ID와 저장 해시, 동일 바이트 재다운로드, 세션 종료 후 사진 접근 차단과 임시 자료 정리도 검사한다. 이 옵션은 HTTP 검사에 적용되며 프론트의 사진 UI를 검증했다는 뜻은 아니다. `--publication`과 함께 사용한다.

화면 사용 순서는 **초안 편집 → 변경 내용 저장 → 내용 검증하기 → PDF 배치 확인 → 미리보기/동의 → PDF 최종 승인 → PDF 파일 준비 → PDF 다운로드**다. 저장되지 않은 내용이 있으면 검증·승인·다운로드를 막는다. 다른 탭에서 문서가 바뀌면 작성 중인 내용은 보존하며 필요한 문장을 복사한 뒤 최신 저장본에서 다시 편집한다. 새로고침으로 저장하지 않은 내용은 복원되지 않는다. 현재 화면은 경고 없는 `passed` 검증만 승인한다. D-07 서버 강제는 구현했으며 개별 경고 확인 UI·계약 1.5 연결은 후속이다.

### DB 개발 (SQLAlchemy Core + ORM + Alembic)

- 기존 가상환경을 사용한다. 의존성/개발용 테스트 도구는 `uv sync --locked --group dev`로 맞춘다.
- 기본 DB는 프로젝트의 `private_runs/app.sqlite3`다. `.venv`와 분리되어 있으며 `DB_PATH` 또는 `PRIVATE_RUNS_DIR` 설정을 바꾸면 사용하는 파일이 달라질 수 있다.
- `app/db.py`의 `connect(settings.db_path)`가 SQLAlchemy Engine/Core로 쿼리를 실행한다. 정상 종료 시 commit, 예외 시 rollback하며 사용을 끝낸 DB 연결은 닫는다. 함께 성공해야 하는 저장 작업은 같은 `with connect(...)` 안에 둔다.
- 기존 `?` 자리표시자 SQL과 행 접근을 지원한다. SQLAlchemy의 `text()`·`select()`·`insert()` 등 Core 표현식도 같은 연결의 `execute()`로 사용할 수 있다. 사용자 값을 SQL 문자열에 직접 끼워 넣지 않고 매개변수로 전달한다.
- 쓰기 순서 보호가 필요한 기존 경로는 `immediate=True`/`BEGIN IMMEDIATE`를 유지한다. `conn.in_transaction`은 SQLAlchemy의 자동 시작 표시가 아닌 실제 SQLite 트랜잭션 상태다.
- `app/orm_models.py`는 ERD v2의 **25개 테이블을 Python 클래스로 표현한 ORM 모델**이다. `app/models.py`는 화면과 주고받는 API 형식을 검사하는 Pydantic 모델이다. 역할이 달라 두 파일을 구분한다.
- `app/db.py`의 `init_orm_db()`는 Alembic으로 빈 DB를 생성하고 변경 이력을 적용한다. 현재 구조는 v11 / `20260929_01`이며 v10 기준 이력 `20260928_01`은 변경하지 않는다. 미관리 v10은 고정 기준 구조와 일치할 때만 이력 관리에 연결하고 기존 v9는 덮어쓰지 않는다. 서버의 `init_db()`는 v11 구조를 검사하며, v10이면 `alembic upgrade head`를 먼저 실행하도록 안내한다. 기존 v1~9 호환 초기화는 유지한다.
- 새 쿼리는 `with orm_session(settings.db_path) as db:` 안에서 `db.add(...)`, `db.scalars(select(Source))` 같은 ORM 방식으로 작성할 수 있다. 여러 행이 함께 저장되어야 하면 같은 세션을 사용한다. 기존 서비스 SQL은 Core 호환 연결을 계속 사용한다. LangGraph 체크포인트 연결도 유지한다.
- 새 DB는 원본 버전·읽기 실행·입력 변경·선택 자료·최종 동의를 기록한다. 이미 선택한 읽기 실행은 재읽기로 덮어쓰지 않으며, 세션 종료/만료 시 이력의 비공개 내용도 함께 비운다. 개별 경고 확인 API는 계약 1.5, 자료 변경 영향 검토·편집 복귀 API는 계약 1.6에서 기존 테이블에 연결했다. C-05 프론트 화면은 후속 작업이다.

#### DB 구조 생성과 변경 이력 (자료 적재와 별개)

마이그레이션은 ‘테이블에 열을 추가했다’ 같은 **DB 구조 변경의 기록**이다. ORM 파일만 수정해도 기존 DB 파일이 자동으로 바뀌지는 않으므로, Alembic 변경 파일을 만들어 적용한다. 첫 버전 `20260928_01`은 25개 테이블의 당시 SQL을 고정해 두며 이후 ORM 수정에 따라 과거 이력이 바뀌지 않는다.

```powershell
# .env의 DB_PATH 또는 PRIVATE_RUNS_DIR가 가리키는 DB에 적용
uv run alembic upgrade head
uv run alembic current
uv run alembic check

# 다른 빈 테스트 DB를 명시적으로 만들 때
uv run alembic -x db_path=private_runs/schema_test/app.sqlite3 upgrade head
```

`upgrade head`는 변경을 최신 버전까지 적용하고, `current`는 적용된 버전을 출력한다. `check`는 ORM과 DB 사이에 Alembic이 감지하는 차이가 있는지 확인한다. 업무용 테이블 25개와 별도로 `alembic_version`이라는 관리 테이블 1개가 생긴다. 여기에 있는 버전 번호는 회사 자료가 아니다. 예전 v9 DB 경로를 지정하면 쓰기 전에 거부하므로 새로운 경로를 설정해야 한다.

자료·원본 버전·읽기 실행은 서로 연결되어 있어 `check`/자동 생성 시 순환 외래 키의 정렬 경고가 발생할 수 있다. 이 검사는 모든 제약의 보존을 보장하지 않으므로 새 변경 파일에서 아래 검토·시험 절차를 따른다.

v11은 ERD에 FK로 표시되었지만 DB 제약이 없던 16개 관계를 추가한다. 입력/문서 버전은 세션·문서 식별자를 묶어 확인하고, 검증/승인/산출물/다운로드와 운영 기록은 대상의 존재를 확인한다. 미리보기는 배치 결과보다 먼저 저장되므로 해당 FK만 트랜잭션 종료 시 검사한다. 승인 대상의 문서·입력·형식 일치와 JSON 내부 근거는 계속 서버에서도 검사한다. 기존 자료는 자동 보정하거나 지우지 않으며 새 관계에 맞지 않는 데이터가 있으면 업그레이드가 실패하고 원래 상태로 돌아간다. 자료 적재는 별도 작업이다. D-07 경고 확인과 C-05 영향 검토는 기존 v11 테이블을 사용하며 이번 C-05에 DB 구조 변경은 없다.

이후 구조를 바꾸는 순서:

1. 개발용 DB를 `upgrade head`로 맞추고 `app/orm_models.py`를 수정한다.
2. `uv run alembic revision --autogenerate -m "describe_change"`로 변경 초안을 만든다.
3. `migrations/versions/`의 새 파일을 검토한다. 이름 변경이 삭제·추가로 생성되지 않았는지, SQLite 테이블 재생성에서 기존 CHECK·UNIQUE·FK·인덱스가 유지되는지 확인한다. 기존 열 제거나 필수 열 추가에는 데이터 보존·변환 규칙도 작성한다.
4. 임시 DB에서 적용과 필요한 되돌리기를 시험한다. SQLite batch 재생성 시 이름 없는 CHECK는 수동으로 포함해야 한다. [Alembic 자동 생성의 한계](https://alembic.sqlalchemy.org/en/latest/autogenerate.html)와 [SQLite batch 제약 보존](https://alembic.sqlalchemy.org/en/latest/batch.html)을 참고한다.
5. 실행 중인 서버를 멈추고 DB와 필요한 파일을 백업한 뒤, 대상 경로를 확인하고 `upgrade head` → `current` → `check`를 실행한다.

DDL과 이력 기록은 하나의 명시적 트랜잭션으로 처리하고 실패 시 함께 취소한다. 마지막에 외래 키 연결을 검사한다. 기준 버전 자체를 지우는 `downgrade base`는 업무 데이터가 하나라도 있으면 거부한다. 변경 파일은 Git에 보관하며 DB 파일과 `.env`는 커밋하지 않는다. 실제 자료 적재는 현재 보류 상태다.

#### 기존 자료를 새 DB에 다시 넣기

```powershell
# 아직 존재하지 않는 새 폴더를 지정한다. 기존 DB/자료/.env는 바꾸지 않는다.
.\.venv\Scripts\python.exe -X utf8 scripts/rebuild_database.py --target-dir private_runs/erd_v2
```

이 프로젝트의 real/demo 묶음 메타데이터와 보관 파일을 검증해 다시 적재하는 명령이다. 기존 mock·세션·작성 문서·승인 기록은 옮기지 않는다. 대상 폴더가 이미 있으면 중단하며, 실패해도 자동 삭제하지 않는다. 자료나 사진 허가를 추측해서 만들지 않는다. 최적화한 카탈로그는 검증된 서비스용 사본을 유지한다. 기존 사진 묶음에 새 사진을 추가하는 증분 적재는 실행 이력 보존 때문에 v10에서 제한하며, 같은 묶음 재실행과 공개 허가 갱신은 지원한다.

성공 후 `.env`의 `PRIVATE_RUNS_DIR=private_runs/erd_v2`로 경로를 지정하고 서버를 다시 시작한다. `DB_PATH`를 별도로 설정했다면 그 경로도 새 DB와 일치시켜야 한다. 이전 `private_runs/app.sqlite3`와 등록 자료는 복구용으로 보관한다. 기존 DB 경로에서 서버를 실행하면 만료 정리가 동작할 수 있으므로 백업 확인에는 읽기 전용 도구를 사용한다. 개발 가상환경은 기존 `.venv` 그대로 사용한다.

검증 결과와 이번 PC에서 적용한 경로는 [task_backend.md 6.22절](task_backend.md#622-erd-v2-orm과-새-db-재적재-2026-09-28)에 기록한다. 자동화 테스트는 임시 DB만 사용한다.

**현재 로컬 상태(2026-09-29):** 사용자 요청으로 자료 적재는 보류했다. `.env`는 `private_runs/erd_v2`를 사용하며, 활성 DB는 **v11 / Alembic `20260929_01`, 업무 테이블 25개·업무 데이터 0건**이다. Alembic 관리 테이블/버전 행은 별도로 유지한다. 이전 `private_runs/app.sqlite3`와 연결 파일은 사용자 요청으로 삭제했다(6.28절). 원본 자료와 새 폴더의 준비용 복사본은 보관하며 이번 구조 변경에서 수정·삭제하지 않았다. 나중에 적재를 요청하면 준비된 `import_packages/real`·`import_packages/demo` 묶음을 기존 적재기로 넣을 수 있다. 기존 대상 폴더에 위 재생성 명령을 다시 실행하면 보호 검사로 중단한다. 다른 PC의 기존 v10 DB는 새 코드를 받은 뒤 서버를 중지하고 `uv run alembic upgrade head`로 갱신한다. 적용·검증 결과는 `task_backend.md` 6.31절을 따른다.

### PDF/DOCX 출력 준비 (BE-07, D-03)
- **DOCX**는 python-docx로 만든다. 실제 배치 검사·승인·다운로드를 사용하려면 서버에 LibreOffice를 설치하고 `EXPORT_LIBREOFFICE_PATH`에 절대 실행 파일 경로를 지정한다(Windows 예: `C:\Program Files\LibreOffice\program\soffice.com`, Linux 예: `/usr/bin/libreoffice`). 자동 활성화하지 않으며 엔진 미설정/없는 경로는 `overflow=not_checked`로 승인을 차단한다. 변환 실패·시간 초과·측정 실패도 통과 처리하지 않는다.
  - 각 검사에 독립 headless 프로필을 사용하고 같은 DOCX의 변환 PDF에서 실제 쪽수·쪽별 본문/사진·인쇄 영역을 검사한다. PNG 미리보기도 이 변환본에서 만든다. 검사 뒤 승인된 원래 DOCX를 그대로 발행하며 AI/렌더를 다시 호출하지 않는다.
  - `EXPORT_RENDER_TIMEOUT_S`는 DOCX 변환·측정에도 적용한다. Windows의 긴 프로필 경로는 짧은 경로 이름과 긴 경로 삭제 지원으로 처리한다.
- **PDF**는 서버에 설치된 Chromium 계열 브라우저(Google Chrome 또는 Microsoft Edge)의 headless 인쇄로 만든다.
  필요한 Python 패키지는 `uv sync`에 포함된다. 브라우저가 없으면 PDF 생성은 `browser_not_found`로 실패한다.
  - **실행 조건**: 서버 프로세스를 root로 실행하지 않는다. Chromium은 root에서 샌드박스 때문에 시작을 거부하며, 어댑터는 `--no-sandbox`를 넣지 않는다(컨테이너도 비root 사용자로).
  - 자동 탐색: Windows는 Chrome(Program Files·LOCALAPPDATA) → Edge, Linux/Mac은 `google-chrome`·`chromium`·`microsoft-edge` 및 macOS의 `/Applications` Chrome·Edge 실행 파일.
  - 다른 위치면 `.env`에 `EXPORT_BROWSER_PATH=<실행 파일 경로>`를 지정한다. 시간 제한은 `EXPORT_RENDER_TIMEOUT_S`(기본 90초).
  - macOS에서는 PDF 쓰기 완료·DOM 출력 완료 후 이번 임시 Chrome에 CDP `Browser.close`를 보내 실제 종료 코드까지 확인한다. 연결은 임시 프로필의 `127.0.0.1` 포트만 사용하며 `websockets`는 직접 의존성으로 포함한다. 출력 파일만 생기고 종료가 지연되는 환경을 위한 처리이며, 시간 초과·비정상 종료와 기존 배치 검사 실패는 그대로 실패로 남는다.
- 한글 서체는 레포에 동봉한 OFL 폰트(Pretendard v1.3.9, `app/templates/fonts/`)를 PDF에 임베드한다.
  DOCX에도 같은 Regular/Bold 글꼴 파일 전체를 포함한다(template_v11). 서버/수신자 PC의 설치 여부에 따른 글꼴 대체를 줄이며, 기존 문서는 배치 재검사·재승인 후 새 DOCX를 내려받는다.
- 렌더 어댑터는 `app/services/export_render.py`이며 배치 검사·Export·다운로드 API는 BE-08에서 연결했다(아래).
- 도구 비교 실험은 `uv run python scripts/experiments/be07_render_candidates.py`로 재현할 수 있다(출력은 `private_runs/be07/`).
  reportlab·playwright 후보는 설치돼 있을 때만 실행되고 없으면 "미실행/설치 제약"으로 기록된다.
- 배치 검사 미리보기(쪽 PNG)는 pypdfium2로 만든다(런타임 의존성, `uv sync`에 포함).

### 배치 검사·승인·출력 흐름 (BE-08)
- `POST /documents/{did}/layout-checks`(202 Job, format=pdf|docx) → `GET /jobs/{jid}` → `GET /documents/{did}`의 `layout_checks[format]` → 미리보기 `GET /assets/{preview_asset_id}`
  → `POST /documents/{did}/approvals`(201) → `POST /exports`(202, ready면 200) → `GET /exports/{eid}/download`.
- PDF와 실제 DOCX 검사 통과본을 승인·출력할 수 있다(계약 1.9, template_v7). DOCX는 LibreOffice 검사 기준이며 Word 등 다른 프로그램·글꼴에서 쪽 나눔이 달라질 수 있으므로 응답의 warnings를 표시한다. 엔진 없는 경우의 HTML/PDF 참고 미리보기는 DOCX 검사 증거가 아니며 승인이 차단된다. 기존 단일 열 DOCX 배치/편집성은 유지하며 PDF 브로슈어 디자인과 동일 배치를 보장하지 않는다.
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
- 계약 1.5부터 허용 warning은 개별 확인 후 승인할 수 있다. `issues/{iid}/resolve`에 `expected_revision`, `input_revision`, `validation_id`, `resolution: {action: "acknowledged", reason: "확인 사유"}`를 보낸다. 현재 검증·내용에 유효한 확인이 없으면 승인/출력은 `WARNING_ACKNOWLEDGEMENT_REQUIRED`다. 명시적 시연 세션의 `DEMO_VALUE`에도 적용하며 근거 오류·필수 내용·수치 충돌·사진 공개 허가·MOCK blocker는 유지한다. 확인 기록은 관련 변경 후 다시 받아야 하고 프론트 확인 UI 연결은 후속이다. 상세는 task_backend.md 6.32절을 따른다.
- 시연 출력은 `template_v1`의 하단 문구 `시연용 · 일부 내용은 임시 데이터입니다`를 사용한다. PDF 인쇄 margin box에 footer를 배치하고 검사 공간에 포함하며 DOCX도 footer를 넣는다. PDF 어느 페이지든 시연 문구가 빠지면 LAYOUT_RENDER_FAILED(reason=demo_footer_missing)로 거부하므로 해당 인쇄 기능을 지원하는 브라우저가 필요하다. 문서→배치 검사→artifact→승인→Export의 demo 식별값을 비교하고 승인된 바이트를 그대로 제공한다. DOCX 승인·출력 제한은 유지한다.
- 서버 설정 변경은 재시작으로 적용한다(동일 DB를 사용하는 프로세스는 같은 설정을 사용). 시연 모드를 끄면 유효한 시연 세션의 조회·캐시·Job 결과·출력은 403 `DEMO_MODE_DISABLED`다. 수명 연장·내용 삭제는 하지 않는다. 소유자 DELETE는 허용하고 종료·만료는 기존 410·정리 규칙이 우선한다. 재활성화 시 실패 Job/Export는 자동 재시도하지 않는다.

등록 자료는 로컬의 gitignore 경로에서만 읽는다. 실제 묶음은 파일 이동·CSV 수정 없이 루트와 JSON 폴더를 구분한다.
```bash
uv run python scripts/import_registered.py --source-dir private_runs/registered_src/real/묶음루트 --ingest-dir private_runs/registered_src/real/묶음루트/06_개발전달 --dry-run
uv run python scripts/import_registered.py --source-dir private_runs/registered_src/demo/묶음루트 --with-demo --dry-run
```
검사 후 적재하려면 동일 명령에서 `--dry-run`만 뺀다. 시연 자료는 `status=demo`, `demo=true`, 표시명·본문의 `[시연]`, 근거 상태 `시연용 임시 문장`을 사용한다. 기존 source_id의 real/mock/demo 변경은 거부한다. 후보 사진은 `photo_candidates.json.path`의 실제 바이트를 적재하며 CSV 원본과 다른 이름·형식도 가능하다. CSV 연결은 같은 경로 또는 명시적 `original_ref: {"폴더": "…", "파일명": "…"}`만 사용한다. 사진 공개 허가 null/false 차단은 그대로다.

`verify_bundle.py`는 기존 fixture 전용이다. 실제 구조 호환성은 `tests/test_registered_import.py`의 가짜 묶음으로 검증한다. 실제 시연 묶음 작성·실자료 로컬 적재·실제 AI 생성 품질은 아직 완료하지 않았다. 회귀 테스트는 `uv run pytest tests/test_demo.py tests/test_registered_import.py`로 실행한다.

## 브로슈어형 문서 배치

설계도의 S01~S03 흐름을 유지하면서 S02 편집 문서와 PDF에 사진 카드·캡션·색상 구성을 적용한다. 기존 `Page.layout_key` 5종(`cover_photo`, `text_photo`, `process_steps`, `product_grid`, `contact_photo`)을 사용하며 알 수 없는 값은 텍스트 배치로 표시한다. 편집 캔버스는 실제 PDF 쪽 나눔의 검사 증거가 아니다. PDF 배치 검사와 승인 절차는 그대로 필요하다.

4쪽 이상이며 사용 가능한 사진이 있으면 실제 LLM은 페이지별 제목·요약·정보 목록·사진 ID를 한 초안 호출에서 구성한다. 지원된 사실 ID와 선택·허용된 사진 설명만 전달하며, 원문의 실제/demo 구분·제외 조건을 보존한다. 사진 없음·1쪽은 기존 글 중심 경로를 유지한다. `process_steps` 목록은 번호 카드, `product_grid` 목록은 비교 카드로 표현하며, 편집 가능한 기존 heading/paragraph/list/image 블록을 사용한다. 등록 사진 공개 허가가 명시적으로 true인 경우만 자동 후보로 제공하며 사진 설명만으로 피사체 검증이 끝났다고 보지 않는다. 템플릿은 `template_v3`이며 이전 검사·승인은 다시 확인해야 한다. DOCX 승인 제한은 유지한다. 로컬 시연 묶음과 회사 사진은 Git에 포함하지 않으며 기존 mock fixture와 내부 demo 출처를 구분한다. 상세 결정은 plan.md 4.37~4.39, 실제 검증은 task_backend.md의 브로슈어 배치 기록을 따른다.

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

2026-09-27에는 개발 전 문서를 정리했다. 당시 기존 BE/AG 작업 상태와 테스트 기록을 유지했고 실행 코드·의존성·DB는 바꾸지 않았다. 현재 공통 계약은 1.6, 데이터 schema_version은 1.0이다. 2026-09-28의 계약 1.2는 재점검 충돌로 인한 기존 승인 무효화·승인 재전송 차단을 반영한다. 상세는 [contracts.md](contracts.md), 기존 경로를 유지한 예시는 [API 예시](handoff/api_examples_v1.1.json)를 따른다. 프론트 계약/예시 사본은 문서 v1.9까지의 동기화 기록이 있으며, 이번 계약 1.6의 사본·프론트 구현은 갱신하지 않았다. 검토 메모의 다른 제안과 미구현 항목은 별도로 유지한다.

Stitch 화면 설계와의 연결 기준은 [prd.md 3~5절](prd.md), 화면 상태별 데이터 연결은 [contracts.md 7.5절](contracts.md)을 따른다. 추가 기능의 채택 여부는 [plan.md 4.1절](plan.md)에서 관리한다. 화면 시연·예시 응답과 실제 기능 완료는 구분한다.

### 로컬 시연: 문구 수정안·내용 검증 함께 켜기

현재 시연의 interactive 모드는 아래 명령으로 실행한다. 내용 검증 한도는 400,000자이며 작성 입력 40,000자·출력 32,000토큰·요청 대기 120초·자동 재시도 0·기존 실행 예산 가드를 유지한다. 수정 요청 문장은 화면/서버 모두 10,000자까지이며 한 번에 블록 하나를 수정한다.

```powershell
$env:PRIVATE_RUNS_DIR='C:\backend\private_runs\demo_preview_20260929_131826'
$env:DB_PATH='C:\backend\private_runs\demo_preview_20260929_131826\app.sqlite3'
$env:EXPORT_LIBREOFFICE_PATH='C:\Program Files\LibreOffice\program\soffice.com'
.\scripts\run_llm.ps1 -Demo -ContentReview -TextProposals -RequestTimeoutSeconds 120 -MaxInputChars 40000 -MaxReviewInputChars 400000 -MaxOutputTokens 32000 -MaxRetries 0
```

`run_llm.ps1`의 `MaxReviewInputChars` 기본값도 400,000자다. 예전 실행 명령에 `-MaxReviewInputChars 120000`이 있으면 그 명시값이 우선하므로 위 명령으로 교체한다. `.env`의 값을 바꿔도 이 실행 스크립트가 덮어쓴다. 검증 한도는 실제 전송 문자열 전체에 적용하며 한도 변경으로 문서·자료를 자르거나 검증 통과로 처리하지 않는다. 서버 재시작 전에 진행 중 작업이 없는지 확인하고 같은 시연 DB를 사용한다. `.env` 비밀값은 수정하지 않는다.

일반 서버는 총 8회/$1·기능별 횟수·동시 AI 작업 1개 제한을 적용하지 않는다. 사용량은 측정만 하며 한 요청의 오류가 다음 요청을 막지 않는다. `-TextProposals`와 `-ContentReview`의 명시 활성화, 수동 중단, 외부 API의 한도, 근거/권한/버전/승인 검사는 유지한다. 과거 `TrialLedger`와 `OPENAI_TRIAL_*`는 명시적으로 사용하는 제한된 평가용이다. 런타임 측정값은 프로세스 메모리 총계와 최근 100회 메타이며 영구 청구 장부가 아니다. 알 수 없는 비용은 미확인으로 표시한다.

미리보기는 첫 heading 블록이 있으면 별도의 페이지 제목을 추가하지 않는다. 저장된 문제도 화면에서 한국어 항목명·권장 조치로 표시하지만 문제 코드와 승인 차단 여부는 바꾸지 않는다. 사진 캡션과 대체 텍스트는 각각 검사하고, 선택·허가된 사진의 등록 설명은 AI 의미 검사에 출처 정보로 전달한다. 등록 설명은 인증·성능·회사 소유의 증빙이 아니다.


초안의 대상 회사명 비교는 공백·대소문자·전각 문자와 앞/뒤의 ㈜/(주)/주식회사 표기 차이를 허용합니다. 영문 번역/약칭을 자동 추정하지는 않습니다. 동일 회사명 표기 확인이 필요한 로컬 운영자는 `.env`의 `COMPANY_NAME_ALIASES`에 확인한 이름 그룹을 JSON으로 설정할 수 있습니다(예: `[["가상 회사", "EXAMPLE COMPANY"]]`). 기본값은 `[]`입니다. 이름에 대한 확인만 적용하며 근거·출처·다른 사실 검증을 생략하지 않습니다. 실제 회사명 설정은 커밋하지 않습니다. 변경 후 서버 재시작 및 자료 재점검이 필요합니다.

브로슈어 생성은 긴 항목에 최대 160자를 배분하고 쪽 전체 분량을 별도로 검사합니다. 알려진 미완결 문장이나 분량 초과를 감지하면 저장 전에 최대 한 번 재작성합니다(추가 API 호출 비용·시간 발생). 실패한 초안을 잘라 저장하지 않습니다.
