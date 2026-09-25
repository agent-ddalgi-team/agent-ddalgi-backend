"""TXT/MD — 옛 backend/parsers.py 이관.

- 행 단위 segment. locator {"line_start": n, "line_end": n}, n은 원본 파일의 1부터 시작하는 행 번호.
- 행 구분은 \\r\\n, \\r, \\n 세 가지만 인정한다(편집기에서 보는 행 번호와 같다).
- 빈 행·공백만 있는 행은 segment를 만들지 않지만 번호는 그대로 센다.
- text는 앞뒤 공백만 제거한다. 행 내부는 바꾸거나 자르지 않는다(MD 기호 보존).
- 파일 맨 앞 UTF-8 BOM은 제거한다. UTF-8 strict — 깨진 바이트를 대체하거나 무시하지 않는다.
"""
from __future__ import annotations

import re

from app.parsers import NO_USABLE_TEXT, UNSUPPORTED_ENCODING, ParseResult, Segment, failed

_LINE_BREAK = re.compile(r"\r\n|\r|\n")


def split_lines(text: str) -> list[str]:
    """행 번호 규칙의 기준. 근거 인용 검사도 이 함수로 행을 나눠야 locator와 같은 행을 가리킨다."""
    return _LINE_BREAK.split(text)


def parse(data: bytes) -> ParseResult:
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return failed(UNSUPPORTED_ENCODING, "UTF-8로 읽을 수 없는 파일입니다.",
                      "UTF-8로 저장한 TXT/MD를 사용해 주세요.")
    segments = [
        Segment({"line_start": no, "line_end": no}, line.strip())
        for no, line in enumerate(split_lines(content), start=1)
        if line.strip()
    ]
    if not segments:
        return failed(NO_USABLE_TEXT, "내용이 비어 있어 텍스트를 추출하지 못했습니다.",
                      "내용이 있는 텍스트 파일을 사용해 주세요.")
    return ParseResult(status="complete", segments=segments, text_available=True)
