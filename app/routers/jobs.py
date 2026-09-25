"""GET /sessions/{sid}/jobs/{jid} — 폴링용. 활동으로 치지 않는다."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.access import require_owner, settings_of
from app.db import connect
from app.models import JobOut
from app.services import jobs, sessions

router = APIRouter(prefix="/sessions/{sid}/jobs", tags=["jobs"])


@router.get("/{jid}", response_model=JobOut)
def get_job(request: Request, sid: str, jid: str):
    owner = require_owner(request)
    with connect(settings_of(request).db_path) as conn:
        sessions.load_active(conn, owner, sid)
        return jobs.get(conn, sid, jid)
