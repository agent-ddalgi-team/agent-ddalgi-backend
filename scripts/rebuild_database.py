"""보관한 등록 자료로 별도 ERD v2 DB를 만든다. 기존 DB/설정/이력은 변경하지 않는다.

  .venv\\Scripts\\python.exe -X utf8 scripts/rebuild_database.py --target-dir private_runs/erd_v2

대상 폴더는 반드시 없어야 한다. 실패한 폴더는 조사할 수 있도록 남기며, 재실행은
다른 새 폴더를 지정한다. 성공해도 .env는 자동 변경하지 않는다.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.services import registered  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

# task_backend.md 6.20에서 바이트/화면을 검증하고 승인한 서비스용 PDF 한 건만 허용한다.
CATALOG_ID = "REAL_DDALGI_V1_CATALOG"
CATALOG_ORIGINAL_HASH = "b6dc4206f27863a3aca5857ae46067f4ed4a5f39aee6753dbbdbb65ff9975847"
CATALOG_SERVICE_HASH = "0cf6b5a389362d5fb9d6d8b6d7075930209b44e76ebfc29edeb9b96c50e205ee"
BUNDLES = {"real": "ddalgi_real_v1", "demo": "ddalgi_demo_v2"}
CSV_FIELDS = ["폴더", "파일명", "PPT페이지", "원본이미지", "처리", "가로px", "세로px"]


class RebuildError(ValueError):
    """기존 자료를 보존하고 재적재를 중단해야 하는 조건."""


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _child(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip() or Path(relative).is_absolute():
        raise RebuildError("묶음 내부의 상대 경로가 필요합니다.")
    candidate = (root / relative).resolve()
    if candidate == root.resolve() or root.resolve() not in candidate.parents:
        raise RebuildError("자료 경로가 허용된 폴더 밖을 가리킵니다.")
    return candidate


def _component(value: Any) -> str:
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or any(c in value for c in '/\\:*?"<>|\0') or value.endswith((" ", "."))):
        raise RebuildError("자료 또는 사진 ID가 올바르지 않습니다.")
    return value


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, data: Any) -> None:
    # 배타적 생성으로 뜻하지 않은 기존 파일 덮어쓰기도 차단한다.
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _copy_verified(source: Path, destination: Path, expected: str | None = None) -> str:
    if not source.is_file():
        raise RebuildError("필요한 보관 파일을 찾지 못했습니다.")
    digest = _digest(source)
    if expected and digest != expected.lower():
        raise RebuildError("보관 파일의 해시가 기록과 다릅니다.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not destination.is_file() or _digest(destination) != digest:
            raise RebuildError("새 묶음의 파일 경로가 서로 충돌합니다.")
    else:
        with source.open("rb") as src, destination.open("xb") as dst:
            shutil.copyfileobj(src, dst)
    if _digest(destination) != digest:
        raise RebuildError("자료 복사 후 해시 검사가 실패했습니다.")
    return digest


def _read_asset_metadata(legacy: Path) -> dict[str, dict[str, Any]]:
    """삭제된 중복 원본 복구에 필요한 사진 경로/해시만 읽는다. 세션 이력은 읽지 않는다."""
    path = legacy / "app.sqlite3"
    if not path.is_file():
        return {}
    wal = path.with_name(path.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise RebuildError("기존 DB에 WAL 내용이 있습니다. 서버를 종료하고 체크포인트 후 다시 실행하세요.")
    # WAL이 없거나 빈 것을 확인했다. immutable은 읽는 과정의 보조 파일 생성도 막는다.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT photo_id, stored_path, content_hash, photo_locator_json FROM assets "
            "WHERE scope='registered' AND photo_id IS NOT NULL AND deleted_at IS NULL"
        )
        result = {}
        for row in rows:
            if row["photo_id"] in result:
                raise RebuildError("기존 사진 ID가 중복되어 원본을 안전하게 선택할 수 없습니다.")
            result[row["photo_id"]] = dict(row)
        return result


def _prepare_sources(legacy: Path, bundle: Path, package: Path, origin: str) -> list[dict]:
    sources = _read_json(bundle / "06_개발전달" / "sources.json")
    if not isinstance(sources, list):
        raise RebuildError("sources.json은 배열이어야 합니다.")
    for raw in sources:
        if raw.get("status") != ("ready" if origin == "real" else "demo"):
            continue  # planning_reference 및 mock 등은 기존 적재기의 제외 규칙을 유지한다.
        sid = _component(raw.get("source_id"))
        old_relative = raw.get("path")
        original = _child(bundle, old_relative) if old_relative else None
        suffix = Path(old_relative or raw.get("filename") or "").suffix.lower()
        retained = _child(legacy, f"registered/{sid}{suffix}")
        # 승인된 서비스용 사본이 있으면 이를 사용한다. 큰 편집용 PDF로 되돌리지 않는다.
        source = retained if retained.is_file() else original
        if source is None or not source.is_file():
            raise RebuildError(f"등록 자료 {sid}의 보관 파일이 없습니다.")
        actual = _digest(source)
        supplied = raw.get("sha256")
        supplied = supplied.lower() if isinstance(supplied, str) else None
        optimized = (origin == "real" and sid == CATALOG_ID
                     and supplied == CATALOG_ORIGINAL_HASH and actual == CATALOG_SERVICE_HASH)
        if supplied and actual != supplied and not optimized:
            raise RebuildError(f"등록 자료 {sid}의 원본 해시가 일치하지 않습니다.")
        relative = f"sources/{sid}{suffix}"
        _copy_verified(source, _child(package, relative), actual)
        raw["path"] = relative
        raw["available_in_package"] = True
        raw["rebuild_provenance"] = {
            "kind": "retained_service_copy" if source == retained else "original_package_file",
            "original_package_path": old_relative,
            "original_package_sha256": supplied,
            "copied_sha256": actual,
        }
        if optimized:
            raw["sha256"] = actual
            raw["sha256_note"] = None  # 새 묶음의 검증 대상은 실제 서비스용 파일이다.
            raw["note"] = (str(raw.get("note") or "") + "\n"
                           "2026-09-28 승인된 서비스용 PDF: Illustrator PieceInfo 편집 데이터만 제외. "
                           f"편집용 원본 SHA-256={CATALOG_ORIGINAL_HASH}; "
                           "보이는 내용·쪽수·픽셀 일치 검증은 task_backend.md 6.20 참조.").strip()
            raw["rebuild_provenance"]["transformation"] = "approved_catalog_pieceinfo_removal"
    _write_json(package / "sources.json", sources)
    chunks = bundle / "06_개발전달" / "company_chunks.jsonl"
    if chunks.is_file():
        _copy_verified(chunks, package / chunks.name)
    return sources


def _prepare_photos(legacy: Path, bundle: Path, package: Path,
                    old_assets: dict[str, dict[str, Any]]) -> int:
    ingest = bundle / "06_개발전달"
    candidates_path = ingest / "photo_candidates.json"
    candidates = _read_json(candidates_path) if candidates_path.is_file() else []
    if not isinstance(candidates, list):
        raise RebuildError("photo_candidates.json은 배열이어야 합니다.")
    csv_path = ingest / "00_이미지목록.csv"
    if not csv_path.is_file():
        csv_path = bundle / "05_이미지" / "00_이미지목록.csv"
    rows: dict[tuple[str, str], dict] = {}
    if csv_path.is_file():
        with csv_path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != CSV_FIELDS:
                raise RebuildError("이미지 목록 CSV 열 구성이 올바르지 않습니다.")
            for row in reader:
                key = (row["폴더"], row["파일명"])
                if key in rows:
                    raise RebuildError("이미지 목록의 원본 경로가 중복됩니다.")
                rows[key] = row
    selected_rows: dict[tuple[str, str], dict] = {}
    prepared = []
    for candidate in candidates:
        if candidate.get("mock"):
            continue
        photo_id = _component(candidate.get("photo_id"))
        original_relative = candidate.get("path")
        file = _child(bundle, original_relative)
        old = old_assets.get(photo_id, {})
        if not file.is_file():
            old_path = old.get("stored_path") or f"registered/images/{photo_id}{file.suffix.lower()}"
            file = _child(legacy, old_path)
        actual = _digest(file) if file.is_file() else None
        if actual is None:
            raise RebuildError(f"사진 후보 {photo_id}의 보관 파일이 없습니다.")
        for expected in (candidate.get("sha256"), old.get("content_hash")):
            if expected and actual != str(expected).lower():
                raise RebuildError(f"사진 후보 {photo_id}의 해시가 일치하지 않습니다.")
        # 기존 허가/캡션/선택/locator 필드를 그대로 보존하고 파일 위치만 바꾼다.
        relative = f"photos/{photo_id}{file.suffix.lower()}"
        _copy_verified(file, _child(package, relative), actual)
        candidate["path"] = relative
        explicit = candidate.get("original_ref")
        if explicit is None:
            matches = [key for key in rows if
                       (registered.IMAGE_CSV_ROOT / key[0] / key[1]).as_posix() == original_relative]
            if matches:
                explicit = dict(zip(("폴더", "파일명"), matches[0]))
                candidate["original_ref"] = explicit
        if explicit is not None:
            if not isinstance(explicit, dict) or set(explicit) != {"폴더", "파일명"}:
                raise RebuildError("사진의 original_ref 형식이 올바르지 않습니다.")
            key = (explicit["폴더"], explicit["파일명"])
            if key not in rows:
                raise RebuildError(f"사진 후보 {photo_id}가 참조하는 CSV 행이 없습니다.")
            original_rel = (registered.IMAGE_CSV_ROOT / key[0] / key[1]).as_posix()
            original = _child(bundle, original_rel)
            locator = json.loads(old.get("photo_locator_json") or "{}")
            known_hash = locator.get("original_sha256") if locator.get("original_path") == original_rel else None
            if not original.is_file():
                # 후보가 편집본이면 원본으로 둔갑시키지 않는다. 기록된 원본 해시가 같을 때만 복구.
                if known_hash != actual:
                    raise RebuildError(f"사진 후보 {photo_id}의 별도 원본을 복구할 수 없습니다.")
                original = file
            _copy_verified(original, _child(package, original_rel), known_hash)
            selected_rows[key] = rows[key]
        prepared.append(candidate)
    _write_json(package / "photo_candidates.json", prepared)
    # 새 묶음이 실제 사용하는 원본 연결만 보존한다. 후보/허가를 추가 생성하지 않는다.
    if selected_rows:
        with (package / "00_이미지목록.csv").open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(selected_rows.values())
    return len(prepared)


def _validate_target(target: Path, legacy: Path) -> None:
    if target == legacy or target in legacy.parents:
        raise RebuildError("새 대상은 기존 저장소 자체 또는 상위 폴더가 될 수 없습니다.")
    for protected in (legacy / "registered", legacy / "registered_src"):
        protected = protected.resolve()
        if target == protected or protected in target.parents:
            raise RebuildError("새 대상은 보관 자료 폴더 안에 만들 수 없습니다.")
    if target.exists():
        raise RebuildError("대상 폴더가 이미 있습니다. 덮어쓰지 않고 중단합니다.")
    if not legacy.is_dir():
        raise RebuildError("기존 저장소 폴더가 없습니다.")


def verify_database(path: Path) -> dict[str, Any]:
    from app.db import ORM_SCHEMA_VERSION, connect
    from app.orm_models import Base

    with connect(path) as conn:
        actual = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name != 'alembic_version'")}
        if actual != set(Base.metadata.tables) or len(actual) != 25:
            raise RebuildError("생성한 DB가 ERD의 25개 테이블과 일치하지 않습니다.")
        if conn.execute("PRAGMA user_version").fetchone()[0] != ORM_SCHEMA_VERSION:
            raise RebuildError("생성한 DB의 스키마 버전이 올바르지 않습니다.")
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RebuildError("새 DB 무결성 검사에 실패했습니다.")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RebuildError("새 DB에 외래 키 위반이 있습니다.")
        counts = {name: conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] for name in sorted(actual)}
        retained_tables = {"sources", "source_versions", "extraction_runs", "segments", "assets", "registered_imports"}
        if any(value for name, value in counts.items() if name not in retained_tables):
            raise RebuildError("새 DB에 재적재 범위 밖의 세션 또는 작업 이력이 들어 있습니다.")
        if conn.execute("SELECT count(*) FROM sources WHERE scope!='registered' OR origin_kind='mock'").fetchone()[0]:
            raise RebuildError("새 DB에 세션 자료 또는 mock 자료가 들어 있습니다.")
        if not (counts["sources"] == counts["source_versions"] == counts["extraction_runs"]):
            raise RebuildError("재적재한 자료의 최초 버전 또는 읽기 이력이 누락되었습니다.")
        if conn.execute(
            "SELECT count(*) FROM sources s LEFT JOIN extraction_runs r ON r.run_id=s.current_run_id "
            "WHERE r.run_id IS NULL OR r.source_id!=s.source_id OR r.source_version!=s.source_version"
        ).fetchone()[0]:
            raise RebuildError("자료의 현재 읽기 이력 연결이 올바르지 않습니다.")
        if any(conn.execute(f'SELECT count(*) FROM "{name}" WHERE run_id IS NULL').fetchone()[0]
               for name in ("segments", "assets")):
            raise RebuildError("구간 또는 사진의 읽기 이력 연결이 누락되었습니다.")
        return {"table_count": len(actual), "schema_version": ORM_SCHEMA_VERSION, "counts": counts,
                "quick_check": "ok", "foreign_key_violations": 0}


def rebuild(target_dir: Path, legacy_root: Path) -> dict[str, Any]:
    """호출자가 새 경로를 지정해야 한다. 실패 시 기존 자료와 설정은 여전히 그대로다."""
    from app.db import init_orm_db

    target, legacy = target_dir.resolve(), legacy_root.resolve()
    _validate_target(target, legacy)
    old_db = legacy / "app.sqlite3"
    old_digest = _digest(old_db) if old_db.is_file() else None
    old_assets = _read_asset_metadata(legacy)
    target.mkdir(parents=True, exist_ok=False)
    packages = []
    for origin, name in BUNDLES.items():
        bundle = _child(legacy, f"registered_src/{origin}/{name}")
        package = target / "import_packages" / origin
        package.mkdir(parents=True, exist_ok=False)
        _prepare_sources(legacy, bundle, package, origin)
        _prepare_photos(legacy, bundle, package, old_assets)
        packages.append((origin, package))
    settings = Settings(private_runs_dir=target, db_path=target / "app.sqlite3",
                        agent_mode="mock", cleanup_sweep_interval_s=0)
    init_orm_db(settings.db_path, settings.private_runs_dir)
    # 두 묶음 모두 먼저 검사한다. 실제 적재 오류가 발생하면 새 대상만 남고 설정 전환은 없다.
    for origin, package in packages:
        registered.run(settings, package, with_demo=(origin == "demo"), dry_run=True)
    summaries = {origin: registered.run(settings, package, with_demo=(origin == "demo")).as_dict()
                 for origin, package in packages}
    verification = verify_database(settings.db_path)
    if old_digest is not None and _digest(old_db) != old_digest:
        raise RebuildError("작업 중 기존 DB가 변경되었습니다. 새 DB로 전환하지 말고 확인하세요.")
    old_wal = old_db.with_name(old_db.name + "-wal")
    if old_wal.exists() and old_wal.stat().st_size:
        raise RebuildError("작업 중 기존 DB에 WAL 내용이 생겼습니다. 새 DB로 전환하지 말고 확인하세요.")
    return {"result": "OK", "imports": summaries, **verification,
            "legacy_database_unchanged": True, "settings_changed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="기존 DB를 보존하며 별도 ERD v2 DB에 real/demo 자료를 재적재한다.")
    parser.add_argument("--target-dir", required=True, type=Path, help="새 저장소 폴더. 이미 있으면 중단")
    parser.add_argument("--legacy-root", default=ROOT / "private_runs", type=Path, help="기존 보관 자료 폴더")
    args = parser.parse_args()
    try:
        result = rebuild(args.target_dir, args.legacy_root)
    except (ValueError, registered.ImportError_, OSError, sqlite3.Error,
            SQLAlchemyError, RuntimeError) as exc:
        # 원문/비밀값을 출력하지 않는다. 실패 폴더를 자동 삭제하지도 않는다.
        message = str(exc) if isinstance(exc, RebuildError) else "준비 또는 적재에 실패했습니다. 기존 DB와 설정은 변경하지 않았습니다."
        print(json.dumps({"result": "ERROR", "code": getattr(exc, "code", type(exc).__name__),
                          "message": message}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
