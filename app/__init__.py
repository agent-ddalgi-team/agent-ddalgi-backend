"""agent-ddalgi-backend 앱 조립. 실행: uv run uvicorn main:app --host 127.0.0.1 --port 8000"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, load_settings
from app.db import init_db
from app.errors import install_error_handlers
from app.routers import jobs, sessions, sources

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    init_db(settings.db_path)

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

    @app.get("/", include_in_schema=False)
    def index() -> dict:
        return {"service": "agent-ddalgi-backend", "docs": "/docs", "api": API_PREFIX}

    return app
