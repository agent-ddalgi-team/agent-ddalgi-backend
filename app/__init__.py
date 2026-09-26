"""agent-ddalgi-backend 앱 조립. 실행: uv run uvicorn main:app --host 127.0.0.1 --port 8000"""
from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, load_settings
from app.db import connect, init_db
from app.errors import install_error_handlers
from app.routers import (approvals, assets, documents, drafts, exports, issues, jobs, layout_checks, preflights, proposals,
                         registered_sources, sessions, sources, validations)
from app.services import artifacts as artifacts_service
from app.services import exports as exports_service
from app.services import jobs as jobs_service

API_PREFIX = "/api/v1"
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    init_db(settings.db_path, settings.private_runs_dir)
    # 이전 프로세스가 남긴 진행 중 작업은 이어갈 수 없다.
    with connect(settings.db_path) as conn:
        stale = jobs_service.fail_stale(conn)
        # BE-08: 발행 중이던 Export는 유효 조건을 전부 재확인해 ready(연결 Job 복구) 또는 실제 사유로 failed.
        recovered = exports_service.recover_after_restart(conn, settings)
    removed = artifacts_service.cleanup_temp_dirs(settings)
    if stale or recovered["ready"] or recovered["failed"] or removed:
        logger.warning("startup: %d unfinished job(s) marked failed, exports recovered ready=%d failed=%d, temp dirs removed=%d",
                       stale, recovered["ready"], recovered["failed"], removed)

    app = FastAPI(title="agent-ddalgi-backend", version="0.1.0")
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _request_id(request: Request, call_next):
        request.state.request_id = f"req_{uuid.uuid4().hex[:12]}"
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    install_error_handlers(app)
    app.include_router(sessions.router, prefix=API_PREFIX)
    app.include_router(sources.router, prefix=API_PREFIX)
    app.include_router(jobs.router, prefix=API_PREFIX)
    app.include_router(assets.router, prefix=API_PREFIX)
    app.include_router(preflights.router, prefix=API_PREFIX)
    app.include_router(drafts.router, prefix=API_PREFIX)
    app.include_router(documents.router, prefix=API_PREFIX)
    app.include_router(proposals.router, prefix=API_PREFIX)
    app.include_router(registered_sources.router, prefix=API_PREFIX)
    app.include_router(validations.router, prefix=API_PREFIX)
    app.include_router(issues.router, prefix=API_PREFIX)
    app.include_router(approvals.router, prefix=API_PREFIX)
    app.include_router(layout_checks.router, prefix=API_PREFIX)
    app.include_router(exports.router, prefix=API_PREFIX)

    @app.get("/", include_in_schema=False)
    def index() -> dict:
        return {"service": "agent-ddalgi-backend", "docs": "/docs", "api": API_PREFIX}

    return app
