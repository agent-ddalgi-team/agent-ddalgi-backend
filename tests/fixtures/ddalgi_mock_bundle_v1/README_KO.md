# 백엔드와 화면 개발용 mock 데이터

자료 수령 확인 문서의 문제 유형과 디자인 가이드의 화면 구성을 참고해 새로 만든 가상 자료 묶음입니다. 모든 회사명·날짜·수량·공정·문장은 가상이며 실제 회사의 원문·사진·연락처·인증값을 포함하지 않습니다.

가상 자료 8개, 텍스트 구간 24개, 테스트 이미지 6개, 화면 상태 27개를 제공합니다. 디자인 가이드의 15개 상태를 모두 포함하며 수정안·사진 후보·저장 충돌·세션 만료 등 12개 상태를 추가했습니다.

## 먼저 확인할 사항

- 아직 등록 자료 CLI를 구현하거나 실제 DB에 적재하지 않았습니다. 이 묶음은 구현·테스트에 사용할 입력과 화면 예시입니다.
- ingest 입력을 사용자가 제공한 schema_samples 1~5절에 맞췄습니다(v1.1). 실제 ZIP을 열거나 같은 적재기로 실행한 결과는 아니며, 선택 확장 필드와 경로 기준은 INGEST_SCHEMA.md에 명시했습니다.
- 전체 자료가 mock=true입니다. 적재기는 --with-mock이 있을 때만 넣어야 합니다. 이 옵션을 강제하는 적재 코드는 아직 이 묶음에 없습니다.
- use_as_company_evidence=true는 이 테스트에서 가상 회사의 근거로 사용할 수 있다는 뜻입니다. 실제 회사에 대한 근거 또는 외부 공개 허가가 아닙니다. mock=true와 MOCK 표시를 함께 확인해야 합니다.
- 승인·Validation·LayoutCheck·Export 데이터는 future_ui_fixture로 표시한 화면 개발용 예시입니다. BE-06~08 구현 완료나 실제 파일 생성 결과가 아닙니다.
- 실제 LLM·백엔드 API·출력 렌더러는 실행하지 않았습니다. 검증 보고서는 데이터 구조·참조·해시·상태 연결 검사 결과입니다.

## 폴더별 사용법

| 경로 | 용도 |
|---|---|
| ingest/sources.json | 등록 자료 8개의 원본 메타데이터 배열 |
| ingest/company_chunks.jsonl | 자료별 텍스트 구간 24개 |
| ingest/00_이미지목록.csv | 실제 팀과 같은 UTF-8 BOM·7열 목록 |
| ingest/photo_candidates.json | 이미지 경로·SHA·후보 선택·공개 확인 상태 |
| ingest/05_이미지/전체_추출이미지/ | CSV 폴더·파일명으로 찾는 PNG 원본 |
| ingest/originals/ | SHA 계산 대상이 되는 가상 UTF-8 TXT 원본 8개 |
| ingest/images/ | 기존 UI 경로 보존용 동일 PNG 사본. 적재 대상에 중복 추가하지 않음 |
| ingest/mock_source_entry.json, mock_chunks.jsonl | MOCK01만 사용하는 최소 예시. 전체 묶음과 중복 적재하지 않는 대체 입력 |
| normalized/segments.jsonl | 기존 UI용 정규화 스냅샷 보존. ID·텍스트·locator는 연결되며 상태 메타데이터는 새 raw 입력과 구분 |
| ui/catalog.json | 자료 카드·이미지·Fact·A/B/C 작성 방향·쪽수 옵션·보조 메타데이터 |
| ui/material_receipt.json | 자료 수령 체크리스트 18개. 자료 수집의 필수 여부와 문서 승인의 필수 여부는 다름 |
| ui/scenarios/index.json | 화면 상태 목록. 각 항목의 path는 이 묶음 루트 기준 |
| ui/scenarios/*.json | API 형태의 data와 화면 전용 ui 상태를 분리한 독립 스냅샷 |
| ui/documents/ | 1·4·6·8·10쪽 목차·페이지 이동용 문서 JSON |
| api_flows/be05_edit_cycle.json | rev1 → PATCH rev2 → apply rev3 → 같은 키 재전송 rev3 → restore rev4 예시 |
| quality/ | locator 변환과 잘못된 적재 입력을 만드는 테스트 명세 |
| verify_bundle.py | DB·네트워크 없이 실행하는 표준 라이브러리 검증기 |
| validation_report.json | 파일·참조 검사와 현재 BE-05 Pydantic 모델 검사 결과 |

## 먼저 사용할 조합

1. 정상 흐름: MOCK01 + MOCK07, source_normal → source_preflight → edit_normal.
2. 날짜 충돌: MOCK03 + MOCK04를 추가하고 edit_conflict. 화면에서 두 원문 위치를 나란히 보여줍니다.
3. 오래된 자료·공정 수 차이: MOCK01(가상 3개) + MOCK02(가상 4개, 2017년). 날짜와 원문을 모두 유지하며 자동으로 하나를 정답으로 고르지 않습니다.
4. 납기 조건: MOCK05를 보완 자료로 선택. 수량·기산 시점·기간·예외 문구를 함께 유지하는 테스트에 사용합니다.
5. 사진 UI는 기존 허용·대기·제외 상태를 보존했습니다. 새 ingest에서는 전부 approved_for_external_use=null이며 후보 선택과 사용 허가를 구분합니다. UI의 가상 허가를 적재 입력으로 역복사하지 마세요.
6. 수정안: edit_proposal, edit_image_candidates, edit_stale_proposal, edit_save_conflict.
7. 승인·출력 화면: approve_ready / blocked / approved / modified / failure. 이 다섯 개는 후속 구현용 fixture입니다.

## 실제 자료와 구분하기

공개 저장소에 넣을 수 있는 것은 이 묶음처럼 처음부터 가상으로 작성한 fixture와 검증 코드입니다. 사용자에게 받은 실제 원문·이미지·추출 JSONL은 복사하지 않았습니다. 실제 묶음은 gitignore된 private_runs/registered_src/ 또는 저장소 밖 경로에서만 사용하세요.

팀 자료의 실제 내용으로 fixture 값을 교체해 커밋하면 안 됩니다. 실제 적재 기록은 원문·사진·파일 경로 없이 건수와 성공/실패 결과만 남기는 것이 이 작업의 요구사항입니다.

## 백엔드 연결 시 필요한 조치

- GET /sources는 계약에 있으나 BE-05 기준 아직 구현되지 않았습니다. 자료 선택, 사전 점검, 출처 검사, 이미지 조회도 현재 세션 자료만 처리하므로 등록 자료 연결이 필요합니다.
- [MOCK] 접두어가 붙은 라벨을 기존 mock Agent는 인식하지 못합니다. 라벨을 읽을 때만 접두어를 분리하고, DB text와 EvidenceRef.excerpt의 표시를 지우지 않아야 합니다.
- document_date·evidence_status·사진 공개 범위는 현재 SourceOut의 정식 필드가 아닙니다. ui/catalog.json의 보조 메타데이터로 제공했고 실제 응답 필드 확장은 팀 계약 확인이 필요합니다.
- ingest의 evidence_status는 실제 한글 값이며 company_confirmation=미확인, publication_allowed=null입니다. 기존 normalized/UI의 usable·needs_confirmation·excluded는 별도 화면 스냅샷 상태입니다. parse_status=complete도 사실 확인 완료를 뜻하지 않습니다.
- --with-mock 없이 0건, 두 번째 동일 적재는 추가 0건이 기대 결과입니다. 단일 예시와 전체 묶음을 합쳐 읽지 마세요.
- 원본 파일명은 실제 TXT입니다. SHA-256은 제공한 TXT 바이트와 일치합니다. PPT/PDF에서 추출했다고 주장하지 않습니다. PPT 12쪽 → {"slide":12} 같은 변환은 quality/locator_cases.json에서 독립적으로 시험합니다.

## 화면 데이터 사용

scenario.data에는 기존 API 형태의 객체를, scenario.ui에는 체크박스·모달·선택 블록·저장 전 입력 등 화면 상태를 두었습니다. fixture 메타데이터와 ui 객체를 서버 요청 본문에 그대로 보내지 마세요.

각 화면 파일은 독립 상태입니다. 동일한 mock ID가 여러 파일에서 반복되며 이 파일들을 한 DB에 모두 적재하면 안 됩니다. 시간 흐름은 api_flows/be05_edit_cycle.json만 연결된 예시입니다.

```javascript
// 개발 환경에서 JSON을 import한 예시
import scene from './ddalgi_mock_bundle_v1/ui/scenarios/edit_proposal.json';
const document = scene.data.document_response.document;
const proposals = scene.data.proposals;
const dialog = scene.ui.dialog;
```

ui/documents의 쪽수는 논리 페이지 수입니다. 실제 PDF 쪽수 측정 결과가 아닙니다. 8·10쪽 fixture의 추가 페이지는 '추가 확인 필요'로 두어 없는 사실로 분량을 채우지 않았습니다.

출력 준비 상태의 artifact ID는 가상입니다. 다운로드 URL이나 실제 PDF/DOCX 파일을 제공하지 않으며, 실패 화면에서 같은 승인본으로 재시도한다는 UI만 표현합니다.

## 검증 실행

```sh
python verify_bundle.py
```

Python 표준 라이브러리만 사용합니다. 이 스크립트는 원본·이미지 SHA, PNG 크기, ID 연결, 원문 인용 위치, mock 표시, 선택 자료 범위, 버전 흐름, 15개 필수 화면 포함 여부, 패키지 체크섬을 검사합니다. UI 픽셀 검증·실제 서버 테스트·AI 의미 검증은 별도입니다.

오류 fixture의 오류 코드는 테스트 설계용 제안입니다. 현재 서버가 이미 같은 코드를 반환한다고 가정하지 마세요.

## v1.1 스키마 정렬 범위

- ui/, api_flows/, quality/, normalized/는 바이트 단위로 보존했습니다. 화면 시나리오는 ingest를 자동 실행한 결과가 아닙니다.
- ingest 데이터와 검증기·체크섬·이 안내·스키마 안내·보고서만 갱신했습니다.
- sources의 모든 항목은 status=mock입니다. 형식별 수령/파싱 상태는 이 값과 별개입니다. MOCK03·04는 origin_group=MOCK_CERT_Q로 묶습니다.
- MOCK07 원본은 실제 이미지 목록 TXT로 바꿔 빈 MOCK08과 SHA가 겹치지 않게 했습니다.
- quality/의 missing_image·path_escape는 이전 CSV 필드(image_id/filename)를 쓰는 설계 명세입니다. 새 실행 테스트에서는 photo_candidates의 photo_id/path 또는 CSV의 폴더/파일명으로 대상 지정이 필요합니다. 해당 폴더는 요청대로 수정하지 않았습니다.
