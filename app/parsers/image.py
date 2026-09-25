"""JPG/PNG — Pillow로 실제 이미지인지 확인하고 크기를 잰다. 글자는 읽지 않는다(OCR 없음).

정상 처리는 complete이지만 text_available=false다(contracts.md Source절). Asset 생성은 reading 서비스가 한다.
"""
from __future__ import annotations

import io

from app.parsers import FILE_CORRUPT, IMAGE_ONLY, ParseResult, failed, warning

_FORMATS = {".jpg": {"JPEG"}, ".jpeg": {"JPEG"}, ".png": {"PNG"}}


def parse(data: bytes, suffix: str) -> ParseResult:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            fmt, width, height = img.format, img.width, img.height
    except Exception:
        return failed(FILE_CORRUPT, "이미지 파일을 열 수 없습니다(손상되었거나 형식이 다릅니다).",
                      "다시 저장한 이미지를 올리거나 이 자료를 제외해 주세요.")
    if fmt not in _FORMATS.get(suffix.lower(), set()):
        return failed(FILE_CORRUPT, "파일 확장자와 실제 이미지 형식이 다릅니다.",
                      "실제 형식에 맞는 확장자로 저장해 올려 주세요.")
    return ParseResult(
        status="complete", text_available=False, image_available=True, width=width, height=height,
        warnings=[warning(IMAGE_ONLY, "이미지 파일입니다. 글자를 읽지 않았습니다.", None,
                          "텍스트 근거는 다른 자료에서 제공해 주세요.")],
    )
