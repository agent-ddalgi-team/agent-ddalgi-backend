"""배치 검사(LayoutCheck) — BE-06은 조회·일치 검사와 해시 계산만. 실행·Job·API는 BE-08.

승인 조건 ⑥: layout_checks 행이 문서·문서 버전·입력 버전·형식·template_version·render_options_hash·asset_manifest_hash와
모두 일치하고 status=passed여야 한다. 런타임에 행을 만들거나 통과로 바꾸는 코드는 없다(테스트에서만 가상 행 삽입).

출력 식별값 세 가지의 정의는 이 모듈 한 곳에만 있다(계약 확인 ㉖, BE-07 확정). 렌더 어댑터(app/services/export_render.py)도
여기서 import해서 쓰므로 렌더러와 승인 검사가 다른 값을 계산할 수 없다.

- TEMPLATE_VERSION: 템플릿(HTML/CSS/측정 JS)·DOCX 배치 상수·동봉 폰트 파일을 대표하는 하나의 문자열. 이 중 하나라도 바뀌면
  올린다(tests/test_be07.py의 가드 테스트가 sha256으로 확인). PDF/DOCX 공통이라 어느 형식의 변경이든 두 형식의 배치 검사가 함께 무효화된다.
- DEFAULT_RENDER_OPTIONS: 형식 공통 렌더 옵션. 값이 바뀌면 RENDER_OPTIONS_HASH가 바뀐다. render()는 호출자 옵션을 받지 않으므로
  해시에 반영되지 않는 가변 옵션은 없다. 브라우저 종류·버전은 옵션이 아니라 실행 환경이라 해시 밖(RenderResult.renderer에 기록).
- asset_manifest_hash: 문서 image 블록 순서대로 (asset_id, content_hash)를 모아 정렬해 해시. 같은 이름으로 파일을 바꿔도 값이 달라진다.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable

from app.models import Document

TEMPLATE_VERSION = "template_v0"
DEFAULT_RENDER_OPTIONS = {
    "template_key": "company_intro",
    "page_size": "A4",
    "margin_mm": 15,
    "font_family": "Pretendard",
    "font_version": "1.3.9",
    "base_font_pt": 10.5,
}


def render_options_hash(options: dict) -> str:
    return hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()[:16]


RENDER_OPTIONS_HASH = render_options_hash(DEFAULT_RENDER_OPTIONS)


def manifest_hash(items: Iterable[tuple[str, str]]) -> str:
    """(asset_id, content_hash) 목록의 해시. DB를 보지 않는 순수 함수 — 스냅샷(렌더)과 승인 검사가 같은 입력으로 같은 값을 얻는다."""
    return hashlib.sha256(json.dumps(sorted(tuple(i) for i in items)).encode()).hexdigest()[:16]


def image_asset_ids(document: Document) -> list[str]:
    """문서의 image 블록 asset_id를 배열 순서대로(중복 포함)."""
    return [block.content.get("asset_id") for page in document.pages for block in page.blocks if block.type == "image"]


def asset_manifest_hash(conn: sqlite3.Connection, document: Document) -> str:
    """승인 검사용: 현재 assets 테이블의 content_hash로 manifest_hash를 계산한다. 없는 asset은 빈 문자열."""
    items = []
    for aid in image_asset_ids(document):
        row = conn.execute("SELECT content_hash FROM assets WHERE asset_id=?", (aid,)).fetchone()
        items.append((aid, row["content_hash"] if row else ""))
    return manifest_hash(items)


def matching_passed(conn: sqlite3.Connection, layout_check_id: str, document: Document, input_revision: int,
                    fmt: str, manifest_hash_value: str) -> tuple[sqlite3.Row | None, str | None]:
    """(행, 불일치 사유). 사유가 None이면 조건 ⑥ 통과."""
    row = conn.execute("SELECT * FROM layout_checks WHERE layout_check_id=?", (layout_check_id,)).fetchone()
    if row is None:
        return None, "layout_check_not_found"
    checks = [
        (row["document_id"] == document.document_id, "document_mismatch"),
        (row["document_revision"] == document.document_revision, "document_revision_mismatch"),
        (row["input_revision"] == input_revision, "input_revision_mismatch"),
        (row["format"] == fmt, "format_mismatch"),
        (row["status"] == "passed", f"status_{row['status']}"),
        (row["template_version"] == TEMPLATE_VERSION, "template_version_mismatch"),
        (row["render_options_hash"] == RENDER_OPTIONS_HASH, "render_options_hash_mismatch"),
        (row["asset_manifest_hash"] == manifest_hash_value, "asset_manifest_hash_mismatch"),
    ]
    for ok, reason in checks:
        if not ok:
            return row, reason
    return row, None
