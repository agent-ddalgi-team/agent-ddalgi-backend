"""배경 정리 스레드 — BE-09. 서버 lifespan에서 시작해 CLEANUP_SWEEP_INTERVAL_S마다 sweep_once를 돈다(0이면 스레드 없음).

sweep 한 번 = ① expires_at 지난 active 세션 만료 확정 ② 내용 제거 안 된 closed/expired 확정 ③ 오래된 점유 되찾기
④ 폴더 점검(늦은 파일 재등록·끝난 Job 임시 폴더) ⑤ 때가 된 큐 작업 처리. 실패는 로그로 남기고 다음 주기에 계속한다.
"""
from __future__ import annotations

import logging
import os
import threading

from app.config import Settings
from app.db import connect
from app.services import cleanup

logger = logging.getLogger(__name__)


def sweep_once(settings: Settings, worker_id: str | None = None, *, limit: int = 50) -> dict[str, int]:
    worker = worker_id or f"sweep-{os.getpid()}"
    counts: dict[str, int] = {}
    with connect(settings.db_path, immediate=True) as conn:
        counts["expired"] = cleanup.expire_due(conn, settings)
        counts["purged"] = cleanup.purge_unpurged(conn, settings)
        counts["reclaimed"] = cleanup.reclaim_stale(conn, settings.cleanup_claim_ttl_s)
        counts.update(cleanup.scan_folders(conn, settings))
    counts.update(cleanup.process_due(settings, worker, limit))
    if any(counts.values()):
        logger.info("cleanup sweep: %s", " ".join(f"{k}={v}" for k, v in counts.items()))
    return counts


class Sweeper:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if self.settings.cleanup_sweep_interval_s <= 0 or self.running:
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="cleanup-sweeper", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        interval = self.settings.cleanup_sweep_interval_s
        while True:
            try:
                sweep_once(self.settings)
            except Exception:  # noqa: BLE001 — 한 번 실패해도 스레드는 유지
                logger.exception("cleanup sweep failed")
            if self._stop.wait(interval):
                return

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
