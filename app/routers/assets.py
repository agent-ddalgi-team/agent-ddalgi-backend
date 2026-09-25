"""GET /sessions/{sid}/assets/{asset_id} — 접근 확인 후 이미지 바이트."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from app.access import require_owner, settings_of
from app.db import connect
from app.errors import ApiError
from app.services import assets, sessions
from app.services.sources import resolve_path

router = APIRouter(prefix="/sessions/{sid}/assets", tags=["assets"])


@router.get("/{asset_id}")
def get_asset(request: Request, sid: str, asset_id: str):
    settings = settings_of(request)
    owner = require_owner(request)
    with connect(settings.db_path) as conn:
        sessions.load_active(conn, owner, sid)
        row = assets.get_ready(conn, sid, asset_id)
    path = resolve_path(settings, row["stored_path"])
    if not path.is_file():
        raise ApiError(410, "ARTIFACT_EXPIRED", "이미지 파일이 더 이상 없습니다.")
    return FileResponse(path, media_type=row["mime_type"], headers={"Cache-Control": "private, no-store"})
