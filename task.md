# 백엔드·Agent 공통 연결표

기준일: 2026-09-23 · v1.2

이 파일은 **서로 무엇을 주고받고 언제 같이 확인하는지**를 정리한다. 공통 task.md에는 상태·날짜·위치만 적고 상세 기록은 개인 task 파일에 둔다.

## 1. 내 작업 파일

| 담당 | 상세 작업과 상태의 원본 | 작업 ID |
|---|---|---|
| 백엔드 | [task_backend.md](task_backend.md) | BE-01~BE-10 |
| Agent | [task_agent.md](task_agent.md) | AG-01~AG-08 |
| 프론트 | 별도 프론트 레포 task.md | FE-01~FE-09 |

공통 데이터·API 규칙은 [contracts.md](contracts.md)가 원본이다. 핵심 역할 3개의 설계는 [agent.md](agent.md)를 따른다. 같은 레포를 쓰더라도 AI에는 먼저 자기 역할과 작업 ID를 지정한다.

## 2. 서로 확인할 연결 지점

| 시점 | 백엔드가 준비할 것 | Agent가 준비할 것 | 함께 확인할 결과 | 관련 작업 | 상태 (BE / AG) |
|---|---|---|---|---|---|
| 시작 | 현재 API·자료/문서 모델·파일 소유 범위 | 기존 사실 스키마·프롬프트·AI 입출력 | 공통 예시 1개와 수정할 파일 범위 | BE-01 / AG-01 | IN_PROGRESS (2026-09-25, task_backend.md 6.1절) / TODO |
| 자료 분석 전 | 읽을 수 있는 구간·출처 위치·사진 ID·선택 범위 | 사실·충돌·누락·추천 | 선택한 자료만 사용하며 원문 위치가 연결됨 | BE-03 / AG-02 | TODO / TODO |
| 확인 후 생성 | 사용자 확인·작업 상태·입력 버전 API | LangGraph 확인/재개와 초안 | 최신 확인으로만 생성되고 중복 재개가 안전함 | BE-04 / AG-03 / AG-04 | TODO / TODO |
| 편집 | 문서 버전·수정안 적용/취소/복원 | 선택 영역 문구·구성·사진 제안 | 적용 전 원본 유지, 적용 후 지정 범위만 반영 | BE-05 / AG-05 / AG-06 | TODO / TODO |
| 검증·승인 | 문제 해결 기록·승인 조건 | 변경 부분 검증과 남은 문제 | 기존 blocker 유지, AI가 자체 승인하지 않음 | BE-06 / AG-07 | TODO / TODO |
| 출력 | 실제 배치·승인 스냅샷·파일 생성 | 출처가 연결된 완성 문서 | 한글·본문·사진·조건이 실제 파일에서 유지됨 | BE-07 / BE-08 / AG-04 / AG-08 | TODO / TODO |
| 종료·만료 | 자료·문서·캐시·파일 접근 차단/정리 | 체크포인트·AI 파생 정보의 정리 지점 | 세션 자료가 다음 세션이나 영구 기록에 남지 않음 | BE-09 / AG-03 | TODO / TODO |
| 통합 | API·승인·출력의 실제 연결 | 근거·내용 품질 평가 | 프론트 실제 연결과 검수 근거까지 확인 | BE-10 / AG-08 / 프론트 FE-08 | TODO / TODO |

각 연결의 완료 근거는 관련 담당의 상세 작업 기록에 남기고 이 표에는 상태·날짜·위치만 적는다. 상대 작업을 기다리는 동안 계약 예시로 가능한 개발은 진행할 수 있지만 실제 연동 완료로 표시하지 않는다.

## 3. 전체 검수의 결과 위치

QA ID는 이전 문서와 동일하다. 아래 표는 결과 위치만 안내한다. 실제 PASS/FAIL/미실행 및 증거는 지정 파일에서 한 번만 기록한다. 공동 확인 항목도 기록 책임은 한 명이 맡는다.

| ID | 확인 내용 | 결과 기록 책임 | 결과 원본 |
|---|---|---|---|
| QA-01 | 등록/세션 자료의 근거 연결 | Agent | [task_agent.md](task_agent.md) |
| QA-02 | 세션 A/B 분리 | 백엔드 | [task_backend.md](task_backend.md) |
| QA-03 | 미지원·손상·암호 파일 | 백엔드 | [task_backend.md](task_backend.md) |
| QA-04 | 불확실한 표·수치 | 백엔드 | [task_backend.md](task_backend.md) |
| QA-05 | 사진만 선택 | Agent | [task_agent.md](task_agent.md) |
| QA-06 | 사진 없는 텍스트 자료 | Agent | [task_agent.md](task_agent.md) |
| QA-07 | 자료/목적 변경 후 옛 점검 사용 | 백엔드 | [task_backend.md](task_backend.md) |
| QA-08 | 실제 사진 표시 | 프론트 | 프론트 레포 task.md · FQ-04 / FQ-09 |
| QA-09 | 1·4·6·8·10쪽 | Agent | [task_agent.md](task_agent.md) |
| QA-10 | 수정안 취소/적용/복원 | 프론트 | 프론트 레포 task.md · FQ-05 / FQ-06 |
| QA-11 | 오래된 수정안·중복 재개 | 백엔드 | [task_backend.md](task_backend.md) |
| QA-12 | 인증·수치 충돌 | Agent | [task_agent.md](task_agent.md) |
| QA-13 | 조건 있는 납기 축약 | Agent | [task_agent.md](task_agent.md) |
| QA-14 | 순서 변경과 사실 문장 변경 | 백엔드 | [task_backend.md](task_backend.md) |
| QA-15 | 사진 없음→추가 업로드/자리 | 프론트 | 프론트 레포 task.md · FQ-07 |
| QA-16 | 승인 API 우회 | 백엔드 | [task_backend.md](task_backend.md) |
| QA-17 | 승인 후 변경 | 백엔드 | [task_backend.md](task_backend.md) |
| QA-18 | PDF/DOCX 파일 열기 | 백엔드 | [task_backend.md](task_backend.md) |
| QA-19 | 일시 실패·재다운로드 | 백엔드 | [task_backend.md](task_backend.md) |
| QA-20 | 세션 종료/만료·삭제 | 백엔드 | [task_backend.md](task_backend.md) |

프론트 소유 QA-08/QA-10/QA-15는 프론트의 관련 FQ 결과와 연결한다. 실제 출력물/저장·버전 등 서버 확인도 필요한 경우 BE-08/BE-05의 확인 근거를 함께 참고한다. 필수 검증 책임은 개발자에게 있고 확인·보조 담당자는 짧은 추가 확인을 수행한다.

## 4. 전달할 때 남길 정보

- 관련 작업 ID와 변경 파일/커밋.
- 입력/출력 예시 또는 재현 방법.
- 실제로 확인한 것과 아직 미연결인 것.
- 상대에게 필요한 확인/변경, 사용한 계약 버전.

같은 원본 계약이나 공유 실행 파일을 바꿀 때에는 다른 담당의 진행 변경을 확인한다. 개인 작업이 끝났다는 이유로 상대 담당의 작업까지 완료 처리하지 않는다.
