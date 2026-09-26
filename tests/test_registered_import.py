"""등록 자료 적재: mock 묶음 규칙, ingestion_cases 10건, locator 케이스, 세션 연결, 업로드용 PDF 2건.

입력은 전부 가상(tests/fixtures/ddalgi_mock_bundle_v1). 실제 회사 자료 없음.
"""
from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.config import Settings
from app.db import connect
from app.services import locators, registered

BUNDLE = Path(__file__).parent / "fixtures" / "ddalgi_mock_bundle_v1"
INGEST = BUNDLE / "ingest"
UPLOAD_PDF = Path(__file__).parent / "fixtures" / "mock_upload_pdf"
BRIEF = {"purpose": "테스트", "emphasis": [], "direction": "balanced", "target_pages": 4, "photo_preference": "balanced"}


@pytest.fixture
def settings(tmp_path):
    return Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "t.sqlite3")


@pytest.fixture
def app(settings):
    return create_app(settings)


def _import(settings, root=INGEST, **kw):
    return registered.run(settings, root, **kw)


def _mutated_bundle(tmp_path, mutate) -> Path:
    """ingest/ 사본을 만들고 mutate(root)로 변형한다."""
    root = tmp_path / "bundle"
    shutil.copytree(INGEST, root)
    mutate(root)
    return root


def _edit_json(path: Path, fn):
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ---------------- 기본 적재 ----------------

def test_mock_bundle_without_flag_adds_nothing(settings, app):
    s = _import(settings)
    assert s.added_sources == 0 and s.added_segments == 0 and s.added_assets == 0 and s.skipped == {"mock": 8}


def test_mock_bundle_with_flag_matches_expected_counts(settings, app):
    s = _import(settings, with_mock=True)
    assert (s.added_sources, s.added_segments, s.added_assets) == (8, 24, 6), s.as_dict()
    assert s.excluded_from_evidence == 1 and s.hash_unverified == 0 and s.errors == []
    with connect(settings.db_path) as conn:
        rows = {r["source_id"]: r for r in conn.execute("SELECT * FROM sources WHERE scope='registered'")}
        assert set(rows) == {f"MOCK0{i}" for i in range(1, 9)}
        assert all(r["session_id"] is None and r["is_mock"] == 1 and r["hash_verified"] == 1 for r in rows.values())
        assert rows["MOCK02"]["document_date"] == "2017" and rows["MOCK01"]["document_date"] == "2026-08-20"
        assert rows["MOCK06"]["use_as_company_evidence"] == 0
        assert rows["MOCK03"]["origin_group"] == rows["MOCK04"]["origin_group"] == "MOCK_CERT_Q"
        assert rows["MOCK01"]["stored_path"] == "registered/MOCK01.txt" and rows["MOCK01"]["name"].startswith("[MOCK]")
        assert rows["MOCK07"]["image_available"] == 1 and rows["MOCK07"]["text_available"] == 0 and rows["MOCK07"]["parse_status"] == "complete"
        assert rows["MOCK08"]["parse_status"] == "failed" and "NO_USABLE_TEXT" in rows["MOCK08"]["warnings_json"]
        seg = conn.execute("SELECT * FROM segments WHERE chunk_id='MOCK01_C001'").fetchone()
        assert seg["text"] == "[MOCK] 회사명: 예시 회사" and json.loads(seg["locator_json"]) == {"line_start": 1, "line_end": 1}
        assert seg["evidence_status"] == "자료에 기재됨" and seg["document_date"] == "2026-08-20" and seg["session_id"] is None
        seg2 = conn.execute("SELECT document_date, evidence_status FROM segments WHERE chunk_id='MOCK02_C001'").fetchone()
        assert seg2["document_date"] == "2017" and seg2["evidence_status"] == "자료에 기재됨 (2017년 카다로그)"
        assets = conn.execute("SELECT * FROM assets WHERE scope='registered' ORDER BY photo_id").fetchall()
        assert [a["photo_id"] for a in assets] == [f"MOCK_IMG0{i}" for i in range(1, 7)]
        assert all(a["source_id"] == "MOCK07" and a["session_id"] is None and a["status"] == "ready" for a in assets)
        assert assets[0]["width"] == 960 and assets[2]["height"] == 960
        assert json.loads(assets[0]["photo_locator_json"]) == {"slide": 1}
        assert (settings.private_runs_dir / "registered" / "images" / "MOCK_IMG01.png").is_file()
        # images/ 사본은 적재하지 않음
        assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM registered_imports").fetchone()[0] == 1


def test_second_import_adds_zero(settings, app):
    _import(settings, with_mock=True)
    s = _import(settings, with_mock=True)
    assert s.added_sources == 0 and s.added_segments == 0 and s.added_assets == 0 and s.already_present == 8
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 8
        assert conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 24


def test_third_run_without_flag_neither_deletes_nor_adds_mock(settings, app):
    _import(settings, with_mock=True)
    _import(settings, with_mock=True)
    s = _import(settings)  # 세 번째: --with-mock 없음
    assert s.added_sources == 0 and s.already_present == 0 and s.skipped == {"mock": 8}
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources WHERE scope='registered' AND deleted_at IS NULL").fetchone()[0] == 8
        assert conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 24
        assert conn.execute("SELECT COUNT(*) FROM assets WHERE deleted_at IS NULL").fetchone()[0] == 6


def test_dry_run_writes_nothing(settings, app):
    s = _import(settings, with_mock=True, dry_run=True)
    assert (s.added_sources, s.added_segments, s.added_assets) == (8, 24, 6) and s.dry_run
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM registered_imports").fetchone()[0] == 0
    assert not (settings.private_runs_dir / "registered").exists()


def test_single_entry_inputs_are_not_merged(settings, app, tmp_path):
    """mock_source_entry.json/mock_chunks.jsonl은 대체 입력. 적재기는 sources.json만 읽으므로 합쳐지지 않는다."""
    s = _import(settings, with_mock=True)
    assert s.added_sources == 8


# ---------------- 실제 팀 형식(status 다양) ----------------

def test_real_like_statuses_and_hash_note(settings, app, tmp_path):
    def mutate(root: Path):
        def fn(data):
            for item in data:
                item["mock"] = False
                item["name"] = item["name"].replace("[MOCK] ", "")
                item["status"] = "ready"
            data[1]["status"] = "missing_original"; data[1]["path"] = None; data[1]["available_in_package"] = False
            data[2]["status"] = "received_by_team_not_in_package"; data[2]["path"] = None; data[2]["available_in_package"] = False
            data[3]["status"] = "planning_reference"
            data[4]["sha256_note"] = "원본 파일의 SHA-256(축소본 제공)"  # 검증 생략
            data[5]["sha256"] = None
        _edit_json(root / "sources.json", fn)
        # 사진은 candidates에서 mock 표시를 지우고 source_id를 없애 실제 형식처럼
        def fn2(data):
            for c in data:
                c.pop("mock", None); c.pop("source_id", None)
        _edit_json(root / "photo_candidates.json", fn2)
    root = _mutated_bundle(tmp_path, mutate)
    s = _import(settings, root)
    assert s.added_sources == 5 and s.skipped == {"missing_original": 1, "received_by_team_not_in_package": 1, "planning_reference": 1}
    assert s.hash_unverified == 2 and s.added_assets == 6
    with connect(settings.db_path) as conn:
        r5 = conn.execute("SELECT hash_verified, hash_note FROM sources WHERE source_id='MOCK05'").fetchone()
        assert r5["hash_verified"] == 0 and "SHA-256" in r5["hash_note"]
        r6 = conn.execute("SELECT hash_verified, hash_note FROM sources WHERE source_id='MOCK06'").fetchone()
        assert r6["hash_verified"] == 0 and r6["hash_note"] == "sha256 없음"
        assert conn.execute("SELECT is_mock FROM sources WHERE source_id='MOCK01'").fetchone()[0] == 0
        # source_id 없는 사진은 이미지 묶음 자료에 붙는다
        assert conn.execute("SELECT COUNT(*) FROM assets WHERE source_id=?", (registered.IMAGE_PSEUDO_SOURCE,)).fetchone()[0] == 6
    # 건너뛴 자료의 구간은 들어가지 않는다
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM segments WHERE source_id='MOCK02'").fetchone()[0] == 0


# ---------------- ingestion_cases.json 10건 ----------------

def _expect_error(settings, root, code, **kw):
    with pytest.raises(registered.ImportError_) as exc:
        registered.run(settings, root, with_mock=True, **kw)
    assert exc.value.code == code, exc.value
    with connect(settings.db_path) as conn:  # 아무것도 적재되지 않음
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0


def test_case_unknown_source(settings, app, tmp_path):
    root = _mutated_bundle(tmp_path, lambda r: (r / "company_chunks.jsonl").write_text(
        (r / "company_chunks.jsonl").read_text(encoding="utf-8").replace('"source_id": "MOCK05"', '"source_id": "MOCK_NOT_FOUND"', 1), encoding="utf-8"))
    _expect_error(settings, root, "UNKNOWN_SOURCE_ID")


def test_case_duplicate_chunk(settings, app, tmp_path):
    def mutate(r):
        p = r / "company_chunks.jsonl"
        lines = p.read_text(encoding="utf-8").splitlines()
        p.write_text("\n".join(lines + [lines[0]]) + "\n", encoding="utf-8")
    _expect_error(settings, _mutated_bundle(tmp_path, mutate), "DUPLICATE_CHUNK_ID")


def test_case_hash_mismatch(settings, app, tmp_path):
    root = _mutated_bundle(tmp_path, lambda r: _edit_json(r / "sources.json", lambda d: d[0].__setitem__("sha256", "0" * 64)))
    _expect_error(settings, root, "HASH_MISMATCH")


def test_case_id_same_hash_changed(settings, app, tmp_path):
    _import(settings, with_mock=True)
    def mutate(r):
        p = r / "originals" / "MOCK01.txt"
        p.write_text(p.read_text(encoding="utf-8") + "[MOCK] 추가 행\n", encoding="utf-8")
        import hashlib
        _edit_json(r / "sources.json", lambda d: d[0].__setitem__("sha256", hashlib.sha256(p.read_bytes()).hexdigest()))
    root = _mutated_bundle(tmp_path, mutate)
    with pytest.raises(registered.ImportError_) as exc:
        registered.run(settings, root, with_mock=True)
    assert exc.value.code == "SOURCE_ID_CONFLICT"
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 8  # 기존 유지


def test_case_missing_image_by_folder_and_filename(settings, app, tmp_path):
    """README v1.1: 옛 image_id/filename 대신 CSV의 폴더/파일명 기준."""
    def mutate(r):
        p = r / "00_이미지목록.csv"
        p.write_text(p.read_text(encoding="utf-8-sig").replace("01_제품,MOCK_IMG01.png", "01_제품,MOCK_MISSING.png"), encoding="utf-8-sig")
    _expect_error(settings, _mutated_bundle(tmp_path, mutate), "IMAGE_NOT_FOUND")


def test_case_path_escape_by_folder(settings, app, tmp_path):
    def mutate(r):
        p = r / "00_이미지목록.csv"
        p.write_text(p.read_text(encoding="utf-8-sig").replace("01_제품,MOCK_IMG01.png", "../../..,outside.png"), encoding="utf-8-sig")
    _expect_error(settings, _mutated_bundle(tmp_path, mutate), "PATH_OUTSIDE_BUNDLE")


def test_case_source_path_escape(settings, app, tmp_path):
    root = _mutated_bundle(tmp_path, lambda r: _edit_json(r / "sources.json", lambda d: d[0].__setitem__("path", "../outside.txt")))
    _expect_error(settings, root, "PATH_OUTSIDE_BUNDLE")


def test_case_unknown_evidence_status(settings, app, tmp_path):
    root = _mutated_bundle(tmp_path, lambda r: (r / "company_chunks.jsonl").write_text(
        (r / "company_chunks.jsonl").read_text(encoding="utf-8").replace('"evidence_status": "자료에 기재됨"', '"evidence_status": "auto_trust_everything"', 1), encoding="utf-8"))
    _expect_error(settings, root, "UNKNOWN_EVIDENCE_STATUS")


def test_case_mock_marker_mismatch(settings, app, tmp_path):
    root = _mutated_bundle(tmp_path, lambda r: _edit_json(r / "sources.json", lambda d: d[0].__setitem__("mock", False)))
    _expect_error(settings, root, "MOCK_MARKER_CONFLICT")


def test_case_unknown_status_and_chunk_text_mismatch(settings, app, tmp_path):
    root = _mutated_bundle(tmp_path, lambda r: _edit_json(r / "sources.json", lambda d: (d[0].__setitem__("status", "weird"), d[0].__setitem__("mock", False))))
    _expect_error(settings, root, "UNKNOWN_STATUS")
    root2 = _mutated_bundle(tmp_path / "b2", lambda r: (r / "company_chunks.jsonl").write_text(
        (r / "company_chunks.jsonl").read_text(encoding="utf-8").replace("[MOCK] 회사명: 예시 회사", "[MOCK] 회사명: 다른 회사", 1), encoding="utf-8"))
    _expect_error(settings, root2, "CHUNK_TEXT_MISMATCH")


# ---------------- locator ----------------

def test_locator_cases_from_bundle():
    for case in json.loads((BUNDLE / "quality" / "locator_cases.json").read_text(encoding="utf-8")):
        if "expected" in case:
            assert locators.to_object(case["input"]) == case["expected"], case
        else:
            with pytest.raises(locators.LocatorError) as exc:
                locators.to_object(case["input"])
            assert exc.value.code == case["error"], case


@pytest.mark.parametrize("value, expected", [
    ("PPT 12쪽 · 가상 제목", {"slide": 12}),
    ("PPT 16쪽 · image42 · 가상 공정", {"slide": 16}),
    ("카다로그 2쪽 · 가상 회사소개 · 연혁", {"page": 2}),
    ("TXT 2행", {"line_start": 2, "line_end": 2}),
    ("MD 문단 3", {"paragraph": 3}),
])
def test_locator_team_patterns(value, expected):
    assert locators.to_object(value) == expected


# ---------------- 세션 연결 ----------------

def _session_client(app):
    c = TestClient(app)
    sid = c.post("/api/v1/sessions", json={"brief": BRIEF}).json()["session_id"]
    return c, sid


def test_get_sources_lists_registered_only(settings, app):
    _import(settings, with_mock=True)
    c, sid = _session_client(app)
    c.post(f"/api/v1/sessions/{sid}/sources", files=[("files", ("a.txt", io.BytesIO(b"x")))])
    items = c.get("/api/v1/sources").json()["items"]
    assert [i["source_id"] for i in items] == [f"MOCK0{i}" for i in range(1, 9)]
    m1 = next(i for i in items if i["source_id"] == "MOCK01")
    assert m1["scope"] == "registered" and m1["session_id"] is None and m1["is_mock"] and m1["expires_at"] is None
    assert m1["document_date"] == "2026-08-20" and len(m1["usable_segment_ids"]) == 11 and m1["name"].startswith("[MOCK]")
    m6 = next(i for i in items if i["source_id"] == "MOCK06")
    assert m6["use_as_company_evidence"] is False
    m7 = next(i for i in items if i["source_id"] == "MOCK07")
    assert len(m7["asset_ids"]) == 6 and m7["image_available"]
    assert c.get("/api/v1/sources?kind=photo").json()["items"] == []
    # 세션 목록에는 등록 자료가 섞이지 않는다
    assert [i["scope"] for i in c.get(f"/api/v1/sessions/{sid}/sources").json()["items"]] == ["session"]
    assert TestClient(app).get("/api/v1/sources").status_code == 401


def test_registered_selection_preflight_evidence_and_asset(settings, app):
    _import(settings, with_mock=True)
    c, sid = _session_client(app)
    # 근거 제외 자료는 선택 불가
    r = c.patch(f"/api/v1/sessions/{sid}/inputs", json={"expected_input_revision": 1, "selected_source_ids": ["MOCK06"]})
    assert r.status_code == 404 and r.json()["error"]["details"]["missing_source_ids"] == ["MOCK06"]
    r = c.patch(f"/api/v1/sessions/{sid}/inputs", json={"expected_input_revision": 1, "selected_source_ids": ["MOCK01", "MOCK07"]})
    assert r.status_code == 200
    r = c.post(f"/api/v1/sessions/{sid}/preflights", json={"expected_input_revision": 2})
    job = c.get(f"/api/v1/sessions/{sid}/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "succeeded", job
    pf = c.get(f"/api/v1/sessions/{sid}/preflights/{job['result_ref']['preflight_id']}").json()
    assert pf["usable_source_ids"] == ["MOCK01"] and pf["can_generate"]
    name = next(f for f in pf["facts"] if f["field_key"] == "company_name")
    assert name["status"] == "supported" and name["value"] == "예시 회사"  # [MOCK] 접두어를 벗겨 읽음
    assert name["evidence_refs"][0]["excerpt"] == "[MOCK] 회사명: 예시 회사"  # excerpt는 라벨 보존
    assert name["evidence_refs"][0]["source_id"] == "MOCK01" and name["evidence_refs"][0]["locator"] == {"line_start": 1, "line_end": 1}
    r = c.post(f"/api/v1/sessions/{sid}/drafts", json={"preflight_id": pf["preflight_id"], "input_revision": 2, "confirmed": True})
    job = c.get(f"/api/v1/sessions/{sid}/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "succeeded"
    doc = c.get(f"/api/v1/sessions/{sid}/documents/{job['result_ref']['document_id']}").json()["document"]
    image = next(b for p in doc["pages"] for b in p["blocks"] if b["type"] == "image")
    r = c.get(f"/api/v1/sessions/{sid}/assets/{image['content']['asset_id']}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")
    # 다른 소유자도 등록 asset은 자기 세션 경로로 볼 수 있다(등록 자료는 공용)
    c2, sid2 = _session_client(app)
    assert c2.get(f"/api/v1/sessions/{sid2}/assets/{image['content']['asset_id']}").status_code == 200
    # 세션 삭제가 등록 자료를 지우지 않는다
    assert c.delete(f"/api/v1/sessions/{sid}").status_code == 200
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources WHERE scope='registered' AND deleted_at IS NULL").fetchone()[0] == 8
        assert conn.execute("SELECT COUNT(*) FROM segments WHERE session_id IS NULL").fetchone()[0] == 24
        assert conn.execute("SELECT COUNT(*) FROM assets WHERE scope='registered' AND deleted_at IS NULL").fetchone()[0] == 6
    assert (settings.private_runs_dir / "registered" / "MOCK01.txt").is_file()
    assert len(c2.get("/api/v1/sources").json()["items"]) == 8


def test_excluded_registered_refs_not_accepted_in_edits(settings, app):
    _import(settings, with_mock=True)
    c, sid = _session_client(app)
    with connect(settings.db_path) as conn:
        seg6 = conn.execute("SELECT segment_id FROM segments WHERE source_id='MOCK06'").fetchone()[0]
    from app.services import refs
    with connect(settings.db_path) as conn:
        loaded = refs.load(conn, sid)
    assert seg6 not in loaded.segment_ids and "MOCK06" not in loaded.source_versions
    assert "MOCK01" in loaded.source_versions and len(loaded.asset_ids) == 6


# ---------------- 업로드 시연용 PDF ----------------

def test_mock_upload_pdfs(settings, app):
    c, sid = _session_client(app)
    files = [("files", (p.name, io.BytesIO(p.read_bytes()))) for p in sorted(UPLOAD_PDF.glob("*MOCK0[18]*.pdf"))]
    assert len(files) == 2
    r = c.post(f"/api/v1/sessions/{sid}/sources", files=files)
    assert r.status_code == 202, r.text
    items = {i["name"]: i for i in c.get(f"/api/v1/sessions/{sid}/sources").json()["items"]}
    m1 = next(v for k, v in items.items() if "MOCK01" in k)
    m8 = next(v for k, v in items.items() if "MOCK08" in k)
    assert m1["parse_status"] == "complete" and m1["text_available"] and len(m1["usable_segment_ids"]) >= 1
    assert m8["parse_status"] == "partial" and not m8["text_available"] and m8["warnings"][0]["code"] == "IMAGE_ONLY"
