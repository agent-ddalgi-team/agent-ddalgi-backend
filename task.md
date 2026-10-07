# 회사소개서 도우미 공통 작업 현황판

> **서비스 목표**: 선택한 회사 자료로 근거가 연결된 소개서를 만들고, 사용자가 수정·검증·승인한 뒤 파일로 내려받는다.\
> **현재 집중 영역**: [F-08 실제 화면 연결·품질 평가] · Agent 최우선은 [F-02 자료 점검·작성 조건 추천] · [F-03 사용자 확인·초안 생성] (전체 진행률: 약 64%)

---

## 1. Quick Status

- **2026-10-07 [F-05·08 / BE-06·10] 미해결 분류·처리 경로 대조:** 종료/만료 데이터의 판정 제한 확인·프론트 위치/근거 연결·GET 복구 보완/UI33묶음 확인·BE-10 진행중 유지. [기록](task_backend.md#unresolved-issue-audit-20261007).

- **2026-10-07 [F-02·03·05·08 / BE-04·06·10] 점검 항목 처리:** 제외·복원/자료 보완 화면 연결·회귀1,357건/UI31묶음 확인·BE-10 진행중 유지. [기록](task_backend.md#preflight-review-actions-20261007).

- **2026-10-07 [F-02·03·05·08 / BE-04·06·10] 숫자·날짜 표기 동등성:** 공통 비교 정규화·회귀1,306건/실제 인용4건 확인·BE-10 진행중 유지. [기록](task_backend.md#numeric-notation-equivalence-20261007).

- **2026-10-07 [F-01·02·08 / BE-04·10] 공개·첨부 단독 점검:** API69건/UI28묶음 통과·BE-10 진행중 유지. [기록](task_backend.md#selected-source-analysis-20261007).

- **2026-10-07 [F-01·02·08 / BE-02·10] DART 명칭 차이 수정:** NAVER 실수집/선택저장·55건 통과·BE-10 진행중 유지. [기록](task_backend.md#dart-legal-name-fix-20261007).

- **2026-10-07 [F-01·02·08 / BE-02·10] DART 실제 수집·기업 검색:** 백엔드186건/프론트UI27묶음·실조회/선택저장 통과. BE-10/F-08 진행중 유지. [기록](task_backend.md#dart-integration-20261007).

- **2026-10-07 [F-02·03·08 / BE-04·10] 초안 진입 조건 대조·보완:** 점검/접수/초안 규칙 공유·최신 점검 GET 복구·사진만 선택 차단·관련1,205건/최종78건·PDF27/DOCX33묶음 통과. BE-10 진행중 유지. [기록](task_backend.md#draft-entry-audit-20261007).

- **2026-10-07 [F-03·08 / BE-04·10] 대상 회사명 법인 표기 오류 수정:** 전체 회사명 정규화·13건 통과·관련1,241건 통과/기존 해시 실패2건 재현·BE-10 진행중 유지. [기록](task_backend.md#target-company-notation-20261007).

- **2026-10-07 [F-06·08 / BE-07·08·10] PDF/DOCX 글꼴 통일:** Pretendard Regular/Bold 전체 임베딩·관련39건 통과/1skip·현재 실문서6쪽 렌더/시각 검수 완료. 기존 배치 재검사·재승인 필요. [기록](task_backend.md#docx-font-parity-20261007).

- **2026-10-07 [F-03·08 / BE-04·10] 프론트 기준 초안 실패 복구:** 같은 점검에서 명시 재확인·새 키 재시도, 날짜 보존·최대1회 내부 재작성. BE-10/F-08 진행중 유지. [기록](task_backend.md#draft-generation-recovery-20261007).

- **2026-10-06 [F-08 · BE-10] 백엔드 잔여 범위 정리:** BE-01~09 서버완료/BE-10 일반품질 진행중·BE 단순가중 약95%·Agent/회사확인/원격공유 구분. [기록](task_backend.md#backend-remaining-scope-20261006).

- **2026-10-06 [F-08 · BE-10] 유사 표현 실제 AI 검수 3건 완료:** 추가정보 경고 없음·수량오류 blocker·유사제목/본문 경고 없음(허용기준 판단 필요). BE-10 진행중 유지. [기록](task_backend.md#semantic-repetition-cases-20261006).

- **2026-10-06 [F-03/08 · BE-04/10] 동일 제목/본문 반복 보완 완료:** 관련163건·현재서버 실제물류1쪽 검증 통과·BE-10 일반 품질은 진행중. [기록](task_backend.md#duplicate-point-label-20261006).

- **2026-10-06 [F-08 · BE-10] 물류 업종 실제 AI 표본 확인:** 정상/누락/충돌 확인·첫 정상초안 반복warning2건은 품질변동으로 유지·BE-10 진행중. [기록](task_backend.md#logistics-quality-cases-20261006).

- **2026-10-06 [F-08 · BE-10/프론트] 가상 미리보기 코드 정리 완료:** develop/b0f58ac·미리보기와 일반DOCX/C-05 검사PASS·미커밋 코드 정리 완료. BE-10 일반 품질/원격 공유는 별도. [기록](task_backend.md#frontend-preview-commit-20261006).

- **2026-10-06 [F-08 · BE-10/프론트] 로컬 프론트 인계 완료:** 계약1.9 대조·현재작업본 빌드/미리보기PASS·README develop/4f93e02. 기존 미커밋 코드 공유와 BE-10 일반 품질은 별도. [인계 기록](task_backend.md#frontend-local-handoff-20261006).

- **2026-10-06 [F-04/08 · BE-05/10] 실제 AI 자료 제외 복귀 표본 완료:** C-05 추가/수치 교체/제외 표본 검수 완료, BE-10 일반 통합 품질 진행중 유지. [자료 제외 검수](task_backend.md#c05-source-exclusion-20261006).

- **2026-10-06 [F-04/08 · BE-05/10] 실제 AI 수치 교체 복귀 표본 완료:** BE-10 진행중 유지. [수치 변경 검수](task_backend.md#c05-number-replacement-20261006).

- **2026-10-06 [F-06/08 · BE-08/10] 백엔드 인계 완료:** 서버 구현·고객용8쪽/품질용9쪽 시연 검수와 실행 안내 정리 완료. BE-10 일반 통합 품질 진행중 유지. [백엔드 인계](task_backend.md#backend-handoff-20261006).

- **2026-10-06 백엔드 완료 기준 대조 [BE-10]:** 서버 구현/시연 결과와 실제 AI·목적별 품질/외부 확인 잔여 항목을 분리했다. C-05 화면·DOCX 승인·공개 사용 허가·실제 preflight 중단 시험은 완료 기록으로 반영한다. BE-10 진행중 유지. [백엔드 최신 기준](task_backend.md#backend-completion-audit-20261006).
- **현재 작업**: **[F-08 실제 화면 연결·품질 평가] [AG-08 / BE-10] → 연결 결과·남은 품질 검수 확인**. 백엔드의 S01~S03 연결·실제 LLM 표본 완주 기록은 유지한다. **Agent 최우선은 [F-02 자료 점검·작성 조건 추천] · [F-03 사용자 확인·초안 생성] [AG-01~04] → 근거 기반 초안 품질 개선**이다. 2026-09-30 · [Agent 현재 작업](task_agent.md#grounded-draft-quality).
- **다음 작업**: **[F-02/03] [AG-01~04] → 실제 합성 평가 후 의미 검토·문체 보완과 실회사 평가**. 6사례 실제 평가 종료·전체 진행중 · 2026-09-30 · [Agent 실행 결과·남은 일](task_agent.md#editorial-live-resume-20260930). 백엔드·프론트 후속은 기존 작업표를 유지한다.
- **주요 메모**: 2026-09-29, 원격 develop / 22fc345에서 PR #21~#23 병합 확인. 현재 계약 1.9·데이터 형식 1.0·DB 코드 v11이며 실제 자료 적재 보류는 유지한다. 이전 시연·화면 시험 결과는 담당별 기록을 따른다. 최신 백엔드 test/시연 환경 검수는 백엔드 완료 기준 대조 기록, Agent 실행 환경은 Agent 기록으로 구분한다.
- **상태 기준**: 최신 develop의 BE-10 진행중과 나머지 BE/AG 상태를 유지한다. 약 64%는 기존 담당 업무 18개를 같은 비중으로 계산한 참고값이다((완료 8 + 진행·부분 완료 7 × 0.5) / 18). F 기능의 완료율이나 진행 순서를 뜻하지 않는다. 실제 서비스 완성 여부는 F-08의 전체 검증으로 판단한다.
- **기록 운영**: [task_backend.md](task_backend.md)·[task_agent.md](task_agent.md)가 담당별 결과의 원본이다. 공통 현황판의 보고는 **[F 번호 기능명] [AG 번호 / BE 번호] → 세부 업무**로 시작한다. 해당 세부 업무의 담당 번호를 옆에 적고 한 담당의 업무이면 그 번호만 표시한다. 이 파일의 2절은 상태·날짜·위치와 기존 업무 참조를 연결하며, 담당 파일의 상태·선행 조건 변경 시 같은 변경에 이 표도 맞춘다. 프론트 작업·검수는 별도 저장소에서 관리한다.

### 번호 읽는 기준

| 표기 | 의미·사용 위치 |
|---|---|
| F-01~F-08 | 세 문서의 공통 기능 번호. 이 공통 현황판에서는 **[F 번호 기능명] [AG 번호 / BE 번호] → 세부 업무**로 담당 업무 번호를 함께 표시한다. 숫자는 기능을 식별하며 실행 순서나 완료 단계를 뜻하지 않는다. |
| AG-*, BE-*, FE-* | 각각 Agent·백엔드·프론트의 담당 업무 번호. 기능명 옆에 표시하고 2절에서는 Agent(AG)와 백엔드(BE) 열을 나눠 적는다. F 번호와 숫자가 같아도 같은 업무가 아니며, 하나의 담당 업무가 여러 기능에 연결될 수 있다. |
| T01, V03, N01, P03A/B, C03, H01A/B 등 | 테스트 사례 번호. 무엇을 시험했는지 설명할 때 ‘테스트 사례’로 표시한다. |
| QA-*, FQ-* | 검수 항목 번호. 검증 결과의 추적에 사용한다. |
| S01~S03 / D-07 / C-05 | 각각 화면 번호 / 정책 결정 번호 / 계약 협의 항목 번호. 테스트 사례 C03와 계약 항목 C-05를 구분한다. |
| 3절, 4.24절, 옛 6.x절 | 문서 안의 위치. 기능 번호와 구분하며 옛 링크는 과거 기록을 찾도록 보존한다. |

**보고 예시:** [F-05 내용 검증·경고 확인·승인] **[AG-07]** → 새 업종 평가 사례 준비 완료. 실제 AI 평가는 미실행, 기능 전체는 진행중. (테스트 사례: H01A~H08B)

진행 상황은 완료한 세부 업무와 남은 업무로 표시한다. 문서에 정해지지 않은 ‘2/4단계’ 같은 순서를 덧붙이지 않는다. 같은 F 기능이라도 담당별 완료 범위가 달라 상태가 다를 수 있으며, 공통 상태는 담당별 결과를 함께 확인한다.

---

## 2. 전체 기능 지도 (Feature Map)

| 기능 번호 | 기능명 | Agent (AG) | 백엔드 (BE) | 상태 | 담당별 상태·날짜·기록 위치 |
|---|---|---|---|---|---|
| **F-01** | 자료 선택·파일 읽기 | **AG-01/02** | **BE-02/03** | 진행중 | BE-02/03 서버 범위 완료·시연 DB 적재·프론트 S01 연결 완료 · 기존 BE 결과 2026-09-29 · AG-01/02 진행중 · 2026-09-30 · [백엔드 5절 F-01](task_backend.md#f-01), [Agent 5절 F-01](task_agent.md#f-01), [Agent 실제 평가 기록](task_agent.md#editorial-live-resume-20260930) · 2026-10-06 프론트 기준 회사 저장·근거 충족도·키 미설정 연결/백엔드70건·계약44건·브라우저30묶음 확인·BE-10 진행중 · [기록](task_backend.md#frontend-company-coverage-20261006)  · 2026-10-07 DART 수집/기업검색 연결 완료·BE-10 진행중 유지 · [백엔드 기록](task_backend.md#dart-integration-20261007)  · 2026-10-07 DART 명칭 차이 수정·BE-10 진행중 유지 · [기록](task_backend.md#dart-legal-name-fix-20261007)  · 2026-10-07 공개/첨부 단독점검 확인·BE-10 진행중 유지 · [기록](task_backend.md#selected-source-analysis-20261007) |
| **F-02** | 자료 점검·작성 조건 추천 | **AG-01/02** | **BE-04** | 진행중 | BE-04 점검 API 완료·프론트 실제 LLM 점검 연결·실자료 점검 통과 · AG-01/02 진행중 · 2026-09-30 · 기존 BE 결과 2026-09-29 · [백엔드 5절 F-02](task_backend.md#f-02), [Agent 5절 F-02](task_agent.md#f-02), [Agent 복합 사실 후속](task_agent.md#whole-fact-point-20260930), [Agent 수치 표기 후속](task_agent.md#numeric-format-followup-20260930) · 2026-10-06 프론트 기준 회사 저장·근거 충족도·키 미설정 연결/백엔드70건·계약44건·브라우저30묶음 확인·BE-10 진행중 · [기록](task_backend.md#frontend-company-coverage-20261006)  · 2026-10-07 초안 진입 조건 대조/최신 점검 복원·검증 완료·품질 후속 · [기록](task_backend.md#draft-entry-audit-20261007)  · 2026-10-07 DART 수집/기업검색 연결 완료·BE-10 진행중 유지 · [백엔드 기록](task_backend.md#dart-integration-20261007)  · 2026-10-07 DART 명칭 차이 수정·BE-10 진행중 유지 · [기록](task_backend.md#dart-legal-name-fix-20261007)  · 2026-10-07 공개/첨부 단독점검 확인·BE-10 진행중 유지 · [기록](task_backend.md#selected-source-analysis-20261007)  · 2026-10-07 표기 동등성/회귀1,306건 확인·진행중 유지 · [기록](task_backend.md#numeric-notation-equivalence-20261007) · 2026-10-07 점검 항목 제외·복원/자료 보완·검증 완료·BE-10 진행중 유지 · [기록](task_backend.md#preflight-review-actions-20261007) |
| **F-03** | 사용자 확인·초안 생성 | **AG-03/04** | **BE-04** | 진행중 | BE-04 서버 범위·저장 전 자동 분할 후속 구현/검증 완료 · AG-03/04 진행중(설정별 실제 PDF 완료·오류 보완) · 2026-10-01 · [백엔드 자동 분할 기록](task_backend.md#draft-pagination-20260930), [백엔드 5절 F-03](task_backend.md#f-03), [Agent 5절 F-03](task_agent.md#f-03), [Agent 동일 본문 반복 정리](task_agent.md#exact-body-repeat-20261001), [Agent 실제 PDF·오류 보완](task_agent.md#actual-four-pdfs-20261001), [Agent 이전 비교용 PDF4종](task_agent.md#four-settings-pdfs-20261001), [Agent 최소 쪽수·본문 확장](task_agent.md#requested-pages-20261001), [Agent 출력 사유 후속](task_agent.md#grouped-draft-notes-20261001), [Agent 시간 상한 후속](task_agent.md#timeout-180-20261001), [Agent 인증·입력·설정별 시험](task_agent.md#compound-cert-schema-20260930) · [로컬 시연 안정화 기록](task_backend.md#name-and-sentence-20260930)  · 2026-10-06 동일자료 품질 심사 목적 실제AI 비교·내용/PDF/DOCX9쪽 통과·사진 단독쪽/설명 보완 후속 · [최신 백엔드 기록](task_backend.md#quality-purpose-comparison-20261006)  · 2026-10-06 사진 단독쪽 분할 경계/설명6개 보완·revision3 내용/PDF/DOCX9쪽 통과·시연 승인 후속 · [최신 기록](task_backend.md#photo-continuation-fix-20261006) · 2026-10-06 동일항목 제목/본문 반복 보완·163건/실제물류 검증 통과·일반품질 진행중 · [기록](task_backend.md#duplicate-point-label-20261006) · 2026-10-07 초안 실패 복구 검증/BE-10 진행중 유지 · [기록](task_backend.md#draft-generation-recovery-20261007)  · 2026-10-07 대상 회사명 법인 표기 보완/검증·기존 해시2건 후속 · [기록](task_backend.md#target-company-notation-20261007)  · 2026-10-07 초안 진입 조건 대조/최신 점검 복원·검증 완료·품질 후속 · [기록](task_backend.md#draft-entry-audit-20261007)  · 2026-10-07 표기 동등성/회귀1,306건 확인·진행중 유지 · [기록](task_backend.md#numeric-notation-equivalence-20261007) · 2026-10-07 점검 항목 제외·복원/자료 보완·검증 완료·BE-10 진행중 유지 · [기록](task_backend.md#preflight-review-actions-20261007) |
| **F-04** | 직접 편집·AI 수정안·사진 | **AG-05/06** | **BE-05** | 진행중 | BE-05 기존 API·C-05 서버·프론트 복귀/가상 자료 검증 완료 · 2026-10-06 · [현재 백엔드 기록](task_backend.md#c05-ui-20261006) · 실제 변경 자료 의미 검증/구조 수정 후속 · AG-05/06 대기(초기 구현 인계 필요) · 2026-09-30 · [백엔드 C-05 기록](task_backend.md#c05-edit-return-20260930), [백엔드 5절 F-04](task_backend.md#f-04), [Agent 개선 검증](task_agent.md#f-03), [Agent 4절](task_agent.md#4-향후-예정-기능), [로컬 시연 안정화 기록](task_backend.md#name-and-sentence-20260930)  · 2026-10-06 실제 AI 보완 자료 C-05 복귀·편집 보존/근거/선택 적용·검증/정리 완료·목적별 품질 후속 · [백엔드 최신 기록](task_backend.md#c05-live-ai-20261006) · 2026-10-06 실제 AI 수치 교체 복귀 표본 완료·통합 품질 진행중 · [기록](task_backend.md#c05-number-replacement-20261006) · 2026-10-06 실제 AI 자료 제외 복귀 표본 완료·BE-10 진행중 · [기록](task_backend.md#c05-source-exclusion-20261006) |
| **F-05** | 내용 검증·경고 확인·승인 | **AG-07** | **BE-06** | 진행중 | BE-06 서버 완료·2026-10-06 점검 blocker 전달 후속 완료 · [백엔드 현재 기록](task_backend.md#preflight-blockers-20261006) · 프론트 경고 확인/승인 연결·실제 LLM 검증 완주 · AG-07 진행중·추가 반복 평가 후순위(2026-09-30)·2/9회 · 기존 결과 2026-09-29 · [백엔드 5절 F-05](task_backend.md#f-05), [Agent 5절 F-05](task_agent.md#f-05) · [Agent 실행 기록](task_agent.md#ag07-review-stability-batch-1) · 2026-09-30 후속 진행중 · [Agent 검증 입력 한도 후속](task_agent.md#review-input-limit-20260930), [Agent 연혁·단계 검증 일치 · 2026-10-01](task_agent.md#sequence-review-parity-20261001), [로컬 시연 안정화 기록](task_backend.md#name-and-sentence-20260930) · 2026-10-06 실제 중복2건 정리/삭제 대상 해결·회귀94건 통과·PDF/DOCX8쪽 통과·점검 근거1건/명시 승인 후속 · [최신 백엔드 검수](task_backend.md#deleted-review-targets-20261006) · 2026-10-06 연도/인증 인용 보완·110건 통과·실제 재점검2회·시연 식별자 인용/새 근거 연결 후속 · [백엔드 연혁 근거 후속](task_backend.md#history-evidence-20261006) · 2026-10-06 식별자/새 근거 연결 완료·113건 통과·revision5 내용검증/형식별8쪽 통과·최종 명시 승인 후속 · [최신 백엔드 근거 연결](task_backend.md#record-evidence-binding-20261006) · 2026-10-06 revision5 시연 PDF/DOCX 승인·다운로드/재사용 검수 완료·실자료 전체/사진 품질 검수 후속 · [최신 백엔드 승인/다운로드](task_backend.md#demo-approval-download-20261006)  · 2026-10-06 공개자료 사용 확인·형식별 승인 복원/실제 화면 다운로드 검수 완료·사진 설명/선명도·전체 의미 검수 후속 · [최신 백엔드 기록](task_backend.md#final-ui-approvals-20261006)  · 2026-10-07 표기 동등성/회귀1,306건 확인·진행중 유지 · [기록](task_backend.md#numeric-notation-equivalence-20261007) · 2026-10-07 점검 항목 제외·복원/자료 보완·검증 완료·BE-10 진행중 유지 · [기록](task_backend.md#preflight-review-actions-20261007) · 2026-10-07 미해결 분류·화면 처리 경로/UI33묶음 확인·BE-10 진행중 유지 · [기록](task_backend.md#unresolved-issue-audit-20261007) |
| **F-06** | PDF·DOCX 내려받기 | **AG-04/08** | **BE-07/08** | 진행중 | BE-07/08 PDF·LibreOffice DOCX 서버 범위 완료 · DOCX 프론트 연결·변환/조회 오류 보완 완료 · 실문서 11→8쪽 빈 쪽 보완 완료·template_v8 · 다섯 분량 PDF/DOCX 출력·승인 회귀100건 완료 · 2026-10-06 · [분량별 기록](task_backend.md#publication-page-matrix-20261006) · DOCX 동시 출력 보존·전체 회귀 완료 · [전체 회귀 기록](task_backend.md#backend-regression-20261006) · 명시적 재검사/승인 표본 검수 후속 · [빈 쪽 기록](task_backend.md#docx-blank-pages-20261006) · [화면 연결 기록](task_backend.md#docx-ui-20261006) · 2026-10-06 · [백엔드 DOCX 기록](task_backend.md#docx-layout-20261006) · PDF 사진 맞춤 후속 · AG-04 진행중·AG-08 대기 · 2026-09-30 · [백엔드 사진 맞춤 기록](task_backend.md#pdf-photo-fit-20260930), [백엔드 기존 HTTP 확인](task_backend.md#http-photo-publication-20260930), [백엔드 5절 F-06](task_backend.md#f-06), [Agent 4절](task_agent.md#4-향후-예정-기능), [Agent 실제 평가 기록](task_agent.md#editorial-live-resume-20260930)  · 2026-10-06 DOCX 문단 공간/본문 추출 대조 보완 완료(template_v9)·실문서8쪽/관련42건 통과·최신 API 재검사/명시 승인 후속 · [보완 기록](task_backend.md#docx-spacing-20261006) · 2026-10-06 식별자/새 근거 연결 완료·113건 통과·revision5 내용검증/형식별8쪽 통과·최종 명시 승인 후속 · [최신 백엔드 근거 연결](task_backend.md#record-evidence-binding-20261006) · 2026-10-06 revision5 시연 PDF/DOCX 승인·다운로드/재사용 검수 완료·실자료 전체/사진 품질 검수 후속 · [최신 백엔드 승인/다운로드](task_backend.md#demo-approval-download-20261006)  · 2026-10-06 공개자료 사용 확인·형식별 승인 복원/실제 화면 다운로드 검수 완료·사진 설명/선명도·전체 의미 검수 후속 · [최신 백엔드 기록](task_backend.md#final-ui-approvals-20261006)  · 2026-10-06 사진 설명6개/중립 공정 설명·DOCX150ppi 확대 제한(template_v10) 완료·회귀40건/실제revision7 내용·PDF/DOCX8쪽 통과·새 명시 승인 후속 · [최신 백엔드 기록](task_backend.md#photo-native-ppi-20261006)  · 2026-10-06 revision7 시연 형식별 승인/다운로드·해시/중복/구버전·무소유 접근/DB 검사 완료·BE-10 잔여 기준 대조 후속 · [최신 백엔드 기록](task_backend.md#native-ppi-approval-download-20261006)  · 2026-10-06 동일자료 품질 심사 목적 실제AI 비교·내용/PDF/DOCX9쪽 통과·사진 단독쪽/설명 보완 후속 · [최신 백엔드 기록](task_backend.md#quality-purpose-comparison-20261006)  · 2026-10-06 사진 단독쪽 분할 경계/설명6개 보완·revision3 내용/PDF/DOCX9쪽 통과·시연 승인 후속 · [최신 기록](task_backend.md#photo-continuation-fix-20261006)  · 2026-10-06 품질목적9쪽 시연 형식별승인/다운로드·파일동일/중복/접근/기존8쪽보존 확인·마무리 인계 후속 · [최신 기록](task_backend.md#quality-purpose-approval-20261006) · 2026-10-06 백엔드 실행/완료 범위 인계 완료·통합 품질 진행중 유지 · [인계](task_backend.md#backend-handoff-20261006)  · 2026-10-07 Pretendard 전체 임베딩/39건·실문서6쪽 확인·배치 재검사 필요 · [기록](task_backend.md#docx-font-parity-20261007) |
| **F-07** | 세션 보호·종료 정리 | **AG-03** | **BE-02/09** | 진행중 | BE-02/09 서버 범위 완료·2026-10-06 Windows 실제 잠금·긴 경로·재시작 정리 확인 · 실제 Chrome 프로필/자식 프로세스·임시 HTTP 서버 재시작 정리 회귀43건 완료 · 2026-10-06 · [최신 기록](task_backend.md#chrome-http-cleanup-20261006) · 실제 AI preflight 종료/강제 중단·복구/명시 재시도 검수 완료 · [최신 백엔드 기록](task_backend.md#live-ai-interruption-20261006) · [기존 기록](task_backend.md#windows-cleanup-20261006) · AG-03 반복 시연 실패 격리·복구 회귀 확인, 전체 진행중 · [백엔드 5절 F-07](task_backend.md#f-07), [Agent 5절 F-07](task_agent.md#f-07) |
| **F-08** | 실제 화면 연결·품질 평가 | **AG-08** | **BE-01/10** | 진행중 | BE-01 완료·BE-10 진행중(S01~S03 실제 연결·설계도 적용·실자료 표본 완주) · 2026-10-06 복원 DB 실제 AI/형식별 검수: PDF8쪽 통과·DOCX9쪽 실패·내용 문제 미해결, DOCX 배치 보완 후속 · [현재 기록](task_backend.md#restored-live-publication-20261006) · 2026-10-06 누락 시연 DB 백업 복원/원자료 검사 완료, 새 세션 검수 후속 · [복원 기록](task_backend.md#demo-db-restore-20261006) · 2026-10-06 시연 세션 만료/본문 정리·등록 원자료 보존 재확인, 새 검수 세션 준비 후속 · [현재 기록](task_backend.md#expired-demo-audit-20261006) · 2026-10-06 전체 백엔드 회귀 완료 · [현재 기록](task_backend.md#backend-regression-20261006) · C-05 실제 PDF/DOCX 가상 화면 연결 검증 완료 · 2026-10-06 · [현재 기록](task_backend.md#c05-ui-20261006) · 실제 HTTP 요청 중단·문서 적용/출력 중복 방지 회귀23건 완료 · 2026-10-06 · [중단 검사](task_backend.md#http-crash-replay-20261006) · 다섯 분량 출력·승인 관련 회귀100건 완료 · 2026-10-06 · [분량별 기록](task_backend.md#publication-page-matrix-20261006) · 실자료/사진 원본·근거 참조 읽기 전용 검수 완료, 사람 검수 후속 · 2026-10-06 · [검수 기록](task_backend.md#real-material-audit-20261006) · AG-08 대기 · 2026-09-29 · [백엔드 3절](task_backend.md#3-현재-진행-작업-f-08-실제-화면-연결품질-평가-now), [백엔드 5절 F-08](task_backend.md#f-08), [Agent 5절 F-08](task_agent.md#f-08) · 2026-09-30 병합 후 회귀·사진 실제 HTTP 확인 완료 · [병합 기록](task_backend.md#merge-verification-20260930), [HTTP 기록](task_backend.md#http-photo-publication-20260930)  · 2026-10-06 DOCX 문단 공간/본문 추출 대조 보완 완료(template_v9)·실문서8쪽/관련42건 통과·최신 API 재검사/명시 승인 후속 · [보완 기록](task_backend.md#docx-spacing-20261006) · 2026-10-06 실제 중복2건 정리/삭제 대상 해결·회귀94건 통과·PDF/DOCX8쪽 통과·점검 근거1건/명시 승인 후속 · [최신 백엔드 검수](task_backend.md#deleted-review-targets-20261006) · 2026-10-06 연도/인증 인용 보완·110건 통과·실제 재점검2회·시연 식별자 인용/새 근거 연결 후속 · [백엔드 연혁 근거 후속](task_backend.md#history-evidence-20261006) · 2026-10-06 식별자/새 근거 연결 완료·113건 통과·revision5 내용검증/형식별8쪽 통과·최종 명시 승인 후속 · [최신 백엔드 근거 연결](task_backend.md#record-evidence-binding-20261006) · 2026-10-06 revision5 시연 PDF/DOCX 승인·다운로드/재사용 검수 완료·실자료 전체/사진 품질 검수 후속 · [최신 백엔드 승인/다운로드](task_backend.md#demo-approval-download-20261006)  · 2026-10-06 실제 AI preflight 종료/중단·복구 검수 완료·기존 정리43건 통과/유료2건 기본skip·전체 품질/사용자 화면 검수 후속 · [최신 백엔드 기록](task_backend.md#live-ai-interruption-20261006)  · 2026-10-06 공개자료 사용 확인·형식별 승인 복원/실제 화면 다운로드 검수 완료·사진 설명/선명도·전체 의미 검수 후속 · [최신 백엔드 기록](task_backend.md#final-ui-approvals-20261006)  · 2026-10-06 사진6개 설명/형식별 실배치PPI·카탈로그/ISO 포함 기준 검수 완료·품질 반영 후속 · [최신 백엔드 기록](task_backend.md#photo-source-quality-20261006)  · 2026-10-06 사진 설명6개/중립 공정 설명·DOCX150ppi 확대 제한(template_v10) 완료·회귀40건/실제revision7 내용·PDF/DOCX8쪽 통과·새 명시 승인 후속 · [최신 백엔드 기록](task_backend.md#photo-native-ppi-20261006)  · 2026-10-06 revision7 시연 형식별 승인/다운로드·해시/중복/구버전·무소유 접근/DB 검사 완료·BE-10 잔여 기준 대조 후속 · [최신 백엔드 기록](task_backend.md#native-ppi-approval-download-20261006)  · 2026-10-06 백엔드 완료 기준/잔여 업무 대조·전체1,807건 통과/10skip/실패0·BE-10 진행중 · [현재 기준](task_backend.md#backend-completion-audit-20261006)  · 2026-10-06 실제 AI 보완 자료 C-05 복귀·편집 보존/근거/선택 적용·검증/정리 완료·목적별 품질 후속 · [백엔드 최신 기록](task_backend.md#c05-live-ai-20261006)  · 2026-10-06 동일자료 품질 심사 목적 실제AI 비교·내용/PDF/DOCX9쪽 통과·사진 단독쪽/설명 보완 후속 · [최신 백엔드 기록](task_backend.md#quality-purpose-comparison-20261006)  · 2026-10-06 사진 단독쪽 분할 경계/설명6개 보완·revision3 내용/PDF/DOCX9쪽 통과·시연 승인 후속 · [최신 기록](task_backend.md#photo-continuation-fix-20261006)  · 2026-10-06 품질목적9쪽 시연 형식별승인/다운로드·파일동일/중복/접근/기존8쪽보존 확인·마무리 인계 후속 · [최신 기록](task_backend.md#quality-purpose-approval-20261006) · 2026-10-06 백엔드 실행/완료 범위 인계 완료·통합 품질 진행중 유지 · [인계](task_backend.md#backend-handoff-20261006) · 2026-10-06 실제 AI 수치 교체 복귀 표본 완료·통합 품질 진행중 · [기록](task_backend.md#c05-number-replacement-20261006) · 2026-10-06 실제 AI 자료 제외 복귀 표본 완료·BE-10 진행중 · [기록](task_backend.md#c05-source-exclusion-20261006) · 2026-10-06 로컬 프론트 계약대조/빌드/미리보기/README 인계 완료·BE-10 진행중 · [기록](task_backend.md#frontend-local-handoff-20261006) · 2026-10-06 가상미리보기 코드검토/일반DOCX 회귀/로컬커밋 완료·BE-10 진행중 · [기록](task_backend.md#frontend-preview-commit-20261006) · 2026-10-06 물류 정상/누락/충돌 실제AI 확인·반복warning 품질 후속/BE-10 진행중 · [기록](task_backend.md#logistics-quality-cases-20261006) · 2026-10-06 동일항목 제목/본문 반복 보완·163건/실제물류 검증 통과·일반품질 진행중 · [기록](task_backend.md#duplicate-point-label-20261006)  · 2026-10-06 유사표현 의미검수3건 완료·제목 요약 허용기준 후속/BE-10 진행중 · [기록](task_backend.md#semantic-repetition-cases-20261006)  · 2026-10-06 서버완료/BE-10 잔여범위 정리·진행중 유지 · [기록](task_backend.md#backend-remaining-scope-20261006) · 2026-10-06 프론트 pull 병합·연결 결함 수정/96건·브라우저 DOCX·미리보기 회귀 통과·BE-10 진행중 · [기록](task_backend.md#frontend-merge-review-fixes-20261006) · 2026-10-06 사용자 지시: 프론트 화면 기준으로 백엔드 연결/화면 축소안 재검토·진행중 · [최신 기준](task_backend.md#frontend-first-integration-20261006) · 2026-10-06 프론트 기준 회사 저장·근거 충족도·키 미설정 연결/백엔드70건·계약44건·브라우저30묶음 확인·BE-10 진행중 · [기록](task_backend.md#frontend-company-coverage-20261006) · 2026-10-07 초안 실패 복구 검증/BE-10 진행중 유지 · [기록](task_backend.md#draft-generation-recovery-20261007)  · 2026-10-07 Pretendard 전체 임베딩/39건·실문서6쪽 확인·배치 재검사 필요 · [기록](task_backend.md#docx-font-parity-20261007)  · 2026-10-07 대상 회사명 법인 표기 보완/검증·기존 해시2건 후속 · [기록](task_backend.md#target-company-notation-20261007)  · 2026-10-07 초안 진입 조건 대조/최신 점검 복원·검증 완료·품질 후속 · [기록](task_backend.md#draft-entry-audit-20261007)  · 2026-10-07 DART 수집/기업검색 연결 완료·BE-10 진행중 유지 · [백엔드 기록](task_backend.md#dart-integration-20261007)  · 2026-10-07 DART 명칭 차이 수정·BE-10 진행중 유지 · [기록](task_backend.md#dart-legal-name-fix-20261007)  · 2026-10-07 공개/첨부 단독점검 확인·BE-10 진행중 유지 · [기록](task_backend.md#selected-source-analysis-20261007)  · 2026-10-07 표기 동등성/회귀1,306건 확인·진행중 유지 · [기록](task_backend.md#numeric-notation-equivalence-20261007) · 2026-10-07 점검 항목 제외·복원/자료 보완·검증 완료·BE-10 진행중 유지 · [기록](task_backend.md#preflight-review-actions-20261007) · 2026-10-07 미해결 분류·화면 처리 경로/UI33묶음 확인·BE-10 진행중 유지 · [기록](task_backend.md#unresolved-issue-audit-20261007) |

---

<a id="3-현재-진행-작업-f-08-실제-화면-연결품질-평가-now"></a>

## 3. 현재 진행 작업: [F-08 실제 화면 연결·품질 평가] [AG-08 / BE-10] [NOW]

**Agent 최우선 (2026-09-30): [F-02 자료 점검·작성 조건 추천] · [F-03 사용자 확인·초안 생성] [AG-01~04] → 근거 기반 초안 품질 개선.** 진행중 · [Agent 3절](task_agent.md#grounded-draft-quality). 아래 기존 통합·검증 기록과 백엔드 상태는 유지한다.

- [x] **[AG-07]** Agent의 원문 검증·부분 재검증·바꿔쓰기 평가 연결을 develop에 반영했다.
- [x] **[BE-06]** 백엔드가 경고 확인을 최신 문서·자료·검증과 연결하고 승인·출력에서 검사한다. 계약 1.5와 예시를 백엔드 원본에 반영했다.
- [x] **[BE-06/10 · 프론트 연계]** 백엔드가 현재 프론트에 계약 1.5의 경고 확인 요청·차단 안내·승인·PDF 흐름을 연결하고 실제 화면·실제 LLM으로 확인했다(F-05의 프론트 연결 항목 완료).
- [x] **[BE-10 · 프론트 연계]** 백엔드가 현재 프론트 S01~S03을 화면설계도 v1.0 배치로 적용하고 mock 전체 흐름 검사 19개 묶음을 통과했다.
- [/] **[AG-07/08 / BE-10] Agent 의미 검증 평가·남은 통합 검수 (진행중)**: 백엔드 PR #21~#23 병합 확인. 프론트 공유 상태는 별도 확인이 필요하다. Agent 추가 반복 평가는 후순위(2026-09-30)이며 [Agent 후속 기록](task_agent.md#ag07-review-evaluation-history)을 따른다.
- [x] **[AG-07]** 새 업종 정상/오류 8쌍·16개 평가 입력과 별도 정답표를 준비했다. [Agent 평가 준비](task_agent.md#ag07-holdout-preparation).
- [x] **[AG-07]** H01A/B 실제 비교와 Codex 원문 대조를 기록했다. [Agent H01 기록](task_agent.md#ag07-h01-live-evaluation).
- [x] **[AG-07]** H02A/B 실제 비교·불일치 중단과 오프라인 재현을 기록했다. [Agent H02 기록](task_agent.md#ag07-h02-live-evaluation).
- [x] **[AG-07]** 중복 지적 지침·독립 오류 평가 준비를 완료했다. [Agent 준비 기록](task_agent.md#ag07-finding-group-preparation).
- [x] **[AG-07]** G01B/C 실제 비교·원문 검토를 완료했다. [Agent G01 기록](task_agent.md#ag07-g01-live-evaluation).
- [x] **[AG-07]** G01A/G02C 실제 비교·저장 손실 재현을 완료했다. [Agent 실행 기록](task_agent.md#ag07-g01a-g02c-live-evaluation).
- [x] **[AG-07]** 독립 문제 저장·ID/확인 이력 보완과 회귀 확인을 완료했다. [Agent 저장 보완 기록](task_agent.md#ag07-independent-issue-storage).
- [x] **[AG-07]** G02A/B 실제 비교와 G02A/B/C 실제 응답의 저장 재생을 완료했다. [Agent 실행 기록](task_agent.md#ag07-g02ab-live-evaluation).
- [x] **[AG-07]** H03A/B 접속 가능 인원·실적 표현 비교와 응답 저장 재생을 완료했다. [Agent H03 기록](task_agent.md#ag07-h03-live-evaluation).
- [x] **[AG-07]** H04A/B 번역·검수 수행 주체 비교와 응답 저장 재생을 완료했다. [Agent H04 기록](task_agent.md#ag07-h04-live-evaluation).
- [x] **[AG-07]** H05A/B 공개 예정·완료 표현 비교와 응답 저장 재생을 완료했다. [Agent H05 기록](task_agent.md#ag07-h05-live-evaluation).
- [x] **[AG-07]** H06A/B 적재 가능 무게·단위 환산 비교와 응답 저장 재생을 완료했다. [Agent H06 기록](task_agent.md#ag07-h06-live-evaluation).
- [x] **[AG-07]** H07A/B 취급 대상·제외 조건 비교와 응답 저장 재생을 완료했다. [Agent H07 기록](task_agent.md#ag07-h07-live-evaluation).
- [x] **[AG-07]** H08A/B 병렬 사실·인과관계 표현 비교와 응답 저장 재생을 완료했다. [Agent H08 기록](task_agent.md#ag07-h08-live-evaluation).
- [x] **[AG-07]** H02A/B 중복 지적 재시험과 응답 저장 재생을 완료했다. 첫 시험 실패는 별도 보존한다. [Agent H02 재시험 기록](task_agent.md#ag07-h02-retest).
- [x] **[AG-07]** 중복·독립 오류 반복 안정성 평가 준비를 완료했다. 준비 당시 실제 반복 평가는 미실행이었다. [Agent 준비 기록](task_agent.md#ag07-review-stability-preparation).
- [x] **[AG-07]** 반복 평가 1묶음 실행·원문/저장 확인을 완료했다. [Agent 1묶음 기록](task_agent.md#ag07-review-stability-batch-1).
- [ ] **[AG-07]** BE-06 담당 검토·남은 새 표현·반복 안정성·사용자 판정과 실제 모델 결과의 서버 저장/승인·화면 연결을 확인한다. [Agent 남은 일](task_agent.md#ag07-independent-issue-storage).
- [ ] **[AG-08 / BE-08/10]** 실자료 전체·사진별 품질 검수와 AG-08 전달, DOCX 승인 범위, C-05 자료 변경 복귀를 담당 간 맞춘다.

### 완료 검증 기준 (Definition of Done)

환경 준비가 필요한 PC는 저장소 루트에서 `uv sync --locked --group dev`를 실행한다. PowerShell에서 `$env:PYTHON_DOTENV_DISABLED = "1"`을 설정한 뒤 검사한다. 아래 백엔드·프론트 검사는 2026-09-29 담당자의 `C:\backend`·`C:\frontend` 실행 기록이다. 현재 Mac의 Agent 실제 반복 1묶음 결과는 [Agent 실행 기록](task_agent.md#ag07-review-stability-batch-1), 준비 검사 결과는 [Agent 준비 기록](task_agent.md#ag07-review-stability-preparation), 이전 Windows 검사는 [Agent PR 준비 기록](task_agent.md#ag07-pr-preparation)을 따른다.

- [x] **[BE-10]** 2026-10-06 최신 전체 회귀 **1,807 passed / 10 skipped / 3 warnings / 실패0**. 이전2026-09-29 실패3건은 이후 전체 검사에서 통과했고, 이번에 발견한 입력 변경/재시작 조회 기대값을 현행 C-05 계약에 맞춰 보완했다. 가상 자료/임시 DB/대역 AI·실제Chrome/LibreOffice 범위이며 실제 유료AI2건은skip이다. [최신 백엔드 완료 기준](task_backend.md#backend-completion-audit-20261006).
- [x] **[AG-07]** 2026-09-29 G01 실제 비교 전 선택 검사 **41 passed / 567 deselected / 1 warning**, 실제 G01B/C·응답 재생 확인 완료. [Agent G01 기록](task_agent.md#ag07-g01-live-evaluation). 이전 전체 608개·관련 2개 결과는 [준비 기록](task_agent.md#ag07-finding-group-preparation), 583개·H02B 불일치는 [H02 기록](task_agent.md#ag07-h02-live-evaluation), 582개는 [H01 기록](task_agent.md#ag07-h01-live-evaluation)·[PR 준비 기록](task_agent.md#ag07-pr-preparation)에 보존한다.
- [x] **[AG-07]** 2026-09-29 G01A/G02C 실제 응답·오프라인 재생과 독립 지적 손실 재현 완료. 당시 결과는 [Agent 실행 기록](task_agent.md#ag07-g01a-g02c-live-evaluation)에 보존한다.
- [x] **[AG-07]** 2026-09-29 독립 지적 저장 보완 후 선택 회귀 830개 최종 통과(최초 828개 통과·포트 제한 2개 재실행 통과). 가짜 모델과 임시 DB의 저장·API 검사이며 실제 전체 화면 연결은 미확인이다. [Agent 저장 보완 기록](task_agent.md#ag07-independent-issue-storage).
- [x] **[AG-07]** 2026-09-29 G02A/B 실제 비교·응답 저장 재생 확인 완료. 코드·테스트 변경이 없어 pytest는 반복 실행하지 않았다. [Agent 실행 기록](task_agent.md#ag07-g02ab-live-evaluation).
- [x] **[AG-07]** 2026-09-29 H03A/B 실제 비교·응답 재생·0/1건 저장과 반복 ID 보존 확인 완료. 기존 회귀 결과는 유지하며 실제 전체 화면 연결은 미확인이다. [Agent H03 기록](task_agent.md#ag07-h03-live-evaluation).
- [x] **[AG-07]** 2026-09-29 H04A/B 실제 비교·응답 재생·0/1건 저장과 반복 ID 보존 확인 완료. 코드·테스트 변경이 없어 pytest는 반복 실행하지 않았다. [Agent H04 기록](task_agent.md#ag07-h04-live-evaluation).
- [x] **[AG-07]** 2026-09-29 H05A/B 실제 비교·응답 재생·0/1건 저장과 반복 ID 보존 확인 완료. 코드·테스트 변경이 없어 pytest는 반복 실행하지 않았다. [Agent H05 기록](task_agent.md#ag07-h05-live-evaluation).
- [x] **[AG-07]** 2026-09-29 H06A/B 실제 비교·응답 재생·0/1건 저장과 반복 ID 보존 확인 완료. 코드·테스트 변경이 없어 pytest는 반복 실행하지 않았다. [Agent H06 기록](task_agent.md#ag07-h06-live-evaluation).
- [x] **[AG-07]** 2026-09-29 H07A/B 실제 비교·응답 재생·0/1건 저장과 반복 ID 보존 확인 완료. 코드·테스트 변경이 없어 pytest는 반복 실행하지 않았다. [Agent H07 기록](task_agent.md#ag07-h07-live-evaluation).
- [x] **[AG-07]** 2026-09-29 H08A/B 실제 비교·응답 재생·0/1건 저장과 반복 ID 보존 확인 완료. 코드·테스트 변경이 없어 pytest는 반복 실행하지 않았다. [Agent H08 기록](task_agent.md#ag07-h08-live-evaluation).
- [x] **[AG-07]** 2026-09-29 H02A/B 재시험·실패 재현 검사 1개 통과·실제 응답 재생·0/1건 저장·반복 ID 보존 확인 완료. 전체 회귀는 반복 실행하지 않았다. [Agent H02 재시험 기록](task_agent.md#ag07-h02-retest).
- [x] **[AG-07]** 2026-09-29 반복 평가 준비 후 Agent 전체 **650 passed / 1 warning**. 가짜 모델 검사이며 준비 당시 실제 반복·승인/화면 연결은 미실행이었다. [Agent 준비 기록](task_agent.md#ag07-review-stability-preparation).
- [x] **[AG-07]** 2026-09-29 반복 1묶음의 실제 응답·원문 대조·저장 재생·부분 집계를 확인했다. 전체 AG-07은 진행중이다. [Agent 1묶음 기록](task_agent.md#ag07-review-stability-batch-1).
- [x] **[BE-10 · 프론트 연계]** `C:\frontend`에서 `node scripts/check-ai-workflow.mjs --publication --photos`가 `result: PASS`다. 2026-09-29 19개 검사 묶음 통과, 시연 PDF 4쪽·26,213바이트.
- [x] **[BE-06/08/10]** 2026-09-30 Mac Chrome에서 실제 HTTP publication 및 사진 포함 검사 완료. [백엔드 실행 기록](task_backend.md#http-photo-publication-20260930).
- [x] **[BE-06 · 프론트 연계]** 프론트 연결 후 화면에서 허용 경고 확인 전·후의 승인 상태가 달라지고, 필수 문제가 남으면 확인 여부와 관계없이 승인이 막힌다. 관련 내용 변경 시 다시 검사·확인을 요구한다. 실제 LLM 시험(6.36·6.42·6.43)과 mock 검사에서 확인했다.
- **이번 검증 기록**: 백엔드 담당의 2026-09-29 실행 결과와 F 번호·상태를 두 담당 파일에 맞췄다. Agent 행과 상태는 바꾸지 않았다. 과거 테스트 수·화면 확인·실제 AI 시험의 원본은 각 담당 파일 5절을 따른다.

---

## 4. 향후 예정 기능

- **[F-08 실제 화면 연결·품질 평가] [AG-08 / BE-01/10] → 변경 공유 확인·실자료 전체 검수**: BE-10 + AG-08 + 프론트 FE-08. 백엔드 PR #21~#23의 병합을 확인했으며 프론트 공유 상태를 확인한 뒤 실자료 전체·사진 19개·목적별 분량을 검수한다. 핵심 검증은 해당 개발 담당이 수행하고, 보조 확인만으로 완료 처리하지 않는다. [백엔드 4절](task_backend.md#4-향후-예정-기능).
- **[F-05 내용 검증·경고 확인·승인] [AG-07 / BE-06] → 실제 검증 품질 확대**: AG-07 진행중·추가 반복 평가 후순위 · 2026-09-30 · [Agent 기존 평가·남은 일](task_agent.md#ag07-review-evaluation-history).
- **[F-04 직접 편집·AI 수정안·사진] [AG-05/06 / BE-05] → 자료 변경 후 편집 유지·수정안·사진 인계**: BE-04/05/06 + AG-02/03/05/06/07. C-05 복귀 흐름, 설계도 S02의 자료 변경 배너·페이지 추가, 백엔드가 초기 구현한 실제 문구 수정안·사진 검증의 Agent 인계. [백엔드 4절](task_backend.md#4-향후-예정-기능)·[Agent 4절](task_agent.md#4-향후-예정-기능).
- **[F-01 자료 선택·파일 읽기] [AG-01/02 / BE-02/03] → 실제 자료·원문 확인**: BE-01~03 + AG-01/02. 원문 조회와 공유 범위 정리. 적재 보류는 유지한다. [백엔드 4절](task_backend.md#4-향후-예정-기능).
- **[F-06 PDF·DOCX 내려받기] [BE-07/08/10] → 서버·시연 완료/다른 조건 품질 후속**: PDF·LibreOffice DOCX의 검사·승인·다운로드와 현재 실자료8쪽 표본 완료. 다른 목적/실자료 조건의 최종 품질은 F-08에서 확인한다. [최신 승인 결과](task_backend.md#native-ppi-approval-download-20261006) · [잔여 기준](task_backend.md#backend-completion-audit-20261006).
- **[F-07 세션 보호·종료 정리] [BE-09/10] → 실제 preflight 중단/복구 완료**: Windows 잠금·Chrome/HTTP·실제 preflight 요청 전송 중 종료/강제 중단·재시작/명시 재시도 확인. 다른 AI 작업은 대역 회귀 범위. [최신 백엔드 기록](task_backend.md#live-ai-interruption-20261006).
- **검수 기록 위치**: Agent QA-01/05/06/09/12/13은 [task_agent.md 5절](task_agent.md#5-완료된-기능-히스토리-누적-아카이브), 백엔드 QA-02/03/04/07/11/14/16/17/18/19/20은 [task_backend.md 5절](task_backend.md#5-완료된-기능-히스토리-누적-아카이브). 프론트 QA-08은 FQ-04/09, QA-10은 FQ-05/06, QA-15는 FQ-07의 별도 저장소 기록을 따른다.

---

## 5. 완료된 기능 히스토리 (누적 아카이브)

상세 결과를 복제하지 않고 완료된 부분의 날짜·범위·원본 위치를 연결한다. 상위 기능 전체의 상태는 2절을 따른다.

<a id="f-01"></a>
<a id="f-01-자료-선택파일-읽기--자료-접수읽기선택-기반"></a>

<a id="f-01-자료-접수읽기선택-기반"></a>

### [F-01 자료 선택·파일 읽기] [AG-01/02 / BE-02/03] → 자료 접수·읽기·선택 기반

- [x] **2026-09-25~29**: BE-02/03 서버 범위, 별도 시연 DB 적재, 현재 프론트 S01 자료 연결 완료. [백엔드 F-01](task_backend.md#f-01).
- **핵심 결정사항/메모**: 등록 원본과 세션 첨부를 구분한다. 시연 자료는 별도 DB에만 있고 기본 DB 적재는 보류다. Agent 연결 한계는 [Agent F-01](task_agent.md#f-01).

<a id="f-02"></a>
<a id="f-02-자료-점검작성-조건-추천-ag-0102--be-04--자료-분석과-구성-추천의-초기-연결"></a>
<a id="f-02-자료-분석과-구성-추천의-초기-연결"></a>
<a id="f-02-자료-점검작성-조건-추천--자료-분석과-구성-추천의-초기-연결"></a>

<a id="f-02-자료-분석과-구성-추천의-연결"></a>

### [F-02 자료 점검·작성 조건 추천] [AG-01/02 / BE-04] → 자료 분석과 구성 추천의 연결

- [x] **2026-09-28~29**: AG-01/02 제한된 실제 AI 시험·초기 추천 구현, 프론트 실제 LLM 점검 연결, 실자료 4개 점검 통과. [Agent F-02](task_agent.md#f-02)·[백엔드 F-02](task_backend.md#f-02).
- **핵심 결정사항/메모**: 선택 자료의 근거·누락·충돌을 보존하고 사용자 설정을 유지한다. AG-01/02 전체는 진행중이다.

<a id="f-03"></a>
<a id="f-03-사용자-확인초안-생성--확인-후-초안-저장조회"></a>

<a id="f-03-확인-후-초안-저장조회"></a>

### [F-03 사용자 확인·초안 생성] [AG-03/04 / BE-04] → 확인 후 초안 저장·조회

- [x] **2026-09-28~29**: S01 실제 AI의 서버·화면 시험, 최초 초안 대기·재개 연결, 실자료 초안 생성 확인. [Agent F-03](task_agent.md#f-03)·[F-07 세션 보호·종료 정리] [AG-03 / BE-02/09](task_agent.md#f-07)·[백엔드 F-03](task_backend.md#f-03).
- **핵심 결정사항/메모**: 최신 사용자 확인·입력 버전·중복 요청 검사를 유지한다. AG-03/04 전체는 진행중이다.

<a id="f-04"></a>
<a id="f-04-직접-편집ai-수정안사진-ag-0506--be-05--직접-편집선택-적용복원-기반"></a>
<a id="f-04-직접-편집선택-적용복원-기반"></a>
<a id="f-04-직접-편집ai-수정안사진--직접-편집선택-적용복원-기반"></a>

<a id="f-04-직접-편집수정안사진-연결"></a>

### [F-04 직접 편집·AI 수정안·사진] [AG-05/06 / BE-05] → 직접 편집·수정안·사진 연결

- [x] **2026-09-25~29**: BE-05 API, 프론트 S02 편집·실제 LLM 문구 수정안·사진 후보 교체·실제 이미지 검증 연결 완료. [백엔드 F-04](task_backend.md#f-04)·[F-08 실제 화면 연결·품질 평가] [AG-08 / BE-01/10](task_backend.md#f-08).
- **핵심 결정사항/메모**: 적용 전 원본 유지. 자동 이미지 검증은 정확성 보장이 아니다. 자료 변경 복귀·구조 수정은 후속이며 AG-05/06 상태는 대기로 유지하고 초기 구현을 인계한다.

<a id="f-05"></a>
<a id="3-현재-진행-작업-f-05-내용-검증경고-확인승인-now"></a>
<a id="3-현재-진행-작업-f-05-내용-검증경고-확인승인-ag-07--be-06-now"></a>
<a id="f-05-내용-검증경고-확인승인-ag-07--be-06--내용-검증-연결개별-경고-확인-서버"></a>
<a id="f-05-내용-검증-연결개별-경고-확인-서버"></a>
<a id="f-05-내용-검증경고-확인승인--내용-검증-연결개별-경고-확인-서버"></a>

<a id="f-05-내용-검증-연결개별-경고-확인실제-llm-검증"></a>

### [F-05 내용 검증·경고 확인·승인] [AG-07 / BE-06] → 내용 검증 연결·개별 경고 확인·실제 LLM 검증

- [x] **2026-09-28~29**: AG-07 일부 실제 비교 시험, BE-06 D-07 서버 처리, 프론트 경고 확인·승인 연결, 가상·실자료 표본의 실제 LLM 검증 완주. PR #18·#20 병합 확인. [Agent F-05](task_agent.md#f-05)·[백엔드 F-05](task_backend.md#f-05).
- **핵심 결정사항/메모**: 필수 문제는 해결 전 차단하며 허용 경고는 유효한 개별 확인이 필요하다. 검증 1회 통과를 사실 정확성 보장으로 보지 않는다.

<a id="f-06"></a>
<a id="f-06-pdfdocx-내려받기--pdf-승인반복-다운로드"></a>

<a id="f-06-pdf-승인반복-다운로드"></a>

### [F-06 PDF·DOCX 내려받기] [AG-04/08 / BE-07/08] → PDF 승인·반복 다운로드

- [x] **2026-09-26~29**: BE-07 도구 확인, BE-08 PDF 경로 검증, 실제 LLM 검증본 PDF(실자료 4쪽) 완주. [백엔드 F-06](task_backend.md#f-06)·[F-08 실제 화면 연결·품질 평가] [AG-08 / BE-01/10](task_backend.md#f-08).
- **핵심 결정사항/메모**: 출력 재시도에 AI 초안을 다시 생성하지 않는다. DOCX는 생성 기반까지만 확인했다.

<a id="f-07"></a>
<a id="f-07-세션-보호종료-정리--세션-정리와-db-관계-보호"></a>

<a id="f-07-세션-정리와-db-관계-보호"></a>

### [F-07 세션 보호·종료 정리] [AG-03 / BE-02/09] → 세션 정리와 DB 관계 보호

- [x] **2026-09-27~29**: BE-09 서버 정리, AG-03 최초 초안 재개 기록 정리, BE-02 DB v11 관계 검사, 시험 세션 정리 확인. [백엔드 F-07](task_backend.md#f-07)·[Agent F-07](task_agent.md#f-07).
- **핵심 결정사항/메모**: 종료·만료 접근 차단과 실제 삭제 완료를 구분한다. 현재 PC의 기본 DB는 등록 자료 0개, 시연 DB는 18개다.

<a id="f-08"></a>
<a id="f-08-실제-화면-연결품질-평가-ag-08--be-0110--가짜-ai-기반-화면-확인과-병합"></a>
<a id="f-08-가짜-ai-기반-화면-확인과-병합"></a>
<a id="f-08-실제-화면-연결품질-평가--가짜-ai-기반-화면-확인과-병합"></a>

<a id="f-08-화면-연결설계도-적용병합"></a>

### [F-08 실제 화면 연결·품질 평가] [AG-08 / BE-01/10] → 화면 연결·설계도 적용·병합

- [x] **2026-09-28~29**: PR #18·#19·#20 병합 확인, 현재 프론트 S01~S03 실제 연결, 화면설계도 v1.0 배치 적용, mock 전체 흐름 19개 묶음 통과. [백엔드 F-08](task_backend.md#f-08)·[Agent F-08](task_agent.md#f-08).
- **핵심 결정사항/메모**: 실자료 전체·사진별·DOCX·자료 변경 복귀를 포함한 전체 검수는 BE-10 진행중/AG-08 대기로 유지한다. 백엔드 변경은 PR #21~#23에 병합되어 있으며 프론트 공유 상태는 이번 작업에서 확인하지 않았다.

- **2026-09-29 표기 동기화 완료**: 공통 현황판에는 기능명 옆의 AG/BE 병기와 담당별 참조 열 분리를 추가했다. [Agent F-08 기록](task_agent.md#f-08)·[백엔드 F-08 기록](task_backend.md#f-08). 기능·담당 업무 참조·테스트 사례 번호의 구분은 [번호 읽는 기준](#번호-읽는-기준)을 따른다.

- **2026-09-29 PR 준비 동기화**: 최신 develop의 상태·기록과 이번 AG-07 평가 준비·F/AG/BE 표기를 통합했다. [Agent PR 준비 기록](task_agent.md#ag07-pr-preparation).
