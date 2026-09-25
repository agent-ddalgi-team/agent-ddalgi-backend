"""GET /sessions/{sid}/documents/{did} — 현재 버전 문서. 편집(PATCH)·수정안·복원은 BE-05."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.access import require_owner, settings_of
from app.db import connect
from app.models import DocumentOut
from app.services import documents, sessions

router = APIRouter(prefix="/sessions/{sid}/documents", tags=["documents"])


@router.get("/{did}", response_model=DocumentOut)
def get_document(request: Request, sid: str, did: str):
    owner = require_owner(request)
    with connect(settings_of(request).db_path) as conn:
        sessions.load_active(conn, owner, sid)
        return DocumentOut(document=documents.get_current(conn, sid, did), validation=None, approval=None)
