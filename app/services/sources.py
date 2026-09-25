"""세션 첨부 저장·조회·삭제. 파일 내용 읽기는 services/reading.py(백그라운드 Job)가 한다.

규칙(옛 backend/main.py 패턴 이관):
- 저장 전에 모든 파일을 검사한다. 하나라도 걸리면 아무것도 저장하지 않는다.
- 크기는 실제 읽은 바이트로 센다(Content-Length를 믿지 않는다). 한도를 넘는 순간 읽기를 멈춘다.
- 저장 이름은 서버가 정한다(source_id + 확장자). 원본 이름은 표시용으로만 보관한다.
- stored_path는 private_runs 기준 상대경로(<session_id>/<source_id>.ext)로 저장한다. PC마다 절대경로가 다르다.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import sqlite3
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.config import Settings
from app.errors import ApiError
from app.models import SourceOut
from app.services.sessions import session_dir
from app.timeutil import now, to_iso

_READ_CHUNK = 1024 * 1024
KINDS = {"company", "interview", "certificate", "photo", "other"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def resolve_path(settings: Settings, stored_path: str) -> Path:
    return settings.private_runs_dir / stored_path


async def _read_limited(upload: UploadFile, limit: int) -> bytes | None:
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(_READ_CHUNK):
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _segment_ids(conn: sqlite3.Connection, source_id: str) -> list[str]:
    return [r["segment_id"] for r in conn.execute(
        "SELECT segment_id FROM segments WHERE source_id=? ORDER BY ordinal", (source_id,))]


def _asset_ids(conn: sqlite3.Connection, source_id: str) -> list[str]:
    return [r["asset_id"] for r in conn.execute(
        "SELECT asset_id FROM assets WHERE source_id=? AND deleted_at IS NULL AND status='ready' ORDER BY rowid",
        (source_id,))]


def _row_to_out(conn: sqlite3.Connection, row: sqlite3.Row) -> SourceOut:
    return SourceOut(
        source_id=row["source_id"],
        source_version=row["source_version"],
        scope=row["scope"],
        session_id=row["session_id"],
        name=row["name"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        kind=row["kind"],
        parse_status=row["parse_status"],
        text_available=bool(row["text_available"]),
        image_available=bool(row["image_available"]),
        usable_segment_ids=_segment_ids(conn, row["source_id"]) if row["text_available"] else [],
        asset_ids=_asset_ids(conn, row["source_id"]),
        warnings=json.loads(row["warnings_json"]),
        expires_at=row["expires_at"],
    )


def list_for_session(conn: sqlite3.Connection, session_id: str) -> list[SourceOut]:
    rows = conn.execute(
        "SELECT * FROM sources WHERE session_id=? AND deleted_at IS NULL ORDER BY rowid", (session_id,)
    ).fetchall()
    return [_row_to_out(conn, r) for r in rows]


def get_row(conn: sqlite3.Connection, session_id: str, source_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM sources WHERE source_id=? AND session_id=? AND deleted_at IS NULL", (source_id, session_id)
    ).fetchone()


def count_for_session(conn: sqlite3.Connection, session_id: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM sources WHERE session_id=? AND deleted_at IS NULL",
                        (session_id,)).fetchone()[0]


async def validate_uploads(settings: Settings, existing_count: int, files: list[UploadFile],
                           kind: str | None) -> list[tuple[str, str, str, bytes]]:
    """(확장자, 표시 이름, mime, 내용) 목록을 돌려준다. 실패하면 아무것도 저장하지 않은 채 ApiError."""
    if kind is not None and kind not in KINDS:
        raise ApiError(400, "INVALID_REQUEST", "kind 값이 올바르지 않습니다.", details={"allowed": sorted(KINDS)})
    if not files:
        raise ApiError(400, "INVALID_REQUEST", "files 필드로 파일을 1개 이상 보내 주세요.")
    if existing_count + len(files) > settings.max_files_per_session:
        raise ApiError(
            413, "FILE_TOO_LARGE",
            f"세션당 파일은 {settings.max_files_per_session}개까지입니다.",
            details={"max_files_per_session": settings.max_files_per_session, "current": existing_count,
                     "requested": len(files)},
        )
    allowed = sorted(ext.lstrip(".") for ext in settings.allowed_extensions)
    for f in files:
        suffix = Path(f.filename or "").suffix.lower()
        if suffix not in settings.allowed_extensions:
            raise ApiError(
                415, "UNSUPPORTED_FILE_TYPE",
                f"지원하지 않는 파일 형식입니다. {', '.join(e.upper() for e in allowed)}를 사용해 주세요.",
                details={"file_name": Path(f.filename or "").name, "allowed": allowed},
            )

    result: list[tuple[str, str, str, bytes]] = []
    for index, f in enumerate(files, start=1):
        content = await _read_limited(f, settings.max_file_bytes)
        if content is None:
            raise ApiError(
                413, "FILE_TOO_LARGE",
                f"파일당 {settings.max_file_bytes // (1024 * 1024)}MB를 넘을 수 없습니다.",
                details={"file_name": Path(f.filename or "").name, "max_file_bytes": settings.max_file_bytes},
            )
        display_name = Path(f.filename or f"file_{index}").name
        suffix = Path(display_name).suffix.lower()
        mime = mimetypes.guess_type(display_name)[0] or (f.content_type or "application/octet-stream")
        result.append((suffix, display_name, mime, content))
    return result


def store(conn: sqlite3.Connection, settings: Settings, session_id: str, expires_at: str, kind: str | None,
          uploads: list[tuple[str, str, str, bytes]]) -> list[SourceOut]:
    """검사를 통과한 파일을 세션 폴더에 쓰고 레코드를 만든다(parse_status=queued). 쓰기 실패 시 이번 파일만 지운다."""
    directory = session_dir(settings, session_id)
    written: list[Path] = []
    created: list[str] = []
    try:
        directory.mkdir(parents=True, exist_ok=True)
        for suffix, display_name, mime, content in uploads:
            source_id = f"src_{uuid.uuid4().hex[:16]}"
            relative = f"{session_id}/{source_id}{suffix}"
            path = settings.private_runs_dir / relative
            path.write_bytes(content)
            written.append(path)
            effective_kind = kind or ("photo" if suffix in IMAGE_SUFFIXES else "other")
            conn.execute(
                "INSERT INTO sources (source_id, session_id, source_version, scope, name, mime_type, size_bytes, kind, "
                "parse_status, text_available, image_available, stored_path, content_hash, created_at, expires_at) "
                "VALUES (?, ?, 1, 'session', ?, ?, ?, ?, 'queued', 0, 0, ?, ?, ?, ?)",
                (source_id, session_id, display_name, mime, len(content), effective_kind, relative,
                 hashlib.sha256(content).hexdigest(), to_iso(now()), expires_at),
            )
            created.append(source_id)
    except OSError:
        for path in written:
            path.unlink(missing_ok=True)
        raise ApiError(500, "INTERNAL_ERROR", "업로드 파일을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                       retryable=True)
    rows = conn.execute(
        f"SELECT * FROM sources WHERE source_id IN ({','.join('?' * len(created))}) ORDER BY rowid", created
    ).fetchall()
    return [_row_to_out(conn, r) for r in rows]


def delete_one(conn: sqlite3.Connection, settings: Settings, session_id: str, source_id: str) -> None:
    row = conn.execute(
        "SELECT stored_path FROM sources WHERE source_id=? AND session_id=? AND scope='session' AND deleted_at IS NULL",
        (source_id, session_id),
    ).fetchone()
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자원을 찾을 수 없습니다.")
    stamp = to_iso(now())
    conn.execute("UPDATE sources SET deleted_at=? WHERE source_id=?", (stamp, source_id))
    conn.execute("DELETE FROM segments WHERE source_id=?", (source_id,))
    conn.execute("UPDATE assets SET deleted_at=? WHERE source_id=? AND deleted_at IS NULL", (stamp, source_id))
    resolve_path(settings, row["stored_path"]).unlink(missing_ok=True)


def exist_in_session(conn: sqlite3.Connection, session_id: str, source_ids: list[str]) -> list[str]:
    """세션에 없는(또는 삭제된) ID 목록을 돌려준다."""
    if not source_ids:
        return []
    rows = conn.execute(
        f"SELECT source_id FROM sources WHERE session_id=? AND deleted_at IS NULL "
        f"AND source_id IN ({','.join('?' * len(source_ids))})",
        [session_id, *source_ids],
    ).fetchall()
    found = {r["source_id"] for r in rows}
    return [sid for sid in source_ids if sid not in found]


def segments_for_source(conn: sqlite3.Connection, source_id: str) -> list[dict]:
    """Agent에게 넘길 구간(BE-04에서 사용). 내부 함수이며 API로 공개하지 않는다."""
    return [
        {"segment_id": r["segment_id"], "source_id": r["source_id"], "source_version": r["source_version"],
         "locator": json.loads(r["locator_json"]), "text": r["text"]}
        for r in conn.execute("SELECT * FROM segments WHERE source_id=? ORDER BY ordinal", (source_id,))
    ]
