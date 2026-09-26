"""배치 검사(LayoutCheck) — BE-06은 조회·일치 검사와 해시 계산만. 실행·Job·API는 BE-08.

승인 조건 ⑥: layout_checks 행이 문서·문서 버전·입력 버전·형식·template_version·render_options_hash·asset_manifest_hash와
모두 일치하고 status=passed여야 한다. 런타임에 행을 만들거나 통과로 바꾸는 코드는 없다(테스트에서만 가상 행 삽입).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from app.models import Document

# 계약 확인 ㉖: BE-07(출력 도구·템플릿) 전 서버 상수.
TEMPLATE_VERSION = "template_v0"
DEFAULT_RENDER_OPTIONS = {"page_size": "A4", "font": "default", "margin_mm": 15}
RENDER_OPTIONS_HASH = hashlib.sha256(json.dumps(DEFAULT_RENDER_OPTIONS, sort_keys=True).encode()).hexdigest()[:16]


def asset_manifest_hash(conn: sqlite3.Connection, document: Document) -> str:
    """문서 image 블록의 (asset_id, content_hash)를 정렬해 해시한다. 같은 이름으로 파일을 바꿔도 값이 달라진다."""
    items = []
    for page in document.pages:
        for block in page.blocks:
            if block.type == "image":
                aid = block.content.get("asset_id")
                row = conn.execute("SELECT content_hash FROM assets WHERE asset_id=?", (aid,)).fetchone()
                items.append((aid, row["content_hash"] if row else ""))
    return hashlib.sha256(json.dumps(sorted(items)).encode()).hexdigest()[:16]


def matching_passed(conn: sqlite3.Connection, layout_check_id: str, document: Document, input_revision: int,
                    fmt: str, manifest_hash: str) -> tuple[sqlite3.Row | None, str | None]:
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
        (row["asset_manifest_hash"] == manifest_hash, "asset_manifest_hash_mismatch"),
    ]
    for ok, reason in checks:
        if not ok:
            return row, reason
    return row, None
