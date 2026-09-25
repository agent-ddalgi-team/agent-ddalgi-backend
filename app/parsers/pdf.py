"""PDF — pypdf. 페이지 단위 segment, locator {"page": n}.

- 암호 PDF: 빈 비밀번호로 열리지 않으면 failed + ENCRYPTED.
- 깨진 파일: failed + FILE_CORRUPT.
- 글자가 없는 페이지(스캔본): 페이지 목록을 담은 IMAGE_ONLY 경고. 전체가 그러면 status=partial,
  text_available=false — 실패가 아니라 '이미지만 있음'으로 표시한다(팀 결정 2026-09-25). OCR은 하지 않는다.
"""
from __future__ import annotations

import io
import logging

from app.parsers import (ENCRYPTED, FILE_CORRUPT, IMAGE_ONLY, PAGE_UNREADABLE, ParseResult, Segment,
                         failed, warning)

logger = logging.getLogger(__name__)


def parse(data: bytes) -> ParseResult:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                if reader.decrypt("") == 0:
                    raise PdfReadError("encrypted")
            except Exception:
                return failed(ENCRYPTED, "암호가 걸린 PDF라 열 수 없습니다.",
                              "암호를 푼 PDF를 올리거나 이 자료를 제외해 주세요.")
        page_count = len(reader.pages)
    except Exception:
        return failed(FILE_CORRUPT, "PDF 파일을 열 수 없습니다(손상되었거나 형식이 다릅니다).",
                      "파일을 다시 저장해 올리거나 이 자료를 제외해 주세요.")

    segments: list[Segment] = []
    empty_pages: list[int] = []
    unreadable_pages: list[int] = []
    for no in range(1, page_count + 1):
        try:
            text = (reader.pages[no - 1].extract_text() or "").strip()
        except Exception:
            logger.warning("pdf page %d extract failed", no, exc_info=True)
            unreadable_pages.append(no)
            continue
        if text:
            segments.append(Segment({"page": no}, text))
        else:
            empty_pages.append(no)

    warnings = []
    if unreadable_pages:
        warnings.append(warning(PAGE_UNREADABLE, f"{len(unreadable_pages)}쪽을 읽는 중 오류가 났습니다.",
                                {"pages": unreadable_pages}, "해당 쪽 내용은 다른 자료로 보완해 주세요."))
    if empty_pages:
        if not segments:
            warnings.append(warning(IMAGE_ONLY, "이미지만 있음, 글자를 읽지 못함 (스캔본으로 보입니다).",
                                    {"pages": empty_pages},
                                    "텍스트가 들어 있는 원본 파일이나 텍스트본을 추가해 주세요."))
        else:
            warnings.append(warning(IMAGE_ONLY, f"{len(empty_pages)}쪽은 이미지만 있어 글자를 읽지 못했습니다.",
                                    {"pages": empty_pages}, "해당 쪽 내용이 필요하면 텍스트본을 추가해 주세요."))

    if not segments:
        return ParseResult(status="partial", warnings=warnings, text_available=False, image_available=False)
    status = "complete" if not (empty_pages or unreadable_pages) else "partial"
    return ParseResult(status=status, segments=segments, warnings=warnings, text_available=True)
