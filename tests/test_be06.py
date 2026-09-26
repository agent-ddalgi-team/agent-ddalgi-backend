"""BE-06 확인: 검증 Job·서버 일반 검사·부분 재검증·Issue 해결·승인 7조건·무효화·멱등·동시성·마이그레이션.

모든 자료는 가상. 승인 성공은 mock 표시 없는 가상 TXT + 테스트 DB에만 넣는 LayoutCheck 행으로 확인한다(격리 fixture).
공식 mock 묶음(--with-mock) 문서는 MOCK_VALUE로 차단되어야 한다. 실제 AI·배치 렌더링은 범위 밖.
"""
from __future__ import annotations

import io
import json
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.agent_mock import MockAgent
from app.config import Settings
from app.db import SCHEMA_VERSION, connect, init_db
from app.services import layout_checks, registered, validation

BUNDLE_INGEST = Path(__file__).parent / "fixtures" / "ddalgi_mock_bundle_v1" / "ingest"
BRIEF = {"purpose": "테스트", "emphasis": [], "direction": "balanced", "target_pages": 4, "photo_preference": "balanced"}
# 격리 fixture: mock 표시 없는 가상 회사 자료
CLEAN_TXT = "회사명: 예시 회사\n회사 개요: 예시 회사는 가상 부품 표면처리와 검사를 하는 테스트 기업입니다.\n사업 분야: 가상 부품 표면처리\n".encode()


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def settings(tmp_path):
    return Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "t.sqlite3")


@pytest.fixture
def app(settings):
    return create_app(settings)


class Ctx:
    """세션 + 초안 rev.1. with_photo=True면 이미지도 첨부. registered_ids로 등록 자료를 선택할 수 있다."""

    def __init__(self, app, *, txt: bytes = CLEAN_TXT, with_photo: bool = True, registered_ids: list[str] | None = None,
                 upload: bool = True):
        self.app = app
        self.c = TestClient(app)
        self.sid = self.c.post("/api/v1/sessions", json={"brief": BRIEF}).json()["session_id"]
        selected = list(registered_ids or [])
        if upload:
            files = [("files", ("a.txt", io.BytesIO(txt)))]
            if with_photo:
                files.append(("files", ("p.png", io.BytesIO(_png()))))
            up = self.c.post(f"/api/v1/sessions/{self.sid}/sources", files=files).json()
            selected += [i["source_id"] for i in up["items"]]
        r = self.c.patch(f"/api/v1/sessions/{self.sid}/inputs", json={"expected_input_revision": 1, "selected_source_ids": selected})
        assert r.status_code == 200, r.text
        self.rev_in = r.json()["input_revision"]
        self.preflight()
        r = self.c.post(f"/api/v1/sessions/{self.sid}/drafts", json={"preflight_id": self.pf, "input_revision": self.rev_in, "confirmed": True})
        job = self.job(r.json()["job_id"])
        assert job["status"] == "succeeded", job
        self.did = job["result_ref"]["document_id"]

    def preflight(self):
        r = self.c.post(f"/api/v1/sessions/{self.sid}/preflights", json={"expected_input_revision": self.rev_in})
        job = self.job(r.json()["job_id"])
        assert job["status"] == "succeeded", job
        self.pf = job["result_ref"]["preflight_id"]

    def job(self, jid):
        return self.c.get(f"/api/v1/sessions/{self.sid}/jobs/{jid}").json()

    def get(self) -> dict:
        return self.c.get(f"/api/v1/sessions/{self.sid}/documents/{self.did}").json()

    def doc(self) -> dict:
        return self.get()["document"]

    def rev(self) -> int:
        return self.doc()["document_revision"]

    def patch(self, ops, expected=None):
        r = self.c.patch(f"/api/v1/sessions/{self.sid}/documents/{self.did}",
                         json={"expected_revision": self.rev() if expected is None else expected, "operations": ops})
        assert r.status_code == 200, r.text
        return r.json()

    def validate(self, expected=None, headers=None):
        r = self.c.post(f"/api/v1/sessions/{self.sid}/documents/{self.did}/validate",
                        json={"expected_revision": self.rev() if expected is None else expected, "input_revision": self.rev_in},
                        headers=headers or {})
        assert r.status_code == 202, r.text
        return self.job(r.json()["job_id"])

    def validated(self) -> dict:
        job = self.validate()
        assert job["status"] == "succeeded", job
        return self.get()["validation"]

    def issues(self) -> list[dict]:
        return self.c.get(f"/api/v1/sessions/{self.sid}/documents/{self.did}/issues").json()["issues"]

    def open_issues(self, code=None):
        return [i for i in self.issues() if i["status"] == "open" and (code is None or i["code"] == code)]

    def resolve(self, iid, action, reason="테스트", expected=None, evidence=None, headers=None):
        body = {"expected_revision": self.rev() if expected is None else expected,
                "resolution": {"action": action, "reason": reason}}
        if evidence:
            body["evidence_refs"] = evidence
        return self.c.post(f"/api/v1/sessions/{self.sid}/issues/{iid}/resolve", json=body, headers=headers or {})

    def layout_row(self, settings, *, fmt="pdf", status="passed", **overrides) -> str:
        """테스트 DB에만 넣는 가상 배치 검사 행(BE-08 전). 런타임 API는 없다."""
        d = self.doc()
        lid = f"lc_test_{fmt}_{d['document_revision']}_{overrides.get('tag', '')}"
        with connect(settings.db_path) as conn:
            from app.services.documents import get_current
            manifest = layout_checks.asset_manifest_hash(conn, get_current(conn, self.sid, self.did))
            values = {"document_id": self.did, "document_revision": d["document_revision"], "input_revision": self.rev_in,
                      "format": fmt, "template_version": layout_checks.TEMPLATE_VERSION,
                      "render_options_hash": layout_checks.RENDER_OPTIONS_HASH, "asset_manifest_hash": manifest,
                      "status": status}
            values.update({k: v for k, v in overrides.items() if k != "tag"})
            conn.execute("INSERT INTO layout_checks (layout_check_id, session_id, document_id, document_revision, input_revision, "
                         "format, template_version, render_options_hash, asset_manifest_hash, status, actual_pages, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 4, 'x')",
                         (lid, self.sid, values["document_id"], values["document_revision"], values["input_revision"],
                          values["format"], values["template_version"], values["render_options_hash"],
                          values["asset_manifest_hash"], values["status"]))
        return lid

    def approve(self, validation_id, layout_check_id, *, fmt="pdf", confirmed=True, expected=None, input_revision=None, headers=None):
        return self.c.post(f"/api/v1/sessions/{self.sid}/documents/{self.did}/approvals",
                           json={"expected_revision": self.rev() if expected is None else expected,
                                 "input_revision": self.rev_in if input_revision is None else input_revision,
                                 "format": fmt, "validation_id": validation_id, "layout_check_id": layout_check_id,
                                 "confirmed": confirmed}, headers=headers or {})

    def make_clean_and_validate(self, settings):
        """가상 자료 문서에서 안내 문구·placeholder를 정리해 검증 통과 상태로 만든다."""
        d = self.doc()
        ops = []
        for p in d["pages"]:
            for b in p["blocks"]:
                if b["type"] == "paragraph" and b["content"]["text"] == "추가 확인 필요":
                    ops.append({"op": "delete_block", "block_id": b["block_id"]})
                if b["type"] == "image_placeholder":
                    ops.append({"op": "delete_block", "block_id": b["block_id"]})
        if ops:
            self.patch(ops)
        v = self.validated()
        return v


def _first(items, **cond):
    return next(i for i in items if all(i.get(k) == v for k, v in cond.items()))


# ================= 마이그레이션 =================

def test_v5_to_v6_migration_keeps_data_and_is_rerunnable(tmp_path):
    runs = tmp_path / "runs"; runs.mkdir()
    db = runs / "app.sqlite3"
    init_db(db, runs)
    with sqlite3.connect(db) as conn:  # v5처럼 되돌린 뒤 데이터 넣기
        for t in ("issues", "validations", "layout_checks", "approvals"):
            conn.execute(f"DROP TABLE {t}")
        conn.execute("ALTER TABLE jobs DROP COLUMN target_key")
        conn.execute("PRAGMA user_version=5")
        conn.execute("INSERT INTO sessions VALUES ('s1','o1','active',1,'{}','[]','t','t','t',NULL,NULL)")
        conn.execute("INSERT INTO jobs (job_id, session_id, kind, status, progress_json, created_at, updated_at) VALUES ('j1','s1','read','succeeded','{}','t','t')")
    init_db(db, runs)
    init_db(db, runs)  # 재실행 안전
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 6
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert "target_key" in [r[1] for r in conn.execute("PRAGMA table_info(jobs)")]
        for t in ("issues", "validations", "layout_checks", "approvals"):
            assert conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone()


# ================= 서버 일반 검사 =================

def test_validate_clean_document_passes_after_cleanup(app, settings):
    ctx = Ctx(app)
    v = ctx.validated()
    codes = {i["code"] for i in ctx.open_issues()}
    assert v["status"] in ("failed", "needs_review") and "PLACEHOLDER_TEXT" in codes and "MOCK_VALUE" not in codes
    assert "REQUIRED_MISSING" not in codes
    v2 = ctx.make_clean_and_validate(settings)
    assert v2["status"] == "passed" and ctx.open_issues() == []
    assert ctx.doc()["status"] == "ready_for_approval"
    assert ctx.c.get(f"/api/v1/sessions/{ctx.sid}").json()["document_summary"]["status"] == "ready_for_approval"
    assert v2["document_revision"] == ctx.rev() and v2["input_revision"] == ctx.rev_in


def test_unsupported_claim_blocker_but_connector_and_labels_allowed(app, settings):
    ctx = Ctx(app)
    ctx.patch([{"op": "insert_block", "page_id": "page_01", "after_block_id": None,
                "block": {"block_id": "b_claim", "type": "paragraph", "content": {"text": "당사는 연간 3만 톤을 처리합니다."}}},
               {"op": "insert_block", "page_id": "page_01", "after_block_id": None,
                "block": {"block_id": "b_conn", "type": "paragraph", "content": {"text": "다음은 주요 공정입니다."}}},
               {"op": "insert_block", "page_id": "page_01", "after_block_id": None,
                "block": {"block_id": "b_label", "type": "heading", "content": {"text": "주요 공정", "level": 2}}},
               {"op": "insert_block", "page_id": "page_01", "after_block_id": None,
                "block": {"block_id": "b_head_claim", "type": "heading", "content": {"text": "ISO 9001 인증 획득 업체", "level": 2}}}])
    ctx.validated()
    claims = ctx.open_issues("UNSUPPORTED_CLAIM")
    flagged = {b for i in claims for b in i["block_ids"]}
    assert "b_claim" in flagged and "b_head_claim" in flagged
    assert "b_conn" not in flagged and "b_label" not in flagged
    assert all(i["severity"] == "blocker" for i in claims)
    # 확인 클릭으로 통과 불가
    r = ctx.resolve(_first(claims, block_ids=["b_claim"])["issue_id"], "acknowledged")
    assert r.status_code == 422 and r.json()["error"]["code"] == "RESOLUTION_NOT_ALLOWED"
    # 주장 삭제 후 재검증으로만 해결
    r = ctx.resolve(_first(claims, block_ids=["b_claim"])["issue_id"], "resolved")
    assert r.status_code == 422 and r.json()["error"]["code"] == "ISSUE_STILL_PRESENT"
    ctx.patch([{"op": "delete_block", "block_id": "b_claim"}])
    ctx.validated()
    iss = _first(ctx.issues(), block_ids=["b_claim"])
    assert iss["status"] == "resolved" and iss["resolution"]["by"] == "server"


def test_caption_claim_is_checked(app, settings):
    ctx = Ctx(app)
    img = next(b for p in ctx.doc()["pages"] for b in p["blocks"] if b["type"] == "image")
    ctx.patch([{"op": "replace_block_content", "block_id": img["block_id"],
                "content": {**img["content"], "caption": "국내 최대 규모 설비 사진"}}])
    ctx.validated()
    assert any(img["block_id"] in i["block_ids"] for i in ctx.open_issues("UNSUPPORTED_CLAIM"))


def test_required_content_needs_real_text_not_only_fact_ids(app, settings):
    ctx = Ctx(app)
    ctx.make_clean_and_validate(settings)
    assert ctx.open_issues("REQUIRED_MISSING") == []
    # 본문을 무관한 내용으로 바꾸고 fact_ids만 남김 → 필수 내용 검사 실패 (제목의 회사명도 바꿔야 회사명 결핍까지 잡힘)
    d = ctx.doc()
    paras = [b for p in d["pages"] for b in p["blocks"] if b["type"] == "paragraph" and b["fact_ids"]]
    heading = d["pages"][0]["blocks"][0]
    ctx.patch([{"op": "replace_block_content", "block_id": b["block_id"], "content": {"text": "무관한 문장입니다."}} for b in paras]
              + [{"op": "replace_block_content", "block_id": heading["block_id"], "content": {"text": "소개", "level": 1}}])
    ctx.validated()
    req = ctx.open_issues("REQUIRED_MISSING")
    assert len(req) == 2 and all(i["severity"] == "blocker" for i in req)
    assert not any(i["block_ids"] for i in req)  # fact_ids는 그대로 남아 있어도 통과하지 않는다
    r = ctx.resolve(req[0]["issue_id"], "excluded")
    assert r.status_code == 422 and r.json()["error"]["code"] == "RESOLUTION_NOT_ALLOWED"


def test_required_business_content_accepts_related_fact_kinds(app, settings):
    """company_summary가 없어도 business_areas 설명이 실제 블록에 있으면 인정."""
    txt = "회사명: 예시 회사\n사업 분야: 가상 부품 표면처리\n".encode()
    ctx = Ctx(app, txt=txt, with_photo=False)
    ctx.make_clean_and_validate(settings)
    assert ctx.open_issues("REQUIRED_MISSING") == []


# ================= MOCK_VALUE =================

def test_official_mock_bundle_document_blocked_by_mock_value(app, settings):
    registered.run(settings, BUNDLE_INGEST, with_mock=True)
    ctx = Ctx(app, registered_ids=["MOCK01", "MOCK07"], upload=False)
    v = ctx.validated()
    mocks = ctx.open_issues("MOCK_VALUE")
    assert v["status"] == "failed" and mocks
    # 이미지 블록(mock 사진)도 원출처로 차단
    img = next(b for p in ctx.doc()["pages"] for b in p["blocks"] if b["type"] == "image")
    assert any(img["block_id"] in i["block_ids"] and "image_source" in i["message"] for i in mocks)
    # excluded·acknowledged 모두 불가
    for action in ("excluded", "acknowledged"):
        r = ctx.resolve(mocks[0]["issue_id"], action)
        assert r.status_code == 422 and r.json()["error"]["code"] == "RESOLUTION_NOT_ALLOWED", action
    # 승인도 차단
    lid = ctx.layout_row(settings)
    r = ctx.approve(v["validation_id"], lid)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_NOT_PASSED"


def test_removing_mock_label_from_text_still_blocked_by_evidence(app, settings):
    registered.run(settings, BUNDLE_INGEST, with_mock=True)
    ctx = Ctx(app, registered_ids=["MOCK01"], upload=False)
    d = ctx.doc()
    heading = d["pages"][0]["blocks"][0]
    assert "[MOCK]" not in heading["content"]["text"]  # 제목 값은 라벨을 벗겨 읽음 → 문자열만으론 못 잡음
    paras = [b for p in d["pages"] for b in p["blocks"] if b["type"] == "paragraph" and b["fact_ids"]]
    ctx.patch([{"op": "replace_block_content", "block_id": b["block_id"],
                "content": {"text": b["content"]["text"].replace("[MOCK] ", "")}} for b in paras])
    ctx.validated()
    mocks = ctx.open_issues("MOCK_VALUE")
    assert any(heading["block_id"] in i["block_ids"] for i in mocks)
    assert all("text" not in i["message"].split("(")[1] or "evidence" in i["message"] or "fact_source" in i["message"]
               for i in mocks if heading["block_id"] in i["block_ids"])
    # 원인 제거(문서에서 mock 근거 블록 삭제 후 가상 자료로 다시 작성)는 이 흐름에서 자료 재선택이 필요 → §6 제한. 여기서는 삭제 후 REQUIRED_MISSING이 남는 것만 확인
    ctx.patch([{"op": "delete_block", "block_id": b["block_id"]} for b in [heading, *paras]])
    ctx.validated()
    assert ctx.open_issues("MOCK_VALUE") == [] or all(heading["block_id"] not in i["block_ids"] for i in ctx.open_issues("MOCK_VALUE"))
    assert ctx.open_issues("REQUIRED_MISSING")


# ================= Issue 해결 =================

def test_placeholder_warning_acknowledge_and_revalidate_status(app, settings):
    ctx = Ctx(app)
    v = ctx.validated()
    ph = ctx.open_issues("PLACEHOLDER_TEXT")[0]
    r = ctx.resolve(ph["issue_id"], "acknowledged", reason="검토함")
    assert r.status_code == 200
    out = r.json()
    assert out["issue"]["status"] == "acknowledged" and out["issue"]["resolution"]["by"].startswith("own_")
    assert out["issue"]["resolution"]["document_revision"] == ctx.rev() and "at" in out["issue"]["resolution"]
    # 같은 조치 재요청은 멱등
    assert ctx.resolve(ph["issue_id"], "acknowledged").status_code == 200
    # 오래된 버전으로는 409
    assert ctx.resolve(ph["issue_id"], "acknowledged", expected=99).status_code == 409


def test_excluded_only_after_actual_removal(app, settings):
    ctx = Ctx(app)
    ctx.patch([{"op": "insert_block", "page_id": "page_02", "after_block_id": None,
                "block": {"block_id": "b_x", "type": "paragraph", "content": {"text": "납기 2일 보장"}}}])
    ctx.validated()
    iss = _first(ctx.open_issues("UNSUPPORTED_CLAIM"), block_ids=["b_x"])
    r = ctx.resolve(iss["issue_id"], "excluded")
    assert r.status_code == 422 and r.json()["error"]["details"]["remaining_block_ids"] == ["b_x"]
    ctx.patch([{"op": "delete_block", "block_id": "b_x"}])
    r = ctx.resolve(iss["issue_id"], "excluded", reason="주장 제외")
    assert r.status_code == 200 and r.json()["issue"]["status"] == "excluded"


def test_agent_issue_resolution_requires_revalidation(app, settings):
    ctx = Ctx(app)
    ctx.patch([{"op": "insert_block", "page_id": "page_02", "after_block_id": None,
                "block": {"block_id": "b_sup", "type": "paragraph", "content": {"text": "다음은 최고 공정입니다."}}}])
    ctx.validated()
    sup = _first(ctx.open_issues("UNVERIFIED_SUPERLATIVE"), block_ids=["b_sup"])
    assert sup["origin"] == "agent" and sup["severity"] == "warning"
    r = ctx.resolve(sup["issue_id"], "resolved")
    assert r.status_code == 422 and r.json()["error"]["code"] == "REVALIDATION_REQUIRED"
    ctx.patch([{"op": "replace_block_content", "block_id": "b_sup", "content": {"text": "다음은 주요 공정입니다."}}])
    ctx.validated()
    assert _first(ctx.issues(), issue_id=sup["issue_id"])["status"] == "resolved"


# ================= 부분 재검증 =================

def test_partial_revalidation_keeps_untouched_blockers_and_skips_agent_on_move(app, settings):
    ctx = Ctx(app)
    ctx.patch([{"op": "insert_block", "page_id": "page_03", "after_block_id": None,
                "block": {"block_id": "b_bad", "type": "paragraph", "content": {"text": "연 매출 100억 달성"}}}])
    MockAgent.validate_calls = 0
    v1 = ctx.validated()
    assert MockAgent.validate_calls == 1 and v1["agent_called"] and v1["base_validation_id"] is None
    bad = _first(ctx.open_issues("UNSUPPORTED_CLAIM"), block_ids=["b_bad"])
    # 순서만 변경 → Agent 호출 없음, blocker 유지, 재사용 기록
    ctx.patch([{"op": "move_page", "page_id": "page_04", "after_page_id": None}])
    v2 = ctx.validated()
    assert MockAgent.validate_calls == 1 and v2["agent_called"] is False
    assert v2["base_validation_id"] == v1["validation_id"] and v2["checked_block_ids"] == []
    assert set(v2["reused_block_ids"]) == {b["block_id"] for p in ctx.doc()["pages"] for b in p["blocks"]}
    assert _first(ctx.issues(), issue_id=bad["issue_id"])["status"] == "open"
    # 다른 블록만 고침 → 그 블록만 검사, b_bad blocker 유지
    ctx.patch([{"op": "rename_page", "page_id": "page_02", "title": "바꿈"},
               {"op": "insert_block", "page_id": "page_02", "after_block_id": None,
                "block": {"block_id": "b_new", "type": "paragraph", "content": {"text": "다음은 소개입니다."}}}])
    v3 = ctx.validated()
    assert MockAgent.validate_calls == 2 and v3["checked_block_ids"] == ["b_new"] and "b_bad" in v3["reused_block_ids"]
    assert _first(ctx.issues(), issue_id=bad["issue_id"])["status"] == "open"


def test_no_reuse_without_prior_validation_or_after_input_change(app, settings):
    ctx = Ctx(app)
    MockAgent.validate_calls = 0
    ctx.patch([{"op": "move_page", "page_id": "page_04", "after_page_id": None}])
    v = ctx.validated()  # 선행 검증 없음 → 전체 검사
    assert MockAgent.validate_calls == 1 and v["agent_called"] and v["reused_block_ids"] == []
    # 입력 변경 → 문서 input_revision 뒤처짐 → validate 409(§6: 재연결 흐름 미지원)
    ctx.c.patch(f"/api/v1/sessions/{ctx.sid}/inputs", json={"expected_input_revision": ctx.rev_in, "brief": BRIEF})
    r = ctx.c.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/validate",
                   json={"expected_revision": ctx.rev(), "input_revision": ctx.rev_in + 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INPUT_REVISION_CONFLICT"


def test_resolution_reopened_when_related_content_changes(app, settings):
    ctx = Ctx(app)
    ctx.validated()
    ph = ctx.open_issues("PLACEHOLDER_TEXT")[0]
    ctx.resolve(ph["issue_id"], "acknowledged")
    # 관련 블록을 다른 안내 문구로 바꿈(같은 identity, 지문 변경) → 다시 open + 이력 보존
    ctx.patch([{"op": "replace_block_content", "block_id": ph["block_ids"][0], "content": {"text": "자료에서 확인되지 않음"}}])
    ctx.validated()
    again = _first(ctx.issues(), issue_id=ph["issue_id"])
    assert again["status"] == "open" and again["resolution"] is None
    with connect(settings.db_path) as conn:
        hist = json.loads(conn.execute("SELECT resolution_history_json FROM issues WHERE issue_id=?", (ph["issue_id"],)).fetchone()[0])
    assert hist and hist[0]["action"] == "acknowledged"


# ================= 검증 Job =================

def test_validation_job_dedup_per_revision_and_discard_when_changed(app, settings, monkeypatch):
    ctx = Ctx(app)
    with connect(settings.db_path) as conn:
        conn.execute("INSERT INTO jobs (job_id, session_id, kind, status, progress_json, input_revision, target_key, created_at, updated_at) "
                     "VALUES ('job_v_running', ?, 'validate', 'running', '{\"stage\":\"validating\",\"message\":null}', ?, ?, 'x', 'x')",
                     (ctx.sid, ctx.rev_in, f"{ctx.did}@1@{ctx.rev_in}"))
    r = ctx.c.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/validate", json={"expected_revision": 1, "input_revision": ctx.rev_in})
    assert r.json()["job_id"] == "job_v_running"
    ctx.patch([{"op": "rename_page", "page_id": "page_02", "title": "x"}])  # rev 2 → 다른 Job
    r = ctx.c.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/validate", json={"expected_revision": 2, "input_revision": ctx.rev_in})
    assert r.json()["job_id"] != "job_v_running"
    # Job 도중 문서 변경 → 결과 폐기
    original = MockAgent.validate

    async def edit_then_validate(self, request):
        ctx.patch([{"op": "rename_page", "page_id": "page_02", "title": "중간 편집"}])
        return await original(self, request)

    monkeypatch.setattr(MockAgent, "validate", edit_then_validate)
    job = ctx.validate()
    assert job["status"] == "failed" and job["error"]["code"] == "DOCUMENT_REVISION_CONFLICT"
    assert ctx.get()["validation"] is None


def test_llm_mode_without_impl_fails(tmp_path):
    settings = Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "t.sqlite3", agent_mode="llm")
    app = create_app(settings)
    c = TestClient(app)
    sid = c.post("/api/v1/sessions", json={"brief": BRIEF}).json()["session_id"]
    up = c.post(f"/api/v1/sessions/{sid}/sources", files=[("files", ("a.txt", io.BytesIO(CLEAN_TXT)))]).json()
    c.patch(f"/api/v1/sessions/{sid}/inputs", json={"expected_input_revision": 1, "selected_source_ids": [up["items"][0]["source_id"]]})
    r = c.post(f"/api/v1/sessions/{sid}/preflights", json={"expected_input_revision": 2})
    job = c.get(f"/api/v1/sessions/{sid}/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "failed" and "agent_llm" in job["error"]["message"]


# ================= 승인 =================

def _ready(app, settings):
    ctx = Ctx(app)
    v = ctx.make_clean_and_validate(settings)
    return ctx, v


def test_approval_success_and_document_status(app, settings):
    ctx, v = _ready(app, settings)
    lid = ctx.layout_row(settings)
    r = ctx.approve(v["validation_id"], lid, headers={"Idempotency-Key": "ap1"})
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["status"] == "active" and a["document_revision"] == ctx.rev() and a["approved_by"].startswith("own_")
    assert a["template_version"] == layout_checks.TEMPLATE_VERSION and a["layout_check_id"] == lid
    out = ctx.get()
    assert out["document"]["status"] == "approved" and out["approval"]["approval_id"] == a["approval_id"]
    assert ctx.c.get(f"/api/v1/sessions/{ctx.sid}").json()["document_summary"]["status"] == "approved"
    # 멱등: 같은 키 → 같은 응답, Approval 1건. 다른 본문 → 409. 키 없이 → 같은 active 재사용(중복 생성 없음)
    r2 = ctx.approve(v["validation_id"], lid, headers={"Idempotency-Key": "ap1"})
    assert r2.status_code == 201 and r2.json() == a
    r3 = ctx.approve(v["validation_id"], lid, fmt="docx", headers={"Idempotency-Key": "ap1"})
    assert r3.status_code == 409 and r3.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    r4 = ctx.approve(v["validation_id"], lid)
    assert r4.status_code == 201 and r4.json()["approval_id"] == a["approval_id"]
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1


@pytest.mark.parametrize("case", ["revision", "input", "validation_other_doc", "validation_old", "blocker", "required",
                                  "layout_missing", "layout_format", "layout_hash", "layout_failed", "layout_old_rev", "not_confirmed"])
def test_approval_conditions_each_fail(app, settings, case):
    ctx, v = _ready(app, settings)
    lid = ctx.layout_row(settings)
    kw = {}
    vid = v["validation_id"]
    if case == "revision":
        kw["expected"] = 99; code = "DOCUMENT_REVISION_CONFLICT"
    elif case == "input":
        kw["input_revision"] = ctx.rev_in + 1; code = "INPUT_REVISION_CONFLICT"
    elif case == "validation_other_doc":
        vid = "val_other"; code = "VALIDATION_NOT_PASSED"
    elif case == "validation_old":
        ctx.patch([{"op": "rename_page", "page_id": "page_02", "title": "새 버전"}])
        ctx.validated(); lid = ctx.layout_row(settings); code = "VALIDATION_NOT_PASSED"  # vid는 rev1의 것
    elif case == "blocker":
        ctx.patch([{"op": "insert_block", "page_id": "page_02", "after_block_id": None,
                    "block": {"block_id": "b_c", "type": "paragraph", "content": {"text": "연 100톤 처리"}}}])
        vid = ctx.validated()["validation_id"]; lid = ctx.layout_row(settings); code = "VALIDATION_NOT_PASSED"
    elif case == "required":
        # 검증 후 필수 블록을 지운 새 버전을 만들고 그 버전의 검증은 하지 않은 채 옛 validation_id로 → not_current
        d = ctx.doc(); h = d["pages"][0]["blocks"][0]
        ctx.patch([{"op": "delete_block", "block_id": h["block_id"]}]); code = "VALIDATION_NOT_PASSED"
    elif case == "layout_missing":
        lid = "lc_none"; code = "LAYOUT_NOT_READY"
    elif case == "layout_format":
        kw["fmt"] = "docx"; code = "LAYOUT_NOT_READY"
    elif case == "layout_hash":
        lid = ctx.layout_row(settings, asset_manifest_hash="deadbeef", tag="h"); code = "LAYOUT_NOT_READY"
    elif case == "layout_failed":
        lid = ctx.layout_row(settings, status="failed", tag="f"); code = "LAYOUT_NOT_READY"
    elif case == "layout_old_rev":
        lid = ctx.layout_row(settings, document_revision=1, tag="o")
        ctx.patch([{"op": "rename_page", "page_id": "page_02", "title": "v"}]); vid = ctx.validated()["validation_id"]; code = "LAYOUT_NOT_READY"
    else:
        kw["confirmed"] = False; code = "APPROVAL_NOT_CONFIRMED"
    r = ctx.approve(vid, lid, **kw)
    assert r.status_code in (409, 422) and r.json()["error"]["code"] == code, (case, r.text)
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    assert ctx.doc()["status"] != "approved"


def test_approval_layout_hash_changes_with_asset(app, settings):
    ctx, v = _ready(app, settings)
    lid = ctx.layout_row(settings)
    with connect(settings.db_path) as conn:  # 같은 asset_id인데 파일 해시가 바뀌면 배치 검사 불일치
        conn.execute("UPDATE assets SET content_hash='changed' WHERE session_id=?", (ctx.sid,))
    r = ctx.approve(v["validation_id"], lid)
    assert r.status_code == 422 and r.json()["error"]["details"]["reason"] == "asset_manifest_hash_mismatch"


@pytest.mark.parametrize("change", ["patch", "move", "apply", "restore", "input"])
def test_approval_invalidated_after_changes(app, settings, change):
    ctx, v = _ready(app, settings)
    a = ctx.approve(v["validation_id"], ctx.layout_row(settings)).json()
    assert ctx.doc()["status"] == "approved"
    if change == "patch":
        ctx.patch([{"op": "rename_page", "page_id": "page_02", "title": "편집"}])
    elif change == "move":
        ctx.patch([{"op": "move_page", "page_id": "page_02", "after_page_id": None}])
    elif change == "apply":
        para = next(b for p in ctx.doc()["pages"] for b in p["blocks"] if b["type"] == "paragraph")
        r = ctx.c.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/proposals",
                       json={"expected_revision": ctx.rev(), "input_revision": ctx.rev_in, "target_block_ids": [para["block_id"]],
                             "instruction": "정리", "kind": "text"})
        pid = ctx.job(r.json()["job_id"])["result_ref"]["proposal_id"]
        assert ctx.c.post(f"/api/v1/sessions/{ctx.sid}/proposals/{pid}/apply", json={"expected_revision": ctx.rev()}).status_code == 200
    elif change == "restore":
        assert ctx.c.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/restore",
                          json={"expected_revision": ctx.rev(), "restore_from_revision": 1}).status_code == 200
    else:
        ctx.c.patch(f"/api/v1/sessions/{ctx.sid}/inputs", json={"expected_input_revision": ctx.rev_in, "brief": BRIEF})
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT status, invalidated_reason FROM approvals WHERE approval_id=?", (a["approval_id"],)).fetchone()
    assert row["status"] == "invalidated" and row["invalidated_reason"] == ("input_changed" if change == "input" else "document_changed")
    out = ctx.get()
    assert out["approval"] is None and out["document"]["status"] != "approved"
    assert ctx.c.get(f"/api/v1/sessions/{ctx.sid}").json()["document_summary"]["status"] == out["document"]["status"]
    # 멱등 재전송이 invalidated를 되살리지 않는다(문서 버전이 달라 409, DB 상태 유지)
    r = ctx.approve(v["validation_id"], a["layout_check_id"], expected=a["document_revision"],
                    input_revision=a["input_revision"], headers={"Idempotency-Key": "none-before"})
    assert r.status_code in (409, 422)
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT status FROM approvals WHERE approval_id=?", (a["approval_id"],)).fetchone()[0] == "invalidated"


def test_approval_access_checks_before_idempotent_replay(app, settings):
    ctx, v = _ready(app, settings)
    lid = ctx.layout_row(settings)
    body = {"expected_revision": ctx.rev(), "input_revision": ctx.rev_in, "format": "pdf", "validation_id": v["validation_id"],
            "layout_check_id": lid, "confirmed": True}
    assert ctx.c.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/approvals", json=body, headers={"Idempotency-Key": "k"}).status_code == 201
    other = TestClient(app); other.post("/api/v1/sessions", json={"brief": BRIEF})
    assert other.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/approvals", json=body, headers={"Idempotency-Key": "k"}).status_code == 404
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE sessions SET status='expired' WHERE session_id=?", (ctx.sid,))
    assert ctx.c.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/approvals", json=body, headers={"Idempotency-Key": "k"}).status_code == 410
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1


def test_concurrent_approval_creates_single_row(app, settings):
    """TestClient 기반 동시 ASGI 요청(스레드 3개). 실서버 네트워크 동시 요청은 별도(미실시).
    응답이 정확히 3개이고 작업 스레드 예외가 없어야 하며, active Approval은 1건이다."""
    ctx, v = _ready(app, settings)
    lid = ctx.layout_row(settings)
    body = {"expected_revision": ctx.rev(), "input_revision": ctx.rev_in, "format": "pdf", "validation_id": v["validation_id"],
            "layout_check_id": lid, "confirmed": True}
    results, errors = [], []

    def worker():
        try:
            c = TestClient(app); c.cookies = ctx.c.cookies
            r = c.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/approvals", json=body)
            results.append((r.status_code, r.json().get("approval_id")))
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert errors == [], errors
    assert len(results) == 3 and all(s == 201 for s, _ in results), results
    assert len({a for _, a in results}) == 1  # 세 응답이 같은 승인을 가리킴
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM approvals WHERE status='active'").fetchone()[0] == 1


# ================= 리뷰 회귀 (Codex 리뷰 4건) =================

def test_review1_resolved_issue_reopens_when_cause_returns(app, settings):
    """MOCK_VALUE → 블록 삭제 → 재검증으로 resolved → restore로 블록 복원 → validate → open blocker, failed."""
    registered.run(settings, BUNDLE_INGEST, with_mock=True)
    ctx = Ctx(app, registered_ids=["MOCK01"], upload=False)
    ctx.validated()
    target = ctx.open_issues("MOCK_VALUE")[0]
    ctx.patch([{"op": "delete_block", "block_id": target["block_ids"][0]}])  # rev 2
    ctx.validated()
    after = _first(ctx.issues(), issue_id=target["issue_id"])
    assert after["status"] == "resolved" and after["resolution"]["by"] == "server"
    r = ctx.c.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/restore", json={"expected_revision": 2, "restore_from_revision": 1})
    assert r.status_code == 200, r.text  # rev 3 = rev 1 내용
    v = ctx.validated()
    back = _first(ctx.issues(), issue_id=target["issue_id"])
    assert back["status"] == "open" and back["resolution"] is None and v["status"] == "failed"
    with connect(settings.db_path) as conn:
        hist = json.loads(conn.execute("SELECT resolution_history_json FROM issues WHERE issue_id=?", (target["issue_id"],)).fetchone()[0])
    assert hist and hist[-1]["previous_status"] == "resolved" and hist[-1]["action"] == "resolved"


def test_review2_agent_cannot_downgrade_server_issue(app, settings, monkeypatch):
    registered.run(settings, BUNDLE_INGEST, with_mock=True)
    ctx = Ctx(app, registered_ids=["MOCK01"], upload=False)
    from app.agent_bridge import ValidateResult
    from app.models import Issue as IssueModel

    async def sneaky(self, request):  # 서버와 같은 code를 warning으로 흉내
        return ValidateResult(issues=[IssueModel(issue_id="a1", scope="content", code="MOCK_VALUE", severity="warning",
                                                 message="agent says fine", block_ids=[request.changed_block_ids[0]])])

    monkeypatch.setattr(MockAgent, "validate", sneaky)
    v = ctx.validated()
    blockers = [i for i in ctx.open_issues("MOCK_VALUE") if i["origin"] == "server"]
    agent_rows = [i for i in ctx.issues() if i["origin"] == "agent"]
    assert blockers and all(i["severity"] == "blocker" for i in blockers) and v["status"] == "failed"
    assert len(agent_rows) == 1 and agent_rows[0]["severity"] == "warning"
    with connect(settings.db_path) as conn:
        keys = [r[0] for r in conn.execute("SELECT identity_key FROM issues WHERE document_id=?", (ctx.did,))]
    assert all(k.split("|")[0] in ("server", "agent") for k in keys)
    assert any(k.startswith("agent|") for k in keys) and any(k.startswith("server|") for k in keys)


def test_review2_legacy_issue_keys_migrate_without_duplicates(app, settings):
    ctx = Ctx(app)
    ctx.validated()
    ph = ctx.open_issues("PLACEHOLDER_TEXT")[0]
    ctx.resolve(ph["issue_id"], "acknowledged", reason="검토")
    with connect(settings.db_path) as conn:  # 옛 형식(origin 없음)으로 되돌려 재시작 상황을 만든다
        conn.execute("UPDATE issues SET identity_key=substr(identity_key, instr(identity_key, '|')+1) WHERE document_id=?", (ctx.did,))
        assert not any(r[0].startswith("server|") for r in conn.execute("SELECT identity_key FROM issues WHERE document_id=?", (ctx.did,)))
        n_before = conn.execute("SELECT COUNT(*) FROM issues WHERE document_id=?", (ctx.did,)).fetchone()[0]
    app2 = create_app(settings)  # 재시작 → 마이그레이션
    app2 = create_app(settings)  # 재실행 안전
    c = TestClient(app2); c.cookies = ctx.c.cookies
    ctx.c = c; ctx.app = app2
    ctx.patch([{"op": "rename_page", "page_id": "page_02", "title": "재검증"}])
    ctx.validated()
    with connect(settings.db_path) as conn:
        rows = conn.execute("SELECT identity_key, status FROM issues WHERE document_id=?", (ctx.did,)).fetchall()
    assert len(rows) == n_before  # 중복 행 없음
    assert all(r["identity_key"].split("|")[0] in ("server", "agent") for r in rows)
    assert _first(ctx.issues(), issue_id=ph["issue_id"])["status"] == "acknowledged"  # 해결 이력 유지


def test_review3a_document_level_agent_blocker_survives_move_only(app, settings, monkeypatch):
    ctx = Ctx(app)
    from app.agent_bridge import ValidateResult
    from app.models import Issue as IssueModel

    async def doc_level(self, request):
        return ValidateResult(issues=[IssueModel(issue_id="d1", scope="content", code="AGENT_DOC_CONCERN", severity="blocker",
                                                 message="문서 전체 문제", block_ids=[])])

    monkeypatch.setattr(MockAgent, "validate", doc_level)
    v1 = ctx.validated()
    doc_issue = _first(ctx.open_issues("AGENT_DOC_CONCERN"))
    assert doc_issue["block_ids"] == [] and v1["status"] == "failed"
    ctx.patch([{"op": "move_page", "page_id": "page_04", "after_page_id": None}])  # 순서만
    v2 = ctx.validated()
    assert v2["agent_called"] is False
    assert _first(ctx.issues(), issue_id=doc_issue["issue_id"])["status"] == "open" and v2["status"] == "failed"


def test_review3b_partial_agent_recheck_preserves_uncovered_issues(app, settings, monkeypatch):
    ctx = Ctx(app)
    ctx.patch([{"op": "insert_block", "page_id": "page_02", "after_block_id": None,
                "block": {"block_id": "b_A", "type": "paragraph", "content": {"text": "다음은 최고 공정입니다."}}}])
    from app.agent_bridge import ValidateResult
    from app.models import Issue as IssueModel
    original = MockAgent.validate

    async def with_doc_level(self, request):
        result = await original(self, request)
        if len(request.changed_block_ids) > 1:  # 전체 검사 때만 문서 전체 Issue 추가
            result.issues.append(IssueModel(issue_id="d1", scope="content", code="AGENT_DOC_CONCERN", severity="blocker",
                                            message="문서 전체 문제", block_ids=[]))
        return result

    monkeypatch.setattr(MockAgent, "validate", with_doc_level)
    ctx.validated()
    a_issue = _first(ctx.open_issues("UNVERIFIED_SUPERLATIVE"), block_ids=["b_A"])
    doc_issue = _first(ctx.open_issues("AGENT_DOC_CONCERN"))
    # 다른 블록 B만 추가 → Agent는 B만 봄. A의 Issue·문서 전체 Issue는 재검사 대상이 아니므로 보존
    ctx.patch([{"op": "insert_block", "page_id": "page_03", "after_block_id": None,
                "block": {"block_id": "b_B", "type": "paragraph", "content": {"text": "다음은 소개입니다."}}}])
    v = ctx.validated()
    assert v["checked_block_ids"] == ["b_B"]
    assert _first(ctx.issues(), issue_id=a_issue["issue_id"])["status"] == "open"
    assert _first(ctx.issues(), issue_id=doc_issue["issue_id"])["status"] == "open"
    assert v["status"] == "failed"


def test_review4_latest_pick_uses_insert_order_not_id_order(app, settings):
    ctx = Ctx(app)
    v = ctx.validated()
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM validations WHERE validation_id=?", (v["validation_id"],)).fetchone()
        # 같은 created_at, 나중에 저장한 행의 ID가 사전순으로 더 작다
        conn.execute("UPDATE validations SET validation_id='val_zzz' WHERE validation_id=?", (v["validation_id"],))
        conn.execute("INSERT INTO validations (validation_id, session_id, document_id, document_revision, input_revision, status, "
                     "issue_ids_json, checks_json, fingerprints_json, base_validation_id, agent_called, created_at, updated_at) "
                     "VALUES ('val_aaa', ?, ?, ?, ?, 'passed', '[]', '[]', ?, NULL, 1, ?, ?)",
                     (row["session_id"], row["document_id"], row["document_revision"], row["input_revision"],
                      row["fingerprints_json"], row["created_at"], row["updated_at"]))
        latest = validation.latest_validation(conn, row["document_id"], row["document_revision"], row["input_revision"])
        assert latest["validation_id"] == "val_aaa"
        # 승인도 같은 규칙
        for aid in ("apr_zzz", "apr_aaa"):
            conn.execute("INSERT INTO approvals (approval_id, session_id, document_id, document_revision, input_revision, format, "
                         "validation_id, layout_check_id, template_version, render_options_hash, asset_manifest_hash, approved_at, "
                         "approved_by, status, created_at) VALUES (?, ?, ?, ?, ?, 'pdf', 'v', 'l', 't', 'r', 'a', 'same', 'o', 'active', 'same')",
                         (aid, row["session_id"], row["document_id"], row["document_revision"], row["input_revision"]))
        from app.services import approvals
        assert approvals.active_for(conn, row["document_id"], row["document_revision"], row["input_revision"])["approval_id"] == "apr_aaa"
    assert ctx.get()["validation"]["validation_id"] == "val_aaa"


def test_db_lock_contention_replay_edit_then_approve(app, settings):
    """DB 트랜잭션 경합 재현: 첫 연결이 BEGIN IMMEDIATE로 쓰기 잠금을 쥔 동안 두 번째 연결의 쓰기 시도는 대기·실패하고,
    첫 연결이 문서를 바꿔 커밋한 뒤 재시도한 승인은 옛 버전 기준이라 거부된다(옛 버전 승인 없음)."""
    ctx, v = _ready(app, settings)
    lid = ctx.layout_row(settings)
    c1 = sqlite3.connect(settings.db_path, timeout=0.2, isolation_level=None)
    c1.execute("BEGIN IMMEDIATE")
    c2 = sqlite3.connect(settings.db_path, timeout=0.2, isolation_level=None)
    with pytest.raises(sqlite3.OperationalError):
        c2.execute("BEGIN IMMEDIATE")  # 잠금 대기 후 실패 = 동시에 쓰지 못함
    # 첫 연결: 문서 새 버전(현재 승인 흐름과 같은 효과)
    c1.execute("UPDATE documents SET current_revision=current_revision+1 WHERE document_id=?", (ctx.did,))
    c1.execute("INSERT INTO document_revisions (document_id, revision, input_revision, status, content_json, created_at) "
               "SELECT document_id, revision+1, input_revision, status, content_json, created_at FROM document_revisions "
               "WHERE document_id=? ORDER BY revision DESC LIMIT 1", (ctx.did,))
    c1.execute("COMMIT"); c1.close(); c2.close()
    r = ctx.approve(v["validation_id"], lid, expected=v["document_revision"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "DOCUMENT_REVISION_CONFLICT"
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


def test_restore_does_not_revive_approval(app, settings):
    ctx, v = _ready(app, settings)
    a = ctx.approve(v["validation_id"], ctx.layout_row(settings)).json()
    ctx.patch([{"op": "rename_page", "page_id": "page_02", "title": "편집"}])
    ctx.c.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/restore", json={"expected_revision": ctx.rev(), "restore_from_revision": a["document_revision"]})
    out = ctx.get()
    assert out["approval"] is None and out["document"]["status"] == "draft"


def test_no_real_company_terms_in_new_code():
    import inspect
    from app.services import approvals as a, issues as i, validation as val, layout_checks as lc
    import app.agent_mock as m
    for mod in (a, i, val, lc, m):
        src = inspect.getsource(mod)
        for banned in ("거산", "케미칼", "Geosan"):
            assert banned not in src, mod.__name__
