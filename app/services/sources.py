"""세션 첨부 저장·조회·삭제. 파일 내용 읽기는 services/reading.py(백그라운드 Job)가 한다.

규칙(옛 backend/main.py 패턴 이관):
- 저장 전에 모든 파일을 검사한다. 하나라도 걸리면 아무것도 저장하지 않는다.
- 크기는 실제 읽은 바이트로 센다(Content-Length를 믿지 않는다). 한도를 넘는 순간 읽기를 멈춘다.
- 저장 이름은 서버가 정한다(source_id + 확장자). 원본 이름은 표시용으로만 보관한다.
- stored_path는 private_runs 기준 상대경로(<session_id>/<source_id>.ext)로 저장한다. PC마다 절대경로가 다르다.
"""
from __future__ import annotations

import hashlib
import base64
import json
import logging
import mimetypes
import re
import stat
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import UploadFile

from app.db import Connection, Row
from app.config import Settings
from app.errors import ApiError
from app.models import SourceOut, SourceTextOut, SourceTextQuery, SourceTextSegment, SourceOriginalQuery
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
        original_download_available=_original_eligible(conn, row, row["source_version"]),
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
          written_paths: list[Path] | None = None, provenance: str = "upload") -> list[SourceOut]:
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
                db_history.source_version(conn, source_id, provenance=provenance)
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


def _text_missing():
    return ApiError(404, "RESOURCE_NOT_FOUND", "요청한 자료 또는 근거를 찾을 수 없습니다. 자료 선택 상태를 확인해 주세요.")


def _text_unavailable():
    return ApiError(410, "SOURCE_VERSION_UNAVAILABLE", "이 근거의 자료 버전·추출 기록을 조회할 수 없습니다. 자료를 다시 선택하고 점검해 주세요.")


def _evidence_refs(value):
    """Only fact/block reference fields, never arbitrary content dictionaries."""
    if isinstance(value, dict):
        yield from (ref for ref in value.get("evidence_refs", []) if isinstance(ref, dict))
        yield from _evidence_refs(value.get("alternatives", []))
    elif isinstance(value, list):
        for item in value:
            yield from _evidence_refs(item)


def _text_group(locator: dict) -> tuple:
    # DOCX stores paragraphs before tables: ordinal alone is not reading order.
    if "slide" in locator:
        return ("slide", locator["slide"])
    if "table" in locator:
        return ("table", locator["table"])
    if "paragraph" in locator:
        return ("paragraph",)
    if "page" in locator:
        return ("page",)
    if "line_start" in locator:
        return ("line",)
    return ("unknown", json.dumps(locator, sort_keys=True))


def _resolve_text_source(conn: Connection, session: Row, source_id: str, query: SourceTextQuery,
                         segment_id: str | None = None):
    """Shared selection/context/version authorization for text and originals."""
    sid = session["session_id"]
    scope, params = evidence_scope(conn, sid)
    source = conn.execute(f"SELECT src.* FROM sources src WHERE src.source_id=? AND {scope}",
                          (source_id, *params)).fetchone()
    if source is None or source_id not in json.loads(session["selected_source_ids"]):
        raise _text_missing()
    # Session attachment timestamps are upload-time snapshots; active-session TTL
    # is authoritative and can be extended by editing (sessions.touch).
    if source["scope"] == "registered" and source["expires_at"] and from_iso(source["expires_at"]) <= now():
        raise _text_unavailable()
    if query.expected_input_revision != session["input_revision"]:
        raise ApiError(409, "INPUT_REVISION_CONFLICT", "자료가 변경되었습니다. 최신 상태를 확인해 주세요.")
    if segment_id is None and (query.context != "selection" or query.focus_segment_id):
        raise ApiError(422, "INVALID_REQUEST", "구간 목록은 현재 선택 문맥으로 조회해 주세요.")
    if segment_id is not None and query.source_version is None:
        raise ApiError(422, "INVALID_REQUEST", "근거 조회에는 자료 버전이 필요합니다.")

    input_revision = session["input_revision"]
    refs = []
    if query.context == "preflight":
        pf = conn.execute("SELECT * FROM preflights WHERE session_id=? AND preflight_id=?",
                          (sid, query.preflight_id)).fetchone()
        if pf is None:
            raise _text_missing()
        if pf["input_revision"] != input_revision:
            raise ApiError(409, "INPUT_REVISION_CONFLICT", "이 점검은 이전 입력의 결과입니다. 최신 점검을 확인해 주세요.")
        refs = list(_evidence_refs(json.loads(pf["facts_json"])))
    elif query.context == "document":
        doc = conn.execute("SELECT d.current_revision, r.* FROM documents d JOIN document_revisions r "
                           "ON r.document_id=d.document_id AND r.revision=d.current_revision "
                           "WHERE d.session_id=? AND d.document_id=?", (sid, query.document_id)).fetchone()
        if doc is None:
            raise _text_missing()
        if query.document_revision != doc["current_revision"]:
            raise ApiError(409, "DOCUMENT_REVISION_CONFLICT", "문서가 변경되었습니다. 최신 저장본에서 근거를 확인해 주세요.")
        input_revision = doc["input_revision"]
        refs = list(_evidence_refs([block for page in json.loads(doc["content_json"]).get("pages", [])
                                   for block in page.get("blocks", [])]))

    matching = [r for r in refs if r.get("source_id") == source_id and
                r.get("source_version") == query.source_version and r.get("segment_id") == segment_id]
    if query.context != "selection" and not matching:
        raise _text_missing()
    if not db_history.enabled(conn):
        raise _text_unavailable()
    pinned = conn.execute(
        "SELECT sel.source_version, sel.run_id, v.original_name, v.purged_at AS version_purged, "
        "v.expires_at, v.stored_path, v.content_hash, v.size_bytes, v.mime_type, v.session_id, v.provenance, "
        "r.status, r.warnings_json, r.purged_at AS run_purged "
        "FROM session_source_selections sel JOIN source_versions v "
        "ON v.source_id=sel.source_id AND v.version=sel.source_version "
        "LEFT JOIN extraction_runs r ON r.run_id=sel.run_id AND r.source_id=sel.source_id "
        "AND r.source_version=sel.source_version "
        "WHERE sel.session_id=? AND sel.input_revision=? AND sel.source_id=?",
        (sid, input_revision, source_id)).fetchone()
    if pinned is None or pinned["version_purged"] or pinned["run_purged"]:
        raise _text_unavailable()
    if source["scope"] == "registered" and pinned["expires_at"] and from_iso(pinned["expires_at"]) <= now():
        raise _text_unavailable()
    if query.source_version is not None and query.source_version != pinned["source_version"]:
        raise _text_unavailable()
    return source, pinned, input_revision, matching


def read_text(conn: Connection, session: Row, source_id: str, query: SourceTextQuery,
              segment_id: str | None = None) -> SourceTextOut:
    """C-01: authorized, pinned extraction text. No file/AI/TTL/approval mutation."""
    source, pinned, input_revision, matching = _resolve_text_source(conn, session, source_id, query, segment_id)
    sid = session["session_id"]
    if pinned["run_id"] is None or pinned["status"] in ("queued", "reading"):
        if source["parse_status"] == "failed":
            raise ApiError(422, "SOURCE_TEXT_UNAVAILABLE", "자료에서 읽어낸 글자가 없습니다. 텍스트 자료를 보완해 주세요.")
        raise ApiError(409, "SOURCE_NOT_READY", "자료를 읽고 있습니다. 잠시 후 다시 확인해 주세요.", retryable=True)
    if pinned["status"] not in ("complete", "partial"):
        raise ApiError(422, "SOURCE_TEXT_UNAVAILABLE", "자료에서 읽어낸 글자가 없습니다. 텍스트 자료를 보완해 주세요.")
    rows = conn.execute("SELECT segment_id, ordinal, locator_json, length(replace(text, char(0), char(1))) AS chars FROM segments "
                        "WHERE source_id=? AND source_version=? AND run_id=? ORDER BY ordinal, segment_id",
                        (source_id, pinned["source_version"], pinned["run_id"])).fetchall()
    by_id = {r["segment_id"]: r for r in rows}
    if not rows:
        raise ApiError(422, "SOURCE_TEXT_UNAVAILABLE", "읽어낸 글자가 없습니다. 이미지 자료는 텍스트본으로 보완해 주세요.")
    if segment_id is not None and segment_id not in by_id:
        raise _text_missing()
    focus = query.focus_segment_id or segment_id
    neighbours = []
    previous = following = None
    if segment_id:
        group = _text_group(json.loads(by_id[segment_id]["locator_json"]))
        group_rows = [r for r in rows if _text_group(json.loads(r["locator_json"])) == group]
        if focus not in {r["segment_id"] for r in group_rows}:
            raise _text_missing()
        index = next(i for i, r in enumerate(group_rows) if r["segment_id"] == focus)
        previous = group_rows[index - 1]["segment_id"] if index else None
        following = group_rows[index + 1]["segment_id"] if index + 1 < len(group_rows) else None
        neighbours = [by_id[x] for x in (previous, following) if x]
        rows = [by_id[focus]]

    binding = hashlib.sha256(json.dumps([sid, source_id, pinned["source_version"], pinned["run_id"],
        input_revision, query.model_dump(exclude={"cursor"}), segment_id], sort_keys=True).encode()).hexdigest()
    index = offset = 0
    if query.cursor:
        try:
            cursor = json.loads(base64.urlsafe_b64decode(query.cursor.encode()))
            if not isinstance(cursor, list) or len(cursor) != 3 or cursor[0] != binding:
                raise ValueError("cursor binding")
            index, offset = cursor[1:]
            if type(index) is not int or type(offset) is not int:
                raise ValueError("cursor types")
            if not (0 <= index < len(rows) and 0 <= offset < max(1, rows[index]["chars"])):
                raise ValueError("cursor position")
        except (ValueError, TypeError, UnicodeError):
            raise ApiError(422, "INVALID_REQUEST", "이어보기 위치가 유효하지 않습니다. 처음부터 다시 확인해 주세요.") from None

    def piece(row, start=0, budget=10000):
        # Python slicing preserves embedded NUL, which SQLite text substr truncates.
        stored = conn.execute("SELECT text FROM segments WHERE segment_id=? AND run_id=?",
                              (row["segment_id"], pinned["run_id"])).fetchone()[0]
        body = stored[start:start + min(budget, 10000)]
        return SourceTextSegment(segment_id=row["segment_id"], locator=json.loads(row["locator_json"]),
                                 text=body, offset=start, total_chars=row["chars"],
                                 truncated=start + len(body) < row["chars"])

    items = []
    budget = 30000 if segment_id else 50000
    while index < len(rows) and len(items) < query.limit and budget > 0:
        part = piece(rows[index], offset, budget)
        items.append(part)
        budget -= len(part.text)
        if part.truncated:
            offset += len(part.text)
            break
        index += 1
        offset = 0
    next_cursor = (base64.urlsafe_b64encode(json.dumps([binding, index, offset]).encode()).decode()
                   if index < len(rows) else None)
    excerpts = list(dict.fromkeys(r.get("excerpt", "") for r in matching))
    return SourceTextOut(source_id=source_id, source_version=pinned["source_version"], run_id=pinned["run_id"],
                         original_download_available=_original_eligible(conn, source, pinned["source_version"]),
                         name=pinned["original_name"], context=query.context, input_revision=input_revision,
                         current_input_revision=session["input_revision"], document_revision=query.document_revision,
                         parse_status=pinned["status"], warnings=json.loads(pinned["warnings_json"]), items=items,
                         context_segments=[piece(r) for r in neighbours], anchor_segment_id=segment_id,
                         target_segment_id=focus, excerpts=[e[:2000] for e in excerpts[:5]],
                         excerpts_truncated=len(excerpts) > 5 or any(len(e) > 2000 for e in excerpts),
                         previous_segment_id=previous, next_segment_id=following, next_cursor=next_cursor)


def _original_eligible(conn: Connection, source: Row, version: int) -> bool:
    # Only user uploads, not registered company files or imported public data.
    if not (source["scope"] == "session" and source["role"] == "evidence" and db_history.enabled(conn)
            and conn.execute("SELECT 1 FROM source_versions WHERE source_id=? AND version=? "
                             "AND session_id=? AND provenance='upload' AND purged_at IS NULL",
                             (source["source_id"], version, source["session_id"])).fetchone()):
        return False
    # Older DART imports also used provenance=upload. Keep their recorded warning
    # as an exclusion even after a later re-extraction changes current warnings.
    warnings = [source["warnings_json"], *(r[0] for r in conn.execute(
        "SELECT warnings_json FROM extraction_runs WHERE source_id=? AND source_version=?",
        (source["source_id"], version)))]
    return not any(w.get("code") == "PUBLIC_OPEN_DATA" for value in warnings for w in json.loads(value))


def read_original(conn: Connection, settings: Settings, session: Row, source_id: str,
                  query: SourceOriginalQuery) -> tuple[bytes, str, str]:
    source = conn.execute("SELECT * FROM sources WHERE source_id=? AND session_id=? AND deleted_at IS NULL",
                          (source_id, session["session_id"])).fetchone()
    if source is None or source["scope"] != "session" or source["role"] != "evidence":
        raise _text_missing()
    text_query = SourceTextQuery(**query.model_dump(exclude={"segment_id"}))
    source, version, _, _ = _resolve_text_source(conn, session, source_id, text_query, query.segment_id)
    if not _original_eligible(conn, source, version["source_version"]):
        raise _text_missing()
    if query.segment_id is not None and not conn.execute(
        "SELECT 1 FROM segments WHERE source_id=? AND source_version=? AND run_id=? AND segment_id=?",
        (source_id, version["source_version"], version["run_id"], query.segment_id)).fetchone():
        raise _text_missing()
    try:
        root = settings.private_runs_dir.resolve(strict=True)
        relative = Path(version["stored_path"])
        if relative.is_absolute() or relative.drive or ".." in relative.parts:
            raise ValueError("path")
        path = root / relative
        # Check every component before resolving: Windows junctions are reparse points too.
        for component in (path, *path.parents):
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("link")
            if component == root:
                break
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root / session["session_id"]) or not resolved.is_file():
            raise ValueError("scope")
        size = version["size_bytes"]
        if not 0 < size <= settings.max_file_bytes:
            raise ValueError("size")
        # Read once and return these exact verified bytes, never reopen a FileResponse path.
        with resolved.open("rb") as stream:
            content = stream.read(size + 1)
        if len(content) != size or hashlib.sha256(content).hexdigest() != version["content_hash"]:
            raise ValueError("hash")
    except (OSError, ValueError, TypeError):
        raise ApiError(410, "SOURCE_ORIGINAL_UNAVAILABLE", "이 버전의 원본 파일을 확인할 수 없습니다. 자료를 다시 첨부해 주세요.") from None
    suffix = path.suffix.lower()
    mime = {".txt": "text/plain", ".md": "text/markdown", ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(suffix)
    if mime is None:
        raise ApiError(410, "SOURCE_ORIGINAL_UNAVAILABLE", "지원하는 형식의 원본 파일이 없습니다.")
    name = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*\u202a-\u202e\u2066-\u2069]', '_', version["original_name"])
    # Preserve a known extension even if stored display metadata is malformed.
    name = Path(name).stem[:160].strip(" .") or "source"
    return content, name + suffix, mime


# DART adapter reuses session file storage, extraction history and the read Job contract.
def dart_status(settings: Settings):
    from app.models import PublicDataStatus
    if settings.dart_api_key:
        return PublicDataStatus(status="ready", configured_providers=["dart"],
                                message="DART 연결 사용 가능 · 특허청·나라장터 미연결")
    return PublicDataStatus(message="DART API 키 미설정 · 특허청·나라장터 미연결")


def _dart_error(status: str) -> ApiError:
    if status in {"010", "011", "012", "901"}:
        return ApiError(503, "DART_AUTH_ERROR", "DART 인증키 승인·유효성·허용 IP를 확인해 주세요.")
    if status == "020":
        return ApiError(429, "DART_RATE_LIMIT", "DART 요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.", retryable=True)
    if status in {"013", "014"}:
        return ApiError(404, "DART_DATA_NOT_FOUND", "이 회사의 DART 공개 자료가 없습니다.")
    return ApiError(502, "DART_SERVICE_ERROR", "DART 응답을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.", retryable=True)


def _dart_xml(data: bytes):
    import xml.etree.ElementTree as ET
    # ElementTree never fetches external DTDs; normal DART documents declare one.
    if b"<!ENTITY" in data.upper().replace(b"\x00", b""):
        raise _dart_error("invalid_xml")
    try:
        return ET.fromstring(data)
    except (ET.ParseError, ValueError):
        # Some older filings use EUC-KR, unsupported by the byte parser.
        try:
            return ET.fromstring(data.decode("euc-kr"))
        except (ET.ParseError, ValueError, UnicodeError):
            raise _dart_error("invalid_xml") from None


def _dart_download(settings: Settings, endpoint: str, params: dict, limit: int = 10 * 1024 * 1024) -> bytes:
    import time
    from urllib.parse import urlencode
    from urllib.request import Request, HTTPRedirectHandler, build_opener
    from urllib.error import URLError
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    if endpoint not in {"corpCode.xml", "company.json", "list.json", "document.xml"}:
        raise _dart_error("endpoint")
    query = urlencode({"crtfc_key": settings.dart_api_key, **params})
    request = Request("https://opendart.fss.or.kr/api/" + endpoint + "?" + query,
                      headers={"User-Agent": "Ddalgi-PublicData/1.0"})
    deadline = time.monotonic() + 45
    try:
        with build_opener(NoRedirect).open(request, timeout=15) as response:
            chunks, size = [], 0
            while chunk := response.read(64 * 1024):
                size += len(chunk)
                if size > limit or time.monotonic() > deadline:
                    raise ApiError(413, "DART_RESPONSE_TOO_LARGE", "DART 자료의 크기 또는 조회 시간 한도를 넘었습니다.")
                chunks.append(chunk)
            data = b"".join(chunks)
    except (URLError, TimeoutError, OSError):
        # Never include the request URL or provider exception: URL contains the key.
        raise ApiError(502, "DART_SERVICE_ERROR", "DART에 연결하지 못했습니다. 연결 상태를 확인해 주세요.", retryable=True) from None
    if endpoint.endswith(".json"):
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            raise _dart_error("json") from None
        if not isinstance(value, dict) or value.get("status") != "000":
            raise _dart_error(value.get("status", "invalid") if isinstance(value, dict) else "invalid")
    elif not data.startswith(b"PK"):
        raise _dart_error(_dart_xml(data).findtext("status", "invalid"))
    return data


def _dart_zip_entries(data: bytes) -> list[tuple[str, bytes]]:
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = [entry for entry in archive.infolist() if not entry.is_dir()]
            if len(entries) > 100 or sum(entry.file_size for entry in entries) > 80 * 1024 * 1024:
                raise ApiError(413, "DART_RESPONSE_TOO_LARGE", "DART 압축 자료의 해제 크기 한도를 넘었습니다.")
            # Read in memory; never extract archive paths onto the filesystem.
            return [(entry.filename, archive.read(entry)) for entry in entries if entry.filename.lower().endswith(".xml")]
    except (ValueError, RuntimeError, OSError, zipfile.BadZipFile):
        raise _dart_error("zip") from None


_DART_COMPANY_CACHE: tuple[str, float, list[tuple[str, str]]] | None = None
_DART_CACHE_LOCK = threading.Lock()


def _dart_company_records(settings: Settings) -> list[tuple[str, str]]:
    import time
    global _DART_COMPANY_CACHE
    if not settings.dart_api_key:
        raise ApiError(503, "PUBLIC_DATA_NOT_CONFIGURED", dart_status(settings).message)
    key_hash = hashlib.sha256(settings.dart_api_key.encode()).hexdigest()
    with _DART_CACHE_LOCK:
        if not _DART_COMPANY_CACHE or _DART_COMPANY_CACHE[0] != key_hash or _DART_COMPANY_CACHE[1] < time.monotonic():
            entries = _dart_zip_entries(_dart_download(settings, "corpCode.xml", {}))
            if len(entries) != 1:
                raise _dart_error("company_index")
            root = _dart_xml(entries[0][1])
            rows = [(row.findtext("corp_code", ""), row.findtext("corp_name", "")) for row in root.findall("list")]
            rows = [(code, name) for code, name in rows if re.fullmatch(r"\d{8}", code) and name.strip()]
            if not rows:
                raise _dart_error("empty_company_index")
            _DART_COMPANY_CACHE = key_hash, time.monotonic() + 86400, rows
        return _DART_COMPANY_CACHE[2]


def search_dart_companies(settings: Settings, query: str) -> list[dict]:
    import unicodedata
    def normalized(value):
        value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()
        return re.sub(r"^(?:\(주\)|주식회사)|(?:\(주\)|주식회사)$", "", value)
    needle = normalized(query)
    if len(needle) < 2:
        return []
    # Confirmed official aliases also serve the full-name comparison; never infer translations.
    from app.config import VERIFIED_DART_COMPANY_NAMES
    verified = VERIFIED_DART_COMPANY_NAMES
    matches = []
    for code, name in _dart_company_records(settings):
        alias = verified.get(code)
        names = (name, alias[1]) if alias and name == alias[0] else (name,)
        candidates = [normalized(value) for value in names if needle in normalized(value)]
        if candidates:
            rank = min(0 if needle == text else 1 if text.startswith(needle) else 2 for text in candidates)
            item = {"corp_code": code, "corp_name": name}
            if len(names) > 1:
                item["display_name"] = f"{name} ({names[1]})"
            matches.append((rank, min(map(len, candidates)), name, code, item))
    return [item for _, _, _, _, item in sorted(matches)[:20]]


def _dart_company_code(settings: Settings, target: str, selected: str | None = None) -> str:
    from app.config import company_names_match
    matches = {code for code, name in _dart_company_records(settings) if company_names_match(target, name)}
    if selected:
        if selected not in matches:
            raise ApiError(422, "DART_COMPANY_MISMATCH", "선택한 DART 회사 고유번호와 회사명이 일치하지 않습니다. 회사를 다시 선택해 주세요.")
        return selected
    if not matches:
        raise ApiError(404, "DART_COMPANY_NOT_FOUND", "DART에서 대상 회사를 찾지 못했습니다. 정식 회사명을 확인해 주세요. 모든 회사가 DART에 등록되는 것은 아닙니다.")
    if len(matches) != 1:
        raise ApiError(422, "DART_COMPANY_AMBIGUOUS", "같은 이름의 DART 회사가 여러 개입니다. 검색 결과에서 해당 회사를 선택해 주세요.")
    return matches.pop()


def _dart_document_blocks(data: bytes) -> list[str]:
    # Some DART archives contain HTML inside files named .xml.
    import re
    if re.match(rb"\s*(?:<\?xml[^>]*>\s*)?(?:<!doctype html[^>]*>\s*)?<html", data, re.I):
        from html.parser import HTMLParser
        class TextOnly(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.blocks = []
                self.ignored = 0
            def handle_starttag(self, tag, attrs):
                if tag in {"script", "style", "head"}:
                    self.ignored += 1
            def handle_endtag(self, tag):
                if tag in {"script", "style", "head"} and self.ignored:
                    self.ignored -= 1
            def handle_data(self, value):
                if not self.ignored:
                    value = " ".join(value.split())
                    if value:
                        self.blocks.append(value)
        try:
            value = data.decode("utf-8-sig")
        except UnicodeError:
            try:
                value = data.decode("cp949")
            except UnicodeError:
                raise _dart_error("encoding") from None
        parser = TextOnly()
        parser.feed(value)
        return parser.blocks
    root = _dart_xml(data)
    return [text for element in root.iter() if element.tag.upper() in {"TITLE", "P", "TD", "TE", "TU"}
            if (text := " ".join(" ".join(element.itertext()).split()))]


def _dart_documents(settings: Settings, target: str, corp_code: str | None = None) -> list[dict]:
    from datetime import timedelta
    from app.config import company_names_match
    code = _dart_company_code(settings, target, corp_code)
    raw = _dart_download(settings, "company.json", {"corp_code": code})
    company = json.loads(raw)
    # DART master uses a disclosure/stock name, company.json may use a legal name.
    # The code must match and at least one provider-supplied name must match in full.
    if company.get("corp_code") != code or not any(
            company_names_match(target, company.get(field) or "") for field in ("corp_name", "stock_name")):
        raise ApiError(422, "DART_COMPANY_MISMATCH", "DART 기업개황의 회사 정보가 선택한 회사와 다릅니다.")
    labels = {"corp_name": "회사명", "stock_name": "공시 등록명", "corp_name_eng": "영문 회사명", "ceo_nm": "대표자", "adres": "주소",
              "est_dt": "설립일", "hm_url": "홈페이지", "phn_no": "전화번호", "induty_code": "업종 코드"}
    lines = [f"금융감독원 DART 기업개황 · 조회일 {now().date().isoformat()}"]
    lines += [f"{label}: {company[field]}" for field, label in labels.items() if company.get(field)]
    company_url = "https://opendart.fss.or.kr/api/company.json?corp_code=" + code
    docs = [{"identity": f"dart:company:{code}", "name": "DART 기업개황.txt", "text": "\n".join(lines),
             "raw": raw, "raw_suffix": ".json", "url": company_url, "date": now().date().isoformat(), "partial": False}]
    try:
        listing = json.loads(_dart_download(settings, "list.json", {"corp_code": code,
            "bgn_de": (now() - timedelta(days=1095)).strftime("%Y%m%d"), "end_de": now().strftime("%Y%m%d"),
            "last_reprt_at": "Y", "page_count": "1", "sort": "date", "sort_mth": "desc"}))
    except ApiError as exc:
        if exc.code == "DART_DATA_NOT_FOUND":
            return docs
        raise
    filings = listing.get("list", [])
    if not isinstance(filings, list):
        raise _dart_error("filings")
    if not filings:
        return docs
    filing = filings[0]
    receipt = filing.get("rcept_no", "")
    if filing.get("corp_code") != code or not re.fullmatch(r"\d{14}", receipt):
        raise _dart_error("filing_identity")
    raw = _dart_download(settings, "document.xml", {"rcept_no": receipt})
    entries = _dart_zip_entries(raw)
    blocks = [block for name, data in entries for block in _dart_document_blocks(data)]
    if not blocks:
        raise _dart_error("empty_document")
    report_name = str(filing.get("report_nm", "공시 문서"))
    url = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + receipt
    header = f"금융감독원 DART 공시 원문\n회사명: {company['corp_name']}\n공시명: {report_name}\n공시 접수일: {filing.get('rcept_dt', '')}\n공시 원문: {url}\n\n"
    # Keep the selected import below ordinary extraction limits and expose truncation.
    limit = min(settings.max_source_chars, 25_000)
    selected, size = [], len(header)
    for block in blocks:
        if size + len(block) + 2 > limit:
            break
        selected.append(block)
        size += len(block) + 2
    if not selected:
        raise ApiError(413, "DART_RESPONSE_TOO_LARGE", "공시 본문 한 구간이 입력 한도를 넘었습니다. 자료 범위를 줄여 주세요.")
    docs.append({"identity": "dart:filing:" + receipt, "name": "DART 공시 원문.txt", "text": header + "\n\n".join(selected),
                 "raw": raw, "raw_suffix": ".zip", "url": url, "date": filing.get("rcept_dt"), "partial": len(selected) < len(blocks)})
    return docs


def run_dart_import(settings: Settings, session_id: str, job_id: str, revision: int, target: str, corp_code: str | None = None) -> None:
    from app.db import connect
    from app.parsers import parse, warning
    from app.services import jobs, reading
    def active(conn):
        row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        policy = sessions.usable(settings, row)
        job = jobs.get(conn, session_id, job_id)
        if job.status not in jobs.ACTIVE:
            return None
        if policy:
            jobs.fail(conn, job_id, "SESSION_EXPIRED" if policy != "demo_disabled" else "DEMO_MODE_DISABLED", "현재 세션에서 DART 자료를 저장할 수 없습니다.", False)
            return None
        if row["input_revision"] != revision:
            jobs.fail(conn, job_id, "INPUT_REVISION_CONFLICT", "조회 중 회사나 작성 조건이 변경되어 DART 자료를 저장하지 않았습니다. 최신 상태에서 다시 가져와 주세요.", False)
            return None
        return row
    try:
        with connect(settings.db_path, immediate=True) as conn:
            if not active(conn):
                return
            if jobs.get(conn, session_id, job_id).status == "running":
                return
            jobs.set_progress(conn, job_id, "dart_fetch", "DART 회사와 최근 공시를 조회하는 중")
            jobs.record_trace(conn, job_id, "public_fetch", {"actual_mode": "public_api", "provider": "dart", "ai_called": False})
        docs = _dart_documents(settings, target, corp_code)
        source_ids = []
        with upload_storage() as written, connect(settings.db_path, immediate=True) as conn:
            row = active(conn)
            if row is None:
                return
            for doc in docs:
                data = doc["text"].encode("utf-8")
                digest = hashlib.sha256(data).hexdigest()
                existing = conn.execute("SELECT source_id FROM sources WHERE session_id=? AND content_hash=? AND deleted_at IS NULL", (session_id, digest)).fetchone()
                if existing:
                    source_ids.append(existing[0])
                    continue
                check_capacity(settings, count_for_session(conn, session_id), 1)
                if len(data) > settings.max_file_bytes:
                    raise ApiError(413, "FILE_TOO_LARGE", "DART 변환 자료가 파일 크기 한도를 넘었습니다.")
                item = store(conn, settings, session_id, row["expires_at"], "company", [(".txt", doc["name"], "text/plain", data)],
                             written_paths=written, provenance="public_import")[0]
                source_ids.append(item.source_id)
                path = settings.private_runs_dir / f"{session_id}/{item.source_id}.dart{doc['raw_suffix']}"
                with path.open("xb") as stream:
                    written.append(path)
                    stream.write(doc["raw"])
                result = parse(data, ".txt")
                result.warnings.append(warning("PUBLIC_OPEN_DATA", "금융감독원 DART 공개 자료", {"provider": "dart", "identity": doc["identity"], "source_url": doc["url"], "document_date": doc["date"]}))
                if doc["partial"]:
                    result.status = "partial"
                    result.warnings.append(warning("TEXT_LIMIT", "공시 원문 중 앞부분 25,000자 범위만 읽었습니다. 저장된 원문과 출처를 확인해 필요한 내용을 별도로 첨부해 주세요."))
                # 날짜를 먼저 저장해 같은 추출 실행의 구간에도 원문 날짜가 연결된다.
                conn.execute("UPDATE sources SET document_date=? WHERE source_id=?", (doc["date"], item.source_id))
                source_row = conn.execute("SELECT * FROM sources WHERE source_id=?", (item.source_id,)).fetchone()
                reading._apply_result(conn, source_row, result)
            jobs.record_trace(conn, job_id, "public_fetch_result", {"provider": "dart", "source_count": len(source_ids)})
            jobs.succeed(conn, job_id, {"type": "sources", "source_ids": source_ids})
            sessions.touch(conn, settings, row)
    except ApiError as exc:
        with connect(settings.db_path, immediate=True) as conn:
            if active(conn) is not None:
                jobs.fail(conn, job_id, exc.code, exc.message, exc.retryable)
    except Exception:
        # Do not log provider exceptions or raw URLs containing the credential.
        _log.error("DART import failed (%s)", job_id)
        with connect(settings.db_path, immediate=True) as conn:
            if active(conn) is not None:
                jobs.fail(conn, job_id, "DART_SERVICE_ERROR", "DART 자료를 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.", True)
