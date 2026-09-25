"""접근 컨텍스트 — 소유자 쿠키.

contracts.md Session절: "세션 식별자 자체를 권한으로 믿지 않는다." 서버가 HttpOnly 쿠키로 소유자 ID를
발급하고, 세션은 그 소유자만 접근한다. 로그인 제품 범위는 미정이므로 브라우저 단위 익명 소유자로 시작한다.
"""
from __future__ import annotations

import uuid

from fastapi import Request, Response

from app.config import Settings
from app.errors import ApiError


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def current_owner(request: Request) -> str | None:
    return request.cookies.get(settings_of(request).owner_cookie_name)


def require_owner(request: Request) -> str:
    """세션 자원 경로에서 쓴다. 쿠키가 없으면 401 — 다른 세션의 존재 여부를 드러내지 않는다."""
    owner = current_owner(request)
    if not owner:
        raise ApiError(401, "UNAUTHORIZED", "접근 정보가 없습니다. 세션을 새로 시작해 주세요.")
    return owner


def ensure_owner(request: Request, response: Response) -> str:
    """세션 생성에서 쓴다. 쿠키가 없으면 새 소유자 ID를 발급해 응답 쿠키로 심는다."""
    owner = current_owner(request)
    if owner:
        return owner
    owner = f"own_{uuid.uuid4().hex}"
    settings = settings_of(request)
    response.set_cookie(
        key=settings.owner_cookie_name,
        value=owner,
        httponly=True,
        samesite="lax",
        secure=settings.owner_cookie_secure,
        max_age=60 * 60 * 24 * 30,
        path="/",
    )
    return owner
