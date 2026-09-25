"""서버 설정. 값은 .env 또는 환경변수에서 읽고, 없으면 여기 기본값을 쓴다.

D-01(파일 형식·크기·개수)과 D-02(세션 만료)의 현재 값은 plan.md 4절에 기록된 개발 제안값이다.
숫자를 코드 곳곳에 박지 않고 이 파일 한 곳에서만 바꾼다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name, "").strip()
    return [item.strip() for item in raw.split(",") if item.strip()] if raw else list(default)


@dataclass(frozen=True)
class Settings:
    private_runs_dir: Path
    db_path: Path
    # D-02: 무활동 120분 / 생성 후 24시간 중 빠른 때. 실제 운영 전 확정.
    session_idle_minutes: int = 120
    session_max_hours: int = 24
    # D-01: BE-02는 TXT/MD만 받는다. PDF/DOCX/PPTX/JPG/PNG는 BE-03에서 파서와 함께 추가.
    max_file_bytes: int = 10 * 1024 * 1024
    max_files_per_session: int = 10
    allowed_extensions: frozenset[str] = frozenset({".txt", ".md"})
    # 프론트(Vite 개발 서버) origin. 배포 시 FRONTEND_ORIGINS로 추가.
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
    # 소유자 쿠키. 세션 ID만으로 권한을 믿지 않기 위한 접근 컨텍스트(contracts.md Session절).
    owner_cookie_name: str = "ddalgi_owner"
    owner_cookie_secure: bool = False


def load_settings() -> Settings:
    private_runs = Path(os.environ.get("PRIVATE_RUNS_DIR") or ROOT / "private_runs")
    if not private_runs.is_absolute():
        private_runs = ROOT / private_runs
    db_path = Path(os.environ.get("DB_PATH") or private_runs / "app.sqlite3")
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    return Settings(
        private_runs_dir=private_runs,
        db_path=db_path,
        session_idle_minutes=_env_int("SESSION_IDLE_MINUTES", 120),
        session_max_hours=_env_int("SESSION_MAX_HOURS", 24),
        max_file_bytes=_env_int("MAX_FILE_BYTES", 10 * 1024 * 1024),
        max_files_per_session=_env_int("MAX_FILES_PER_SESSION", 10),
        cors_origins=_env_list("FRONTEND_ORIGINS", ["http://localhost:5173", "http://127.0.0.1:5173"]),
        owner_cookie_secure=os.environ.get("OWNER_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"},
    )
