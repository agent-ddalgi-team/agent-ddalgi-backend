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
import logging
import mimetypes
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import UploadFile

from app.db import Connection, Row
from app.config import Settings
from app.errors import ApiError
from app.models import SourceOut
from app.services.sessions import session_dir
from app.services import db_history, sessions
from app.timeutil import from_iso, now, to_iso

_READ_CHUNK = 1024 * 1024
KINDS = {"company", "interview", "certificate", "photo", "other"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
_log = logging.getLogger(__name__)


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


def _segment_ids(conn: Connection, source_id: str) -> list[str]:
    run_sql, run_params = db_history.current_run_filter(conn, source_id)
    return [r["segment_id"] for r in conn.execute(
        "SELECT segment_id FROM segments WHERE source_id=?" + run_sql + " ORDER BY ordinal", (source_id, *run_params))]


def _asset_ids(conn: Connection, source_id: str) -> list[str]:
    run_sql, run_params = db_history.current_run_filter(conn, source_id)
    return [r["asset_id"] for r in conn.execute(
        "SELECT asset_id FROM assets WHERE source_id=? AND deleted_at IS NULL AND status='ready'" + run_sql + " ORDER BY rowid",
        (source_id, *run_params))]


def _row_to_out(conn: Connection, row: Row) -> SourceOut:
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
        usable_segment_ids=_segment_ids(conn, row["source_id"]) if row["text_available"] and row["role"] == "evidence" else [],
        asset_ids=_asset_ids(conn, row["source_id"]),
        warnings=json.loads(row["warnings_json"]),
        expires_at=row["expires_at"],
        document_date=row["document_date"],
        use_as_company_evidence=bool(row["use_as_company_evidence"]),
        is_mock=bool(row["is_mock"]),
        origin_kind=row["origin_kind"],
        role=row["role"],
    )


def list_for_session(conn: Connection, session_id: str) -> list[SourceOut]:
    rows = conn.execute(
        "SELECT * FROM sources WHERE session_id=? AND deleted_at IS NULL ORDER BY rowid", (session_id,)
    ).fetchall()
    return [_row_to_out(conn, r) for r in rows]


def get_row(conn: Connection, session_id: str, source_id: str) -> Row | None:
    return conn.execute(
        "SELECT * FROM sources WHERE source_id=? AND session_id=? AND deleted_at IS NULL", (source_id, session_id)
    ).fetchone()


def count_for_session(conn: Connection, session_id: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM sources WHERE session_id=? AND deleted_at IS NULL",
                        (session_id,)).fetchone()[0]


def check_capacity(settings: Settings, existing_count: int, requested: int) -> None:
    if existing_count + requested > settings.max_files_per_session:
        raise ApiError(
            413, "FILE_TOO_LARGE",
            f"세션당 파일은 {settings.max_files_per_session}개까지입니다.",
            details={"max_files_per_session": settings.max_files_per_session, "current": existing_count,
                     "requested": requested},
        )


async def validate_uploads(settings: Settings, existing_count: int, files: list[UploadFile],
                           kind: str | None) -> list[tuple[str, str, str, bytes]]:
    """파일 자체를 검사한다. 세션의 남은 개수는 재전송 확인 후 쓰기 잠금 안에서 다시 검사한다."""
    if kind is not None and kind not in KINDS:
        raise ApiError(400, "INVALID_REQUEST", "kind 값이 올바르지 않습니다.", details={"allowed": sorted(KINDS)})
    if not files:
        raise ApiError(400, "INVALID_REQUEST", "files 필드로 파일을 1개 이상 보내 주세요.")
    check_capacity(settings, existing_count, len(files))
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


@contextmanager
def upload_storage(written: list[Path] | None = None):
    """DB 커밋까지 실패한 업로드의 파일을 정리한다. 기존 세션 파일은 건드리지 않는다."""
    written = [] if written is None else written
    try:
        yield written
    except BaseException:
        for path in written:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # 정리 실패가 원래 오류를 가리지 않게 하고 세션 종료 정리에서 재시도한다.
                _log.warning("Failed to remove rolled-back upload %s", path.name)
        raise


def effective_kind(kind: str | None, suffix: str) -> str:
    return kind or ("photo" if suffix in IMAGE_SUFFIXES else "other")


def store(conn: Connection, settings: Settings, session_id: str, expires_at: str, kind: str | None,
          uploads: list[tuple[str, str, str, bytes]], *, role: str = "evidence",
          written_paths: list[Path] | None = None) -> list[SourceOut]:
    """검사를 통과한 파일을 세션 폴더에 쓰고 레코드를 만든다(parse_status=queued). 쓰기 실패 시 이번 파일만 지운다.

    BE-09: 파일을 쓰기 전에 BEGIN IMMEDIATE로 세션 확정(종료·만료)과 직렬화하고 세션이 살아 있는지 다시 본다.
    닫힌 세션 폴더에 늦게 파일을 쓰지 않는다(폴더 삭제 뒤 재생성 방지)."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    if role not in {"evidence", "instruction"}:
        raise ApiError(400, "INVALID_REQUEST", "첨부 역할이 올바르지 않습니다.")
    alive = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    policy = sessions.usable(settings, alive)
    if policy in {"closed", "expired"}:
        status = "expired" if alive is None or alive["status"] == "active" else alive["status"]
        raise ApiError(410, "SESSION_EXPIRED", "세션이 종료되었거나 만료되어 파일을 저장하지 않았습니다.", details={"status": status})
    if policy == "demo_disabled":
        raise sessions.demo_disabled_error()
    directory = session_dir(settings, session_id)
    created: list[str] = []
    try:
        with upload_storage(written_paths) as written:
            directory.mkdir(parents=True, exist_ok=True)
            for suffix, display_name, mime, content in uploads:
                source_id = f"src_{uuid.uuid4().hex[:16]}"
                relative = f"{session_id}/{source_id}{suffix}"
                path = settings.private_runs_dir / relative
                # 독점 생성에 성공한 파일만 추적한다. 부분 쓰기 실패도 정리 대상이다.
                with path.open("xb") as stream:
                    written.append(path)
                    stream.write(content)
                conn.execute(
                    "INSERT INTO sources (source_id, session_id, source_version, scope, name, mime_type, size_bytes, kind, "
                    "parse_status, text_available, image_available, stored_path, content_hash, created_at, expires_at, role) "
                    "VALUES (?, ?, 1, 'session', ?, ?, ?, ?, 'queued', 0, 0, ?, ?, ?, ?, ?)",
                    (source_id, session_id, display_name, mime, len(content), effective_kind(kind, suffix), relative,
                      hashlib.sha256(content).hexdigest(), to_iso(now()), expires_at, role),
                )
                db_history.source_version(conn, source_id, provenance="upload")
                created.append(source_id)
            rows = conn.execute(
                f"SELECT * FROM sources WHERE source_id IN ({','.join('?' * len(created))}) ORDER BY rowid", created
            ).fetchall()
            return [_row_to_out(conn, r) for r in rows]
    except OSError:
        raise ApiError(500, "INTERNAL_ERROR", "업로드 파일을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                       retryable=True)


def delete_one(conn: Connection, settings: Settings, session_id: str, source_id: str) -> None:
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
    db_history.purge_source(conn, source_id, stamp)
    resolve_path(settings, row["stored_path"]).unlink(missing_ok=True)


def evidence_scope(conn: Connection, session_id: str) -> tuple[str, tuple]:
    """Shared SQL predicate for source selection, Agent inputs and reference validation (alias src)."""
    session = conn.execute("SELECT demo FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    demo = int(bool(session and session["demo"]))
    return ("((src.scope='session' AND src.session_id=?) OR (src.scope='registered' AND src.use_as_company_evidence=1)) "
            "AND src.deleted_at IS NULL AND src.role='evidence' AND (src.origin_kind<>'demo' OR ?=1)", (session_id, demo))


def check_selection_policy(conn: Connection, session_id: str, source_ids: list[str]) -> None:
    session = conn.execute("SELECT demo FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    for sid in source_ids:
        row = conn.execute("SELECT role, origin_kind FROM sources WHERE source_id=? AND deleted_at IS NULL "
                           "AND ((scope='session' AND session_id=?) OR scope='registered')", (sid, session_id)).fetchone()
        if row is None:
            continue  # Existing existence check hides other owners' sources.
        if row["role"] == "instruction":
            raise ApiError(422, "SOURCE_ROLE_NOT_EVIDENCE", "작성 조건 첨부는 회사 근거로 선택할 수 없습니다.")
        if row["origin_kind"] == "demo" and not (session and session["demo"]):
            raise ApiError(422, "DEMO_SOURCE_NOT_ALLOWED", "시연 자료는 시연 세션에서만 사용할 수 있습니다.")


def exist_in_session(conn: Connection, session_id: str, source_ids: list[str]) -> list[str]:
    """선택할 수 없는 ID 목록을 돌려준다. 선택 가능 = 이 세션의 첨부 또는 등록 자료(근거 사용 허용된 것)."""
    if not source_ids:
        return []
    marks = ",".join("?" * len(source_ids))
    scope, params = evidence_scope(conn, session_id)
    rows = conn.execute(
        f"SELECT src.source_id FROM sources src WHERE src.source_id IN ({marks}) AND {scope}",
        [*source_ids, *params],
    ).fetchall()
    found = {r["source_id"] for r in rows}
    return [sid for sid in source_ids if sid not in found]


def segments_for_source(conn: Connection, source_id: str) -> list[dict]:
    """Agent에게 넘길 구간(BE-04에서 사용). 내부 함수이며 API로 공개하지 않는다."""
    run_sql, run_params = db_history.current_run_filter(conn, source_id)
    return [
        {"segment_id": r["segment_id"], "source_id": r["source_id"], "source_version": r["source_version"],
         "locator": json.loads(r["locator_json"]), "text": r["text"]}
        for r in conn.execute("SELECT * FROM segments WHERE source_id=?" + run_sql + " ORDER BY ordinal", (source_id, *run_params))
    ]
