# CLAUDE.md
이 저장소의 AI 작업 규칙은 AGENTS.md 에 있다. 작업 시작 전에 AGENTS.md → plan.md → prd.md → contracts.md 를 읽고,
백엔드 담당이면 task_backend.md, Agent 담당이면 task_agent.md + agent.md 를 따른다.
파일 관리: 새 파일은 경로·용도를 먼저 설명하고 확인받는다. 작업일지·임시 파일을 저장소에 만들지 않는다.
.env 값은 출력하지 않는다. .venv/, private_runs/, .env 는 커밋하지 않는다.

## task 파일 동기화 (고정)
- task_backend.md / task_agent.md 는 개인 작업의 원본, task.md 는 둘이 같이 보는 연결표다.
- 개인 task 파일에서 BE-*/AG-* 의 상태나 선행 조건을 바꾸면 **같은 커밋에서** task.md 2절(연결 지점)의 해당 행도 갱신한다.
  task.md 에는 상태 · 날짜 · "task_backend.md 6절 참조" 식의 위치만 적고 상세 내용은 복제하지 않는다.
- 다른 담당의 행과 상태는 바꾸지 않는다. 바꿔야 하면 요청 항목으로 남긴다.
- 작업 완료 보고 전에 두 파일이 같은 상태를 말하는지 확인한다.

## DB
- DB 는 아직 설계 전이다. 개발하면서 설계한다. 스키마·테이블을 미리 만들지 않고, 필요한 작업(BE-02 이후)에서 필요한 만큼만 추가하고 plan.md 4절에 결정을 기록한다.
- 옛 레포(C:\vscode\Backend_old)의 schema.sql · database.py 는 참고만 한다.
