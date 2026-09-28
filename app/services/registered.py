"""등록 자료(scope=registered) 적재 서비스. CLI scripts/import_registered.py와 테스트가 같은 함수를 쓴다.

입력(팀 묶음, INGEST_SCHEMA.md v1.1): bundle_root 기준 상대 경로 + ingest_dir의
  sources.json · company_chunks.jsonl · 00_이미지목록.csv · photo_candidates.json (뒤 둘은 없어도 됨)

규칙
- ready는 실제 입수 자료(확인 완료 뜻 아님), mock/demo는 각각 --with-mock/--with-demo에서만 적재한다.
- sha256: path 파일이 있으면 바이트 해시 비교(불일치 = 오류, 전체 중단). sha256_note가 있으면 검증 생략·hash_verified=false.
  sha256=null은 중복 판정에 쓰지 않는다. 검증된 해시만 같은 ID 재적재의 동일성 판단에 쓴다.
- 경로: bundle_root 밖으로 나가면 오류. 이미지 후보는 독립 바이트를 검증하며 CSV 관계는 명시적 연결만 쓴다.
- use_as_company_evidence=false는 적재하되 표시만 하고 선택·근거에서 제외한다.
- document_date는 문자열 그대로("2017"도). date_from_filename은 별도 보존.
- [MOCK] 라벨은 text·excerpt에서 지우지 않는다. 오류는 하나라도 있으면 아무것도 적재하지 않는다(한 트랜잭션).
- 재적재(BE-08): 이미 있는 사진은 source_id·photo_id·바이트 해시가 같아야 한다(다르면 PHOTO_ID_CONFLICT). 공개 허가
  (approved_for_external_use)가 묶음과 다르면 update_publication=True(CLI --update-publication)일 때만 갱신하고, 같은 트랜잭션에서
  관련 승인 무효화·활성 Export 확정 실패까지 처리한다. 플래그 없이는 건수(publication_pending)만 센다. dry-run은 아무것도 바꾸지 않는다.
"""
from __future__ import annotations

import csv
import hashlib
import json
import io
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import Connection, connect
from app.models import SourceOut
from app.services import locators
from app.timeutil import now, to_iso

IMPORT_STATUSES = {"ready"}
MOCK_STATUS = "mock"
DEMO_STATUS = "demo"
DEMO_LABEL = "[시연]"
DEMO_EVIDENCE_STATUS = "시연용 임시 문장"
SKIP_STATUSES = {"missing_original", "planning_reference", "content_confirmed_original_missing",
                 "received_by_team_not_in_package"}
EVIDENCE_STATUSES = {"자료에 기재됨", "이미지에서 판독한 발췌", "자료에 기재됨 (2017년 카다로그)"}
IMAGE_CSV_ROOT = Path("05_이미지") / "전체_추출이미지"
IMAGE_PSEUDO_SOURCE = "REGISTERED_IMAGES"   # candidates에 source_id가 없는 실제 팀 자료용 이미지 묶음 자료
IMAGE_PSEUDO_SOURCES = {"real": IMAGE_PSEUDO_SOURCE, "mock": "REGISTERED_MOCK_IMAGES", "demo": "REGISTERED_DEMO_IMAGES"}
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
    with_demo: bool = False
    added_sources: int = 0
    added_segments: int = 0
    added_assets: int = 0
    already_present: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    excluded_from_evidence: int = 0
    hash_unverified: int = 0
    publication_updated: int = 0        # 공개 허가를 실제로 갱신한 사진 수(update_publication=True)
    publication_pending: int = 0        # 묶음과 값이 다르지만 갱신하지 않은 사진 수(플래그 없음 또는 dry-run)
    approvals_invalidated: int = 0
    exports_finalized: int = 0
    candidates_without_original: int = 0
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
    origin_kind: str = "real"
    chunks: list[dict] = field(default_factory=list)
    photos: list[dict] = field(default_factory=list)


def _origin_kind(raw: dict, where: str) -> str:
    for key in ("mock", "demo"):
        if key in raw and type(raw[key]) is not bool:
            raise ImportError_("INVALID_INPUT", f"{key}는 boolean이어야 합니다", where)
    mock = raw.get("mock", False)
    demo = raw.get("demo", False)
    if mock and demo:
        raise ImportError_("DEMO_MARKER_CONFLICT", "mock과 demo를 함께 표시할 수 없습니다", where)
    if raw.get("status") == DEMO_STATUS or demo:
        if raw.get("status") != DEMO_STATUS or not demo:
            raise ImportError_("DEMO_MARKER_CONFLICT", "status=demo와 demo=true는 함께여야 합니다", where)
        return "demo"
    return "mock" if mock or raw.get("status") == MOCK_STATUS else "real"


def _safe_id(value: Any, where: str) -> str:
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or any(c in value for c in '/\\:*?"<>|\0') or value.endswith((" ", "."))):
        raise ImportError_("INVALID_INPUT", "ID는 안전한 파일명 구성요소여야 합니다", where)
    return value


def _check_source(bundle_root: Path, raw: dict, with_mock: bool, with_demo: bool,
                  summary: ImportSummary) -> _Source | None:
    sid = raw.get("source_id")
    where = f"sources.json {sid}"
    if not sid or not isinstance(sid, str):
        raise ImportError_("INVALID_INPUT", "source_id가 없습니다", "sources.json")
    _safe_id(sid, where)
    status = raw.get("status")
    origin_kind = _origin_kind(raw, where)
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
    elif origin_kind == "demo":
        display = raw.get("name") or raw.get("filename") or sid
        if not display.startswith(DEMO_LABEL) or MOCK_LABEL in display:
            raise ImportError_("DEMO_MARKER_CONFLICT", "시연 자료의 표시명은 [시연]으로 시작해야 합니다", where)
        if not with_demo:
            summary.skipped["demo"] = summary.skipped.get("demo", 0) + 1
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
    return _Source(raw=raw, file=file, package_hash=package_hash, hash_verified=hash_verified,
                   hash_note=hash_note, origin_kind=origin_kind)


def _check_chunks(chunks: list[dict], sources: dict[str, _Source], all_sources: dict[str, dict]) -> None:
    seen: set[str] = set()
    for row in chunks:
        cid, sid = row.get("chunk_id"), row.get("source_id")
        where = f"company_chunks.jsonl {cid}"
        if not cid:
            raise ImportError_("INVALID_INPUT", "chunk_id가 없습니다", "company_chunks.jsonl")
        if cid in seen:
            raise ImportError_("DUPLICATE_CHUNK_ID", "chunk_id가 중복됩니다", where)
        seen.add(cid)
        if sid not in all_sources:
            raise ImportError_("UNKNOWN_SOURCE_ID", f"sources.json에 없는 source_id: {sid}", where)
        origin_kind = _origin_kind(all_sources[sid], where)
        allowed_statuses = {DEMO_EVIDENCE_STATUS} if origin_kind == "demo" else EVIDENCE_STATUSES
        if row.get("evidence_status") not in allowed_statuses:
            raise ImportError_("UNKNOWN_EVIDENCE_STATUS", f"허용되지 않은 evidence_status: {row.get('evidence_status')!r}", where)
        if not isinstance(row.get("text"), str) or not row["text"].strip():
            raise ImportError_("INVALID_INPUT", "text가 비어 있습니다", where)
        if origin_kind == "demo" and (not row["text"].startswith(DEMO_LABEL) or MOCK_LABEL in row["text"]):
            raise ImportError_("DEMO_MARKER_CONFLICT", "시연 구간은 [시연] 접두어를 유지해야 합니다", where)
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


def _read_images(bundle_root: Path, ingest_dir: Path, sources: dict[str, _Source],
                 all_sources: dict[str, dict], with_mock: bool, with_demo: bool,
                 summary: ImportSummary) -> list[dict]:
    """후보 바이트를 독립 검증한다. CSV 연결은 동일 경로 또는 명시적 original_ref만 허용한다."""
    from PIL import Image

    csv_path = ingest_dir / "00_이미지목록.csv"
    if not csv_path.is_file():
        csv_path = bundle_root / "05_이미지" / "00_이미지목록.csv"
    cand_path = ingest_dir / "photo_candidates.json"
    rows = []
    expected = ["폴더", "파일명", "PPT페이지", "원본이미지", "처리", "가로px", "세로px"]
    if csv_path.is_file():
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != expected:
                raise ImportError_("INVALID_INPUT", "00_이미지목록.csv는 정해진 7열이어야 합니다", "00_이미지목록.csv")
            rows = list(reader)
    by_path: dict[Path, dict] = {}
    by_ref: dict[tuple[str, str], dict] = {}
    for r in rows:
        rel = (IMAGE_CSV_ROOT / r["폴더"] / r["파일명"]).as_posix()
        where = f"00_이미지목록.csv {r['폴더']}/{r['파일명']}"
        file = _safe_path(bundle_root, rel, where)
        if not file.is_file():
            raise ImportError_("IMAGE_NOT_FOUND", "CSV의 이미지 파일이 묶음에 없습니다", where)
        if file in by_path:
            raise ImportError_("INVALID_INPUT", "CSV 이미지 경로가 중복됩니다", where)
        entry = {"rel": rel, "file": file, "csv": r}
        by_path[file] = entry
        by_ref[(r["폴더"], r["파일명"])] = entry
    candidates = _read_json(cand_path) if cand_path.is_file() else []
    if not isinstance(candidates, list):
        raise ImportError_("INVALID_INPUT", "photo_candidates.json은 배열이어야 합니다")
    images = []
    seen: set[str] = set()
    for c in candidates:
        where = "photo_candidates.json"
        photo_id = _safe_id(c.get("photo_id"), where)
        where = f"photo_candidates.json {photo_id}"
        if photo_id in seen:
            raise ImportError_("PHOTO_ID_CONFLICT", "photo_id가 중복됩니다", where)
        seen.add(photo_id)
        sid = c.get("source_id")
        if sid is not None and sid not in all_sources:
            raise ImportError_("UNKNOWN_SOURCE_ID", "사진의 source_id가 sources.json에 없습니다", where)
        for key in ("mock", "demo"):
            if key in c and type(c[key]) is not bool:
                raise ImportError_("INVALID_INPUT", f"사진 {key}는 boolean이어야 합니다", where)
        if c.get("mock") and c.get("demo"):
            raise ImportError_("DEMO_MARKER_CONFLICT", "사진의 mock/demo 마커가 충돌합니다", where)
        origin = _origin_kind(all_sources[sid], where) if sid is not None else (
            "mock" if c.get("mock") else "demo" if c.get("demo") else "real")
        if (c.get("mock") and origin != "mock") or (c.get("demo") and origin != "demo"):
            raise ImportError_("DEMO_MARKER_CONFLICT", "사진 마커와 소유 자료의 출처 구분이 다릅니다", where)
        if ((origin == "mock" and not with_mock) or (origin == "demo" and not with_demo)
                or (sid is not None and sid not in sources)):
            continue
        file = _safe_path(bundle_root, c.get("path"), where)
        if not file.is_file():
            raise ImportError_("IMAGE_NOT_FOUND", "후보 파일이 묶음에 없습니다", where)
        data = file.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if c.get("sha256") and str(c["sha256"]).lower() != digest:
            raise ImportError_("HASH_MISMATCH", "후보 sha256이 실제 바이트와 다릅니다", where)
        try:
            with Image.open(io.BytesIO(data)) as img:
                img.load()  # 헤더만 정상인 잘린 이미지도 거부
                width, height = img.size
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise ImportError_("IMAGE_DECODE_FAILED", "후보 이미지 전체를 읽을 수 없습니다", where) from exc
        for key, actual in (("width", width), ("height", height)):
            if key in c and (type(c[key]) is not int or c[key] != actual):
                raise ImportError_("IMAGE_SIZE_MISMATCH", "후보 크기가 실제 이미지와 다릅니다", where)
        original = by_path.get(file)
        explicit = c.get("original_ref")
        if explicit is not None:
            if (not isinstance(explicit, dict) or set(explicit) != {"폴더", "파일명"}
                    or not all(isinstance(v, str) for v in explicit.values())):
                raise ImportError_("INVALID_INPUT", "original_ref는 폴더·파일명이어야 합니다", where)
            linked = by_ref.get((explicit["폴더"], explicit["파일명"]))
            if linked is None:
                raise ImportError_("IMAGE_NOT_FOUND", "original_ref의 CSV 행이 없습니다", where)
            if original is not None and original is not linked:
                raise ImportError_("INVALID_INPUT", "동일 경로와 original_ref가 다른 원본을 가리킵니다", where)
            original = linked
        if original is not None and original["file"] == file:
            if (str(width), str(height)) != (original["csv"]["가로px"], original["csv"]["세로px"]):
                raise ImportError_("IMAGE_SIZE_MISMATCH", "CSV 크기가 실제 이미지와 다릅니다", where)
        _publication_value(c, where)
        try:
            locator = locators.to_object(c["locator"]) if c.get("locator") else {}
        except locators.LocatorError as exc:
            raise ImportError_(exc.code, "사진 locator를 읽을 수 없습니다", where) from exc
        if original is not None:
            original_hash = _sha256(original["file"])
            locator.update(original_path=original["rel"], original_sha256=original_hash,
                           original_hash_matches=(digest == original_hash))
        else:
            summary.candidates_without_original += 1
        images.append({"file": file, "data": data, "content_hash": digest,
                       "width": width, "height": height, "candidate": c, "locator": locator,
                       "owner": sid or IMAGE_PSEUDO_SOURCES[origin], "origin_kind": origin})
    return images


# ---------------- 적재 ----------------

def _publication_value(candidate: dict, where: str) -> int | None:
    """approved_for_external_use는 JSON boolean/null만. 문자열("false")·숫자·배열·객체는 입력 오류(전체 롤백)."""
    value = candidate.get("approved_for_external_use")
    if value is None:
        return None
    if type(value) is bool:   # bool은 int의 하위형이라 isinstance(1, bool)로는 못 거른다
        return int(value)
    raise ImportError_("INVALID_INPUT", f"approved_for_external_use는 true/false/null만 허용합니다(받은 값: {type(value).__name__})", where)


def _display_name(raw: dict) -> str:
    return raw.get("name") or raw.get("filename") or raw["source_id"]


def _mime(path: Path | None, filename: str) -> str:
    import mimetypes
    return mimetypes.guess_type(path.name if path else filename)[0] or "application/octet-stream"


def import_bundle(settings: Settings, bundle_root: Path, ingest_dir: Path | None = None, *,
                  with_mock: bool = False, with_demo: bool = False, dry_run: bool = False,
                  update_publication: bool = False) -> ImportSummary:
    bundle_root = Path(bundle_root)
    ingest_dir = Path(ingest_dir) if ingest_dir else bundle_root
    summary = ImportSummary(dry_run=dry_run, with_mock=with_mock, with_demo=with_demo)
    sources_path = ingest_dir / "sources.json"
    if not sources_path.is_file():
        raise ImportError_("INVALID_INPUT", "sources.json이 없습니다", str(ingest_dir.name))
    raw_sources = _read_json(sources_path)
    if not isinstance(raw_sources, list):
        raise ImportError_("INVALID_INPUT", "sources.json은 배열이어야 합니다", "sources.json")
    all_sources = {r.get("source_id"): r for r in raw_sources}
    if len(all_sources) != len(raw_sources):
        raise ImportError_("INVALID_INPUT", "source_id가 중복됩니다", "sources.json")

    sources: dict[str, _Source] = {}
    for raw in raw_sources:
        checked = _check_source(bundle_root, raw, with_mock, with_demo, summary)
        if checked is not None:
            sources[raw["source_id"]] = checked
    chunks_path = ingest_dir / "company_chunks.jsonl"
    _check_chunks(_read_jsonl(chunks_path) if chunks_path.is_file() else [], sources, all_sources)
    images = _read_images(bundle_root, ingest_dir, sources, all_sources, with_mock, with_demo, summary)

    with connect(settings.db_path, immediate=True) as conn:
        stamp = to_iso(now())
        to_add: list[_Source] = []
        for sid, src in sources.items():
            existing = conn.execute("SELECT content_hash, hash_verified, origin_kind FROM sources WHERE source_id=? AND scope='registered'",
                                    (sid,)).fetchone()
            if existing is not None:
                if existing["origin_kind"] != src.origin_kind:
                    raise ImportError_("SOURCE_ORIGIN_CONFLICT", "같은 source_id의 출처 구분을 바꿀 수 없습니다", sid)
                if (src.hash_verified and existing["hash_verified"] and src.package_hash != existing["content_hash"]):
                    raise ImportError_("SOURCE_ID_CONFLICT", "같은 source_id인데 검증된 원본 해시가 다릅니다", sid)
                summary.already_present += 1
                continue
            to_add.append(src)
            if not src.raw.get("use_as_company_evidence", True):
                summary.excluded_from_evidence += 1

        pseudo_origins = {e["origin_kind"] for e in images if e["candidate"].get("source_id") is None}
        for origin in pseudo_origins:
            pseudo_id = IMAGE_PSEUDO_SOURCES[origin]
            existing = conn.execute("SELECT scope, origin_kind FROM sources WHERE source_id=?", (pseudo_id,)).fetchone()
            if pseudo_id in sources or (existing and (existing["scope"] != "registered" or existing["origin_kind"] != origin)):
                raise ImportError_("SOURCE_ORIGIN_CONFLICT", "이미지 묶음 자료의 출처 구분이 충돌합니다", pseudo_id)
        # 충돌은 파일을 복사하거나 공개 허가를 갱신하기 전에 전체 검사한다.
        for entry in images:
            c = entry["candidate"]
            existing = conn.execute(
                "SELECT source_id, content_hash FROM assets WHERE photo_id=? AND scope='registered' AND deleted_at IS NULL",
                (c["photo_id"],)).fetchone()
            if existing and (existing["source_id"] != entry["owner"] or existing["content_hash"] != entry["content_hash"]):
                raise ImportError_("PHOTO_ID_CONFLICT", "같은 photo_id의 자료 또는 바이트 해시가 다릅니다", c["photo_id"])
        from app.services import db_history

        if db_history.enabled(conn):
            # source_id 없는 사진은 고정된 가상 묶음 하나에 속한다. 이미 읽기 실행에
            # 연결한 묶음에 새 사진을 덧붙이면 run_id=NULL 사진이 목록에서 사라지거나
            # 과거 입력의 사진 집합이 바뀌므로, 파일 복사·공개 허가 갱신 전에 거부한다.
            for entry in images:
                candidate = entry["candidate"]
                if candidate.get("source_id") is not None:
                    continue
                owner = conn.execute("SELECT current_run_id FROM sources WHERE source_id=?",
                                     (entry["owner"],)).fetchone()
                if owner is None or owner["current_run_id"] is None:
                    continue
                existing = conn.execute(
                    "SELECT 1 FROM assets WHERE photo_id=? AND scope='registered' AND deleted_at IS NULL",
                    (candidate["photo_id"],)).fetchone()
                if existing is None:
                    raise ImportError_(
                        "SOURCE_HISTORY_CONFLICT",
                        "이미 기록된 독립 사진 묶음에는 사진을 추가할 수 없습니다. "
                        "새 source_id가 있는 자료 묶음으로 등록하거나 새 DB에 전체 재적재하세요.",
                        candidate["photo_id"])
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
                "use_as_company_evidence, hash_verified, hash_note, is_mock, imported_at, note, origin_kind, role) "
                "VALUES (?, NULL, 1, 'registered', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'evidence')",
                (sid, _display_name(raw), _mime(src.file, raw.get("filename") or ""),
                 src.file.stat().st_size if src.file else 0, "company", parse_status, int(has_text), int(has_images),
                 rel, src.package_hash or "", json.dumps(warnings, ensure_ascii=False), stamp,
                 raw.get("origin_group") or sid,
                 raw.get("document_date"), int(bool(raw.get("document_date_verified", False))),
                 raw.get("date_from_filename"), raw.get("extraction_method"),
                 int(bool(raw.get("use_as_company_evidence", True))), int(src.hash_verified), src.hash_note,
                 int(src.origin_kind == "mock"), stamp, raw.get("note"), src.origin_kind))
            for ordinal, row in enumerate(src.chunks, start=1):
                conn.execute(
                    "INSERT INTO segments (segment_id, source_id, source_version, session_id, ordinal, locator_json, text, "
                    "created_at, chunk_id, evidence_status, document_date, extraction_method) "
                    "VALUES (?, ?, 1, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (f"seg_{uuid.uuid4().hex[:16]}", sid, ordinal, json.dumps(row["_locator"], ensure_ascii=False),
                     row["text"], stamp, row["chunk_id"], row["evidence_status"],
                     row.get("document_date") or raw.get("document_date") or raw.get("date_from_filename"),
                     row.get("extraction_method")))

        for origin in pseudo_origins:
            pseudo_id = IMAGE_PSEUDO_SOURCES[origin]
            if not dry_run and conn.execute("SELECT 1 FROM sources WHERE source_id=?", (pseudo_id,)).fetchone() is None:
                label = {"real": "", "mock": "[MOCK] ", "demo": "[시연] "}[origin]
                conn.execute(
                    "INSERT INTO sources (source_id, session_id, source_version, scope, name, mime_type, size_bytes, kind, "
                    "parse_status, text_available, image_available, stored_path, content_hash, created_at, imported_at, origin_kind, is_mock, role) "
                    "VALUES (?, NULL, 1, 'registered', ?, 'text/csv', 0, 'photo', "
                    "'complete', 0, 1, 'registered/images', '', ?, ?, ?, ?, 'evidence')",
                    (pseudo_id, label + "이미지 후보 목록", stamp, stamp, origin, int(origin == "mock")))
        added_ids = {s.raw["source_id"] for s in to_add}
        for entry in images:
            c = entry["candidate"]
            owner = entry["owner"]
            photo_id = c["photo_id"]
            existing_asset = conn.execute(
                "SELECT asset_id, source_id, content_hash, approved_for_external_use FROM assets "
                "WHERE photo_id=? AND scope='registered' AND deleted_at IS NULL", (photo_id,)).fetchone()
            if existing_asset is not None:
                # 같은 사진인지 확인(source_id + 바이트 해시). 파일 중복 생성은 없고 공개 허가만 명시적 경로로 갱신한다.
                where = f"photo_candidates.json {photo_id}"
                if existing_asset["source_id"] != owner or existing_asset["content_hash"] != entry["content_hash"]:
                    raise ImportError_("PHOTO_ID_CONFLICT", "같은 photo_id인데 자료(source_id) 또는 바이트 해시가 다릅니다", where)
                new_value = _publication_value(c, where)
                if new_value != existing_asset["approved_for_external_use"]:
                    if update_publication and not dry_run:
                        from app.services import publication as publication_service

                        conn.execute("UPDATE assets SET approved_for_external_use=? WHERE asset_id=?", (new_value, existing_asset["asset_id"]))
                        counts = publication_service.on_publication_changed(conn, existing_asset["asset_id"],
                                                                             existing_asset["approved_for_external_use"], new_value)
                        summary.publication_updated += 1
                        summary.approvals_invalidated += counts["approvals_invalidated"]
                        summary.exports_finalized += counts["exports_finalized"]
                    else:
                        summary.publication_pending += 1
                continue
            if c.get("source_id") is not None and owner not in added_ids:
                continue  # 이미 있는 자료의 사진은 다시 넣지 않는다
            width, height = entry["width"], entry["height"]
            summary.added_assets += 1
            if dry_run:
                continue
            rel = f"registered/images/{photo_id}{entry['file'].suffix.lower()}"
            (settings.private_runs_dir / rel).write_bytes(entry["data"])
            conn.execute(
                "INSERT INTO assets (asset_id, source_id, source_version, scope, session_id, origin, mime_type, width, height, "
                "content_hash, status, stored_path, created_at, expires_at, photo_id, caption_candidate, selected_as_candidate, "
                "approved_for_external_use, photo_locator_json) "
                "VALUES (?, ?, 1, 'registered', NULL, 'source_image', ?, ?, ?, ?, 'ready', ?, ?, NULL, ?, ?, ?, ?, ?)",
                (f"asset_{uuid.uuid4().hex[:16]}", owner, _mime(entry["file"], ""), width, height,
                 entry["content_hash"], rel, stamp, photo_id, c.get("caption_candidate"),
                 int(bool(c.get("selected_as_candidate"))),
                 _publication_value(c, f"photo_candidates.json {photo_id}"),
                 json.dumps(entry.get("locator"), ensure_ascii=False) if entry.get("locator") else None))

        if not dry_run:
            from app.services import db_history

            for source_id in added_ids | {IMAGE_PSEUDO_SOURCES[o] for o in pseudo_origins}:
                # 재적재로 이미 존재하는 실행은 중복 생성하지 않는다.
                if db_history.enabled(conn) and conn.execute(
                        "SELECT current_run_id FROM sources WHERE source_id=?", (source_id,)).fetchone()[0] is None:
                    db_history.finish_extraction(conn, source_id, method="registered_bundle")

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
        with_demo: bool = False, dry_run: bool = False, update_publication: bool = False) -> ImportSummary:
    """import_bundle의 편의 함수. dry-run은 검사·집계만 하고 아무것도 쓰지 않는다."""
    try:
        return import_bundle(settings, bundle_root, ingest_dir, with_mock=with_mock, with_demo=with_demo, dry_run=dry_run,
                             update_publication=update_publication)
    except _DryRun as exc:
        return exc.summary


# ---------------- 조회 ----------------

def list_registered(conn: Connection, kind: str | None = None, *, include_demo: bool = False) -> list[SourceOut]:
    from app.services.sources import _row_to_out  # 같은 출력 모양

    query = "SELECT * FROM sources WHERE scope='registered' AND deleted_at IS NULL"
    params: list[Any] = []
    if not include_demo:
        query += " AND origin_kind!='demo'"
    if kind:
        query += " AND kind=?"
        params.append(kind)
    query += " ORDER BY source_id"
    return [_row_to_out(conn, r) for r in conn.execute(query, params)]
