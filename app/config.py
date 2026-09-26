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
    # D-01: 파일당 10MB, 세션당 10개. 형식 7종(BE-03). 이미지는 저장·asset만, 글자는 읽지 않는다(OCR 없음).
    max_file_bytes: int = 10 * 1024 * 1024
    max_files_per_session: int = 10
    allowed_extensions: frozenset[str] = frozenset({".txt", ".md", ".pdf", ".docx", ".pptx", ".jpg", ".jpeg", ".png"})
    # 자료 하나에서 읽는 글자 수 상한. 넘으면 조용히 자르지 않고 partial + 경고(TEXT_LIMIT)로 표시한다.
    max_source_chars: int = 100_000
    # 프론트(Vite 개발 서버) origin. 배포 시 FRONTEND_ORIGINS로 추가.
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
    # 소유자 쿠키. 세션 ID만으로 권한을 믿지 않기 위한 접근 컨텍스트(contracts.md Session절).
    owner_cookie_name: str = "ddalgi_owner"
    owner_cookie_secure: bool = False
    # AI 실행 모드. mock = 가짜 결과(실제 호출 없음, 기본) / llm = Agent 구현(app/agent_llm.py) — 없으면 Job failed.
    agent_mode: str = "mock"
    # BE-07 출력(D-03): PDF는 시스템 Chromium 계열 브라우저(Chrome/Edge)의 headless 인쇄로 만든다. 비어 있으면 자동 탐색.
    # 렌더 옵션(용지·여백·폰트)은 설정이 아니라 app/services/layout_checks.py의 DEFAULT_RENDER_OPTIONS(해시 대상)다.
    export_browser_path: str | None = None
    export_render_timeout_s: int = 90
    # BE-08: Export 만료(분). 세션 만료와 같거나 그 이전으로 잘린다.
    export_ttl_minutes: int = 120


def load_settings() -> Settings:
    agent_mode = (os.environ.get("AGENT_MODE") or "mock").strip().lower()
    if agent_mode not in {"mock", "llm"}:
        # 오설정을 조용히 mock으로 바꾸면 실제 분석처럼 보일 수 있어 시작을 중단한다(옛 코드 규칙 유지).
        raise RuntimeError(f"AGENT_MODE는 mock 또는 llm이어야 합니다(현재: {agent_mode!r}). .env를 확인해 주세요.")
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
        max_source_chars=_env_int("MAX_SOURCE_CHARS", 100_000),
        cors_origins=_env_list("FRONTEND_ORIGINS", ["http://localhost:5173", "http://127.0.0.1:5173"]),
        owner_cookie_secure=os.environ.get("OWNER_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"},
        agent_mode=agent_mode,
        export_browser_path=(os.environ.get("EXPORT_BROWSER_PATH") or "").strip() or None,
        export_render_timeout_s=_env_int("EXPORT_RENDER_TIMEOUT_S", 90),
        export_ttl_minutes=_env_int("EXPORT_TTL_MINUTES", 120),
    )
