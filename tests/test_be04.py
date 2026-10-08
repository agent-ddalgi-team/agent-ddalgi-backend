"""BE-04 확인: 사전 점검·초안 Job, 오래된 입력·미확인·중복·만료 처리, AI 결과 검사, 문서 버전 구조, llm 미연결.

mock Agent 결과는 가짜 회사("예시 회사") 기준. 실제 회사 정보 없음. 실제 LLM 호출 없음.
"""
from __future__ import annotations

import io
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.agent_bridge import AgentError, AnalyzeResult, DraftResult
from app.agent_mock import MockAgent
from app.config import Settings
from app.db import connect, init_orm_db
from app.models import Block, EvidenceRef, Page
from app.services import ai_jobs

BRIEF = {"purpose": "테스트", "emphasis": [], "direction": "balanced", "target_pages": 4, "photo_preference": "balanced"}
SOURCE_A = "회사명: 예시 회사\n회사 개요: 예시용 기업입니다.\n사업 분야: 예시 사업 A\n공정 수: 2개\n납기 표현: 빠른 납기\n".encode()
SOURCE_B = "회사명: 다른 예시 회사\n".encode()


@pytest.fixture
def settings(tmp_path):
    return Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "t.sqlite3")


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


def _session(client) -> str:
    return client.post("/api/v1/sessions", json={"brief": BRIEF}).json()["session_id"]


def _upload(client, sid, *files, kind=None) -> list[str]:
    r = client.post(f"/api/v1/sessions/{sid}/sources", data={"kind": kind} if kind else None,
                    files=[("files", (n, io.BytesIO(c))) for n, c in files])
    assert r.status_code == 202, r.text
    return [i["source_id"] for i in r.json()["items"]]


def _select(client, sid, source_ids, expected=1) -> int:
    r = client.patch(f"/api/v1/sessions/{sid}/inputs",
                     json={"expected_input_revision": expected, "selected_source_ids": source_ids})
    assert r.status_code == 200, r.text
    return r.json()["input_revision"]


def _preflight(client, sid, rev) -> dict:
    r = client.post(f"/api/v1/sessions/{sid}/preflights", json={"expected_input_revision": rev})
    assert r.status_code == 202, r.text
    job = client.get(f"/api/v1/sessions/{sid}/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "succeeded", job
    return client.get(f"/api/v1/sessions/{sid}/preflights/{job['result_ref']['preflight_id']}").json()


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (0, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


# ---------------- 사전 점검 ----------------

def test_preflight_facts_issues_and_can_generate(client):
    sid = _session(client)
    src = _upload(client, sid, ("a.txt", SOURCE_A), ("photo.png", _png()))
    rev = _select(client, sid, src)
    pf = _preflight(client, sid, rev)
    assert pf["input_revision"] == rev and pf["can_generate"] is True and pf["confirmed_at"] is None
    assert pf["usable_source_ids"] == [src[0]]  # 이미지는 텍스트 근거가 아님
    by_key = {f["field_key"]: f for f in pf["facts"]}
    assert len(by_key) == 14
    assert by_key["company_name"]["status"] == "supported" and by_key["company_name"]["value"] == "예시 회사"
    ev = by_key["company_name"]["evidence_refs"][0]
    assert ev["source_id"] == src[0] and ev["segment_id"].startswith("seg_") and ev["locator"] == {"line_start": 1, "line_end": 1}
    assert by_key["lead_time"]["status"] == "needs_confirmation"
    assert by_key["certifications"]["status"] == "missing" and by_key["certifications"]["evidence_refs"] == []
    codes = {i["code"]: i for i in pf["issues"]}
    assert codes["LEAD_TIME_UNQUANTIFIED"]["severity"] == "warning" and codes["LEAD_TIME_UNQUANTIFIED"]["fact_ids"] == [by_key["lead_time"]["fact_id"]]
    assert codes["IMAGE_ONLY_SOURCE"]["source_ids"] == [src[1]]
    assert "REQUIRED_MISSING" not in codes
    assert pf["recommendations"]["suggested_pages"] == 4


def test_preflight_conflict_and_required_missing(client):
    sid = _session(client)
    src = _upload(client, sid, ("a.txt", SOURCE_A), ("b.txt", SOURCE_B))
    pf = _preflight(client, sid, _select(client, sid, src))
    name = next(f for f in pf["facts"] if f["field_key"] == "company_name")
    assert name["status"] == "conflict" and name["value"] is None and len(name["alternatives"]) == 2
    assert any(i["code"] == "VALUE_CONFLICT" and i["severity"] == "blocker" for i in pf["issues"])
    sid2 = _session(client)
    src2 = _upload(client, sid2, ("only.txt", "사업 분야: 뭔가".encode()))
    pf2 = _preflight(client, sid2, _select(client, sid2, src2))
    assert sum(1 for i in pf2["issues"] if i["code"] == "REQUIRED_MISSING") == 2


def test_preflight_without_text_sources_cannot_generate(client):
    sid = _session(client)
    src = _upload(client, sid, ("photo.png", _png()))
    pf = _preflight(client, sid, _select(client, sid, src))
    assert pf["can_generate"] is False and pf["usable_source_ids"] == []
    r = client.post(f"/api/v1/sessions/{sid}/drafts",
                    json={"preflight_id": pf["preflight_id"], "input_revision": 2, "confirmed": True})
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_USABLE_TEXT"


def test_preflight_stale_revision_409_and_duplicate_returns_same_job(client, settings):
    sid = _session(client)
    src = _upload(client, sid, ("a.txt", SOURCE_A))
    rev = _select(client, sid, src)
    r = client.post(f"/api/v1/sessions/{sid}/preflights", json={"expected_input_revision": rev - 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INPUT_REVISION_CONFLICT"
    # 진행 중인 같은 버전의 작업이 있으면 그 job을 돌려준다
    with connect(settings.db_path) as conn:
        conn.execute("INSERT INTO jobs (job_id, session_id, kind, status, progress_json, input_revision, created_at, updated_at) "
                     "VALUES ('job_running', ?, 'preflight', 'running', '{\"stage\":\"analyzing\",\"message\":null}', ?, 'x', 'x')",
                     (sid, rev))
    r = client.post(f"/api/v1/sessions/{sid}/preflights", json={"expected_input_revision": rev})
    assert r.status_code == 202 and r.json() == {"job_id": "job_running", "status": "running", "kind": "preflight",
                                                 "session_id": sid, "created_at": "x"}


def test_preflight_job_fails_if_input_changed_while_running(client, settings, monkeypatch):
    """Job이 도는 사이 입력이 바뀌면 결과를 버린다(오래된 점검 사용 금지, BR-05)."""
    sid = _session(client)
    src = _upload(client, sid, ("a.txt", SOURCE_A))
    rev = _select(client, sid, src)
    original = MockAgent.analyze

    async def analyze_then_bump(self, request):
        with connect(settings.db_path) as conn:
            conn.execute("UPDATE sessions SET input_revision=input_revision+1 WHERE session_id=?", (sid,))
        return await original(self, request)

    monkeypatch.setattr(MockAgent, "analyze", analyze_then_bump)
    r = client.post(f"/api/v1/sessions/{sid}/preflights", json={"expected_input_revision": rev})
    job = client.get(f"/api/v1/sessions/{sid}/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "failed" and job["error"]["code"] == "INPUT_REVISION_CONFLICT"
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM preflights WHERE session_id=?", (sid,)).fetchone()[0] == 0


@pytest.fixture(params=[9, 10], ids=["legacy-v9", "orm-v10"])
def retry_api(request, tmp_path):
    settings = Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "retry.sqlite3",
                        agent_mode="mock", cleanup_sweep_interval_s=0)
    if request.param == 10:
        init_orm_db(settings.db_path, settings.private_runs_dir)
    with TestClient(create_app(settings)) as client:
        yield client, settings


@pytest.mark.parametrize("kind", ["preflight", "draft"])
@pytest.mark.parametrize("terminal", ["succeeded", "failed"])
def test_active_job_join_key_replays_after_finish_and_explicit_retry(retry_api, monkeypatch, kind, terminal):
    """진행 중 작업을 받은 요청도 첫 응답을 기억한다. 실패 재시도는 새 키로 명시한다."""
    client, settings = retry_api
    sid = _session(client)
    source_ids = _upload(client, sid, ("fake.txt", SOURCE_A))
    rev = _select(client, sid, source_ids)
    base = f"/api/v1/sessions/{sid}"
    path = f"{base}/{kind}s"
    body = {"expected_input_revision": rev}
    if kind == "draft":
        pf = _preflight(client, sid, rev)
        body = {"preflight_id": pf["preflight_id"], "input_revision": rev, "confirmed": True}
    accepted_body = dict(body)

    runner_name = f"run_{kind}_job"
    runner = getattr(ai_jobs, runner_name)
    scheduled = []
    monkeypatch.setattr(ai_jobs, runner_name, lambda *args: scheduled.append(args))
    first = client.post(path, json=body, headers={"Idempotency-Key": "original"})
    joined = client.post(path, json=body, headers={"Idempotency-Key": "joined"})
    assert first.status_code == joined.status_code == 202
    assert joined.json() == first.json()
    assert len(scheduled) == 1

    method_name = "analyze" if kind == "preflight" else "draft"
    original_method = getattr(MockAgent, method_name)
    calls = []

    async def finish(self, request):
        calls.append(request)
        if terminal == "failed":
            raise AgentError("AI_RATE_LIMIT", "가짜 일시 실패", retryable=True)
        return await original_method(self, request)

    monkeypatch.setattr(MockAgent, method_name, finish)
    runner(*scheduled[0])
    monkeypatch.setattr(ai_jobs, runner_name, runner)
    job_url = f"{base}/jobs/{first.json()['job_id']}"
    job = client.get(job_url).json()
    assert job["status"] == terminal

    replay = client.post(path, json=body, headers={"Idempotency-Key": "joined"})
    assert replay.status_code == 202 and replay.json() == joined.json()
    changed_body = dict(body)
    changed_body["expected_input_revision" if kind == "preflight" else "input_revision"] = rev + 1
    conflict = client.post(path, json=changed_body, headers={"Idempotency-Key": "joined"})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert len(calls) == 1
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE session_id=? AND kind=?", (sid, kind)).fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    # 실패는 이전 작업에 남는다. 자료와 선택은 보존하고 새 요청으로 다시 진행할 수 있다.
    if terminal == "failed":
        monkeypatch.setattr(MockAgent, method_name, original_method)
        session = client.get(base).json()
        assert session["selected_source_ids"] == source_ids and session["input_revision"] == rev
        assert session["document_summary"] is None
        if kind == "draft":
            latest = _preflight(client, sid, rev)
            body = {**body, "preflight_id": latest["preflight_id"]}
        retry = client.post(path, json=body, headers={"Idempotency-Key": "explicit-retry"})
        assert retry.status_code == 202 and retry.json()["job_id"] != first.json()["job_id"]
        assert client.get(f"{base}/jobs/{retry.json()['job_id']}").json()["status"] == "succeeded"
        assert client.get(job_url).json() == job
        assert client.post(path, json=body, headers={"Idempotency-Key": "explicit-retry"}).json() == retry.json()

    # 새 합류 키도 세션에 묶여 종료 뒤에는 캐시 응답을 노출하지 않는다.
    assert client.delete(base).status_code == 200
    gone = client.post(path, json=accepted_body,
                       headers={"Idempotency-Key": "joined"})
    assert gone.status_code == 410 and gone.json()["error"]["code"] == "SESSION_EXPIRED"


def test_agent_output_with_unknown_segment_is_rejected(client, settings, monkeypatch):
    sid = _session(client)
    src = _upload(client, sid, ("a.txt", SOURCE_A))
    rev = _select(client, sid, src)
    original = MockAgent.analyze

    async def tampered(self, request):
        result = await original(self, request)
        fact = next(f for f in result.facts if f.status == "supported")
        fact.evidence_refs[0] = replace_ref(fact.evidence_refs[0], segment_id="seg_not_in_session")
        return result

    monkeypatch.setattr(MockAgent, "analyze", tampered)
    r = client.post(f"/api/v1/sessions/{sid}/preflights", json={"expected_input_revision": rev})
    job = client.get(f"/api/v1/sessions/{sid}/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "failed" and job["error"]["code"] == "AGENT_OUTPUT_INVALID"


def replace_ref(ref: EvidenceRef, **kw) -> EvidenceRef:
    return EvidenceRef(**{**ref.model_dump(), **kw})


def test_agent_error_maps_to_job_error(client, monkeypatch):
    sid = _session(client)
    src = _upload(client, sid, ("a.txt", SOURCE_A))
    rev = _select(client, sid, src)

    async def boom(self, request):
        raise AgentError("AI_RATE_LIMIT", "한도 초과", retryable=True)

    monkeypatch.setattr(MockAgent, "analyze", boom)
    r = client.post(f"/api/v1/sessions/{sid}/preflights", json={"expected_input_revision": rev})
    job = client.get(f"/api/v1/sessions/{sid}/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "failed" and job["error"] == {"code": "AI_RATE_LIMIT", "message": "한도 초과", "retryable": True,
                                                          "details": {}, "request_id": None}


def test_llm_mode_without_agent_impl_fails_not_mocks(tmp_path, monkeypatch):
    """실제 Agent 파일이 있어도 불러오기 실패를 재현하고 mock 대체를 막는다."""
    from unittest.mock import Mock

    import_stub = Mock()
    import_stub.import_module.side_effect = ModuleNotFoundError("테스트용 Agent 불러오기 실패")
    # 연결부의 importlib 참조만 교체한다. 다른 라이브러리의 import에는 영향을 주지 않는다.
    monkeypatch.setattr("app.agent_bridge.importlib", import_stub)
    settings = Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "t.sqlite3", agent_mode="llm")
    client = TestClient(create_app(settings))
    sid = _session(client)
    src = _upload(client, sid, ("a.txt", SOURCE_A))
    rev = _select(client, sid, src)
    r = client.post(f"/api/v1/sessions/{sid}/preflights", json={"expected_input_revision": rev})
    job = client.get(f"/api/v1/sessions/{sid}/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "failed" and job["error"]["code"] == "SERVICE_TEMPORARY_FAILURE"
    assert "agent_llm" in job["error"]["message"]
    import_stub.import_module.assert_called_once_with("app.agent_llm")


def test_sync_bridge_implementation_also_works(client, monkeypatch):
    """Agent가 동기 함수로 구현해도 붙는다."""
    sid = _session(client)
    src = _upload(client, sid, ("a.txt", SOURCE_A))
    rev = _select(client, sid, src)
    original = MockAgent.analyze

    def sync_analyze(self, request):
        import asyncio
        return asyncio.run(original(self, request))

    monkeypatch.setattr(MockAgent, "analyze", sync_analyze)
    assert _preflight(client, sid, rev)["can_generate"] is True


# ---------------- 초안 ----------------

def test_draft_requires_confirmation_and_matching_revision(client):
    sid = _session(client)
    src = _upload(client, sid, ("a.txt", SOURCE_A))
    rev = _select(client, sid, src)
    pf = _preflight(client, sid, rev)
    r = client.post(f"/api/v1/sessions/{sid}/drafts", json={"preflight_id": pf["preflight_id"], "input_revision": rev, "confirmed": False})
    assert r.status_code == 422 and r.json()["error"]["code"] == "PREFLIGHT_NOT_CONFIRMED"
    r = client.post(f"/api/v1/sessions/{sid}/drafts", json={"preflight_id": pf["preflight_id"], "input_revision": rev - 1, "confirmed": True})
    assert r.status_code == 409
    r = client.post(f"/api/v1/sessions/{sid}/drafts", json={"preflight_id": "pf_nope", "input_revision": rev, "confirmed": True})
    assert r.status_code == 404
    # 입력이 바뀌면 옛 점검으로 생성 불가(QA-07)
    _select(client, sid, src, expected=rev)
    r = client.post(f"/api/v1/sessions/{sid}/drafts", json={"preflight_id": pf["preflight_id"], "input_revision": rev + 1, "confirmed": True})
    assert r.status_code == 409 and r.json()["error"]["details"]["preflight_input_revision"] == rev


def test_draft_creates_document_rev1_with_evidence_and_asset(client, settings):
    sid = _session(client)
    src = _upload(client, sid, ("a.txt", SOURCE_A), ("photo.png", _png()))
    rev = _select(client, sid, src)
    pf = _preflight(client, sid, rev)
    r = client.post(f"/api/v1/sessions/{sid}/drafts", json={"preflight_id": pf["preflight_id"], "input_revision": rev, "confirmed": True})
    assert r.status_code == 202 and r.json()["kind"] == "draft"
    job = client.get(f"/api/v1/sessions/{sid}/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "succeeded" and job["result_ref"]["type"] == "document"
    did = job["result_ref"]["document_id"]
    assert client.get(f"/api/v1/sessions/{sid}/preflights/{pf['preflight_id']}").json()["confirmed_at"] is not None
    out = client.get(f"/api/v1/sessions/{sid}/documents/{did}").json()
    doc = out["document"]
    assert out["validation"] is None and out["approval"] is None
    assert doc["schema_version"] == "1.0" and doc["document_revision"] == 1 and doc["input_revision"] == rev
    assert doc["title"] == "예시 회사" and doc["target_pages"] == 4 and len(doc["pages"]) == 4
    assert doc["status"] == "draft"  # warning만 있고 blocker 없음
    first = doc["pages"][0]["blocks"]
    assert first[0]["type"] == "heading" and first[0]["content"]["text"] == "예시 회사" and first[0]["fact_ids"]
    assert first[0]["evidence_refs"][0]["segment_id"].startswith("seg_")
    image = next(b for b in first if b["type"] == "image")
    asset_id = image["content"]["asset_id"]
    assert client.get(f"/api/v1/sessions/{sid}/assets/{asset_id}").status_code == 200
    # 근거 없는 문단 없음
    for page in doc["pages"]:
        for b in page["blocks"]:
            if b["type"] == "paragraph":
                assert b["fact_ids"] or b["content"]["text"] == "추가 확인 필요"
    # 세션 요약에 문서가 보인다
    assert client.get(f"/api/v1/sessions/{sid}").json()["document_summary"] == {"document_id": did, "document_revision": 1, "status": "draft", "demo": False}
    # 버전 구조: documents 머리 + document_revisions 1행
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT current_revision FROM documents WHERE document_id=?", (did,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM document_revisions WHERE document_id=?", (did,)).fetchone()[0] == 1
    # 두 번째 생성은 거부(기존 편집 덮어쓰기 금지)
    r = client.post(f"/api/v1/sessions/{sid}/drafts", json={"preflight_id": pf["preflight_id"], "input_revision": rev, "confirmed": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "DOCUMENT_EXISTS"


@pytest.mark.parametrize("case", ["split", "unavailable", "input_changed", "bad_reference"])
def test_editorial_draft_layout_preparation_saves_once_and_rechecks_boundaries(client, settings, monkeypatch, case):
    from app.models import EditorialRecord, FactSelection, PageDesign
    from app.services import export_render as er
    sid = _session(client)
    src = _upload(client, sid, ("a.txt", SOURCE_A), ("photo.png", _png()))
    rev = _select(client, sid, src)
    pf = _preflight(client, sid, rev)
    original_draft = MockAgent.draft
    calls = []
    async def editorial(self, request):
        calls.append("draft")
        result = await original_draft(self, request)
        for p in result.pages:
            p.design = PageDesign()
        result.editorial = EditorialRecord(input_revision=rev, requested_pages=4, generated_pages=len(result.pages),
            page_count_reason="시험 구성", selections=[FactSelection(fact_id=f.fact_id,
                disposition="optional" if f.status == "supported" else "review", reason="시험 근거") for f in request.preflight.facts])
        return result
    def paginate(snapshot, out_dir, config, *, reduction_priorities=None):
        calls.append("paginate")
        assert out_dir.is_dir() and config.db_path == settings.db_path
        # Acquiring a second writer also verifies browser work holds no DB write lock.
        with connect(settings.db_path, immediate=True) as conn:
            if case == "input_changed":
                conn.execute("UPDATE sessions SET input_revision=input_revision+1 WHERE session_id=?", (sid,))
        pages = [p.model_copy(deep=True) for p in snapshot.pages]
        if case == "split":
            extra = pages[0].model_copy(deep=True)
            extra.page_id = "page_continuation"
            extra.blocks = pages[0].blocks[2:]
            pages[0].blocks = pages[0].blocks[:2]
            pages.insert(1, extra)
        if case == "bad_reference":
            pages[0].blocks[0].fact_ids = ["nonexistent_fact"]
        return er.DraftPagination(pages, "unavailable" if case == "unavailable" else "passed", 1)
    monkeypatch.setattr(MockAgent, "draft", editorial)
    monkeypatch.setattr(er, "paginate_draft", paginate)
    payload = {"preflight_id": pf["preflight_id"], "input_revision": rev, "confirmed": True}
    response = client.post(f"/api/v1/sessions/{sid}/drafts", json=payload)
    assert response.status_code == 202
    job = client.get(f"/api/v1/sessions/{sid}/jobs/{response.json()['job_id']}").json()
    assert calls == ["draft", "paginate"]
    assert not list((settings.private_runs_dir / sid / "artifacts").glob("tmp_*"))
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM layout_checks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
        if case in {"input_changed", "bad_reference"}:
            assert job["status"] == "failed"
            assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
            assert job["error"]["code"] == ("INPUT_REVISION_CONFLICT" if case == "input_changed" else "AGENT_OUTPUT_INVALID")
            return
    assert job["status"] == "succeeded"
    document = client.get(f"/api/v1/sessions/{sid}/documents/{job['result_ref']['document_id']}").json()["document"]
    assert len(document["pages"]) == (5 if case == "split" else 4)
    assert document["editorial"]["generated_pages"] == len(document["pages"])
    assert document["target_pages"] == document["editorial"]["requested_pages"] == 4
    assert document["status"] == "draft"  # Public status comes from validation/approval, not the stored preparation hint.
    if case == "unavailable":
        assert "완료하지 못했습니다" in document["editorial"]["page_count_reason"]
        with connect(settings.db_path) as conn:
            assert conn.execute("SELECT status FROM document_revisions WHERE document_id=?", (document["document_id"],)).fetchone()[0] == "review_required"
    assert client.post(f"/api/v1/sessions/{sid}/drafts", json=payload).status_code == 409
    assert calls == ["draft", "paginate"]


def test_draft_with_blocker_is_review_required_and_no_photo_gives_placeholder(client):
    sid = _session(client)
    src = _upload(client, sid, ("a.txt", SOURCE_A), ("b.txt", SOURCE_B))
    rev = _select(client, sid, src)
    pf = _preflight(client, sid, rev)
    r = client.post(f"/api/v1/sessions/{sid}/drafts", json={"preflight_id": pf["preflight_id"], "input_revision": rev, "confirmed": True})
    job = client.get(f"/api/v1/sessions/{sid}/jobs/{r.json()['job_id']}").json()
    doc = client.get(f"/api/v1/sessions/{sid}/documents/{job['result_ref']['document_id']}").json()["document"]
    assert doc["status"] == "review_required" and doc["title"] == "예시 회사" or doc["title"] == "예시 회사"
    assert any(b["type"] == "image_placeholder" for b in doc["pages"][0]["blocks"])


def test_draft_output_with_foreign_asset_is_rejected(client, monkeypatch):
    sid = _session(client)
    src = _upload(client, sid, ("a.txt", SOURCE_A))
    rev = _select(client, sid, src)
    pf = _preflight(client, sid, rev)
    original = MockAgent.draft

    async def tampered(self, request):
        result = await original(self, request)
        result.pages[0].blocks.append(Block(block_id="block_x", type="image",
                                            content={"asset_id": "asset_other", "alt": "", "caption": "", "fit": "contain"}))
        return result

    monkeypatch.setattr(MockAgent, "draft", tampered)
    r = client.post(f"/api/v1/sessions/{sid}/drafts", json={"preflight_id": pf["preflight_id"], "input_revision": rev, "confirmed": True})
    job = client.get(f"/api/v1/sessions/{sid}/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "failed" and job["error"]["code"] == "AGENT_OUTPUT_INVALID"
    assert client.get(f"/api/v1/sessions/{sid}").json()["document_summary"] is None


def test_other_owner_cannot_read_preflight_or_document(client, settings):
    sid = _session(client)
    src = _upload(client, sid, ("a.txt", SOURCE_A))
    rev = _select(client, sid, src)
    pf = _preflight(client, sid, rev)
    r = client.post(f"/api/v1/sessions/{sid}/drafts", json={"preflight_id": pf["preflight_id"], "input_revision": rev, "confirmed": True})
    did = client.get(f"/api/v1/sessions/{sid}/jobs/{r.json()['job_id']}").json()["result_ref"]["document_id"]
    other = TestClient(create_app(settings))
    other.post("/api/v1/sessions", json={"brief": BRIEF})
    assert other.get(f"/api/v1/sessions/{sid}/preflights/{pf['preflight_id']}").status_code == 404
    assert other.get(f"/api/v1/sessions/{sid}/documents/{did}").status_code == 404
    assert other.post(f"/api/v1/sessions/{sid}/preflights", json={"expected_input_revision": rev}).status_code == 404


def test_mock_never_contains_real_company_terms():
    """mock 고정 문구에 실제 회사 정보가 없는지(값은 전부 입력 구간에서만 온다)."""
    import inspect
    import app.agent_mock as m
    text = inspect.getsource(m)
    for banned in ("거산", "케미칼", "Geosan"):
        assert banned not in text
