"""등록 자료(scope=registered) 적재 서비스. CLI scripts/import_registered.py와 테스트가 같은 함수를 쓴다.

입력(팀 묶음, INGEST_SCHEMA.md v1.1): bundle_root 기준 상대 경로 + ingest_dir의
  sources.json · company_chunks.jsonl · 00_이미지목록.csv · photo_candidates.json (뒤 둘은 없어도 됨)

규칙
- status=ready만 적재. mock은 --with-mock일 때만. 나머지 status는 건너뛰고 건수로 남긴다. 모르는 status는 오류.
- sha256: path 파일이 있으면 바이트 해시 비교(불일치 = 오류, 전체 중단). sha256_note가 있으면 검증 생략·hash_verified=false.
  sha256=null은 중복 판정에 쓰지 않는다. 검증된 해시만 같은 ID 재적재의 동일성 판단에 쓴다.
- 경로: bundle_root 밖으로 나가면 오류. 이미지는 CSV·candidates에 적힌 정식 경로만(images/ 사본은 적재하지 않음).
- use_as_company_evidence=false는 적재하되 표시만 하고 선택·근거에서 제외한다.
- document_date는 문자열 그대로("2017"도). date_from_filename은 별도 보존.
- [MOCK] 라벨은 text·excerpt에서 지우지 않는다. 오류는 하나라도 있으면 아무것도 적재하지 않는다(한 트랜잭션).
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import connect
from app.models import SourceOut
from app.services import locators
from app.timeutil import now, to_iso

IMPORT_STATUSES = {"ready"}
MOCK_STATUS = "mock"
SKIP_STATUSES = {"missing_original", "planning_reference", "content_confirmed_original_missing",
                 "received_by_team_not_in_package"}
EVIDENCE_STATUSES = {"자료에 기재됨", "이미지에서 판독한 발췌", "자료에 기재됨 (2017년 카다로그)"}
IMAGE_CSV_ROOT = Path("05_이미지") / "전체_추출이미지"
IMAGE_PSEUDO_SOURCE = "REGISTERED_IMAGES"   # candidates에 source_id가 없는 실제 팀 자료용 이미지 묶음 자료
MOCK_LABEL = "[MOCK]"


class ImportError_(Exception):
    """적재 규칙 위반. code는 quality/ingestion_cases.json의 제안 코드를 따른다."""

    def __init__(self, code: str, message: str, where: str | None = None) -> None:
        super().__init__(f"{code}: {message}" + (f" ({where})" if where else ""))
        self.code = code
        self.message = message
        self.where = where


@dataclass
class ImportSummary:
    dry_run: bool
    with_mock: bool
    added_sources: int = 0
    added_segments: int = 0
    added_assets: int = 0
    already_present: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    excluded_from_evidence: int = 0
    hash_unverified: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


# ---------------- 읽기 ----------------

def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ImportError_("INVALID_INPUT", f"{path.name} {no}행이 JSON이 아닙니다") from exc
    return rows


def _safe_path(bundle_root: Path, relative: str, where: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ImportError_("INVALID_INPUT", "path가 비어 있습니다", where)
    root = bundle_root.resolve()
    candidate = (root / relative).resolve()
    if root != candidate and root not in candidate.parents:
        raise ImportError_("PATH_OUTSIDE_BUNDLE", "묶음 루트 밖 경로입니다", where)
    return candidate


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------- 검사 ----------------

@dataclass
class _Source:
    raw: dict
    file: Path | None
    package_hash: str | None
    hash_verified: bool
    hash_note: str | None
    chunks: list[dict] = field(default_factory=list)
    photos: list[dict] = field(default_factory=list)


def _check_source(bundle_root: Path, raw: dict, with_mock: bool, summary: ImportSummary) -> _Source | None:
    sid = raw.get("source_id")
    where = f"sources.json {sid}"
    if not sid or not isinstance(sid, str):
        raise ImportError_("INVALID_INPUT", "source_id가 없습니다", "sources.json")
    status = raw.get("status")
    is_mock = bool(raw.get("mock", False))
    if status == MOCK_STATUS or is_mock:
        if status != MOCK_STATUS or not is_mock:
            raise ImportError_("MOCK_MARKER_CONFLICT", "status=mock과 mock=true는 함께여야 합니다", where)
        display = raw.get("name") or raw.get("filename") or sid
        if MOCK_LABEL not in display:
            raise ImportError_("MOCK_MARKER_CONFLICT", "mock 자료의 표시명에 [MOCK]가 없습니다", where)
        if not with_mock:
            summary.skipped["mock"] = summary.skipped.get("mock", 0) + 1
            return None
    elif status in SKIP_STATUSES:
        summary.skipped[status] = summary.skipped.get(status, 0) + 1
        return None
    elif status not in IMPORT_STATUSES:
        raise ImportError_("UNKNOWN_STATUS", f"허용되지 않은 status: {status!r}", where)

    path = raw.get("path")
    if raw.get("available_in_package") and not path:
        raise ImportError_("PATH_NOT_FOUND", "available_in_package=true인데 path가 없습니다", where)
    file: Path | None = None
    if path:
        file = _safe_path(bundle_root, path, where)
        if not file.is_file():
            raise ImportError_("PATH_NOT_FOUND", "path 파일이 묶음에 없습니다", where)

    supplied = raw.get("sha256")
    note = raw.get("sha256_note")
    package_hash = _sha256(file) if file else None
    if note:
        hash_verified, hash_note = False, str(note)
        summary.hash_unverified += 1
    elif supplied and file:
        if supplied.lower() != package_hash:
            raise ImportError_("HASH_MISMATCH", "제공된 sha256이 파일 바이트와 다릅니다", where)
        hash_verified, hash_note = True, None
    else:
        hash_verified, hash_note = False, ("sha256 없음" if not supplied else "원본 미포함")
        summary.hash_unverified += 1
    return _Source(raw=raw, file=file, package_hash=package_hash, hash_verified=hash_verified, hash_note=hash_note)


def _check_chunks(chunks: list[dict], sources: dict[str, _Source], all_ids: set[str]) -> None:
    seen: set[str] = set()
    for row in chunks:
        cid, sid = row.get("chunk_id"), row.get("source_id")
        where = f"company_chunks.jsonl {cid}"
        if not cid:
            raise ImportError_("INVALID_INPUT", "chunk_id가 없습니다", "company_chunks.jsonl")
        if cid in seen:
            raise ImportError_("DUPLICATE_CHUNK_ID", "chunk_id가 중복됩니다", where)
        seen.add(cid)
        if sid not in all_ids:
            raise ImportError_("UNKNOWN_SOURCE_ID", f"sources.json에 없는 source_id: {sid}", where)
        if row.get("evidence_status") not in EVIDENCE_STATUSES:
            raise ImportError_("UNKNOWN_EVIDENCE_STATUS", f"허용되지 않은 evidence_status: {row.get('evidence_status')!r}", where)
        if not isinstance(row.get("text"), str) or not row["text"].strip():
            raise ImportError_("INVALID_INPUT", "text가 비어 있습니다", where)
        try:
            locator = locators.to_object(row.get("locator"))
        except locators.LocatorError as exc:
            raise ImportError_(exc.code, f"locator를 읽을 수 없습니다: {exc.value}", where) from exc
        if sid not in sources:
            continue  # 건너뛴 자료의 구간은 함께 건너뛴다
        src = sources[sid]
        # TXT 원본이 묶음에 있으면 그 행의 내용이 실제로 같은지 본다(verify_bundle과 같은 규칙).
        if src.file is not None and src.file.suffix.lower() in {".txt", ".md"} and "line_start" in locator:
            lines = src.file.read_text(encoding="utf-8-sig").splitlines()
            n = locator["line_start"]
            if n > len(lines) or lines[n - 1].strip() != row["text"].strip():
                raise ImportError_("CHUNK_TEXT_MISMATCH", "locator가 가리키는 원본 행과 text가 다릅니다", where)
        row = {**row, "_locator": locator}
        src.chunks.append(row)


def _read_images(bundle_root: Path, ingest_dir: Path, sources: dict[str, _Source], with_mock: bool
                 ) -> list[dict]:
    """CSV 행 ↔ candidates를 잇는다. CSV에 적힌 정식 경로만 인정한다. 돌려주는 목록은 적재할 이미지."""
    csv_path = ingest_dir / "00_이미지목록.csv"
    cand_path = ingest_dir / "photo_candidates.json"
    if not csv_path.is_file():
        return []
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    expected = ["폴더", "파일명", "PPT페이지", "원본이미지", "처리", "가로px", "세로px"]
    if rows and list(rows[0].keys()) != expected:
        raise ImportError_("INVALID_INPUT", f"00_이미지목록.csv 열이 {expected}가 아닙니다", "00_이미지목록.csv")
    by_path: dict[str, dict] = {}
    for r in rows:
        rel = (IMAGE_CSV_ROOT / r["폴더"] / r["파일명"]).as_posix()
        where = f"00_이미지목록.csv {r['폴더']}/{r['파일명']}"
        file = _safe_path(bundle_root, rel, where)
        if not file.is_file():
            raise ImportError_("IMAGE_NOT_FOUND", "CSV의 이미지 파일이 묶음에 없습니다", where)
        by_path[rel] = {"rel": rel, "file": file, "csv": r}
    candidates = _read_json(cand_path) if cand_path.is_file() else []
    for c in candidates:
        where = f"photo_candidates.json {c.get('photo_id')}"
        rel = c.get("path")
        if rel not in by_path:
            raise ImportError_("IMAGE_NOT_FOUND", "candidates의 path가 CSV 목록에 없습니다", where)
        entry = by_path[rel]
        if c.get("sha256") and c["sha256"].lower() != _sha256(entry["file"]):
            raise ImportError_("HASH_MISMATCH", "candidates의 sha256이 파일과 다릅니다", where)
        if c.get("mock") and not with_mock:
            continue
        try:
            entry["locator"] = locators.to_object(c["locator"]) if c.get("locator") else None
        except locators.LocatorError as exc:
            raise ImportError_(exc.code, f"사진 locator를 읽을 수 없습니다: {exc.value}", where) from exc
        entry["candidate"] = c
    images = []
    for entry in by_path.values():
        c = entry.get("candidate")
        if c is None:
            continue  # candidates에 없는 CSV 행은 적재하지 않는다(후보로 검토된 이미지만)
        sid = c.get("source_id")
        if sid is not None and sid not in sources:
            continue  # 건너뛴 자료(예: mock 미포함)의 사진
        images.append(entry)
    return images


# ---------------- 적재 ----------------

def _display_name(raw: dict) -> str:
    return raw.get("name") or raw.get("filename") or raw["source_id"]


def _mime(path: Path | None, filename: str) -> str:
    import mimetypes
    return mimetypes.guess_type(path.name if path else filename)[0] or "application/octet-stream"


def import_bundle(settings: Settings, bundle_root: Path, ingest_dir: Path | None = None, *,
                  with_mock: bool = False, dry_run: bool = False) -> ImportSummary:
    bundle_root = Path(bundle_root)
    ingest_dir = Path(ingest_dir) if ingest_dir else bundle_root
    summary = ImportSummary(dry_run=dry_run, with_mock=with_mock)
    sources_path = ingest_dir / "sources.json"
    if not sources_path.is_file():
        raise ImportError_("INVALID_INPUT", "sources.json이 없습니다", str(ingest_dir.name))
    raw_sources = _read_json(sources_path)
    if not isinstance(raw_sources, list):
        raise ImportError_("INVALID_INPUT", "sources.json은 배열이어야 합니다", "sources.json")
    all_ids = {r.get("source_id") for r in raw_sources}
    if len(all_ids) != len(raw_sources):
        raise ImportError_("INVALID_INPUT", "source_id가 중복됩니다", "sources.json")

    sources: dict[str, _Source] = {}
    for raw in raw_sources:
        checked = _check_source(bundle_root, raw, with_mock, summary)
        if checked is not None:
            sources[raw["source_id"]] = checked
    chunks_path = ingest_dir / "company_chunks.jsonl"
    _check_chunks(_read_jsonl(chunks_path) if chunks_path.is_file() else [], sources, all_ids)
    images = _read_images(bundle_root, ingest_dir, sources, with_mock)

    with connect(settings.db_path, immediate=True) as conn:
        stamp = to_iso(now())
        to_add: list[_Source] = []
        for sid, src in sources.items():
            existing = conn.execute("SELECT content_hash, hash_verified FROM sources WHERE source_id=? AND scope='registered'",
                                    (sid,)).fetchone()
            if existing is not None:
                if (src.hash_verified and existing["hash_verified"] and src.package_hash != existing["content_hash"]):
                    raise ImportError_("SOURCE_ID_CONFLICT", "같은 source_id인데 검증된 원본 해시가 다릅니다", sid)
                summary.already_present += 1
                continue
            to_add.append(src)
            if not src.raw.get("use_as_company_evidence", True):
                summary.excluded_from_evidence += 1

        pseudo_needed = any(e.get("candidate", {}).get("source_id") is None for e in images)
        registered_dir = settings.private_runs_dir / "registered"
        if not dry_run:
            registered_dir.mkdir(parents=True, exist_ok=True)
            (registered_dir / "images").mkdir(exist_ok=True)

        for src in to_add:
            raw = src.raw
            sid = raw["source_id"]
            suffix = src.file.suffix.lower() if src.file else Path(raw.get("filename") or "").suffix.lower()
            rel = f"registered/{sid}{suffix}" if src.file else f"registered/{sid}.missing"
            if not dry_run and src.file:
                shutil.copyfile(src.file, settings.private_runs_dir / rel)
            has_text = bool(src.chunks)
            has_images = any(e["candidate"].get("source_id") == sid for e in images)
            parse_status = "complete" if (has_text or has_images) else "failed"
            warnings = [] if (has_text or has_images) else [
                {"locator": None, "code": "NO_USABLE_TEXT", "message": "읽을 수 있는 구간이 없습니다.",
                 "action": "텍스트본이나 추출 구간을 추가해 주세요."}]
            if src.file is None:
                warnings.append({"locator": None, "code": "ORIGINAL_NOT_IN_PACKAGE",
                                 "message": "원본 파일이 묶음에 없습니다.", "action": None})
            summary.added_sources += 1
            summary.added_segments += len(src.chunks)
            if dry_run:
                continue
            conn.execute(
                "INSERT INTO sources (source_id, session_id, source_version, scope, name, mime_type, size_bytes, kind, "
                "parse_status, text_available, image_available, stored_path, content_hash, warnings_json, created_at, "
                "expires_at, origin_group, document_date, document_date_verified, date_from_filename, extraction_method, "
                "use_as_company_evidence, hash_verified, hash_note, is_mock, imported_at, note) "
                "VALUES (?, NULL, 1, 'registered', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, _display_name(raw), _mime(src.file, raw.get("filename") or ""),
                 src.file.stat().st_size if src.file else 0, "company", parse_status, int(has_text), int(has_images),
                 rel, src.package_hash or "", json.dumps(warnings, ensure_ascii=False), stamp,
                 raw.get("origin_group") or sid,
                 raw.get("document_date"), int(bool(raw.get("document_date_verified", False))),
                 raw.get("date_from_filename"), raw.get("extraction_method"),
                 int(bool(raw.get("use_as_company_evidence", True))), int(src.hash_verified), src.hash_note,
                 int(bool(raw.get("mock", False))), stamp, raw.get("note")))
            for ordinal, row in enumerate(src.chunks, start=1):
                conn.execute(
                    "INSERT INTO segments (segment_id, source_id, source_version, session_id, ordinal, locator_json, text, "
                    "created_at, chunk_id, evidence_status, document_date, extraction_method) "
                    "VALUES (?, ?, 1, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (f"seg_{uuid.uuid4().hex[:16]}", sid, ordinal, json.dumps(row["_locator"], ensure_ascii=False),
                     row["text"], stamp, row["chunk_id"], row["evidence_status"],
                     row.get("document_date") or raw.get("document_date") or raw.get("date_from_filename"),
                     row.get("extraction_method")))

        if pseudo_needed and not dry_run:
            if conn.execute("SELECT 1 FROM sources WHERE source_id=?", (IMAGE_PSEUDO_SOURCE,)).fetchone() is None:
                conn.execute(
                    "INSERT INTO sources (source_id, session_id, source_version, scope, name, mime_type, size_bytes, kind, "
                    "parse_status, text_available, image_available, stored_path, content_hash, created_at, imported_at) "
                    "VALUES (?, NULL, 1, 'registered', '이미지 목록(00_이미지목록.csv)', 'text/csv', 0, 'photo', "
                    "'complete', 0, 1, 'registered/images', '', ?, ?)", (IMAGE_PSEUDO_SOURCE, stamp, stamp))
        added_ids = {s.raw["source_id"] for s in to_add}
        for entry in images:
            c = entry["candidate"]
            owner = c.get("source_id") or IMAGE_PSEUDO_SOURCE
            if c.get("source_id") is not None and owner not in added_ids:
                continue  # 이미 있는 자료의 사진은 다시 넣지 않는다
            photo_id = c.get("photo_id") or entry["csv"]["파일명"]
            if conn.execute("SELECT 1 FROM assets WHERE photo_id=? AND scope='registered' AND deleted_at IS NULL",
                            (photo_id,)).fetchone():
                continue
            from PIL import Image
            with Image.open(entry["file"]) as img:
                width, height = img.width, img.height
            if (str(width), str(height)) != (entry["csv"]["가로px"], entry["csv"]["세로px"]):
                raise ImportError_("IMAGE_SIZE_MISMATCH", "CSV의 가로/세로가 실제 이미지와 다릅니다",
                                   f"00_이미지목록.csv {entry['csv']['파일명']}")
            summary.added_assets += 1
            if dry_run:
                continue
            rel = f"registered/images/{photo_id}{entry['file'].suffix.lower()}"
            shutil.copyfile(entry["file"], settings.private_runs_dir / rel)
            conn.execute(
                "INSERT INTO assets (asset_id, source_id, source_version, scope, session_id, origin, mime_type, width, height, "
                "content_hash, status, stored_path, created_at, expires_at, photo_id, caption_candidate, selected_as_candidate, "
                "approved_for_external_use, photo_locator_json) "
                "VALUES (?, ?, 1, 'registered', NULL, 'source_image', ?, ?, ?, ?, 'ready', ?, ?, NULL, ?, ?, ?, ?, ?)",
                (f"asset_{uuid.uuid4().hex[:16]}", owner, _mime(entry["file"], ""), width, height,
                 _sha256(entry["file"]), rel, stamp, photo_id, c.get("caption_candidate"),
                 int(bool(c.get("selected_as_candidate"))),
                 None if c.get("approved_for_external_use") is None else int(bool(c["approved_for_external_use"])),
                 json.dumps(entry.get("locator"), ensure_ascii=False) if entry.get("locator") else None))

        conn.execute(
            "INSERT INTO registered_imports (import_id, bundle_label, with_mock, dry_run, summary_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"imp_{uuid.uuid4().hex[:12]}", hashlib.sha256(bundle_root.resolve().name.encode()).hexdigest()[:16],
             int(with_mock), int(dry_run), json.dumps(summary.as_dict(), ensure_ascii=False), stamp))
        if dry_run:
            conn.rollback()
            raise _DryRun(summary)
    return summary


class _DryRun(Exception):
    def __init__(self, summary: ImportSummary) -> None:
        self.summary = summary


def run(settings: Settings, bundle_root: Path, ingest_dir: Path | None = None, *, with_mock: bool = False,
        dry_run: bool = False) -> ImportSummary:
    """import_bundle의 편의 함수. dry-run은 검사·집계만 하고 아무것도 쓰지 않는다."""
    try:
        return import_bundle(settings, bundle_root, ingest_dir, with_mock=with_mock, dry_run=dry_run)
    except _DryRun as exc:
        return exc.summary


# ---------------- 조회 ----------------

def list_registered(conn: sqlite3.Connection, kind: str | None = None) -> list[SourceOut]:
    from app.services.sources import _row_to_out  # 같은 출력 모양

    query = "SELECT * FROM sources WHERE scope='registered' AND deleted_at IS NULL"
    params: list[Any] = []
    if kind:
        query += " AND kind=?"
        params.append(kind)
    query += " ORDER BY source_id"
    return [_row_to_out(conn, r) for r in conn.execute(query, params)]
