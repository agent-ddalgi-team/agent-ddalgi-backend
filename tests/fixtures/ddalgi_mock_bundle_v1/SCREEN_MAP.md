# 화면별 mock 연결표

디자인 가이드의 세 단계·15개 상태를 기준으로 작성했습니다. API 객체와 화면 전용 상태는 분리되어 있습니다.

| 화면 파일 | 화면 | 표시·연결할 데이터 | 구분 |
|---|---|---|---|
| [source_normal](ui/scenarios/source_normal.json) | 등록 자료로 시작 | source_list, source_metadata, presets, page_options | 기존 객체 형식 기반 예시 |
| [source_upload](ui/scenarios/source_upload.json) | 이번 세션에만 첨부 | session_source_list, session_segments, expires_at | 기존 객체 형식 기반 예시 |
| [source_preflight](ui/scenarios/source_preflight.json) | 목적·분량 사전 확인 | preflight.recommendations, can_generate, confirmed_at, 확인 체크 | 기존 객체 형식 기반 예시 |
| [source_unread](ui/scenarios/source_unread.json) | 파일 내용을 읽지 못함 | parse_status=failed, warnings, 보완·제외 안내 | 기존 객체 형식 기반 예시 |
| [source_photoonly](ui/scenarios/source_photoonly.json) | 사진만 선택함 | image_available, text_available, can_generate=false | 기존 객체 형식 기반 예시 |
| [edit_normal](ui/scenarios/edit_normal.json) | 목차·페이지·블록 편집 | document.pages, block_id, fact_ids, evidence_refs | 기존 객체 형식 기반 예시 |
| [edit_conflict](ui/scenarios/edit_conflict.json) | 인증 날짜 불일치 비교 | Fact.alternatives, Issue, 양쪽 EvidenceRef 원문 | 후속 구현용 화면 예시 |
| [edit_required](ui/scenarios/edit_required.json) | 필수 본문 누락 | 비어 있는 본문, blocker, 제외 불가 | 후속 구현용 화면 예시 |
| [edit_nophoto](ui/scenarios/edit_nophoto.json) | 사진 없이 글 중심 편집 | 사진 없는 페이지, photo_preference=none | 기존 객체 형식 기반 예시 |
| [edit_overflow](ui/scenarios/edit_overflow.json) | 페이지 넘침 | 긴 문단, layout issue, 대상 block_id | 후속 구현용 화면 예시 |
| [approve_ready](ui/scenarios/approve_ready.json) | 최종 확인·형식 선택 | 최신 Validation·LayoutCheck, final_confirmed=false | 후속 구현용 화면 예시 |
| [approve_blocked](ui/scenarios/approve_blocked.json) | 미해결 2건으로 승인 차단 | 미해결 Issue 2개, 승인 비활성 이유 | 후속 구현용 화면 예시 |
| [approve_approved](ui/scenarios/approve_approved.json) | 승인 완료·출력 준비 | 승인 버전·형식, Export ready, 가상 artifact ID | 후속 구현용 화면 예시 |
| [approve_modified](ui/scenarios/approve_modified.json) | 승인 후 편집·재승인 필요 | 문서 rev4, 과거 검사 rev3, invalidated 승인 | 후속 구현용 화면 예시 |
| [approve_failure](ui/scenarios/approve_failure.json) | 출력 실패·같은 승인본 재시도 | Export failed, retryable, 같은 approval_id | 후속 구현용 화면 예시 |
| [edit_proposal](ui/scenarios/edit_proposal.json) | AI 수정안 전후 비교·적용·거절 | 현재 블록과 Proposal.changes 비교, 적용·거절 버튼 | 기존 객체 형식 기반 예시 |
| [edit_image_candidates](ui/scenarios/edit_image_candidates.json) | 사진 후보 선택·가로세로 비율 비교 | candidates, selected_candidate_id, 이미지 메타데이터 | 기존 객체 형식 기반 예시 |
| [edit_no_image_candidates](ui/scenarios/edit_no_image_candidates.json) | 사진 후보 없음·추가 업로드 안내 | 빈 후보 오류, 사진 자리, 업로드 안내 | 기존 객체 형식 기반 예시 |
| [edit_stale_proposal](ui/scenarios/edit_stale_proposal.json) | 오래된 수정안 적용 거부 | Proposal 기준 rev3와 현재 rev4, 409 안내 | 기존 객체 형식 기반 예시 |
| [edit_save_conflict](ui/scenarios/edit_save_conflict.json) | 저장 충돌·사용자 입력 보존 | 409 현재 버전, local_draft_text 보존 | 기존 객체 형식 기반 예시 |
| [edit_unsaved](ui/scenarios/edit_unsaved.json) | 저장되지 않은 수정 | unsaved_changes, 저장 전 승인 차단 | 기존 객체 형식 기반 예시 |
| [edit_resolved_history](ui/scenarios/edit_resolved_history.json) | 처리한 문제 접기·해결 기록 보기 | Issue.resolution, show_resolved | 후속 구현용 화면 예시 |
| [source_empty](ui/scenarios/source_empty.json) | 등록 자료·첨부 없음 | 빈 목록, 첫 첨부·선택 안내 | 기존 객체 형식 기반 예시 |
| [source_loading](ui/scenarios/source_loading.json) | 읽기 진행·작업 폴링 | Job running, progress.stage/message | 기존 객체 형식 기반 예시 |
| [edit_input_changed](ui/scenarios/edit_input_changed.json) | 자료 변경 후 사전 확인 해제 | 입력 rev4·문서 입력 rev3, 사전 확인 무효 | 기존 객체 형식 기반 예시 |
| [source_expired](ui/scenarios/source_expired.json) | 세션 만료·새 세션 안내 | 410 오류, 서버 문서 없음, 새 세션 행동 | 기존 객체 형식 기반 예시 |
| [source_old_material](ui/scenarios/source_old_material.json) | 2017년 자료·현재 적용 여부 안내 | document_date=2017, 보조 메타데이터, 경고 | 기존 객체 형식 기반 예시 |

## 화면 설계에서 보완한 데이터

- 자료 카드: 자료 종류·형식·읽기 상태, 사용 여부, 날짜, 사진 공개 범위를 서로 구분합니다.
- 편집기: 페이지·블록 ID를 고정하고 출처 패널을 Fact와 EvidenceRef로 연결합니다.
- AI 패널: 제안 전후 내용, 대상 블록, 후보 사진, 생성 기준 버전을 보여줍니다.
- 승인 화면: 현재 버전과 검사·승인 버전의 일치 여부를 보여주고 미해결 문제는 요약합니다.
- 출력 화면: 출력 상태와 승인 상태를 별도로 보여줍니다. 실패해도 승인본은 유지합니다.
- 자료 수령표: 수집상 필수 자료와 모든 문서에서 필수인 내용을 분리합니다. 인증·사진 부족을 무조건 문서 생성 불가로 만들지 않습니다.

## 기준이 다를 때

디자인 가이드의 도구 제안·구현 현황 문구는 과거 시점 설명입니다. 이 묶음은 화면 구성만 참고하며 BE-05의 계약·코드보다 우선하는 지침으로 사용하지 않았습니다. Chroma·Playwright·새 출력 도구를 도입하지 않았습니다.

가이드의 6–8쪽 추천 표시는 계약의 단일 suggested_pages 값 8로 표현했습니다. 실제 추천 알고리즘을 구현한 것은 아닙니다.

자료수령 문서의 인증 날짜·공정 수 불일치, 사진 공개 범위 미확인, 선택 자료 누락은 가상 날짜·가상 숫자·가상 인증으로 바꾸어 재현했습니다. 실제 문서 내용은 패키지에 넣지 않았습니다.
