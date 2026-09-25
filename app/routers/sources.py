"""POST/GET /sessions/{sid}/sources · DELETE /sessions/{sid}/sources/{source_id}"""
from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, BackgroundTasks, File, Form, Header, Request, UploadFile
from fastapi.responses import JSONResponse

from app.access import require_owner, settings_of
from app.db import connect
from app.errors import ApiError
from app.models import SourceDeleteOut, SourceListOut, UploadOut
from app.services import idempotency, jobs, reading, sessions, sources

router = APIRouter(prefix="/sessions/{sid}/sources", tags=["sources"])


@router.post("", status_code=202, response_model=UploadOut)
async def upload_sources(request: Request, sid: str, background_tasks: BackgroundTasks,
                         files: list[UploadFile] = File(default=[]),
                         kind: str | None = Form(default=None),
                         idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    """저장 후 202. 읽기는 백그라운드 Job(kind=read)이 하며 GET jobs/{jid}·GET sources로 진행을 본다."""
    settings = settings_of(request)
    owner = require_owner(request)
    with connect(settings.db_path) as conn:
        row = sessions.load_active(conn, owner, sid)
        existing = sources.count_for_session(conn, sid)
        uploads = await sources.validate_uploads(settings, existing, files, kind)
        digest = hashlib.sha256(
            json.dumps([(name, hashlib.sha256(content).hexdigest()) for _, name, _, content in uploads]).encode()
        ).hexdigest()
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest)
        if replay is not None:
            return replay
        items = sources.store(conn, settings, sid, row["expires_at"], kind, uploads)
        job = jobs.create(conn, sid, "read", f"파일 읽기 대기 중 (0/{len(items)})")
        sessions.touch(conn, settings, row)
        out = UploadOut(job_id=job.job_id, items=items)
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, 202, out.model_dump())
    # DB 커밋이 끝난 뒤(with 블록 밖) 백그라운드 읽기를 예약한다.
    background_tasks.add_task(reading.run_read_job, settings, sid, job.job_id, [i.source_id for i in items])
    return JSONResponse(status_code=202, content=out.model_dump())


@router.get("", response_model=SourceListOut)
def list_sources(request: Request, sid: str):
    owner = require_owner(request)
    with connect(settings_of(request).db_path) as conn:
        sessions.load_active(conn, owner, sid)
        return SourceListOut(items=sources.list_for_session(conn, sid))


@router.delete("/{source_id}", response_model=SourceDeleteOut)
def delete_source(request: Request, sid: str, source_id: str, expected_input_revision: int):
    settings = settings_of(request)
    owner = require_owner(request)
    with connect(settings.db_path) as conn:
        row = sessions.load_active(conn, owner, sid)
        if expected_input_revision != row["input_revision"]:
            raise ApiError(
                409, "INPUT_REVISION_CONFLICT",
                "입력이 변경되었습니다. 최신 상태를 불러온 뒤 다시 요청해 주세요.",
                details={"expected_input_revision": expected_input_revision,
                         "current_input_revision": row["input_revision"]},
            )
        sources.delete_one(conn, settings, sid, source_id)
        selected = json.loads(row["selected_source_ids"])
        revision = row["input_revision"]
        if source_id in selected:
            # 선택 중이던 자료를 지우면 입력이 바뀐 것이므로 버전을 올린다(사전 확인 무효화).
            selected = [s for s in selected if s != source_id]
            revision = sessions.bump_input_revision(conn, sid, brief=None, selected_source_ids=selected)
        sessions.touch(conn, settings, row)
    return SourceDeleteOut(source_id=source_id, deleted=True, input_revision=revision)
