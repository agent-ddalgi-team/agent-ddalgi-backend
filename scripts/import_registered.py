"""등록 자료 적재 CLI.

  uv run python scripts/import_registered.py --source-dir <묶음 루트> [--ingest-dir <JSON 폴더>] [--with-mock] [--dry-run]

- --source-dir: sources.path·photo_candidates.path의 기준 폴더(자료 묶음 루트).
- --ingest-dir: sources.json 등이 있는 폴더. 생략하면 --source-dir와 같다.
  mock 묶음: --source-dir tests/fixtures/ddalgi_mock_bundle_v1/ingest --with-mock
  실제 묶음: --source-dir private_runs/registered_src/real/<루트> [--ingest-dir .../06_개발전달]
- --with-mock 없이는 status=mock 자료를 넣지 않는다. --dry-run은 검사·집계만 하고 아무것도 쓰지 않는다.
- --update-publication: 이미 있는 등록 사진의 공개 허가(approved_for_external_use)를 묶음 값으로 갱신한다(BE-08).
  같은 사진(source_id·photo_id·바이트 해시)만 갱신하며, 관련 승인 무효화·활성 Export 확정 실패까지 같은 트랜잭션으로 처리한다.
- 출력은 건수 요약뿐이다. 원문·파일 경로·해시는 찍지 않는다(실제 회사 자료 보호).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.services import registered  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="등록 자료 묶음을 DB에 적재한다.")
    parser.add_argument("--source-dir", required=True, help="자료 묶음 루트(path 기준 폴더)")
    parser.add_argument("--ingest-dir", default=None, help="sources.json 등이 있는 폴더(기본: --source-dir)")
    parser.add_argument("--with-mock", action="store_true", help="status=mock 자료도 적재")
    parser.add_argument("--dry-run", action="store_true", help="검사·집계만 하고 쓰지 않음")
    parser.add_argument("--update-publication", action="store_true", help="기존 등록 사진의 공개 허가를 묶음 값으로 갱신")
    args = parser.parse_args()

    settings = load_settings()
    init_db(settings.db_path, settings.private_runs_dir)
    try:
        summary = registered.run(settings, Path(args.source_dir), Path(args.ingest_dir) if args.ingest_dir else None,
                                 with_mock=args.with_mock, dry_run=args.dry_run, update_publication=args.update_publication)
    except registered.ImportError_ as exc:
        print(json.dumps({"result": "ERROR", "code": exc.code, "message": exc.message, "where": exc.where},
                         ensure_ascii=False))
        return 1
    print(json.dumps({"result": "OK", **summary.as_dict()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
