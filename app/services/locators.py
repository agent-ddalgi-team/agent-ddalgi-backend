"""팀 자료의 문자열 locator → contracts.md EvidenceRef.locator 객체.

패턴(verify_bundle.py의 팀 규칙과 같음). 뒤에 " · 제목" 같은 꼬리가 붙어도 앞의 위치만 읽는다.
- "PPT 12쪽 [· …]"            → {"slide": 12}
- "PDF 3쪽" / "카다로그 3쪽"    → {"page": 3}
- "TXT 2행"                    → {"line_start": 2, "line_end": 2}
- "DOCX 4문단" / "MD 문단 3"   → {"paragraph": 4}
"12쪽"처럼 형식이 없으면 AMBIGUOUS_LOCATOR, 그 외 읽을 수 없으면 INVALID_LOCATOR. 0·음수는 INVALID.
"""
from __future__ import annotations

import re
from typing import Any

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^PPT ([1-9]\d*)쪽(?:\s*·\s*.+)?$"), "slide"),
    (re.compile(r"^(?:카다로그|카탈로그|PDF) ([1-9]\d*)쪽(?:\s*·\s*.+)?$"), "page"),
    (re.compile(r"^TXT ([1-9]\d*)행$"), "line"),
    (re.compile(r"^MD 문단 ([1-9]\d*)$"), "paragraph"),
    (re.compile(r"^DOCX ([1-9]\d*)문단$"), "paragraph"),
]


class LocatorError(ValueError):
    def __init__(self, code: str, value: str) -> None:
        super().__init__(f"{code}: {value!r}")
        self.code = code
        self.value = value


def to_object(value: Any) -> dict[str, int]:
    if not isinstance(value, str) or not value.strip():
        raise LocatorError("INVALID_LOCATOR", str(value))
    text = value.strip()
    for pattern, kind in _PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        n = int(m.group(1))
        if kind == "line":
            return {"line_start": n, "line_end": n}
        return {kind: n}
    if re.fullmatch(r"\d+쪽", text):
        raise LocatorError("AMBIGUOUS_LOCATOR", text)
    raise LocatorError("INVALID_LOCATOR", text)
