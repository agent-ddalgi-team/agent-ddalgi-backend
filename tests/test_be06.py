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
        if action == "acknowledged":
            checked = self.get()["validation"]
            body.update(input_revision=self.rev_in, validation_id=checked["validation_id"] if checked else "val_missing")
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


CONFLICT_TXT = CLEAN_TXT + "공정 수: 2개\n공정 수: 3개\n".encode()


def test_unreferenced_conflict_survives_cleanup_partial_validation_and_blocks_approval(app, settings):
    ctx = Ctx(app, txt=CONFLICT_TXT, with_photo=False)
    pf = ctx.c.get(f"/api/v1/sessions/{ctx.sid}/preflights/{ctx.pf}").json()
    source_issue = _first(pf["issues"], code="VALUE_CONFLICT")
    assert not any(set(source_issue["fact_ids"]) & set(b["fact_ids"])
                   for p in ctx.doc()["pages"] for b in p["blocks"])
    v1 = ctx.validated()
    assert v1["status"] == "failed"
    conflict = _first(ctx.open_issues("VALUE_CONFLICT"), origin="preflight")
    assert conflict["block_ids"] == [] and conflict["severity"] == "blocker"
    assert conflict["fact_ids"] == source_issue["fact_ids"]
    assert conflict["source_ids"] == source_issue["source_ids"]
    assert ctx.open_issues("PLACEHOLDER_TEXT")
    assert not ctx.open_issues("REQUIRED_MISSING") and not ctx.open_issues("MOCK_VALUE")
    for action, code in (("acknowledged", "RESOLUTION_NOT_ALLOWED"), ("resolved", "ISSUE_STILL_PRESENT"),
                         ("excluded", "ISSUE_STILL_PRESENT")):
        result = ctx.resolve(conflict["issue_id"], action)
        assert result.status_code == 422 and result.json()["error"]["code"] == code

    # 안내 삭제·순서 변경으로 충돌을 해결 처리하지 않는다. Agent를 생략해도 전체 충돌을 유지한다.
    v2 = ctx.make_clean_and_validate(settings)
    assert v2["status"] == "failed" and not ctx.open_issues("PLACEHOLDER_TEXT")
    MockAgent.validate_calls = 0
    ctx.patch([{"op": "move_page", "page_id": "page_04", "after_page_id": None}])
    v3 = ctx.validated()
    assert v3["agent_called"] is False and MockAgent.validate_calls == 0
    ctx.patch([{"op": "insert_block", "page_id": "page_02", "after_block_id": None,
                "block": {"block_id": "b_conflict_connector", "type": "paragraph",
                          "content": {"text": "다음은 자료입니다."}}}])
    v4 = ctx.validated()
    assert v4["checked_block_ids"] == ["b_conflict_connector"] and MockAgent.validate_calls == 1
    assert v4["status"] == "failed"
    assert [i["issue_id"] for i in ctx.open_issues()] == [conflict["issue_id"]]
    assert conflict["issue_id"] in v4["issue_ids"]
    assert ctx.doc()["status"] == "review_required"
    assert ctx.c.get(f"/api/v1/sessions/{ctx.sid}").json()["document_summary"]["status"] == "review_required"
    result = ctx.approve(v4["validation_id"], ctx.layout_row(settings))
    assert result.status_code == 422 and result.json()["error"]["code"] == "VALIDATION_NOT_PASSED"
    assert result.json()["error"]["details"]["issue_ids"] == [conflict["issue_id"]]
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM approvals WHERE document_id=?", (ctx.did,)).fetchone()[0] == 0


@pytest.mark.parametrize("clear_facts", [False, True])
def test_unreferenced_conflict_missing_targets_cannot_be_excluded(app, settings, monkeypatch, clear_facts):
    original = MockAgent.analyze

    async def analyze(self, request):
        result = await original(self, request)
        for issue in result.issues:
            if issue.code == "VALUE_CONFLICT":
                issue.source_ids = []
                if clear_facts:
                    issue.fact_ids = []
        return result

    monkeypatch.setattr(MockAgent, "analyze", analyze)
    ctx = Ctx(app, txt=CONFLICT_TXT, with_photo=False)
    assert ctx.make_clean_and_validate(settings)["status"] == "failed"
    conflict = _first(ctx.open_issues("VALUE_CONFLICT"), origin="preflight")
    assert bool(conflict["source_ids"]) is not clear_facts  # Fact의 실제 근거로 출처를 보완한다.
    for action in ("excluded", "resolved"):
        result = ctx.resolve(conflict["issue_id"], action)
        assert result.status_code == 422 and result.json()["error"]["code"] == "ISSUE_STILL_PRESENT"


def test_latest_preflight_conflict_blocks_previously_passed_validation(app, settings, monkeypatch):
    ctx, v = _ready(app, settings)
    lid = ctx.layout_row(settings)
    original = MockAgent.analyze

    async def analyze(self, request):
        from app.models import Issue
        result = await original(self, request)
        result.issues.append(Issue(issue_id="iss_later_conflict", scope="content", code="VALUE_CONFLICT",
                                   severity="blocker", message="같은 입력의 재점검에서 충돌을 발견했습니다.",
                                   source_ids=[request.sources[0].source_id]))
        return result

    monkeypatch.setattr(MockAgent, "analyze", analyze)
    ctx.preflight()
    # 수정 전 저장본 호환: 당시에는 재점검 충돌이 문서 Issue·Validation으로 전달되지 않았다.
    with connect(settings.db_path) as conn:
        conn.execute("DELETE FROM issues WHERE document_id=? AND origin='preflight'", (ctx.did,))
        conn.execute("UPDATE validations SET status='passed' WHERE validation_id=?", (v["validation_id"],))
    assert ctx.open_issues("VALUE_CONFLICT") == []
    result = ctx.approve(v["validation_id"], lid)
    assert result.status_code == 422 and result.json()["error"]["code"] == "VALIDATION_NOT_PASSED"
    assert "재검증" in result.json()["error"]["message"]
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM approvals WHERE document_id=?", (ctx.did,)).fetchone()[0] == 0


@pytest.mark.parametrize("issue_state", ["missing", "warning", "resolved", "excluded", "acknowledged"])
def test_conflicting_fact_remains_blocker_without_open_blocker_issue(app, settings, monkeypatch, issue_state):
    original = MockAgent.analyze

    async def analyze(self, request):
        result = await original(self, request)
        if issue_state == "missing":
            result.issues = []
        elif issue_state == "warning":
            for issue in result.issues:
                issue.severity = "warning"
        return result

    monkeypatch.setattr(MockAgent, "analyze", analyze)
    ctx = Ctx(app, txt=CONFLICT_TXT, with_photo=False)
    if issue_state in {"resolved", "excluded", "acknowledged"}:
        # 새 Agent 응답의 임의 해결은 거부한다. 과거 저장 점검의 닫힌 Issue에도
        # conflict Fact가 살아 있으면 blocker로 복구되는 기존 보호는 유지한다.
        with connect(settings.db_path) as conn:
            row = conn.execute("SELECT issues_json FROM preflights WHERE preflight_id=?", (ctx.pf,)).fetchone()
            issues = json.loads(row[0])
            for issue in issues:
                issue["status"] = issue_state
            conn.execute("UPDATE preflights SET issues_json=? WHERE preflight_id=?", (json.dumps(issues), ctx.pf))
    assert ctx.make_clean_and_validate(settings)["status"] == "failed"
    conflicts = ctx.open_issues("VALUE_CONFLICT")
    assert len(conflicts) == 1 and conflicts[0]["origin"] == "preflight"
    assert conflicts[0]["severity"] == "blocker" and conflicts[0]["block_ids"] == []


def test_preflight_conflict_resolves_on_recheck_and_reopens_with_history(app, settings, monkeypatch):
    ctx = Ctx(app, txt=CONFLICT_TXT, with_photo=False)
    assert ctx.make_clean_and_validate(settings)["status"] == "failed"
    conflict = _first(ctx.open_issues("VALUE_CONFLICT"), origin="preflight")
    original = MockAgent.analyze

    async def corrected_analysis(self, request):
        # 가짜 분석 대역: 충돌 없는 최신 분석 결과의 서버 전달만 검증한다.
        result = await original(self, request)
        result.issues = []
        for fact in result.facts:
            if fact.status == "conflict":
                fact.status = "supported"
                fact.value = fact.alternatives[0]["value"]
                fact.alternatives = []
        return result

    monkeypatch.setattr(MockAgent, "analyze", corrected_analysis)
    ctx.preflight()
    assert _first(ctx.issues(), issue_id=conflict["issue_id"])["status"] == "open"
    v2 = ctx.validated()
    assert v2["status"] == "passed"
    resolved = _first(ctx.issues(), issue_id=conflict["issue_id"])
    assert resolved["status"] == "resolved" and resolved["resolution"]["by"] == "server"
    assert resolved["resolution"]["validation_id"] == v2["validation_id"]
    assert ctx.approve(v2["validation_id"], ctx.layout_row(settings)).status_code == 201

    monkeypatch.setattr(MockAgent, "analyze", original)
    ctx.preflight()
    v3 = ctx.validated()
    assert v3["status"] == "failed"
    reopened = _first(ctx.open_issues("VALUE_CONFLICT"), origin="preflight")
    assert reopened["issue_id"] == conflict["issue_id"] and reopened["resolution"] is None
    with connect(settings.db_path) as conn:
        history = json.loads(conn.execute("SELECT resolution_history_json FROM issues WHERE issue_id=?",
                                         (conflict["issue_id"],)).fetchone()[0])
        assert len(history) == 1 and history[0]["previous_status"] == "resolved"


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
        conn.execute("INSERT INTO sessions (session_id, owner_id, status, input_revision, brief_json, selected_source_ids, created_at, last_activity_at, expires_at, closed_at, cleanup_status) VALUES ('s1','o1','active',1,'{}','[]','t','t','t',NULL,NULL)")
        conn.execute("INSERT INTO jobs (job_id, session_id, kind, status, progress_json, created_at, updated_at) VALUES ('j1','s1','read','succeeded','{}','t','t')")
    init_db(db, runs)
    init_db(db, runs)  # 재실행 안전
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION >= 6   # v7(BE-08)에서도 v5→v6 경로 유지
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


@pytest.mark.parametrize("kind", ["heading", "paragraph", "list"])
def test_exact_section_labels_do_not_exempt_body_text(app, kind):
    ctx = Ctx(app, with_photo=False)
    labels = ["회사명", "회사소개서 초안", "회사 개요", "인증·승인·특허", "대응 범위", "납기 조건"]
    ids = {f"label_{i}" for i in range(len(labels))}
    ctx.patch([{"op": "insert_block", "page_id": "page_01", "after_block_id": None,
                "block": {"block_id": f"label_{i}", "type": kind,
                          "content": {"items": [text]} if kind == "list" else
                                     {"text": text, **({"level": 2} if kind == "heading" else {})}}}
               for i, text in enumerate(labels)])
    ctx.validated()
    flagged = {bid for issue in ctx.open_issues("UNSUPPORTED_CLAIM") for bid in issue["block_ids"]}
    assert flagged & ids == (set() if kind == "heading" else ids)


@pytest.mark.parametrize("claim", ["회사명: 믿음", "회사 개요: 세계 최고 기업", "인증·승인·특허 획득",
                                   "대응 범위 국내 최대", "납기 조건: 2일 보장"])
def test_section_label_with_added_claim_stays_blocked_after_revalidation(app, claim):
    ctx = Ctx(app, with_photo=False)
    ctx.patch([{"op": "insert_block", "page_id": "page_01", "after_block_id": None,
                "block": {"block_id": "label", "type": "heading", "content": {"text": "납기 조건", "level": 2}}},
               {"op": "insert_block", "page_id": "page_01", "after_block_id": "label",
                "block": {"block_id": "note", "type": "paragraph", "content": {"text": "추가 확인 필요"}}}])
    ctx.validated()
    assert not any("label" in issue["block_ids"] for issue in ctx.open_issues("UNSUPPORTED_CLAIM"))
    warning = _first(ctx.open_issues("PLACEHOLDER_TEXT"), block_ids=["note"])
    ctx.patch([{"op": "replace_block_content", "block_id": "label", "content": {"text": claim, "level": 2}}])
    ctx.validated()
    issue = _first(ctx.open_issues("UNSUPPORTED_CLAIM"), block_ids=["label"])
    assert issue["severity"] == "blocker"
    assert ctx.resolve(issue["issue_id"], "acknowledged").status_code == 422
    ctx.patch([{"op": "replace_block_content", "block_id": "label", "content": {"text": "납기 조건", "level": 2}}])
    ctx.validated()
    assert _first(ctx.issues(), issue_id=issue["issue_id"])["status"] == "resolved"
    assert _first(ctx.issues(), issue_id=warning["issue_id"])["status"] == "open"


def test_preflight_confirmation_warning_is_not_promoted_and_source_blocker_is_preserved():
    from types import SimpleNamespace
    from app.models import Fact, Issue
    fact = Fact(fact_id="f", field_key="lead_time", value="확인 필요", status="needs_confirmation")
    warning = Issue(issue_id="warning", scope="content", code="LEAD_TIME_UNQUANTIFIED", severity="warning",
                    message="납기 확인 필요", fact_ids=["f"])
    ctx = SimpleNamespace(facts={"f": fact}, preflight_issues=[warning])
    assert validation.preflight_conflicts(ctx) == []
    ctx.preflight_issues.append(Issue(issue_id="source_blocker", scope="source", code="SOURCE_REVIEW_REQUIRED",
                                     severity="blocker", message="자료 확인 필요", source_ids=["s"]))
    problems = validation.preflight_conflicts(ctx)
    assert len(problems) == 1 and problems[0].scope == "source" and problems[0].source_ids == ["s"]


def _add_unconfirmed_preflight_blocker(monkeypatch):
    from app.models import Fact, Issue
    original = MockAgent.analyze

    async def analyze(self, request):
        result = await original(self, request)
        source = next(source for source in request.sources if source.segments)
        evidence = next(fact.evidence_refs for fact in result.facts if fact.evidence_refs)
        result.facts.append(Fact(fact_id="fact_night_unconfirmed", field_key="night_operation",
                                 value="야간 운영 확인 필요", status="needs_confirmation", evidence_refs=evidence))
        result.issues.append(Issue(issue_id="preflight_night_blocker", scope="content", code="UNSUPPORTED_CLAIM",
                                  severity="blocker", message="야간 운영 여부는 자료 보완 후 확인해야 합니다.",
                                  fact_ids=["fact_night_unconfirmed"], source_ids=[source.source_id]))
        return result

    monkeypatch.setattr(MockAgent, "analyze", analyze)
    return original


@pytest.fixture(params=["legacy", "orm_v11"])
def preflight_bridge_app(tmp_path, request):
    from app.db import init_orm_db, ORM_SCHEMA_VERSION, SCHEMA_VERSION
    settings = Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "bridge.sqlite3",
                        cleanup_sweep_interval_s=0)
    if request.param == "orm_v11":
        init_orm_db(settings.db_path, settings.private_runs_dir)
    yield create_app(settings), settings
    with connect(settings.db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == (ORM_SCHEMA_VERSION if request.param == "orm_v11" else SCHEMA_VERSION)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_unreferenced_needs_confirmation_blocker_survives_initial_save_and_partial_validation(preflight_bridge_app, monkeypatch):
    app, settings = preflight_bridge_app
    original = _add_unconfirmed_preflight_blocker(monkeypatch)
    ctx = Ctx(app, with_photo=False)
    assert not any("fact_night_unconfirmed" in b["fact_ids"] for p in ctx.doc()["pages"] for b in p["blocks"])
    issue = next(i for i in ctx.open_issues("UNSUPPORTED_CLAIM") if i["origin"] == "preflight")
    assert issue["fact_ids"] == ["fact_night_unconfirmed"] and issue["block_ids"] == []
    assert ctx.doc()["status"] == "review_required"
    assert ctx.c.get(f"/api/v1/sessions/{ctx.sid}").json()["document_summary"]["status"] == "review_required"
    checked = ctx.make_clean_and_validate(settings)
    assert checked["status"] == "failed"
    assert ctx.approve(checked["validation_id"], ctx.layout_row(settings)).status_code == 422
    for action in ("resolved", "excluded", "acknowledged"):
        assert ctx.resolve(issue["issue_id"], action).status_code == 422
    paragraph = next(b for p in ctx.doc()["pages"] for b in p["blocks"] if b["type"] == "paragraph")
    ctx.patch([{"op": "replace_block_content", "block_id": paragraph["block_id"],
                "content": dict(paragraph["content"], text=paragraph["content"]["text"] + " ")}])
    assert ctx.validated()["status"] == "failed"
    assert _first(ctx.issues(), issue_id=issue["issue_id"])["status"] == "open"
    # 최신 점검에서 원인이 사라져도 클릭으로 닫지 않고 문서 재검증을 거친다.
    monkeypatch.setattr(MockAgent, "analyze", original)
    ctx.preflight()
    assert ctx.resolve(issue["issue_id"], "resolved").status_code == 422
    assert ctx.validated()["status"] == "passed"
    assert _first(ctx.issues(), issue_id=issue["issue_id"])["status"] == "resolved"


def test_same_input_recheck_non_conflict_blocker_invalidates_current_approval(preflight_bridge_app, monkeypatch):
    app, settings = preflight_bridge_app
    ctx = Ctx(app)
    checked = ctx.make_clean_and_validate(settings)
    approved = ctx.approve(checked["validation_id"], ctx.layout_row(settings))
    assert approved.status_code == 201
    _add_unconfirmed_preflight_blocker(monkeypatch)
    ctx.preflight()
    assert ctx.get()["approval"] is None and ctx.doc()["status"] == "review_required"
    assert any(i["origin"] == "preflight" for i in ctx.open_issues("UNSUPPORTED_CLAIM"))
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT status FROM approvals WHERE approval_id=?", (approved.json()["approval_id"],)).fetchone()[0] == "invalidated"


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


@pytest.mark.parametrize("status,linked,available,visible,expected", [
    ("needs_confirmation", True, True, True, "자료 점검에서"),
    ("supported", False, True, True, "연결되지 않았습니다"),
    ("supported", True, False, True, "자료 점검에서"),
    ("supported", True, True, False, "문서 전체에서"),
    ("supported", True, True, True, None),
])
def test_required_company_diagnostic_locates_text_without_weakening_checks(status, linked, available, visible, expected):
    from app.models import Document, Fact
    from app.services.refs import SessionRefs
    fact = Fact(fact_id="f_name", field_key="company_name", value="예시 회사", status=status)
    doc = Document(document_id="doc", session_id="sess", document_revision=1, input_revision=1,
                   title="소개", target_pages=1, status="draft", pages=[{
                       "page_id": "p1", "title": "소개", "layout_key": "text_photo", "blocks": [{
                           "block_id": "b1", "type": "heading", "content": {"text": "예시 회사 | 소개" if visible else "소개", "level": 1},
                           "fact_ids": ["f_name"] if linked else [],
                       }]}])
    ctx = validation.Context({}, {}, {}, set(), SessionRefs(set(), {}, set(), {"f_name"} if available else set()),
                             {fact.fact_id: fact}, [])
    issues, _ = validation.server_checks(doc, ctx)
    required = [i for i in issues if i.code == "REQUIRED_MISSING" and i.fact_ids == ["f_name"]]
    if expected is None:
        assert required == []
    else:
        assert len(required) == 1 and required[0].severity == "blocker"
        assert expected in required[0].message
        assert required[0].block_ids == (["b1"] if visible else [])


def test_required_business_content_accepts_related_fact_kinds(app, settings):
    """company_summary가 없어도 business_areas 설명이 실제 블록에 있으면 인정."""
    txt = "회사명: 예시 회사\n사업 분야: 가상 부품 표면처리\n".encode()
    ctx = Ctx(app, txt=txt, with_photo=False)
    ctx.make_clean_and_validate(settings)
    assert ctx.open_issues("REQUIRED_MISSING") == []


def test_company_alias_match_is_explicit_and_does_not_accept_unrelated_claim(monkeypatch):
    from app.models import Fact
    monkeypatch.setenv("COMPANY_NAME_ALIASES", json.dumps([["㈜가상표면기술", "가상표면기술", "EXAMPLE SURFACE"]]))
    fact = Fact(fact_id="f", field_key="company_name", value="EXAMPLE SURFACE", status="supported")
    assert validation._required_value_in_text(fact, "가상표면기술 | 소개")
    assert not validation._required_value_in_text(fact, "다른가상표면기술회사")
    business = fact.model_copy(update={"field_key": "business_areas"})
    assert not validation._required_value_in_text(business, "가상표면기술 | 소개")
    monkeypatch.delenv("COMPANY_NAME_ALIASES")
    assert not validation._required_value_in_text(fact, "가상표면기술 | 소개")


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


def test_llm_mode_without_impl_fails(tmp_path, monkeypatch):
    """실제 파일·모델 설정과 무관하게 Agent 불러오기 실패가 작업 실패로 이어진다."""
    from unittest.mock import Mock

    import_stub = Mock()
    import_stub.import_module.side_effect = ModuleNotFoundError("테스트용 Agent 불러오기 실패")
    monkeypatch.setattr("app.agent_bridge.importlib", import_stub)
    settings = Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "t.sqlite3", agent_mode="llm")
    app = create_app(settings)
    c = TestClient(app)
    sid = c.post("/api/v1/sessions", json={"brief": BRIEF}).json()["session_id"]
    up = c.post(f"/api/v1/sessions/{sid}/sources", files=[("files", ("a.txt", io.BytesIO(CLEAN_TXT)))]).json()
    c.patch(f"/api/v1/sessions/{sid}/inputs", json={"expected_input_revision": 1, "selected_source_ids": [up["items"][0]["source_id"]]})
    r = c.post(f"/api/v1/sessions/{sid}/preflights", json={"expected_input_revision": 2})
    job = c.get(f"/api/v1/sessions/{sid}/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "failed" and "agent_llm" in job["error"]["message"]
    assert job["error"]["code"] == "SERVICE_TEMPORARY_FAILURE"
    import_stub.import_module.assert_called_once_with("app.agent_llm")


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

@pytest.mark.parametrize("caption,registered,expected", [
    ("소개서의 생산라인 사진", None, True),
    ("AI 생성 시연 콘셉트: 어두운 배경의 금속 부품 표지 이미지 · 실제 회사 제품 아님", "AI 생성 시연 콘셉트: 어두운 배경의 금속 부품 표지 이미지 · 실제 회사 제품 아님", True),
    ("AI 생성 시연 콘셉트: 어두운 배경의 금속 부품 표지 이미지 · 실제 회사 제품 아님", None, False),
    ("국내 최대 규모 설비 사진", "국내 최대 규모 설비 사진", False),
    ("ISO 9001 인증 설비", "ISO 9001 인증 설비", False),
    ("생산 능력 300개", "생산 능력 300개", False),
])
def test_photo_description_is_not_doubled_or_promoted_to_factual_evidence(caption, registered, expected):
    from types import SimpleNamespace
    from app.models import Block
    block = Block(block_id="photo", type="image", content={"asset_id": "asset", "caption": caption, "alt": caption})
    ctx = SimpleNamespace(asset_captions={"asset": registered} if registered else {})
    assert validation.image_has_descriptive_caption(block, ctx) is expected
    block.content["alt"] = "실제 회사가 보유한 최대 규모 설비"
    assert not validation.image_has_descriptive_caption(block, ctx)


def test_duplicate_caption_and_alt_do_not_create_server_blocker(app):
    ctx = Ctx(app)
    block = next(b for p in ctx.doc()["pages"] for b in p["blocks"] if b["type"] == "image")
    ctx.patch([{"op": "replace_block_content", "block_id": block["block_id"], "content": {
        **block["content"], "caption": "소개서의 생산라인 사진", "alt": "소개서의 생산라인 사진"}}])
    ctx.validated()
    assert not any(block["block_id"] in i["block_ids"] for i in ctx.open_issues("UNSUPPORTED_CLAIM"))


@pytest.fixture
def c05_ctx(tmp_path):
    from app.db import init_orm_db

    settings = Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "c05.sqlite3",
                        cleanup_sweep_interval_s=0)
    init_orm_db(settings.db_path, settings.private_runs_dir)
    app = create_app(settings)
    with TestClient(app):
        ctx = Ctx(app)
        try:
            yield ctx, settings
        finally:
            ctx.c.close()


def _c05_review(ctx, *, selected=None):
    body = {"expected_input_revision": ctx.rev_in, "brief": {**BRIEF, "purpose": "자료 보완 후 기존 편집 유지"}}
    if selected is not None:
        body["selected_source_ids"] = selected
    response = ctx.c.patch(f"/api/v1/sessions/{ctx.sid}/inputs", json=body)
    assert response.status_code == 200, response.text
    ctx.rev_in = response.json()["input_revision"]
    ctx.preflight()
    route = f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/impact-reviews"
    response = ctx.c.post(route, json={"expected_revision": ctx.rev(), "input_revision": ctx.rev_in,
                                     "preflight_id": ctx.pf, "confirmed": True})
    assert response.status_code == 201, response.text
    return route + "/" + response.json()["review_id"] + "/apply"


def _c05_apply(ctx, route, **extra):
    response = ctx.c.post(route, json={"expected_revision": ctx.rev(), "input_revision": ctx.rev_in,
                                     "keep_reason": "선택 자료와 기존 편집의 근거를 대조했습니다.", **extra})
    assert response.status_code == 200, response.text
    return response.json()


def test_c05_apply_runs_full_validation_preserves_blockers_and_invalidates_approval(c05_ctx):
    ctx, settings = c05_ctx
    checked = ctx.make_clean_and_validate(settings)
    approval_response = ctx.approve(checked["validation_id"], ctx.layout_row(settings))
    assert approval_response.status_code == 201, approval_response.text
    approval_id = approval_response.json()["approval_id"]
    ctx.patch([{"op": "insert_block", "page_id": "page_02", "after_block_id": None,
                "block": {"block_id": "c05_existing_claim", "type": "paragraph",
                          "content": {"text": "추가 근거가 없는 회사의 새로운 사업 설명입니다."}}}])
    assert ctx.validated()["status"] == "failed"
    blocker = _first(ctx.open_issues("UNSUPPORTED_CLAIM"), block_ids=["c05_existing_claim"])
    before = ctx.doc()
    applied = _c05_apply(ctx, _c05_review(ctx))
    assert ctx.job(applied["validation_job_id"])["status"] == "succeeded"
    after = ctx.get()
    assert after["document"]["pages"] == before["pages"]
    assert after["validation"]["input_revision"] == ctx.rev_in
    assert set(after["validation"]["checked_block_ids"]) == {
        block["block_id"] for page in before["pages"] for block in page["blocks"]}
    assert after["validation"]["status"] == "failed"
    assert _first(ctx.issues(), issue_id=blocker["issue_id"])["status"] == "open"
    assert after["approval"] is None and after["document"]["status"] == "review_required"
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT status FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()[0] == "invalidated"


def test_c05_followup_edit_cannot_reintroduce_an_old_preflight_fact(c05_ctx, monkeypatch):
    ctx, _ = c05_ctx
    old = next(block for page in ctx.doc()["pages"] for block in page["blocks"]
               if block["type"] == "paragraph" and block["fact_ids"])
    original = MockAgent.analyze

    async def new_ids(self, request):
        result = await original(self, request)
        for fact in result.facts:
            fact.fact_id = "c05_latest_" + fact.fact_id
        for issue in result.issues:
            issue.fact_ids = ["c05_latest_" + fid for fid in issue.fact_ids]
        return result

    monkeypatch.setattr(MockAgent, "analyze", new_ids)
    applied = _c05_apply(ctx, _c05_review(ctx))
    assert ctx.job(applied["validation_job_id"])["status"] == "succeeded"
    assert not ctx.open_issues("EVIDENCE_INVALID")
    ctx.patch([{"op": "insert_block", "page_id": "page_02", "after_block_id": None,
                "block": {**old, "block_id": "c05_old_fact"}}])
    assert ctx.validated()["status"] == "failed"
    assert _first(ctx.open_issues("EVIDENCE_INVALID"), block_ids=["c05_old_fact"])["severity"] == "blocker"


def test_c05_followup_edit_cannot_reintroduce_a_deselected_photo(c05_ctx):
    ctx, settings = c05_ctx
    old = next(block for page in ctx.doc()["pages"] for block in page["blocks"] if block["type"] == "image")
    with connect(settings.db_path) as conn:
        selected = [row[0] for row in conn.execute("SELECT source_id FROM sources WHERE session_id=? AND mime_type='text/plain'",
                                                 (ctx.sid,))]
    assert selected
    applied = _c05_apply(ctx, _c05_review(ctx, selected=selected),
                         operations=[{"op": "delete_block", "block_id": old["block_id"]}])
    assert ctx.job(applied["validation_job_id"])["status"] == "succeeded"
    assert not ctx.open_issues("EVIDENCE_INVALID")
    ctx.patch([{"op": "insert_block", "page_id": "page_01", "after_block_id": None,
                "block": {**old, "block_id": "c05_old_photo"}}])
    assert ctx.validated()["status"] == "failed"
    assert _first(ctx.open_issues("EVIDENCE_INVALID"), block_ids=["c05_old_photo"])["severity"] == "blocker"


@pytest.mark.parametrize("change", ["new_fact_ids", "needs_confirmation"])
def test_c05_same_input_recheck_uses_latest_fact_ids_and_status(c05_ctx, monkeypatch, change):
    from app.services import documents, preflights

    ctx, settings = c05_ctx
    _c05_apply(ctx, _c05_review(ctx))
    bound_pf = ctx.pf
    target = next(block for page in ctx.doc()["pages"] for block in page["blocks"]
                  if block["type"] == "paragraph" and block["fact_ids"])
    fact_id = target["fact_ids"][0]
    original = MockAgent.analyze

    async def rechecked(self, request):
        result = await original(self, request)
        for fact in result.facts:
            if change == "new_fact_ids":
                fact.fact_id = "rechecked_" + fact.fact_id
            elif fact.fact_id == fact_id:
                fact.status = "needs_confirmation"
        if change == "new_fact_ids":
            for issue in result.issues:
                issue.fact_ids = ["rechecked_" + fid for fid in issue.fact_ids]
        return result

    monkeypatch.setattr(MockAgent, "analyze", rechecked)
    ctx.preflight()
    assert ctx.pf != bound_pf
    with connect(settings.db_path) as conn:
        context = validation.load_context(conn, ctx.sid, preflights.get(conn, ctx.sid, bound_pf))
        assert context.selected_preflight.preflight_id == ctx.pf
        drafts, _ = validation.server_checks(documents.get_current(conn, ctx.sid, ctx.did), context)
        assert any(d.code == "EVIDENCE_INVALID" and d.block_ids == [target["block_id"]] for d in drafts)
    assert ctx.validated()["status"] == "failed"
    assert _first(ctx.open_issues("EVIDENCE_INVALID"), block_ids=[target["block_id"]])["severity"] == "blocker"


def test_c05_applied_document_stays_review_required_without_completed_validation(c05_ctx, monkeypatch):
    from app.services import ai_jobs, jobs

    ctx, settings = c05_ctx
    route = _c05_review(ctx)
    monkeypatch.setattr(ai_jobs, "run_validate_job", lambda *args: None)
    applied = _c05_apply(ctx, route)
    out = ctx.get()
    assert out["validation"] is None and out["document"]["status"] == "review_required"
    with connect(settings.db_path) as conn:
        jobs.fail(conn, applied["validation_job_id"], "SERVICE_TEMPORARY_FAILURE", "가짜 검증 실패", True)
    out = ctx.get()
    assert out["validation"] is None and out["document"]["status"] == "review_required"


def test_v11_without_applied_impact_review_keeps_legacy_context(c05_ctx):
    from app.services import preflights

    ctx, settings = c05_ctx
    with connect(settings.db_path) as conn:
        context = validation.load_context(conn, ctx.sid, preflights.get(conn, ctx.sid, ctx.pf))
    assert context.selected_sources is None and context.selected_preflight is None


def test_c05_same_input_preflight_invalidates_approval_and_previous_validation(c05_ctx):
    ctx, settings = c05_ctx
    applied = _c05_apply(ctx, _c05_review(ctx))
    assert ctx.job(applied["validation_job_id"])["status"] == "succeeded"
    checked = ctx.make_clean_and_validate(settings)
    assert checked["status"] == "passed"
    layout_id = ctx.layout_row(settings)
    response = ctx.approve(checked["validation_id"], layout_id)
    assert response.status_code == 201, response.text
    approval_id = response.json()["approval_id"]
    before, previous_pf = ctx.doc(), ctx.pf
    original_facts = ctx.c.get(f"/api/v1/sessions/{ctx.sid}/preflights/{previous_pf}").json()["facts"]

    ctx.preflight()  # 같은 입력·같은 사실도 새로운 점검 결과다.
    latest_pf = ctx.c.get(f"/api/v1/sessions/{ctx.sid}/preflights/{ctx.pf}").json()
    assert ctx.pf != previous_pf and latest_pf["facts"] == original_facts
    assert latest_pf["input_revision"] == before["input_revision"] and latest_pf["confirmed_at"] is None
    after = ctx.get()
    assert after["document"]["pages"] == before["pages"]
    assert after["document"]["document_revision"] == before["document_revision"]
    assert after["approval"] is None and after["validation"] is None
    with connect(settings.db_path) as conn:
        approval = conn.execute("SELECT status, invalidated_reason FROM approvals WHERE approval_id=?",
                                (approval_id,)).fetchone()
        assert tuple(approval) == ("invalidated", "preflight_changed")
        assert validation.latest_validation(conn, ctx.did, ctx.rev(), ctx.rev_in) is None
        assert conn.execute("SELECT validation_id FROM validations WHERE validation_id=?",
                            (checked["validation_id"],)).fetchone() is not None
    rejected = ctx.approve(checked["validation_id"], layout_id)
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "VALIDATION_NOT_PASSED"


def test_c05_same_input_new_preflight_can_be_confirmed_and_rebound_with_full_validation(c05_ctx):
    ctx, settings = c05_ctx
    _c05_apply(ctx, _c05_review(ctx))
    ctx.make_clean_and_validate(settings)
    previous_pf, unchanged_input = ctx.pf, ctx.rev_in
    ctx.preflight()
    assert ctx.pf != previous_pf and ctx.rev_in == unchanged_input
    # 적용 전에 새 점검으로 검증해도 명시 확인과 적용 후 전체 검증을 대신하지 않는다.
    prior_check = ctx.validated()
    assert prior_check["status"] == "passed"
    denied = ctx.approve(prior_check["validation_id"], ctx.layout_row(settings))
    assert denied.status_code == 422 and denied.json()["error"]["code"] == "PREFLIGHT_NOT_CONFIRMED"
    before = ctx.doc()
    route = f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/impact-reviews"
    response = ctx.c.post(route, json={"expected_revision": ctx.rev(), "input_revision": ctx.rev_in,
                                      "preflight_id": ctx.pf, "confirmed": True})
    assert response.status_code == 201, response.text
    review = response.json()
    assert review["from_input_revision"] == review["to_input_revision"] == unchanged_input
    assert review["preflight_id"] == ctx.pf and review["status"] == "pending"
    assert ctx.doc() == before
    calls_before_apply = MockAgent.validate_calls
    applied = _c05_apply(ctx, route + "/" + review["review_id"] + "/apply")
    assert applied["document_revision"] == before["document_revision"] + 1
    assert applied["input_revision"] == unchanged_input
    assert ctx.job(applied["validation_job_id"])["status"] == "succeeded"
    out = ctx.get()
    checked = out["validation"]
    assert out["document"]["pages"] == before["pages"]
    assert checked["validation_id"] != prior_check["validation_id"]
    assert checked["agent_called"] and MockAgent.validate_calls == calls_before_apply + 1
    assert checked["base_validation_id"] is None and checked["reused_block_ids"] == []
    assert set(checked["checked_block_ids"]) == {block["block_id"] for page in before["pages"] for block in page["blocks"]}
    with connect(settings.db_path) as conn:
        bound = conn.execute("SELECT input_revision, preflight_id FROM document_revisions WHERE document_id=? AND revision=?",
                             (ctx.did, applied["document_revision"])).fetchone()
        assert tuple(bound) == (unchanged_input, ctx.pf)
        check_records = json.loads(conn.execute("SELECT checks_json FROM validations WHERE validation_id=?",
                                               (checked["validation_id"],)).fetchone()[0])
        assert any(record["check_key"] == "preflight:" + ctx.pf for record in check_records)


@pytest.mark.parametrize("previously_applied", [False, True])
def test_c05_confirming_review_cannot_skip_apply_before_final_approval(c05_ctx, previously_applied):
    ctx, settings = c05_ctx
    if previously_applied:
        _c05_apply(ctx, _c05_review(ctx))
    assert ctx.make_clean_and_validate(settings)["status"] == "passed"
    before = ctx.doc()
    ctx.preflight()  # 입력·사실 ID는 같아도 새 점검의 영향 검토를 적용해야 한다.
    route = f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/impact-reviews"
    response = ctx.c.post(route, json={"expected_revision": ctx.rev(), "input_revision": ctx.rev_in,
                                      "preflight_id": ctx.pf, "confirmed": True})
    assert response.status_code == 201, response.text
    review = response.json()
    assert review["status"] == "pending"
    confirmed = ctx.c.get(f"/api/v1/sessions/{ctx.sid}/preflights/{ctx.pf}").json()
    assert confirmed["confirmed_at"] is not None
    checked = ctx.validated()
    assert checked["status"] == "passed"
    denied = ctx.approve(checked["validation_id"], ctx.layout_row(settings))
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "IMPACT_REVIEW_REQUIRED"
    assert ctx.get()["approval"] is None
    assert ctx.doc()["document_revision"] == before["document_revision"]
    assert ctx.doc()["pages"] == before["pages"]
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM confirmations WHERE impact_review_id=?",
                            (review["review_id"],)).fetchone()[0] == 0

    applied = _c05_apply(ctx, route + "/" + review["review_id"] + "/apply")
    assert ctx.job(applied["validation_job_id"])["status"] == "succeeded"
    after = ctx.get()
    assert after["validation"]["status"] == "passed"
    assert after["document"]["document_revision"] == before["document_revision"] + 1
    approved = ctx.approve(after["validation"]["validation_id"], ctx.layout_row(settings))
    assert approved.status_code == 201, approved.text
    assert ctx.get()["approval"]["approval_id"] == approved.json()["approval_id"]
