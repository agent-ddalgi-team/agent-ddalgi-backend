"""형식별 파서 진입점. 파일 바이트와 확장자를 받아 ParseResult를 돌려준다.

파서는 '글자를 읽는 일'만 한다(plan.md 3절). 그 글이 무슨 뜻인지, 서로 충돌하는지는 Agent 몫이다.
파서는 예외를 밖으로 던지지 않고 ParseResult.status=failed + warnings로 표현한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 경고 코드(Source.warnings[].code). 계약에 고정되지 않은 백엔드 제안값.
ENCRYPTED = "ENCRYPTED"            # 암호가 걸려 열 수 없음
FILE_CORRUPT = "FILE_CORRUPT"      # 형식이 깨졌거나 열 수 없음(암호 DOCX/PPTX 포함)
UNSUPPORTED_ENCODING = "UNSUPPORTED_ENCODING"
NO_USABLE_TEXT = "NO_USABLE_TEXT"  # 텍스트 자료인데 읽을 글자가 없음
IMAGE_ONLY = "IMAGE_ONLY"          # 이미지만 있어 글자를 읽지 못함(스캔 PDF, 사진)
PAGE_UNREADABLE = "PAGE_UNREADABLE"
TEXT_LIMIT = "TEXT_LIMIT"          # 글자 수 상한 초과 — 뒤쪽은 근거로 쓰지 않음


@dataclass
class Segment:
    locator: dict[str, Any]
    text: str


@dataclass
class ParseResult:
    status: str                                   # complete / partial / failed
    segments: list[Segment] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    text_available: bool = False
    image_available: bool = False
    width: int | None = None
    height: int | None = None


def warning(code: str, message: str, locator: dict[str, Any] | None = None, action: str | None = None) -> dict:
    return {"locator": locator, "code": code, "message": message, "action": action}


def failed(code: str, message: str, action: str | None = None) -> ParseResult:
    return ParseResult(status="failed", warnings=[warning(code, message, None, action)])


def parse(data: bytes, suffix: str) -> ParseResult:
    from app.parsers import image, office, pdf, text

    suffix = suffix.lower()
    if suffix in {".txt", ".md"}:
        return text.parse(data)
    if suffix == ".pdf":
        return pdf.parse(data)
    if suffix == ".docx":
        return office.parse_docx(data)
    if suffix == ".pptx":
        return office.parse_pptx(data)
    if suffix in {".jpg", ".jpeg", ".png"}:
        return image.parse(data, suffix)
    return failed(FILE_CORRUPT, "지원하지 않는 형식입니다.")
