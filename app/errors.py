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
from starlette.exceptions import HTTPException

from app.models import ApiErrorDetail, ApiErrorOut

logger = logging.getLogger(__name__)


def _documented_error(description: str, code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "model": ApiErrorOut,
        "description": description,
        "content": {
            "application/json": {
                "example": {
                    "error": {
                        "code": code,
                        "message": message,
                        "retryable": retryable,
                        "details": {},
                        "request_id": "req_example",
                    },
                },
            },
        },
    }


# 요청 형식 오류는 400, 형식은 맞지만 업무 조건을 충족하지 못한 요청은 422다.
# 모든 API에 공통 봉투를 등록하여 FastAPI의 기본 HTTPValidationError 문서와 혼동하지 않는다.
API_ERROR_RESPONSES = {
    400: _documented_error("요청 본문·경로·쿼리의 형식 오류", "INVALID_REQUEST", "요청 형식이 올바르지 않습니다."),
    401: _documented_error("요청자 확인 필요", "UNAUTHORIZED", "요청자를 확인할 수 없습니다."),
    403: _documented_error("요청 자원에 접근할 수 없음", "FORBIDDEN", "요청한 자원에 접근할 수 없습니다."),
    404: _documented_error("접근 가능한 범위에서 자원을 찾을 수 없음", "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다."),
    405: _documented_error("지원하지 않는 HTTP 메서드", "METHOD_NOT_ALLOWED", "지원하지 않는 요청 방식입니다."),
    409: _documented_error("버전·멱등 키·현재 처리 상태 충돌", "DOCUMENT_REVISION_CONFLICT", "문서가 변경되었습니다. 최신 문서에서 다시 요청해 주세요."),
    410: _documented_error("세션 또는 파일 만료", "SESSION_EXPIRED", "세션이 만료되었습니다."),
    413: _documented_error("파일 크기 또는 개수 제한 초과", "FILE_TOO_LARGE", "첨부파일 제한을 초과했습니다."),
    415: _documented_error("지원하지 않는 파일 또는 요청 형식", "UNSUPPORTED_FILE_TYPE", "지원하지 않는 파일 형식입니다."),
    422: _documented_error("요청 형식은 유효하지만 확인·근거·승인 등 업무 조건이 충족되지 않음", "PREFLIGHT_NOT_CONFIRMED", "사전 점검 결과를 확인한 뒤 생성할 수 있습니다."),
    500: _documented_error("내부 처리 실패", "INTERNAL_ERROR", "처리 중 오류가 발생했습니다.", retryable=True),
}


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
                   retryable: bool = False, details: dict[str, Any] | None = None,
                   *, headers: dict[str, str] | None = None) -> JSONResponse:
    request_id = request_id_of(request)
    payload = ApiErrorOut(error=ApiErrorDetail(
        code=code,
        message=message,
        retryable=retryable,
        details=details or {},
        request_id=request_id,
    ))
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers={**(headers or {}), "X-Request-Id": request_id},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc.status_code, exc.code, exc.message, exc.retryable, exc.details)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
        # 프레임워크의 404/405·multipart 오류도 같은 봉투를 사용한다.
        # detail에는 내부 경로나 파서 예외가 들어갈 수 있으므로 그대로 노출하지 않는다.
        code, message = {
            400: ("INVALID_REQUEST", "요청 형식이 올바르지 않습니다."),
            404: ("RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다."),
            405: ("METHOD_NOT_ALLOWED", "지원하지 않는 요청 방식입니다."),
            413: ("FILE_TOO_LARGE", "첨부파일 제한을 초과했습니다."),
            415: ("UNSUPPORTED_MEDIA_TYPE", "지원하지 않는 요청 형식입니다."),
        }.get(exc.status_code, ("HTTP_ERROR", "요청을 처리할 수 없습니다."))
        return error_response(request, exc.status_code, code, message,
                              retryable=exc.status_code >= 500, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI 기본 422 {"detail": [...]} 대신 공통 봉투. 받은 값(input)은 되돌려주지 않는다.
        fields = sorted({".".join(str(p) for p in e.get("loc", ()) if p != "body") or "body"
                         for e in exc.errors()})
        return error_response(
            request, 400, "INVALID_REQUEST",
            "요청 형식이 올바르지 않습니다.",
            details={"fields": fields},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error (request_id=%s)", request_id_of(request))
        return error_response(request, 500, "INTERNAL_ERROR", "처리 중 오류가 발생했습니다.", retryable=True)
