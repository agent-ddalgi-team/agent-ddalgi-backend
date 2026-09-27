# 팀 샘플에 맞춘 적재 입력 v1.1

사용자가 제공한 schema_samples 1~5절과 대조했습니다. 이 문서는 가상 데이터의 스키마 안내이며 실제 회사의 내용·원본 경로·해시를 복사하지 않았습니다. 실제 ZIP 전체와 적재기 통합 실행은 아직 검증하지 않았습니다.

## 경로와 공통 파서

이 패키지의 자료 묶음 루트는 ingest/입니다. sources.path와 photo_candidates.path는 이 루트 기준입니다. sources.filename은 표시 파일명이며 파일 접근에는 path를 사용합니다. 실제 묶음에서는 06_개발전달에 JSON이 있어도 path의 기준은 자료 묶음 루트입니다. 파서는 bundle_root, sources/chunks/CSV/candidates 경로를 인자로 받아 동일한 규칙을 사용해야 합니다.

CSV 폴더는 묶음 루트/05_이미지/전체_추출이미지/ 아래 상대 폴더입니다. images/에는 기존 UI filename 보존용 동일 바이트 사본이 있습니다. 이미지 목록과 candidates에 나온 정식 경로만 적재하고 두 폴더를 재귀 검색해 중복 등록하지 않습니다.

## sources.json

최상위 배열. source_id, filename, available_in_package(bool), status, path(str|null), sha256(str|null), date_from_filename(str|null), document_date_verified(bool), extraction_method(str|null), origin_group(str), use_as_company_evidence(bool), note를 사용합니다. mock(bool), document_date, document_date_note는 샘플에 있는 선택 필드입니다.

허용 status: ready / missing_original / planning_reference / content_confirmed_original_missing / received_by_team_not_in_package / mock. 이 공식 가상 묶음 8건은 모두 mock입니다. 종전 reference_only/image_only 상태값은 ingest에서 제거했습니다.

name은 사용자가 요구한 [MOCK] 표시용 선택 확장 필드입니다. 실제 팀 항목에는 없어도 되며, 적재기는 name을 필수로 요구하지 않고 filename을 표시명으로 쓸 수 있어야 합니다. mock 적재 시 표시명에 [MOCK]를 보장해야 합니다. 이전 kind/mime_type/size_bytes/origin_format/source_version/document_date_precision 확장은 제거했습니다.

origin_group은 파생 자료를 하나의 증거 원천으로 묶습니다. MOCK03·04는 가상 인증의 국문·영문 대조본이므로 MOCK_CERT_Q를 공유합니다. 다른 항목은 각 source_id를 사용합니다. 그룹 안 자료를 독립 증거로 중복 집계하지 않습니다.

use_as_company_evidence=false인 MOCK06은 검색·근거에서 제외합니다. 나머지 true는 --with-mock 테스트 안에서 가상 회사의 근거로 쓰기 위한 값입니다. 실제 회사에 관한 사실 또는 외부 공개 승인이 아닙니다. 옛 항목별 참고표 mock은 이번 묶음에 합치지 않았습니다.

원본 파일명에 날짜가 없으므로 date_from_filename=null입니다. 가상 자료 시점은 document_date에 보존했습니다. 2017은 연도 정밀도 그대로이며 월·일을 만들지 않습니다. 날짜 우선순위는 chunk.document_date → source.document_date → source.date_from_filename입니다. document_date_verified=false를 유지합니다.

## company_chunks.jsonl

chunk_id, source_id, origin_group, locator(str), text, extraction_method, evidence_status, company_confirmation, publication_allowed, notes, mock을 사용합니다. 오래된 가상 카다로그 역할의 MOCK02에만 document_date=2017을 직접 둡니다. 나머지는 출처 날짜를 참조합니다. 임의의 field_key는 raw 입력에서 제거했습니다.

extraction_method의 실제 값은 parser / manual / manual_excerpt입니다. 현재 가상 TXT 행은 parser, MOCK02의 가상 발췌는 manual_excerpt입니다. source 단위의 mock과 chunk 단위 추출 방식은 별개입니다.

evidence_status 허용 값은 자료에 기재됨 / 이미지에서 판독한 발췌 / 자료에 기재됨 (2017년 카다로그)입니다. 현재 TXT 입력에는 첫째·셋째 값을 사용합니다. company_confirmation은 모두 미확인, publication_allowed는 모두 null입니다. 원문 기재나 candidate 선택만으로 true 또는 확인됨으로 바꾸지 않습니다.

locator는 전부 문자열입니다. 현재 원본이 실제 TXT이므로 샘플에서 제안한 TXT {n}행을 사용하고 해당 행의 내용까지 검증합니다. PPT/PDF 원본을 만들지 않고 TXT 위치를 PPT 위치라고 바꾸지 않았습니다. 검증기는 PPT {n}쪽 · 제목, PPT {n}쪽 · image{k} · 제목, 카다로그 {n}쪽 · 제목도 별도 가상 사례로 검사합니다. 객체 변환 예시는 normalized/에만 있고 raw chunks에는 없습니다.

normalized/segments.jsonl의 ID·본문·locator 연결은 유지됩니다. 그 파일의 synthetic_fixture/usable/needs_confirmation/excluded는 기존 화면 스냅샷 메타데이터이며, 새 한글 evidence_status를 실제 적재해 얻은 출력이라고 간주하지 않습니다. 새 적재기는 원래 값·origin_group·미확인·공개 여부를 보존해야 합니다.

mock_source_entry.json과 mock_chunks.jsonl은 MOCK01 한 출처만 넣는 대체 입력입니다. 전체 sources/chunks에 이미 있으므로 두 입력을 합쳐 읽지 않습니다. 샘플의 옛 15행 참고표는 추가하지 않았습니다.

## 00_이미지목록.csv와 photo_candidates.json

CSV는 UTF-8 BOM, 아래 7열을 정확히 이 순서로 사용합니다.

```text
폴더,파일명,PPT페이지,원본이미지,처리,가로px,세로px
```

SHA·공개 상태·mock·source_id 열을 추가하지 않았습니다. 처리 값은 원본 그대로 추출이며 PPT페이지 1~6은 가상 이미지 배치 위치입니다. 원본이미지도 가상 식별자입니다. PNG는 합성 도형이고 실제 PPTX에서 추출한 결과라는 뜻이 아닙니다. reason에 이 구분을 적었습니다.

photo_candidates.json은 항목 배열입니다. photo_id, path, caption_candidate, locator, reason, width, height, ratio, selected_as_candidate, approved_for_external_use, caption_confirmed, confirmed_by, confirmed_at을 사용합니다. 모든 공개 확인은 null, 캡션 확인은 false, 확인자·시각은 null입니다. 앞 4개가 후보로 선택되어 있지만 공개 허가는 아닙니다.

사용자 요청에 따라 sha256(해당 PNG 파일 바이트)을 candidates에 추가했습니다. mock/출처 연결을 위한 mock=true와 source_id=MOCK07도 선택 확장입니다. 이 세 필드는 실제 팀 candidates 샘플에 없으므로 공통 파서가 필수로 요구해서는 안 됩니다. 실제 자료의 source 연계는 자료 목록/원천 정보로 결정해야 하며 PPT locator만으로 여러 출처 중 하나를 추측하면 안 됩니다.

기존 UI에서 외부 사용 허용·대기·제외로 둔 상태는 변경하지 않았습니다. ingest가 모두 미확인인 상태와 독립적으로 검사하며 UI 허가를 raw 입력에 역으로 복사하지 않습니다.

## SHA 검증과 중복 판정 — 샘플 5절

1. path 파일이 있고 sha256이 있으면 실제 바이트의 SHA-256과 비교합니다. 불일치는 오류입니다.
2. sha256_note에 원본의 해시 또는 원본 파일의 SHA-256이라고 명시되면 제공된 축소본 바이트와 비교하지 않고 hash_verified=false로 기록합니다. 검증 생략 사유도 보존합니다.
3. sha256=null은 중복 판정에 사용하지 않습니다. 파일이 있어 계산할 수 있더라도 제공 해시 검증 성공으로 표시하지 않습니다.
4. path=null이면 원본 미포함으로 기록합니다. path가 있는데 파일이 없으면 오류입니다. available_in_package=true인데 path=null도 오류입니다.
5. 검증되지 않은 원본 해시로 서로 다른 축소본을 같다고 판단하지 않도록 이 검증 예시의 dedup_sha256은 검증된 해시만 제공합니다. 서버의 저장 정책 확정 시 원래 sha256과 계산한 패키지 파일 해시를 분리해야 합니다.

현재 MOCK 원본 8개는 실제 포함된 TXT 바이트의 검증 가능한 해시입니다. MOCK07 사진 목록과 MOCK08 빈 TXT는 서로 다른 SHA입니다. 검증기의 자체 가상 사례에는 해시 불일치·null·미포함·축소본 예외·경로 이탈을 포함합니다. 실제 296MB 원본이나 축소 PDF는 포함하지 않습니다.

## 검증 범위

verify_bundle.py는 표준 라이브러리만 사용합니다. raw 스키마·파일 해시·CSV/candidates 연계·기존 화면 참조·원본 대비 보존 해시를 검사합니다. 실제 적재 CLI, DB, 서버, LLM을 실행하지 않습니다. quality/는 이전 테스트 설계 명세로 보존했고, CSV 변경에 따른 필드 치환은 README에 설명했습니다.
