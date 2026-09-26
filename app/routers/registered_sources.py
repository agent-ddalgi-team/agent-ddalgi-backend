"""GET /sources — 등록 자료 목록(contracts.md 4절). 세션과 무관하며 소유자 쿠키만 확인한다.

use_as_company_evidence=false인 자료도 목록에는 나오지만(표시용) 선택·근거에서는 서버가 거른다.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.access import require_owner, settings_of
from app.db import connect
from app.models import SourceListOut
from app.services import registered

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=SourceListOut)
def list_registered_sources(request: Request, kind: str | None = Query(default=None)):
    require_owner(request)
    with connect(settings_of(request).db_path) as conn:
        return SourceListOut(items=registered.list_registered(conn, kind))
