"""세션 정리 운영 CLI(BE-09). 배경 sweep과 같은 코드(app/services/sweeper.sweep_once)를 손으로 한 번 돌리거나 큐를 점검한다.

  uv run python scripts/cleanup_sessions.py --once            # 만료 확정·내용 제거·폴더 점검·큐 처리 1회
  uv run python scripts/cleanup_sessions.py --once --dry-run  # 무엇을 할지 건수만(변경 없음)
  uv run python scripts/cleanup_sessions.py --list-failed     # 재시도 상한에 닿은 작업 목록(ID·종류·횟수·오류 종류)
  uv run python scripts/cleanup_sessions.py --retry-failed    # failed → pending(다음 --once 또는 배경 sweep이 처리)

출력은 ID·건수·오류 종류뿐이다. 원문·파일명·경로는 찍지 않는다. 서버가 떠 있으면 배경 sweep이 같은 일을 하므로 보통 필요 없다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.services import cleanup, sweeper  # noqa: E402
from app.timeutil import now, to_iso  # noqa: E402


def _dry_run(settings) -> dict[str, int]:
    stamp = to_iso(now())
    with connect(settings.db_path) as conn:
        expired_due = conn.execute("SELECT COUNT(*) FROM sessions WHERE status='active' AND expires_at <= ?", (stamp,)).fetchone()[0]
        unpurged = conn.execute("SELECT COUNT(*) FROM sessions WHERE status IN ('closed', 'expired') AND purged_at IS NULL").fetchone()[0]
        due = conn.execute("SELECT COUNT(*) FROM cleanup_queue WHERE status='pending' AND next_retry_at <= ?", (stamp,)).fetchone()[0]
        running = conn.execute("SELECT COUNT(*) FROM cleanup_queue WHERE status='running'").fetchone()[0]
        summary = cleanup.queue_summary(conn)
    root = settings.private_runs_dir
    folders = 0
    if root.is_dir():
        with connect(settings.db_path) as conn:
            for entry in root.iterdir():
                if entry.is_dir() and conn.execute("SELECT 1 FROM sessions WHERE session_id=? AND status!='active'", (entry.name,)).fetchone():
                    folders += 1
    return {"would_expire": expired_due, "would_purge": unpurged, "tasks_due": due, "tasks_running": running,
            "closed_session_folders_present": folders, **{f"queue_{k}": v for k, v in summary.items()}}


def main() -> int:
    parser = argparse.ArgumentParser(description="세션 종료·만료 정리를 한 번 실행하거나 큐를 점검한다.")
    parser.add_argument("--once", action="store_true", help="sweep 1회(만료 확정·내용 제거·폴더 점검·큐 처리)")
    parser.add_argument("--dry-run", action="store_true", help="--once와 함께: 변경 없이 건수만")
    parser.add_argument("--list-failed", action="store_true", help="재시도 상한에 닿은 작업 목록")
    parser.add_argument("--retry-failed", action="store_true", help="failed 작업을 pending으로 되돌림")
    args = parser.parse_args()
    if not (args.once or args.list_failed or args.retry_failed):
        parser.print_help()
        return 2

    settings = load_settings()
    init_db(settings.db_path, settings.private_runs_dir)
    if args.list_failed:
        with connect(settings.db_path) as conn:
            rows = cleanup.list_failed(conn)
        print(f"failed tasks: {len(rows)}")
        for r in rows:
            print(f"  {r['task_id']} session={r['session_id']} kind={r['kind']} attempt={r['attempt']} error={r['last_error']} at={r['updated_at']}")
    if args.retry_failed:
        with connect(settings.db_path, immediate=True) as conn:
            n = cleanup.retry_failed(conn)
        print(f"retry-failed: {n} task(s) -> pending")
    if args.once:
        if args.dry_run:
            print("dry-run:", " ".join(f"{k}={v}" for k, v in _dry_run(settings).items()))
        else:
            counts = sweeper.sweep_once(settings, worker_id="cli")
            print("sweep:", " ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
