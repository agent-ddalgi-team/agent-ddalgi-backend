"""POST /sessions/{sid}/exports (202 신규·진행 중 / 200 ready 재사용) · GET /sessions/{sid}/exports/{eid}/download

- 멱등 순서: 접근 확인 → 같은 키·다른 본문은 409 고정 → 같은 본문은 현재 필수 조건(승인·버전·식별값·허가·만료·무결성) 통과 후
  저장된 최초 HTTP 상태·본문을 그대로 반환. 만료는 같은 키 410, 새 키로만 새 Export.
- 다운로드는 파일을 다시 만들지 않고 승인 산출물(불변 artifact)을 스트리밍한다. 응답에 서버 경로를 넣지 않는다.
"""
from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Header, Request
from fastapi.responses import FileResponse, JSONResponse

from app.access import require_owner, settings_of
from app.db import connect
from app.errors import ApiError
from app.models import ExportAccepted, ExportCreate
from app.services import artifacts, exports, idempotency, jobs, sessions

router = APIRouter(prefix="/sessions/{sid}/exports", tags=["exports"])
DOWNLOAD_NAME = {"pdf": ("company_intro_draft.pdf", "회사소개서_초안.pdf", "application/pdf"),
                 "docx": ("company_intro_draft.docx", "회사소개서_초안.docx",
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}


@router.post("", response_model=ExportAccepted)
def create_export(request: Request, sid: str, body: ExportCreate, background_tasks: BackgroundTasks,
                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    settings = settings_of(request)
    owner = require_owner(request)
    digest = idempotency.body_hash(body.model_dump())
    need_job, job_id, export_id = False, None, None
    with connect(settings.db_path, immediate=True) as conn:
        row = sessions.load_active(conn, owner, sid)                                      # ① 접근·세션
        replay = idempotency.replay_or_none(conn, idempotency_key, owner, request.url.path, digest)   # ② 같은 키·다른 본문 409
        approval = conn.execute("SELECT * FROM approvals WHERE approval_id=?", (body.approval_id,)).fetchone()
        verdict = exports.approval_validity(conn, settings, row, approval, body.format)   # ③ 현재 필수 조건(캐시보다 먼저)
        if not verdict.ok:
            exports.reject_invalid_artifact(conn, approval, verdict)   # 누락·변조 → 승인 무효화(커밋 후 오류)
            exports.commit_and_raise(conn, verdict)
        if replay is not None:
            cached = json.loads(replay.body)
            erow = exports.get(conn, sid, cached["export"]["export_id"])
            if erow["status"] in exports.ACTIVE and exports.export_expired(erow):
                exports.finalize_expired(conn, erow)
                erow = exports.get(conn, sid, erow["export_id"])
            if erow["finalized_reason"] == "expired":
                exports.commit_and_raise(conn, exports.Verdict(False, 410, "ARTIFACT_EXPIRED",
                                                               "출력 결과가 만료되었습니다. 새 Idempotency-Key로 다시 요청해 주세요.",
                                                               details={"export_id": erow["export_id"], "expires_at": erow["expires_at"]}))
            return replay                                                                  # ④ 저장된 최초 HTTP 상태·본문 그대로
        erow, need_job = exports.create_or_reuse(conn, settings, row, approval)
        export_id, job_id = erow["export_id"], erow["job_id"]
        if need_job:
            job = jobs.create(conn, sid, "export", "출력 준비 중", input_revision=row["input_revision"], target_key=erow["reuse_key"])
            exports.attach_job(conn, export_id, job.job_id)
            job_id = job.job_id
        fresh = exports.get(conn, sid, export_id)
        status_code = 200 if fresh["status"] == "ready" else 202
        out = ExportAccepted(export=exports.to_out(fresh), job_id=job_id)
        sessions.touch(conn, settings, row)
        idempotency.remember(conn, idempotency_key, owner, request.url.path, digest, status_code, out.model_dump())
    if need_job:
        background_tasks.add_task(exports.run_export_job, settings, sid, job_id, export_id)
    return JSONResponse(status_code=status_code, content=out.model_dump())


@router.get("/{eid}/download")
def download_export(request: Request, sid: str, eid: str):
    settings = settings_of(request)
    owner = require_owner(request)
    with connect(settings.db_path, immediate=True) as conn:
        row = sessions.load_active(conn, owner, sid)                       # 소유·세션 유효(만료 410)
        erow = exports.get(conn, sid, eid)
        artifact = exports.download_check(conn, settings, row, erow)      # ready·미만료·승인 active·현재 버전·허가·무결성
        path = artifacts.path_of(settings, artifact)
        fmt = erow["format"]
    ascii_name, utf8_name, media_type = DOWNLOAD_NAME[fmt]
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(utf8_name)}"
    return FileResponse(path, media_type=media_type,
                        headers={"Content-Disposition": disposition, "Cache-Control": "private, no-store"})
