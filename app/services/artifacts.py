"""불변 산출물(artifact) — BE-08. 배치 검사가 만든 PDF/DOCX 파일을 한 번 저장하고 이후 덮어쓰지 않는다.

- 위치: private_runs/<session_id>/artifacts/<artifact_id>.<ext> (세션 폴더 아래 → 종료·만료 정리(BE-09) 대상)
- 행: artifacts(sha256·크기·식별값·renderer). 승인·Export는 artifact_id로 파일을 고정한다.
- verify(): 존재·크기·sha256을 매번 다시 확인한다. 누락·변조는 자동 재렌더로 교체하지 않는다(재검사·재승인 필요).
- 응답에는 stored_path를 넣지 않는다.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.services.export_render import RenderResult
from app.services.sessions import session_dir
from app.timeutil import now, to_iso

ARTIFACT_DIR = "artifacts"
PREVIEW_DIR = "previews"
TEMP_PREFIX = "tmp_"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def artifacts_dir(settings: Settings, session_id: str) -> Path:
    return session_dir(settings, session_id) / ARTIFACT_DIR


def temp_dir(settings: Settings, session_id: str, job_id: str) -> Path:
    """Job 전용 임시 폴더. 렌더러가 같은 이름(document_id_revN.ext)으로 저장해도 서로 다른 Job이 덮어쓰지 않는다."""
    path = artifacts_dir(settings, session_id) / f"{TEMP_PREFIX}{job_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def discard_temp(path: Path) -> None:
    from app.services.export_render import rmtree_retry

    rmtree_retry(path)


def cleanup_temp_dirs(settings: Settings, older_than_s: float | None = None) -> int:
    """임시 폴더 정리. older_than_s=None이면 전부(서버 시작 시: 실행 중인 Job이 없다), 값을 주면 그보다 오래된 것만(실행 중 정기 정리).
    브라우저 자식 프로세스가 프로필 폴더를 잠깐 더 잡아 즉시 지우지 못한 폴더를 여기서 마저 지운다."""
    import time

    from app.services.export_render import rmtree_retry

    removed = 0
    root = settings.private_runs_dir
    if not root.is_dir():
        return 0
    now_ts = time.time()
    for session in root.iterdir():
        adir = session / ARTIFACT_DIR
        if not adir.is_dir():
            continue
        for tmp in adir.glob(f"{TEMP_PREFIX}*"):
            if not tmp.is_dir():
                continue
            if older_than_s is not None and now_ts - tmp.stat().st_mtime < older_than_s:
                continue
            if rmtree_retry(tmp, attempts=2):
                removed += 1
    return removed


def store(conn: sqlite3.Connection, settings: Settings, session_id: str, result: RenderResult, *,
          document_id: str, document_revision: int, input_revision: int, layout_check_id: str) -> sqlite3.Row:
    """렌더 결과 파일을 불변 artifact로 옮기고 행을 만든다. 파일은 임시 폴더에서 최종 이름으로 os.replace(같은 볼륨)."""
    artifact_id = f"art_{uuid.uuid4().hex[:16]}"
    ext = result.file_path.suffix.lower()
    final_dir = artifacts_dir(settings, session_id)
    final_dir.mkdir(parents=True, exist_ok=True)
    final = final_dir / f"{artifact_id}{ext}"
    digest = sha256_of(result.file_path)
    size = result.file_path.stat().st_size
    os.replace(result.file_path, final)
    rel = final.relative_to(settings.private_runs_dir).as_posix()
    conn.execute(
        "INSERT INTO artifacts (artifact_id, session_id, document_id, document_revision, input_revision, format, stored_path, "
        "sha256, size_bytes, template_version, render_options_hash, asset_manifest_hash, renderer, actual_pages, layout_check_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (artifact_id, session_id, document_id, document_revision, input_revision, result.format, rel, digest, size,
         result.template_version, result.render_options_hash, result.asset_manifest_hash, result.renderer,
         result.actual_pages, layout_check_id, to_iso(now())))
    return get(conn, artifact_id)


def get(conn: sqlite3.Connection, artifact_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()


def path_of(settings: Settings, row: sqlite3.Row) -> Path:
    return settings.private_runs_dir / row["stored_path"]


@dataclass(frozen=True)
class Integrity:
    ok: bool
    reason: str | None   # missing / size_mismatch / hash_mismatch / not_found


def verify(conn: sqlite3.Connection, settings: Settings, artifact_id: str | None) -> Integrity:
    """존재·크기·sha256을 확인한다. 브라우저·렌더러와 무관하게 파일 자체만 본다."""
    if not artifact_id:
        return Integrity(False, "not_found")
    row = get(conn, artifact_id)
    if row is None:
        return Integrity(False, "not_found")
    path = path_of(settings, row)
    if not path.is_file():
        return Integrity(False, "missing")
    if path.stat().st_size != row["size_bytes"]:
        return Integrity(False, "size_mismatch")
    if sha256_of(path) != row["sha256"]:
        return Integrity(False, "hash_mismatch")
    return Integrity(True, None)


def identity_matches(row: sqlite3.Row, template_version: str, render_options_hash: str, asset_manifest_hash: str, fmt: str) -> bool:
    return (row["template_version"] == template_version and row["render_options_hash"] == render_options_hash
            and row["asset_manifest_hash"] == asset_manifest_hash and row["format"] == fmt)
