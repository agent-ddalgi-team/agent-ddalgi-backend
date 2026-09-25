"""agent-ddalgi-backend 앱 조립. 실행: uv run uvicorn main:app --host 127.0.0.1 --port 8000"""
from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, load_settings
from app.db import connect, init_db
from app.errors import install_error_handlers
from app.routers import assets, documents, drafts, jobs, preflights, proposals, sessions, sources
from app.services import jobs as jobs_service

API_PREFIX = "/api/v1"
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    init_db(settings.db_path, settings.private_runs_dir)
    # 이전 프로세스가 남긴 진행 중 작업은 이어갈 수 없다.
    with connect(settings.db_path) as conn:
        stale = jobs_service.fail_stale(conn)
    if stale:
        logger.warning("startup: %d unfinished job(s) marked failed", stale)

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

    @app.get("/", include_in_schema=False)
    def index() -> dict:
        return {"service": "agent-ddalgi-backend", "docs": "/docs", "api": API_PREFIX}

    return app
