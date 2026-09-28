"""세션 종료·만료 정리 — BE-09.

한 세션의 정리는 두 단계다.
1. 확정(finalize, 호출자의 BEGIN IMMEDIATE 트랜잭션 안): 상태(closed/expired)·closed_at → 내용 제거(purge) → 끝나지 않은 Job cancelled →
   활성 Export failed → 멱등 응답 본문 비움 → sessions.purged_at → cleanup_queue에 session_dir 작업 등록. 접근 차단은 이 커밋 즉시다.
2. 바이트 삭제(process): 큐 작업을 점유(claim_token)해 private_runs/<session_id>/ 를 지운다. 실패하면 지수 백오프
   (min(2^attempt분, 60분))로 재시도하고 상한(CLEANUP_MAX_ATTEMPTS) 뒤 failed로 둔다(자동 재등록 없음, CLI --retry-failed).
   파일이 이미 없으면 성공이다. 처리 중 점유가 CLEANUP_CLAIM_TTL_S를 넘기면(프로세스 중단) 다음 sweep이 되찾는다.

- 삭제 대상은 언제나 private_runs/<session_id>/ 아래로 제한한다(target_rel 형식 검사 + 세션 행 존재 + 경로의 어느 구성 요소도
  symlink/junction이 아니고 resolve 결과가 그 자리 그대로일 것). 링크면 삭제 없이 failed(unsafe_target) — 링크 대상(등록 자료·다른 세션)에
  재귀 삭제가 닿지 않는다. 삭제는 resolve하지 않은 경로에 하고, rmtree_retry가 안쪽 링크는 링크만 끊는다.
- 등록 자료(scope=registered, private_runs/registered/)·registered_imports·검증/배치/승인/Export/artifact/Job 행(메타)은 남긴다.
- cleanup=done 판정(verify_state) 하나로 통일: purged_at 있음 AND 세션 폴더 없음 AND pending/running/failed 작업 없음.
  폴더가 다시 생기면(늦게 쓰인 파일) 새 작업을 등록하고 pending으로 되돌린다. 내부 failed는 응답에서 pending으로 보인다.
- 로그에는 ID·건수·오류 종류만 남긴다(원문·파일명 없음).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import timedelta
from pathlib import Path

from app.config import Settings
from app.db import Connection, Row, connect
from app.services import idempotency, jobs
from app.services.artifacts import ARTIFACT_DIR, TEMP_PREFIX
from app.services.export_render import is_link
from app.services.sessions import session_dir
from app.timeutil import now, plus, to_iso

logger = logging.getLogger(__name__)

KIND_SESSION_DIR = "session_dir"
KIND_ORPHAN_TMP = "orphan_tmp"
UNFINISHED = ("pending", "running", "failed")      # cleanup=done을 막는 상태(내부 failed는 응답에서 pending)
ORPHAN_TMP_MIN_AGE_S = 300                         # 활성 세션의 끝난 Job 임시 폴더는 5분 지난 것만
REASON_CLOSED = "session_closed"
REASON_EXPIRED = "session_expired"


# ---------------- 1단계: 확정(상태·내용 제거·큐 등록) ----------------

# 문제 해결 기록(resolution_json·resolution_history_json)에서 남기는 감사 필드. 원문(reason·excerpt·message 등)은 허용 목록 밖이라 지워진다.
RESOLUTION_KEEP = ("action", "by", "at", "document_revision", "input_revision", "validation_id",
                   "reopened_at", "reopened_by_validation", "previous_status")
EVIDENCE_REF_KEEP = ("source_id", "source_version", "segment_id", "locator")


def _loads(text):
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def _purge_resolution(obj) -> dict | None:
    if not isinstance(obj, dict):
        return None
    out = {k: obj[k] for k in RESOLUTION_KEEP if k in obj}
    if isinstance(obj.get("evidence_refs"), list):
        out["evidence_refs"] = [{k: ref[k] for k in EVIDENCE_REF_KEEP if k in ref} for ref in obj["evidence_refs"] if isinstance(ref, dict)]
    out["purged"] = True
    return out


def _purge_content(conn: Connection, session_id: str, stamp: str) -> None:
    """세션 내용(원문·파생·초안·편집안·문제 문구)을 비운다. 행과 ID·시각·상태·해시(메타)는 남긴다."""
    from app.services import db_history

    db_history.purge_session(conn, session_id, stamp)
    conn.execute("UPDATE sessions SET brief_json='{}', selected_source_ids='[]' WHERE session_id=?", (session_id,))
    conn.execute("UPDATE sources SET name='', warnings_json='[]', deleted_at=COALESCE(deleted_at, ?) "
                 "WHERE session_id=? AND scope='session'", (stamp, session_id))
    conn.execute("DELETE FROM segments WHERE session_id=? OR source_id IN "
                 "(SELECT source_id FROM sources WHERE session_id=? AND scope='session')", (session_id, session_id))
    conn.execute("UPDATE assets SET deleted_at=COALESCE(deleted_at, ?) WHERE session_id=?", (stamp, session_id))
    conn.execute("UPDATE layout_previews SET deleted_at=COALESCE(deleted_at, ?) WHERE session_id=?", (stamp, session_id))
    conn.execute("UPDATE preflights SET facts_json='[]', issues_json='[]', recommendations_json='[]' WHERE session_id=?", (session_id,))
    conn.execute("UPDATE documents SET title='' WHERE session_id=?", (session_id,))
    conn.execute("UPDATE document_revisions SET content_json='{}' WHERE document_id IN "
                 "(SELECT document_id FROM documents WHERE session_id=?)", (session_id,))
    conn.execute("UPDATE proposals SET instruction='', changes_json='{}' WHERE session_id=?", (session_id,))
    for r in conn.execute("SELECT issue_id, resolution_json, resolution_history_json FROM issues WHERE session_id=?", (session_id,)).fetchall():
        resolution = _purge_resolution(_loads(r["resolution_json"])) if r["resolution_json"] else None
        history = [_purge_resolution(h) for h in (_loads(r["resolution_history_json"]) or []) if isinstance(h, dict)]
        conn.execute("UPDATE issues SET message='', resolution_json=?, resolution_history_json=? WHERE issue_id=?",
                     (json.dumps(resolution, ensure_ascii=False) if resolution is not None else None,
                      json.dumps(history, ensure_ascii=False), r["issue_id"]))


def finalize(conn: Connection, settings: Settings, session_id: str, reason: str) -> dict:
    """세션을 closed(reason=session_closed) 또는 expired로 확정하고, 아직이면 내용 제거·Job 취소·Export 확정·멱등 본문 비움·큐 등록까지
    한 트랜잭션에서 한다(호출자가 BEGIN IMMEDIATE 안에서 부르고 커밋). 재호출은 상태만 맞추고 내용 제거를 반복하지 않는다."""
    from app.services import exports as exports_service   # 순환 import 방지

    row = conn.execute("SELECT status, purged_at FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if row is None:
        return {"status": None, "purged": False}
    stamp = to_iso(now())
    target = "closed" if reason == REASON_CLOSED else "expired"
    status = row["status"]
    if status == "active" or (target == "closed" and status != "closed"):
        conn.execute("UPDATE sessions SET status=?, closed_at=COALESCE(closed_at, ?) WHERE session_id=?", (target, stamp, session_id))
        status = target
    purged = False
    if row["purged_at"] is None:
        _purge_content(conn, session_id, stamp)
        jobs.purge_errors_for_session(conn, session_id)     # 먼저 비우고
        jobs.cancel_for_session(conn, session_id)           # 취소 문구(고정)는 남긴다
        exports_service.finalize_for_session(conn, session_id, reason)
        idempotency.purge_for_session(conn, session_id, stamp)
        conn.execute("UPDATE sessions SET purged_at=?, cleanup_status='pending' WHERE session_id=?", (stamp, session_id))
        enqueue(conn, session_id, KIND_SESSION_DIR, session_id)
        purged = True
        logger.info("session finalized: %s status=%s", session_id, status)
    return {"status": status, "purged": purged}


def expire_due(conn: Connection, settings: Settings) -> int:
    """expires_at이 지난 active 세션을 expired로 확정한다(배경 sweep)."""
    rows = conn.execute("SELECT session_id FROM sessions WHERE status='active' AND expires_at <= ?", (to_iso(now()),)).fetchall()
    for r in rows:
        finalize(conn, settings, r["session_id"], REASON_EXPIRED)
    return len(rows)


def purge_unpurged(conn: Connection, settings: Settings) -> int:
    """closed/expired인데 내용 제거가 안 된 세션(v8 이전에 닫힌 것 포함)을 확정한다. 폴더 유무와 무관하게 큐에도 넣는다."""
    rows = conn.execute("SELECT session_id, status FROM sessions WHERE status IN ('closed', 'expired') AND purged_at IS NULL").fetchall()
    for r in rows:
        finalize(conn, settings, r["session_id"], REASON_CLOSED if r["status"] == "closed" else REASON_EXPIRED)
    return len(rows)


# ---------------- 큐 ----------------

def enqueue(conn: Connection, session_id: str, kind: str, target_rel: str) -> str | None:
    """같은 대상의 pending/running 작업이 있으면 만들지 않는다(부분 UNIQUE). failed는 자동으로 되살리지 않는다."""
    if conn.execute("SELECT 1 FROM cleanup_queue WHERE session_id=? AND kind=? AND target_rel=? AND status='failed'",
                    (session_id, kind, target_rel)).fetchone():
        return None
    task_id = f"clt_{uuid.uuid4().hex[:16]}"
    stamp = to_iso(now())
    cur = conn.execute(
        "INSERT OR IGNORE INTO cleanup_queue (task_id, session_id, kind, target_rel, status, attempt, next_retry_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?)", (task_id, session_id, kind, target_rel, stamp, stamp, stamp))
    return task_id if cur.rowcount else None


def claim(conn: Connection, worker_id: str, limit: int = 50, *, session_id: str | None = None,
          ignore_schedule: bool = False) -> list[Row]:
    """pending 작업을 running으로 점유한다(호출자의 BEGIN IMMEDIATE 안). 점유 토큰이 결과 기록의 조건이 된다."""
    stamp = to_iso(now())
    sql, params = "SELECT task_id FROM cleanup_queue WHERE status='pending'", []
    if not ignore_schedule:
        sql += " AND next_retry_at <= ?"
        params.append(stamp)
    if session_id:
        sql += " AND session_id=?"
        params.append(session_id)
    sql += " ORDER BY next_retry_at, rowid LIMIT ?"
    params.append(limit)
    claimed: list[Row] = []
    for r in conn.execute(sql, params).fetchall():
        token = uuid.uuid4().hex
        cur = conn.execute("UPDATE cleanup_queue SET status='running', claimed_by=?, claimed_at=?, claim_token=?, updated_at=? "
                           "WHERE task_id=? AND status='pending'", (worker_id, stamp, token, stamp, r["task_id"]))
        if cur.rowcount:
            claimed.append(conn.execute("SELECT * FROM cleanup_queue WHERE task_id=?", (r["task_id"],)).fetchone())
    return claimed


def reclaim_stale(conn: Connection, ttl_s: int, *, all_running: bool = False) -> int:
    """점유한 채 끝나지 않은 작업(중단된 프로세스)을 pending으로 되돌린다. 서버 시작 시에는 전부(단일 프로세스 가정)."""
    stamp = to_iso(now())
    cutoff = to_iso(now() - timedelta(seconds=ttl_s))
    cur = conn.execute(
        "UPDATE cleanup_queue SET status='pending', claim_token=NULL, claimed_by=NULL, claimed_at=NULL, next_retry_at=?, updated_at=?, "
        "last_error='stale_claim' WHERE status='running' AND (? OR claimed_at IS NULL OR claimed_at <= ?)",
        (stamp, stamp, int(all_running), cutoff))
    return cur.rowcount


def _backoff_minutes(attempt: int) -> int:
    return min(2 ** attempt, 60)


def _safe_target(settings: Settings, session_id: str, kind: str, target_rel: str) -> Path | None:
    """삭제 경로를 private_runs/<session_id>/ 아래로만 허용한다. 어긋나면 None(작업은 failed로 확정, 삭제 없음).

    세션 루트·중간 경로·대상이 symlink/junction이면 거부하고, resolve 결과가 제자리(private_runs 실제 위치 + target_rel)와 다르면 거부한다.
    돌려주는 경로는 resolve하지 않은 경로다(링크를 따라간 위치를 지우지 않기 위해)."""
    if not session_id or session_id in (".", "..") or "/" in session_id or "\\" in session_id or session_id.startswith("registered"):
        return None
    parts = Path(target_rel).parts
    if not parts or parts[0] != session_id or any(p in ("", ".", "..") for p in parts) or any(("/" in p or "\\" in p) for p in parts):
        return None
    if kind == KIND_SESSION_DIR and parts != (session_id,):
        return None
    if kind == KIND_ORPHAN_TMP and not (len(parts) == 3 and parts[1] == ARTIFACT_DIR and parts[2].startswith(TEMP_PREFIX)):
        return None
    if kind not in (KIND_SESSION_DIR, KIND_ORPHAN_TMP):
        return None
    base = settings.private_runs_dir
    probe = base
    for part in parts:
        probe = probe / part
        if is_link(probe):
            return None
    target = base / target_rel
    try:
        root = base.resolve()
        if target.resolve() != root.joinpath(*parts) or target.resolve() == root:
            return None
    except OSError:
        return None
    return target


def _remove_tree(path: Path) -> bool:
    """폴더 삭제(재시도 포함, 링크는 거부·안쪽 링크는 링크만 끊음). 테스트가 실패를 주입하려고 바꿔 끼운다."""
    from app.services.export_render import rmtree_retry

    if is_link(path):
        return False
    return rmtree_retry(path)


def process(settings: Settings, task: Row) -> str:
    """점유한 작업 하나를 처리한다. 반환: done / retry / failed. 결과 기록은 점유 토큰이 아직 유효할 때만 쓴다."""
    target = _safe_target(settings, task["session_id"], task["kind"], task["target_rel"])
    known = False
    if target is not None:
        with connect(settings.db_path) as conn:
            known = conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (task["session_id"],)).fetchone() is not None
    if target is None or not known:
        return _record_failure(settings, task, "unsafe_target", final=True)
    if is_link(target):
        return _record_failure(settings, task, "unsafe_target", final=True)
    ok = (not target.exists()) or _remove_tree(target)
    if ok and (target.exists() or is_link(target)):
        ok = False
    if ok:
        return _record_done(settings, task)
    return _record_failure(settings, task, "remove_failed")


def _refresh_after_task(conn: Connection, settings: Settings, session_id: str) -> None:
    """유효한 점유 토큰으로 작업 결과를 반영한 뒤(같은 트랜잭션), 종료·만료(purged) 세션이면 작업 종류와 무관하게 세션 전체 cleanup
    상태를 다시 계산한다. 같은 배치에서 session_dir → orphan_tmp 순서로 끝나도 마지막 완료가 done을 기록한다(리뷰 6).
    활성 세션의 tmp 정리는 세션 정리 상태가 아니므로 cleanup_status를 건드리지 않는다."""
    row = conn.execute("SELECT status FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if row is not None and row["status"] != "active":
        verify_state(conn, settings, session_id)


def _record_done(settings: Settings, task: Row) -> str:
    stamp = to_iso(now())
    with connect(settings.db_path, immediate=True) as conn:
        cur = conn.execute("UPDATE cleanup_queue SET status='done', done_at=?, updated_at=?, claim_token=NULL, last_error=NULL "
                           "WHERE task_id=? AND claim_token=? AND status='running'", (stamp, stamp, task["task_id"], task["claim_token"]))
        if cur.rowcount:
            _refresh_after_task(conn, settings, task["session_id"])
    return "done"


def _record_failure(settings: Settings, task: Row, error_kind: str, *, final: bool = False) -> str:
    stamp = to_iso(now())
    attempt = task["attempt"] + 1
    failed = final or attempt >= settings.cleanup_max_attempts
    status = "failed" if failed else "pending"
    retry_at = to_iso(plus(now(), minutes=_backoff_minutes(attempt)))
    with connect(settings.db_path, immediate=True) as conn:
        cur = conn.execute("UPDATE cleanup_queue SET status=?, attempt=?, next_retry_at=?, last_error=?, updated_at=?, claim_token=NULL "
                           "WHERE task_id=? AND claim_token=? AND status='running'",
                           (status, attempt, retry_at, error_kind, stamp, task["task_id"], task["claim_token"]))
        if cur.rowcount:
            _refresh_after_task(conn, settings, task["session_id"])
    logger.warning("cleanup task %s (%s) %s: attempt=%d error=%s", task["task_id"], task["kind"], status, attempt, error_kind)
    return "failed" if failed else "retry"


def process_due(settings: Settings, worker_id: str, limit: int = 50) -> dict[str, int]:
    """실행할 때가 된 작업을 점유해 처리한다(배경 sweep·CLI)."""
    counts = {"claimed": 0, "done": 0, "retry": 0, "failed": 0}
    with connect(settings.db_path, immediate=True) as conn:
        tasks = claim(conn, worker_id, limit)
    for task in tasks:
        counts["claimed"] += 1
        counts[process(settings, task)] += 1
    return counts


def run_for_session(settings: Settings, session_id: str, worker_id: str = "request") -> dict[str, int]:
    """DELETE 직후: 이 세션의 pending 작업을 예약 시각과 무관하게 지금 처리한다(실패해도 큐가 재시도)."""
    counts = {"claimed": 0, "done": 0, "retry": 0, "failed": 0}
    with connect(settings.db_path, immediate=True) as conn:
        tasks = claim(conn, worker_id, 10, session_id=session_id, ignore_schedule=True)
    for task in tasks:
        counts["claimed"] += 1
        counts[process(settings, task)] += 1
    return counts


# ---------------- 상태 판정·폴더 점검 ----------------

def _complete_gone_targets(conn: Connection, settings: Settings, session_id: str) -> int:
    """상위(세션 폴더) 삭제로 대상이 이미 사라진 pending/failed 하위 작업(orphan_tmp 등)을 done(target_gone)으로 정합하게 닫는다.
    running은 점유자가 자기 토큰으로 마무리하므로 건드리지 않는다. attempt는 유지한다."""
    stamp = to_iso(now())
    closed = 0
    for t in conn.execute("SELECT task_id, kind, target_rel FROM cleanup_queue WHERE session_id=? AND status IN ('pending', 'failed')",
                          (session_id,)).fetchall():
        target = _safe_target(settings, session_id, t["kind"], t["target_rel"])
        if target is None or target.exists() or is_link(target):
            continue
        closed += conn.execute("UPDATE cleanup_queue SET status='done', done_at=?, updated_at=?, last_error='target_gone', claim_token=NULL "
                               "WHERE task_id=? AND status IN ('pending', 'failed')", (stamp, stamp, t["task_id"])).rowcount
    return closed


def verify_state(conn: Connection, settings: Settings, session_id: str) -> str:
    """cleanup=done 판정(하나의 기준): purged_at 있음 AND 세션 폴더 없음 AND 미완료(pending/running/failed) 작업 없음.
    재등록 가능 여부는 같은 대상(session_dir=<sid>) 기준으로만 본다 — 다른 대상(사라진 tmp)의 failed가 세션 정리를 막지 않는다.
    폴더가 다시 있으면(늦게 쓰인 파일) 새 작업을 등록하고 pending. 같은 대상의 failed는 자동 재등록하지 않는다(attempt 유지, --retry-failed).
    결과를 sessions.cleanup_status에 기록한다."""
    row = conn.execute("SELECT purged_at, cleanup_status FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if row is None:
        return "pending"
    state = "done" if row["purged_at"] else "pending"
    folder = session_dir(settings, session_id)
    present = folder.exists() or is_link(folder)
    if not present:
        _complete_gone_targets(conn, settings, session_id)
    marks = ",".join("?" * len(UNFINISHED))
    same_target = conn.execute(f"SELECT COUNT(*) FROM cleanup_queue WHERE session_id=? AND kind=? AND target_rel=? AND status IN ({marks})",
                               (session_id, KIND_SESSION_DIR, session_id, *UNFINISHED)).fetchone()[0]
    unfinished = conn.execute(f"SELECT COUNT(*) FROM cleanup_queue WHERE session_id=? AND status IN ({marks})",
                              (session_id, *UNFINISHED)).fetchone()[0]
    if unfinished:
        state = "pending"
    if present:
        state = "pending"
        if not same_target and row["purged_at"]:
            enqueue(conn, session_id, KIND_SESSION_DIR, session_id)
    if row["cleanup_status"] != state:
        conn.execute("UPDATE sessions SET cleanup_status=? WHERE session_id=?", (state, session_id))
    return state


def scan_folders(conn: Connection, settings: Settings) -> dict[str, int]:
    """private_runs의 세션 폴더를 훑는다. 정리된 세션의 폴더가 다시 있으면 재등록(late_dirs), 활성 세션의 끝난 Job 임시 폴더는
    orphan_tmp로 등록한다(queued/running Job의 폴더는 나이와 무관하게 보호). 세션이 아닌 폴더(registered 등)는 손대지 않는다."""
    counts = {"late_dirs": 0, "orphan_tmp": 0}
    root = settings.private_runs_dir
    if not root.is_dir():
        return counts
    for entry in root.iterdir():
        if not entry.is_dir() or is_link(entry):
            continue
        sid = entry.name
        row = conn.execute("SELECT status, purged_at FROM sessions WHERE session_id=?", (sid,)).fetchone()
        if row is None:
            continue
        if row["status"] != "active":
            if row["purged_at"] is not None and verify_state(conn, settings, sid) == "pending":
                counts["late_dirs"] += 1
            continue
        adir = entry / ARTIFACT_DIR
        if not adir.is_dir() or is_link(adir):
            continue
        for tmp in adir.glob(f"{TEMP_PREFIX}*"):
            if not tmp.is_dir() or is_link(tmp) or time.time() - tmp.stat().st_mtime < ORPHAN_TMP_MIN_AGE_S:
                continue
            job = conn.execute("SELECT status FROM jobs WHERE job_id=?", (tmp.name[len(TEMP_PREFIX):],)).fetchone()
            if job is not None and job["status"] in jobs.ACTIVE:
                continue
            if enqueue(conn, sid, KIND_ORPHAN_TMP, f"{sid}/{ARTIFACT_DIR}/{tmp.name}"):
                counts["orphan_tmp"] += 1
    return counts


# ---------------- 운영 도구(CLI) ----------------

def retry_failed(conn: Connection) -> int:
    stamp = to_iso(now())
    cur = conn.execute("UPDATE cleanup_queue SET status='pending', attempt=0, next_retry_at=?, updated_at=?, last_error=NULL "
                       "WHERE status='failed'", (stamp, stamp))
    return cur.rowcount


def list_failed(conn: Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT task_id, session_id, kind, attempt, last_error, updated_at FROM cleanup_queue WHERE status='failed' ORDER BY updated_at").fetchall()]


def queue_summary(conn: Connection) -> dict[str, int]:
    return {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM cleanup_queue GROUP BY status").fetchall()}
