"""공통 오류 응답. contracts.md 4절 '오류 응답' 모양을 따른다.

{"error": {"code", "message", "retryable", "details", "request_id"}}
내부 경로·원문·예외 내용은 응답에 넣지 않는다.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str,
                 retryable: bool = False, details: dict[str, Any] | None = None) -> None:
        super().__init__(f"{status_code} {code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


def request_id_of(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if not rid:
        rid = f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = rid
    return rid


def error_response(request: Request, status_code: int, code: str, message: str,
                   retryable: bool = False, details: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
            "request_id": request_id_of(request),
        }},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc.status_code, exc.code, exc.message, exc.retryable, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI 기본 422 {"detail": [...]} 대신 공통 봉투. 받은 값(input)은 되돌려주지 않는다.
        fields = sorted({".".join(str(p) for p in e.get("loc", ()) if p != "body") for e in exc.errors()})
        return error_response(
            request, 400, "INVALID_REQUEST",
            "요청 형식이 올바르지 않습니다.",
            details={"fields": fields},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error (request_id=%s)", request_id_of(request))
        return error_response(request, 500, "INTERNAL_ERROR", "처리 중 오류가 발생했습니다.", retryable=True)
