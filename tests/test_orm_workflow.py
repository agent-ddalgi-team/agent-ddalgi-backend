"""현재 ERD API/이력 회귀. 가상 자료와 mock Agent만 사용하며 브라우저는 실행하지 않는다."""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.config import Settings
from app.db import ORM_SCHEMA_VERSION, connect, init_orm_db
from app.parsers import ParseResult, Segment
from app.services import preflights, reading, registered
from test_be04 import BRIEF, SOURCE_A, _select, _session, _upload
from test_be06 import Ctx


INGEST = Path(__file__).parent / "fixtures" / "ddalgi_mock_bundle_v1" / "ingest"


def _text_view(client, content=SOURCE_A, name="evidence.txt"):
    sid = _session(client)
    source_id = _upload(client, sid, (name, content))[0]
    revision = _select(client, sid, [source_id])
    route = f"/api/v1/sessions/{sid}/sources/{source_id}/segments"
    query = {"expected_input_revision": revision}
    return sid, source_id, revision, route, query


def test_source_text_read_only_pagination_and_cursor_binding(client, settings):
    long_text = "가🙂" * 12501 + "\x00<script>저장된 텍스트</script>"
    sid, source_id, revision, route, query = _text_view(client, (long_text + "\n마지막 줄").encode())
    with connect(settings.db_path) as conn:
        before = {table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table}")]
                  for table in ("sessions", "jobs", "sources", "input_revisions", "confirmations")}
    response = client.get(route, params={**query, "limit": 1})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    first = response.json()
    assert first["items"][0]["locator"] == {"line_start": 1, "line_end": 1}
    texts, cursor, count = [], None, 0
    while True:
        params = {**query, "limit": 1, **({"cursor": cursor} if cursor else {})}
        out = client.get(route, params=params).json()
        texts.extend(item["text"] for item in out["items"])
        assert sum(len(i["text"]) for i in out["items"]) <= 50000
        assert all(len(i["text"]) <= 10000 for i in out["items"])
        cursor, count = out["next_cursor"], count + 1
        if not cursor:
            break
        assert count < 10
    assert "".join(texts) == long_text + "마지막 줄"
    assert "stored_path" not in response.text and "private_runs" not in response.text
    for cursor in ("!bad", "W10=", first["next_cursor"]):
        # Last cursor was issued for limit=1, not the default page size.
        bad = client.get(route, params={**query, "cursor": cursor})
        assert bad.status_code == 422, bad.text
    with connect(settings.db_path) as conn:
        after = {table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table}")] for table in before}
    assert before == after


@pytest.mark.parametrize("mutation", ["unselected", "instruction", "foreign", "revoked", "demo", "purged", "deleted"])
def test_source_text_access_scope(client, settings, mutation):
    sid, source_id, revision, route, query = _text_view(client)
    if mutation == "unselected":
        query["expected_input_revision"] = _select(client, sid, [], expected=revision)
    else:
        with connect(settings.db_path) as conn:
            if mutation == "instruction":
                conn.execute("UPDATE sources SET role='instruction' WHERE source_id=?", (source_id,))
            elif mutation == "foreign":
                other = _session(client)
                conn.execute("UPDATE sources SET session_id=? WHERE source_id=?", (other, source_id))
            elif mutation == "revoked":
                conn.execute("UPDATE sources SET scope='registered', session_id=NULL, use_as_company_evidence=0 WHERE source_id=?", (source_id,))
            elif mutation == "demo":
                conn.execute("UPDATE sources SET origin_kind='demo' WHERE source_id=?", (source_id,))
            elif mutation == "purged":
                conn.execute("UPDATE extraction_runs SET purged_at='2026-01-01T00:00:00Z' WHERE source_id=?", (source_id,))
            else:
                conn.execute("UPDATE sources SET deleted_at='2026-01-01T00:00:00Z' WHERE source_id=?", (source_id,))
    out = client.get(route, params=query)
    assert out.status_code == (410 if mutation == "purged" else 404), out.text
    assert out.headers["cache-control"] == "private, no-store"
    assert "회사명:" not in out.text


def test_source_text_owner_expiry_and_revision(client, settings, app):
    sid, source_id, revision, route, query = _text_view(client)
    with TestClient(app) as stranger:
        assert stranger.get(route, params=query).status_code == 401
        _session(stranger)
        assert stranger.get(route, params=query).status_code == 404
    assert client.get(route, params={**query, "expected_input_revision": 1}).status_code == 409
    assert client.get(route, params={**query, "context": "document"}).status_code == 400
    assert client.get(route, params={**query, "preflight_id": "fake"}).status_code == 400
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE sessions SET expires_at='2020-01-01T00:00:00Z' WHERE session_id=?", (sid,))
    assert client.get(route, params=query).status_code == 410


def test_source_text_pinned_run_and_preflight_references(client, settings):
    from test_be04 import _preflight
    sid, source_id, revision, route, query = _text_view(client)
    pf = _preflight(client, sid, revision)
    ref = next(f["evidence_refs"][0] for f in pf["facts"] if f["evidence_refs"])
    params = {**query, "context": "preflight", "preflight_id": pf["preflight_id"], "source_version": ref["source_version"]}
    target = route + "/" + ref["segment_id"]
    first = client.get(target, params=params)
    assert first.status_code == 200, first.text
    assert ref["excerpt"] in first.json()["excerpts"]
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
        reading._apply_result(conn, row, ParseResult(status="complete", text_available=True,
            segments=[Segment({"line_start": 1, "line_end": 1}, "다시 추출한 내용")]))
        new_segment = conn.execute("SELECT segment_id FROM segments WHERE run_id=(SELECT current_run_id FROM sources WHERE source_id=?)", (source_id,)).fetchone()[0]
    second = client.get(target, params=params)
    assert second.json() == first.json()
    assert client.get(route + "/" + new_segment, params=params).status_code == 404
    assert client.get(target, params={**params, "source_version": 999}).status_code == 404
    assert client.get(target, params={**query, "source_version": 999}).status_code == 410
    assert client.get(route, params=params).status_code == 422
    # Changing input invalidates the preflight context even if the source remains selected.
    next_revision = _select(client, sid, [source_id], expected=revision)
    if next_revision == revision:
        extra = _upload(client, sid, ("extra.txt", b"extra"))[0]
        next_revision = _select(client, sid, [source_id, extra], expected=revision)
    assert client.get(target, params={**params, "expected_input_revision": next_revision}).status_code == 409


def test_source_text_document_context_and_unlinked_reference(client, settings):
    from app.models import Page, Block, EvidenceRef
    from app.services import documents
    sid, source_id, revision, route, query = _text_view(client)
    item = client.get(route, params=query).json()["items"][0]
    ref = EvidenceRef(source_id=source_id, source_version=1, segment_id=item["segment_id"], locator=item["locator"], excerpt=item["text"])
    with connect(settings.db_path) as conn:
        did = documents.create_initial(conn, sid, revision, "근거 문서", 4,
            [Page(page_id="p", title="개요", layout_key="text", blocks=[Block(block_id="b", type="paragraph",
                 content={"text": "편집한 문구"}, evidence_refs=[ref])])], "draft")
    params = {**query, "context": "document", "document_id": did, "document_revision": 1, "source_version": 1}
    target = route + "/" + item["segment_id"]
    out = client.get(target, params=params)
    assert out.status_code == 200, out.text
    assert out.json()["excerpts"] == [item["text"]]
    other = client.get(route, params=query).json()["items"][1]["segment_id"]
    assert client.get(route + "/" + other, params=params).status_code == 404
    assert client.get(target, params={**params, "document_revision": 2}).status_code == 409
    assert client.get(target, params={**params, "document_id": "other"}).status_code == 404
    extra = _upload(client, sid, ("extra.txt", b"extra"))[0]
    changed = _select(client, sid, [source_id, extra], expected=revision)
    historical = client.get(target, params={**params, "expected_input_revision": changed})
    assert historical.status_code == 200, historical.text
    assert historical.json()["input_revision"] == revision
    assert historical.json()["current_input_revision"] == changed


@pytest.mark.parametrize("status", ["reading", "failed", "image", "partial"])
def test_source_text_reading_outcomes(client, settings, monkeypatch, status):
    sid = _session(client)
    with monkeypatch.context() as patch:
        if status == "reading":
            patch.setattr(reading, "run_read_job", lambda *args: None)
        else:
            patch.setattr(reading, "parse", lambda *_: ParseResult(
                status="partial" if status in ("image", "partial") else "failed",
                text_available=status == "partial", segments=[Segment({"page": 2}, "일부 글자")] if status == "partial" else [],
                warnings=[{"code": "IMAGE_ONLY", "message": "이미지만 있음", "locator": {"page": 1}, "action": "텍스트 보완"}]))
        source_id = _upload(client, sid, ("read.txt", SOURCE_A))[0]
    rev = _select(client, sid, [source_id])
    out = client.get(f"/api/v1/sessions/{sid}/sources/{source_id}/segments", params={"expected_input_revision": rev})
    assert out.status_code == {"reading": 409, "failed": 422, "image": 422, "partial": 200}[status], out.text
    if status == "partial":
        assert out.json()["warnings"][0]["code"] == "IMAGE_ONLY"


def test_source_text_docx_context_and_long_neighbour(client, settings, monkeypatch):
    segments = [Segment({"paragraph": 1}, "첫 문단"), Segment({"paragraph": 2}, "긴" * 10001),
                Segment({"table": 1, "row": 1, "col": 1}, "표의 별도 위치")]
    with monkeypatch.context() as patch:
        patch.setattr(reading, "parse", lambda *_: ParseResult(status="complete", text_available=True, segments=segments))
        sid, source_id, revision, route, query = _text_view(client)
    items = client.get(route, params=query).json()["items"]
    target = route + "/" + items[0]["segment_id"]
    params = {**query, "source_version": 1}
    out = client.get(target, params=params).json()
    assert len(out["context_segments"]) == 1
    neighbour = out["context_segments"][0]
    assert neighbour["truncated"] is True
    focus = {**params, "focus_segment_id": neighbour["segment_id"]}
    first = client.get(target, params=focus).json()
    second = client.get(target, params={**focus, "cursor": first["next_cursor"]}).json()
    assert first["items"][0]["text"] + second["items"][0]["text"] == "긴" * 10001
    with connect(settings.db_path) as conn:
        table_id = conn.execute("SELECT segment_id FROM segments WHERE source_id=? AND ordinal=(SELECT MAX(ordinal) FROM segments WHERE source_id=?)", (source_id, source_id)).fetchone()[0]
    assert client.get(target, params={**params, "focus_segment_id": table_id}).status_code == 404


@pytest.mark.parametrize("kind", ["txt", "md", "pdf", "docx", "pptx"])
def test_source_text_real_parser_locator_roundtrip(client, kind):
    from test_be03 import make_pdf, make_docx, make_pptx
    files = {
        "txt": (b"first\n\nthird", {"line_start": 3, "line_end": 3}),
        "md": (b"# title\n\n**bold**", {"line_start": 3, "line_end": 3}),
        "pdf": (make_pdf(["first page", "second page"]), {"page": 2}),
        "docx": (make_docx(["paragraph"], [["table cell"]]), {"table": 1, "row": 1, "col": 1}),
        "pptx": (make_pptx([["shape one"], ["shape two"]]), {"slide": 2, "shape": 1}),
    }
    content, expected = files[kind]
    sid, source_id, revision, route, query = _text_view(client, content, f"sample.{kind}")
    result = client.get(route, params=query)
    assert result.status_code == 200, result.text
    target = next(item for item in result.json()["items"] if item["locator"] == expected)
    one = client.get(route + "/" + target["segment_id"], params={**query, "source_version": 1})
    assert one.status_code == 200, one.text
    assert one.json()["items"][0] == target
    if kind in ("docx", "pptx"):
        assert one.json()["context_segments"] == []


def test_source_text_conflict_alternatives_are_server_bound(client):
    from test_be04 import _preflight, SOURCE_B
    sid, source_id, revision, route, query = _text_view(client)
    other = _upload(client, sid, ("other.txt", SOURCE_B))[0]
    revision = _select(client, sid, [source_id, other], expected=revision)
    pf = _preflight(client, sid, revision)
    fact = next(f for f in pf["facts"] if f["field_key"] == "company_name")
    assert fact["status"] == "conflict"
    for candidate in fact["alternatives"]:
        ref = candidate["evidence_refs"][0]
        out = client.get(f"/api/v1/sessions/{sid}/sources/{ref['source_id']}/segments/{ref['segment_id']}",
            params={"expected_input_revision": revision, "context": "preflight", "preflight_id": pf["preflight_id"], "source_version": ref["source_version"]})
        assert out.status_code == 200, out.text
        assert ref["excerpt"] in out.json()["excerpts"]


def test_source_text_arbitrary_content_is_not_a_document_reference(client, settings):
    from app.models import Page, Block
    from app.services import documents
    sid, source_id, revision, route, query = _text_view(client)
    item = client.get(route, params=query).json()["items"][0]
    forged = {"source_id": source_id, "source_version": 1, "segment_id": item["segment_id"],
              "locator": item["locator"], "excerpt": item["text"]}
    with connect(settings.db_path) as conn:
        did = documents.create_initial(conn, sid, revision, "본문 중첩 참조", 4,
            [Page(page_id="p", title="개요", layout_key="text", blocks=[Block(block_id="b", type="paragraph",
                 content={"text": "사용자 본문", "evidence_refs": [forged]})])], "draft")
    out = client.get(route + "/" + item["segment_id"], params={**query, "context": "document",
        "document_id": did, "document_revision": 1, "source_version": 1})
    assert out.status_code == 404


def test_source_text_total_budget_and_missing_history(client, settings):
    sid, source_id, revision, route, query = _text_view(client, ("\n".join(str(n) * 10000 for n in range(6))).encode())
    out = client.get(route, params=query)
    assert out.status_code == 200, out.text
    assert sum(len(item["text"]) for item in out.json()["items"]) == 50000
    after = client.get(route, params={**query, "cursor": out.json()["next_cursor"]}).json()
    assert after["items"][0]["text"] == "5" * 10000 and after["next_cursor"] is None
    with connect(settings.db_path) as conn:
        conn.execute("DELETE FROM session_source_selections WHERE session_id=?", (sid,))
    gone = client.get(route, params=query)
    assert gone.status_code == 410
    assert gone.json()["error"]["code"] == "SOURCE_VERSION_UNAVAILABLE"


def test_source_text_attachment_uses_live_session_expiry(client, settings):
    sid, source_id, revision, route, query = _text_view(client)
    # Editing extends the session, not historical upload/version timestamps.
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE sources SET expires_at='2020-01-01T00:00:00Z' WHERE source_id=?", (source_id,))
        conn.execute("UPDATE source_versions SET expires_at='2020-01-01T00:00:00Z' WHERE source_id=?", (source_id,))
    assert client.get(route, params=query).status_code == 200
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE sessions SET expires_at='2020-01-01T00:00:00Z' WHERE session_id=?", (sid,))
    assert client.get(route, params=query).status_code == 410


@pytest.fixture
def settings(tmp_path):
    value = Settings(
        private_runs_dir=tmp_path / "runs",
        db_path=tmp_path / "runs" / "orm.sqlite3",
        agent_mode="mock",
        demo_mode=True,
        cleanup_sweep_interval_s=0,
        export_browser_path=str(tmp_path / "no-browser.exe"),
    )
    init_orm_db(value.db_path, value.private_runs_dir)
    return value


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as value:
        yield value


def _assert_integrity(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == ORM_SCHEMA_VERSION
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def _warning_validation(flow):
    response = flow.c.post(f"/api/v1/sessions/{flow.sid}/documents/{flow.did}/validate",
                           json={"expected_revision": flow.rev(), "input_revision": flow.rev_in})
    assert response.status_code == 202, response.text
    flow.job(response.json()["job_id"])
    return flow.get()["validation"]


def _warning_flow(app, settings):
    from test_be08_rules import Flow
    flow = Flow(app, settings, upload_png=False)
    flow.patch([{"op": "insert_block", "page_id": "page_02", "after_block_id": None,
                 "block": {"block_id": "b_warning", "type": "paragraph", "content": {"text": "추가 확인 필요"}}}])
    checked = _warning_validation(flow)
    assert checked["status"] == "needs_review"
    issue = next(i for i in _flow_issues(flow) if i["code"] == "PLACEHOLDER_TEXT")
    body = {"expected_revision": flow.rev(), "input_revision": flow.rev_in, "validation_id": checked["validation_id"],
            "resolution": {"action": "acknowledged", "reason": "선택 항목의 안내 문구임을 확인함"}}
    route = f"/api/v1/sessions/{flow.sid}/issues/{issue['issue_id']}/resolve"
    return flow, checked, issue, route, body


def _flow_issues(flow):
    return flow.c.get(f"/api/v1/sessions/{flow.sid}/documents/{flow.did}/issues").json()["issues"]


def test_warning_ack_required_for_approval_and_recorded_once(app, settings):
    flow, checked, issue, route, body = _warning_flow(app, settings)
    layout = flow.fabricate()
    response = flow.approve(checked["validation_id"], layout["layout_check_id"])
    assert response.status_code == 422 and response.json()["error"]["code"] == "WARNING_ACKNOWLEDGEMENT_REQUIRED"
    key = {"Idempotency-Key": "ack-warning"}
    response = flow.c.post(route, json=body, headers=key)
    assert response.status_code == 200, response.text
    assert response.json()["validation"]["status"] == "passed"
    assert flow.c.post(route, json=body, headers=key).json() == response.json()
    assert flow.c.post(route, json=body).status_code == 200
    approval = flow.approve(checked["validation_id"], layout["layout_check_id"], key="approve-warning")
    assert approval.status_code == 201, approval.text
    exported = flow.export_ready(approval.json()["approval_id"])
    assert flow.download(exported["export_id"]).status_code == 200
    with connect(settings.db_path) as conn:
        rows = conn.execute("SELECT * FROM confirmations WHERE kind='warning_ack'").fetchall()
        assert len(rows) == 1
        record = rows[0]
        assert record["issue_id"] == issue["issue_id"] and record["validation_id"] == checked["validation_id"]
        assert record["confirmed_by"] == response.json()["issue"]["resolution"]["by"]
        assert record["confirmed_at"] == response.json()["issue"]["resolution"]["at"]
        assert json.loads(record["reasons_json"])[0]["reason"] == body["resolution"]["reason"]
        _assert_integrity(conn)
        conn.execute("UPDATE confirmations SET status='invalidated' WHERE kind='warning_ack'")
    # 오래된 승인 성공 응답·기존 파일도 확인 기록의 유효성 검사를 우회하지 않는다.
    assert flow.approve(checked["validation_id"], layout["layout_check_id"], key="approve-warning").status_code == 422
    assert flow.download(exported["export_id"]).status_code == 422


@pytest.mark.parametrize("field,value,status,code", [
    ("input_revision", None, 400, "INVALID_REQUEST"),
    ("validation_id", None, 400, "INVALID_REQUEST"),
    ("validation_id", "val_old", 422, "REVALIDATION_REQUIRED"),
    ("input_revision", 999, 409, "INPUT_REVISION_CONFLICT"),
    ("expected_revision", 999, 409, "DOCUMENT_REVISION_CONFLICT"),
])
def test_warning_ack_rejects_missing_or_stale_context(app, settings, field, value, status, code):
    flow, _, _, route, body = _warning_flow(app, settings)
    body[field] = value
    response = flow.c.post(route, json=body)
    assert response.status_code == status and response.json()["error"]["code"] == code, response.text
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM confirmations").fetchone()[0] == 0


def test_warning_ack_reuse_requires_revalidation_and_related_changes_reopen(app, settings):
    flow, _, issue, route, body = _warning_flow(app, settings)
    original = flow.c.post(route, json=body, headers={"Idempotency-Key": "ack"})
    assert original.status_code == 200, original.text
    proof = original.json()["issue"]["resolution"]
    flow.patch([{"op": "rename_page", "page_id": "page_03", "title": "다른 페이지 제목"}])
    assert flow.c.post(route, json=body, headers={"Idempotency-Key": "ack"}).status_code == 409
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM confirmations WHERE status='active'").fetchone()[0] == 0
    reused = _warning_validation(flow)
    assert reused["status"] == "passed"
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM confirmations WHERE kind='warning_ack' AND status='active'").fetchone()
        assert row["document_revision"] == flow.rev() and row["validation_id"] == reused["validation_id"]
        assert row["confirmed_at"] == proof["at"]
        assert json.loads(row["reasons_json"])[0]["reused_from_confirmation_id"]
    flow.patch([{"op": "replace_block_content", "block_id": "b_warning", "content": {"text": "자료에서 확인되지 않음"}}])
    newer = _warning_validation(flow)
    current = next(i for i in _flow_issues(flow) if i["issue_id"] == issue["issue_id"])
    assert current["status"] == "open" and current["resolution"] is None
    assert newer["status"] == "needs_review"
    body.update(expected_revision=flow.rev(), validation_id=newer["validation_id"])
    assert flow.c.post(route, json=body).status_code == 200
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM confirmations WHERE kind='warning_ack' AND status='active'").fetchone()[0] == 1
        assert len(json.loads(conn.execute("SELECT resolution_history_json FROM issues WHERE issue_id=?", (issue["issue_id"],)).fetchone()[0])) == 1
        _assert_integrity(conn)


@pytest.mark.parametrize("code", ["VALUE_CONFLICT", "UNVERIFIED_SUPERLATIVE", "MOCK_VALUE", "UNKNOWN_WARNING"])
def test_warning_label_cannot_allow_accuracy_or_unknown_issue(app, settings, code):
    flow, _, issue, route, body = _warning_flow(app, settings)
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE issues SET code=? WHERE issue_id=?", (code, issue["issue_id"]))
    response = flow.c.post(route, json=body)
    assert response.status_code == 422 and response.json()["error"]["code"] == "RESOLUTION_NOT_ALLOWED"


def test_warning_confirmation_failure_rolls_back_issue_and_idempotency(app, settings, monkeypatch):
    from app.services import db_history
    flow, _, issue, route, body = _warning_flow(app, settings)
    def fail(*args):
        raise RuntimeError("confirmation write failed")
    monkeypatch.setattr(db_history, "record_warning", fail)
    with pytest.raises(RuntimeError, match="confirmation write failed"):
        flow.c.post(route, json=body, headers={"Idempotency-Key": "ack"})
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT status FROM issues WHERE issue_id=?", (issue["issue_id"],)).fetchone()[0] == "open"
        assert conn.execute("SELECT COUNT(*) FROM confirmations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM idempotency_keys WHERE idem_key='ack'").fetchone()[0] == 0


def test_new_warning_invalidates_approval_and_old_ack_replay(app, settings, monkeypatch):
    from app.services import validation
    flow, checked, _, route, body = _warning_flow(app, settings)
    assert flow.c.post(route, json=body, headers={"Idempotency-Key": "ack"}).status_code == 200
    layout = flow.fabricate()
    approved = flow.approve(checked["validation_id"], layout["layout_check_id"], key="approval")
    assert approved.status_code == 201, approved.text
    export = flow.export_ready(approved.json()["approval_id"])
    original = validation.server_checks
    def changed_warning(document, context):
        drafts, checks = original(document, context)
        for draft in drafts:
            if draft.code == "PLACEHOLDER_TEXT":
                draft.message = "새로 확인해야 할 안내 문구"
        return drafts, checks
    monkeypatch.setattr(validation, "server_checks", changed_warning)
    assert _warning_validation(flow)["status"] == "needs_review"
    assert flow.c.post(route, json=body, headers={"Idempotency-Key": "ack"}).status_code == 422
    assert flow.approve(checked["validation_id"], layout["layout_check_id"], key="approval").status_code == 409
    assert flow.download(export["export_id"]).status_code == 409
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT invalidated_reason FROM approvals").fetchone()[0] == "validation_changed"
        assert conn.execute("SELECT COUNT(*) FROM confirmations WHERE status='active'").fetchone()[0] == 0
        _assert_integrity(conn)


def test_warning_ack_pending_validation_and_input_change(app, settings):
    flow, _, _, route, body = _warning_flow(app, settings)
    with connect(settings.db_path) as conn:
        conn.execute("INSERT INTO jobs (job_id, session_id, kind, status, progress_json, input_revision, target_key, created_at, updated_at) "
                     "VALUES ('job_pending_ack', ?, 'validate', 'running', '{}', ?, ?, 't', 't')",
                     (flow.sid, flow.rev_in, f"{flow.did}@{flow.rev()}@{flow.rev_in}"))
    assert flow.c.post(route, json=body).json()["error"]["code"] == "REVALIDATION_REQUIRED"
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE jobs SET status='failed' WHERE job_id='job_pending_ack'")
    assert flow.c.post(route, json=body).status_code == 200
    changed = flow.c.patch(f"/api/v1/sessions/{flow.sid}/inputs", json={
        "expected_input_revision": flow.rev_in, "brief": {"purpose": "변경된 목적"}})
    assert changed.status_code == 200, changed.text
    assert flow.c.post(route, json=body).json()["error"]["code"] == "INPUT_REVISION_CONFLICT"
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM confirmations WHERE status='active'").fetchone()[0] == 0


def test_concurrent_warning_ack_stores_one_confirmation(app, settings):
    flow, _, _, route, body = _warning_flow(app, settings)
    barrier = Barrier(3)
    def acknowledge():
        with TestClient(app) as client:
            client.cookies = flow.c.cookies
            barrier.wait(timeout=10)
            return client.post(route, json=body)
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(lambda _: acknowledge(), range(3)))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM confirmations WHERE kind='warning_ack'").fetchone()[0] == 1


def test_s01_real_http_smoke():
    from scripts.check_s01_http import run_check

    result = run_check(timeout_s=30)
    assert result["status"] == "passed"
    assert result["counts_before_close"]["jobs"] == 3
    assert result["counts_before_close"]["documents"] == 1


def test_s01_http_server_stops_and_removes_temp_files_on_failure():
    import httpx
    import threading
    from scripts.check_s01_http import temporary_server

    with pytest.raises(RuntimeError, match="simulated client failure"):
        with temporary_server() as (address, settings):
            directory = settings.private_runs_dir.parent
            with httpx.Client(base_url=address, trust_env=False) as client:
                assert client.get("/").status_code == 200
            raise RuntimeError("simulated client failure")
    assert not directory.exists()
    assert not any(thread.name == "s01-http-check" for thread in threading.enumerate())
    with httpx.Client(base_url=address, timeout=1, trust_env=False) as client:
        # Windows may time out instead of immediately refusing a closed port.
        with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
            client.get("/")


def test_c05_real_http_source_change_preserves_edits_and_revalidates():
    from scripts.check_s01_http import run_check

    result = run_check(timeout_s=30, impact=True)
    assert result["status"] == result["impact"]["status"] == "passed"
    assert {"source_added", "apply_replay", "full_revalidation", "closed_review_410"} <= set(result["impact"]["checks"])


@pytest.fixture(params=[9, ORM_SCHEMA_VERSION], ids=["legacy-v9", "orm-current"])
def upload_case(tmp_path, request):
    """구형/ERD DB 양쪽에서 같은 업로드 요청·오류·재전송 규칙을 확인한다."""
    value = Settings(private_runs_dir=tmp_path / "uploads", db_path=tmp_path / "uploads" / "test.sqlite3",
                     agent_mode="mock", cleanup_sweep_interval_s=0, max_files_per_session=3,
                     export_browser_path=str(tmp_path / "no-browser.exe"))
    if request.param == ORM_SCHEMA_VERSION:
        init_orm_db(value.db_path, value.private_runs_dir)
    application = create_app(value)
    with TestClient(application, raise_server_exceptions=False) as client:
        sid = _session(client)
        yield value, application, client, sid


def _post_upload(client, sid, key="upload", *, names=("company.txt",), **data):
    return client.post(f"/api/v1/sessions/{sid}/sources", headers={"Idempotency-Key": key}, data=data,
                       files=[("files", (name, SOURCE_A, "text/plain")) for name in names])


def test_upload_replay_at_capacity_and_metadata_conflict(upload_case):
    settings, _, client, sid = upload_case
    names = ("company.txt", "memo.txt", "more.txt")
    first = _post_upload(client, sid, names=names, kind="company")
    again = _post_upload(client, sid, names=names, kind="company")
    assert first.status_code == again.status_code == 202
    assert first.json() == again.json()
    for data in ({"kind": "interview"}, {"kind": "company", "role": "instruction"}):
        conflict = _post_upload(client, sid, names=names, **data)
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE kind='read'").fetchone()[0] == 1


def test_upload_replay_matches_default_kind_and_keeps_legacy_cache(upload_case):
    settings, _, client, sid = upload_case
    first = _post_upload(client, sid)
    assert first.status_code == 202
    # v9 역할 구분 도입 전 응답도 evidence로 읽는다. 저장된 해시 형식을 바꾸지 않는다.
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT response_json FROM idempotency_keys WHERE idem_key='upload'").fetchone()
        payload = json.loads(row[0])
        for item in payload["items"]:
            item.pop("role")
        conn.execute("UPDATE idempotency_keys SET response_json=? WHERE idem_key='upload'", (json.dumps(payload),))
    again = _post_upload(client, sid, kind="other")
    assert again.status_code == 202 and again.json() == payload
    assert _post_upload(client, sid, kind="company").status_code == 409


@pytest.mark.parametrize("same_key", [True, False], ids=["duplicate-key", "capacity-race"])
def test_concurrent_uploads_recheck_replay_and_capacity(upload_case, monkeypatch, same_key):
    settings, application, client, sid = upload_case
    from app.services import sources

    # 남은 자리는 하나. 두 요청의 파일 검사가 끝난 뒤 동시에 저장 단계로 진행한다.
    assert _post_upload(client, sid, "existing", names=("old1.txt", "old2.txt")).status_code == 202
    rendezvous = Barrier(2, timeout=10)
    validate = sources.validate_uploads

    async def both_validated(*args, **kwargs):
        uploads = await validate(*args, **kwargs)
        rendezvous.wait()
        return uploads

    monkeypatch.setattr(sources, "validate_uploads", both_validated)
    with TestClient(application) as left, TestClient(application) as right:
        left.cookies.update(client.cookies)
        right.cookies.update(client.cookies)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(_post_upload, left, sid, "shared")
            b = pool.submit(_post_upload, right, sid, "shared" if same_key else "other")
            responses = [a.result(timeout=20), b.result(timeout=20)]
    assert sorted(r.status_code for r in responses) == ([202, 202] if same_key else [202, 413])
    if same_key:
        assert responses[0].json() == responses[1].json()
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE kind='read'").fetchone()[0] == 2
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert len(list((settings.private_runs_dir / sid).glob("src_*.txt"))) == 3


@pytest.mark.parametrize("failure", ["partial-file", "history", "job", "remember", "commit"])
def test_failed_upload_rolls_back_files_rows_and_can_retry(upload_case, monkeypatch, failure):
    settings, _, client, sid = upload_case
    from app.db import DatabaseConnection
    from app.services import db_history, idempotency, jobs

    first = _post_upload(client, sid, "existing")
    assert first.status_code == 202
    source = first.json()["items"][0]["source_id"]
    _select(client, sid, [source])
    directory = settings.private_runs_dir / sid
    original_files = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    before_session = client.get(f"/api/v1/sessions/{sid}").json()

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected upload failure")

    with monkeypatch.context() as patch:
        if failure == "partial-file":
            open_file = Path.open
            writes = []

            @contextmanager
            def partial_write(path, mode="r", *args, **kwargs):
                with open_file(path, mode, *args, **kwargs) as stream:
                    if mode == "xb" and path.parent == directory:
                        writes.append(path)
                        if len(writes) == 2:
                            class BrokenStream:
                                def write(self, content):
                                    stream.write(content[:3])
                                    stream.flush()
                                    raise OSError("injected disk failure")
                            yield BrokenStream()
                            return
                    yield stream

            patch.setattr(Path, "open", partial_write)
        elif failure == "commit":
            commit = DatabaseConnection.commit

            def fail_final_commit(conn):
                if conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] > 1:
                    fail()
                commit(conn)

            patch.setattr(DatabaseConnection, "commit", fail_final_commit)
        else:
            target, method = {"history": (db_history, "source_version"), "job": (jobs, "create"),
                              "remember": (idempotency, "remember")}[failure]
            patch.setattr(target, method, fail)
        response = _post_upload(client, sid, "retry", names=("new1.txt", "new2.txt"))
        assert response.status_code == 500, response.text
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"

    assert {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()} == original_files
    assert client.get(f"/api/v1/sessions/{sid}").json() == before_session
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE kind='read'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM idempotency_keys WHERE idem_key='retry'").fetchone()[0] == 0
        if conn.execute("PRAGMA user_version").fetchone()[0] == ORM_SCHEMA_VERSION:
            assert conn.execute("SELECT COUNT(*) FROM source_versions").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM extraction_runs").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    retry = _post_upload(client, sid, "retry", names=("new1.txt", "new2.txt"))
    assert retry.status_code == 202, retry.text
    assert len(client.get(f"/api/v1/sessions/{sid}/sources").json()["items"]) == 3


def test_upload_rechecks_session_after_reading_files(upload_case, monkeypatch):
    settings, _, client, sid = upload_case
    from app.services import cleanup, sessions, sources

    validate = sources.validate_uploads

    async def close_after_validation(*args, **kwargs):
        uploads = await validate(*args, **kwargs)
        with connect(settings.db_path, immediate=True) as conn:
            owner = conn.execute("SELECT owner_id FROM sessions WHERE session_id=?", (sid,)).fetchone()[0]
            sessions.close(conn, settings, owner, sid)
        cleanup.run_for_session(settings, sid)
        return uploads

    monkeypatch.setattr(sources, "validate_uploads", close_after_validation)
    response = _post_upload(client, sid)
    assert response.status_code == 410, response.text
    assert not (settings.private_runs_dir / sid).exists()
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM idempotency_keys WHERE idem_key='upload'").fetchone()[0] == 0


def test_upload_filename_collision_preserves_original_file(upload_case, monkeypatch):
    settings, _, client, sid = upload_case
    from app.services import sources

    first = _post_upload(client, sid)
    assert first.status_code == 202
    source_id = first.json()["items"][0]["source_id"]
    path = settings.private_runs_dir / sid / f"{source_id}.txt"
    original = path.read_bytes()
    # 의도적으로 이름이 충돌해도 기존 파일을 덮어쓰거나 실패 정리에서 지우지 않는다.
    monkeypatch.setattr(sources, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex=source_id[4:])))
    response = client.post(f"/api/v1/sessions/{sid}/sources", headers={"Idempotency-Key": "collision"},
                           files=[("files", ("different.txt", b"different content", "text/plain"))])
    assert response.status_code == 500
    assert path.read_bytes() == original
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_upload_and_input_changes_preserve_prior_snapshots(client, settings):
    sid = _session(client)
    source_id = _upload(client, sid, ("company.txt", SOURCE_A))[0]
    revision = _select(client, sid, [source_id])
    updated_brief = {**BRIEF, "purpose": "다음 제안용"}
    response = client.patch(
        f"/api/v1/sessions/{sid}/inputs",
        json={"expected_input_revision": revision, "brief": updated_brief},
    )
    assert response.status_code == 200, response.text
    assert response.json()["input_revision"] == 3
    stale = client.patch(
        f"/api/v1/sessions/{sid}/inputs",
        json={"expected_input_revision": revision, "brief": BRIEF},
    )
    assert stale.status_code == 409
    with connect(settings.db_path) as conn:
        source = conn.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
        version = conn.execute("SELECT * FROM source_versions WHERE source_id=?", (source_id,)).fetchone()
        run = conn.execute("SELECT * FROM extraction_runs WHERE source_id=?", (source_id,)).fetchone()
        assert version["version"] == source["source_version"] == 1
        assert version["original_name"] == "company.txt"
        assert version["content_hash"] == source["content_hash"]
        assert run["run_id"] == source["current_run_id"] and run["status"] == "complete"
        segments = conn.execute("SELECT * FROM segments WHERE source_id=?", (source_id,)).fetchall()
        assert len(segments) > 0 and all(s["run_id"] == run["run_id"] for s in segments)
        snapshots = conn.execute(
            "SELECT revision, brief_json FROM input_revisions WHERE session_id=? ORDER BY revision", (sid,)
        ).fetchall()
        assert [r["revision"] for r in snapshots] == [1, 2, 3]
        assert [json.loads(r["brief_json"])["purpose"] for r in snapshots] == [BRIEF["purpose"], BRIEF["purpose"], "다음 제안용"]
        selections = conn.execute(
            "SELECT input_revision, source_id, source_version, run_id FROM session_source_selections "
            "WHERE session_id=? ORDER BY input_revision", (sid,),
        ).fetchall()
        assert [tuple(row) for row in selections] == [(2, source_id, 1, run["run_id"]), (3, source_id, 1, run["run_id"])]
        _assert_integrity(conn)


@pytest.mark.parametrize("reread_status", ["complete", "failed"])
def test_pending_selection_pins_first_read_and_reread_preserves_its_evidence(client, settings, monkeypatch, reread_status):
    sid = _session(client)
    pending = []
    read_job = reading.run_read_job
    with monkeypatch.context() as patch:
        patch.setattr(reading, "run_read_job", lambda *args: pending.append(args))
        source_id = _upload(client, sid, ("pending.txt", SOURCE_A))[0]
    _select(client, sid, [source_id])
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT run_id FROM session_source_selections WHERE source_id=?", (source_id,)).fetchone()[0] is None
        assert preflights.build_sources(conn, sid, [source_id]) == []
    assert len(pending) == 1
    read_job(*pending[0])
    with connect(settings.db_path, immediate=True) as conn:
        row = conn.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
        original_run = row["current_run_id"]
        original_ids = {r[0] for r in conn.execute("SELECT segment_id FROM segments WHERE source_id=?", (source_id,))}
        assert original_run is not None and original_ids
        assert conn.execute("SELECT run_id FROM session_source_selections WHERE source_id=?", (source_id,)).fetchone()[0] == original_run
        reread = ParseResult(
            status=reread_status,
            segments=[Segment({"line_start": 1, "line_end": 1}, "회사명: 다시 읽은 가상 회사")] if reread_status == "complete" else [],
            text_available=reread_status == "complete",
        )
        reading._apply_result(conn, row, reread)
        assert conn.execute("SELECT current_run_id FROM sources WHERE source_id=?", (source_id,)).fetchone()[0] != original_run
        assert conn.execute("SELECT COUNT(*) FROM extraction_runs WHERE source_id=?", (source_id,)).fetchone()[0] == 2
        assert {r[0] for r in conn.execute("SELECT segment_id FROM segments WHERE run_id=?", (original_run,))} == original_ids
        assert conn.execute("SELECT run_id FROM session_source_selections WHERE source_id=?", (source_id,)).fetchone()[0] == original_run
        selected = preflights.build_sources(conn, sid, [source_id])
        assert len(selected) == 1 and selected[0].parse_status == "complete"
        assert {s.segment_id for s in selected[0].segments} == original_ids
        assert all("다시 읽은" not in s.text for s in selected[0].segments)
        _assert_integrity(conn)


def test_failed_first_read_can_be_replaced_by_success_for_pending_selection(client, settings, monkeypatch):
    sid = _session(client)
    with monkeypatch.context() as patch:
        patch.setattr(reading, "parse", lambda *_args: ParseResult(status="failed"))
        source_id = _upload(client, sid, ("retry.txt", SOURCE_A))[0]
    _select(client, sid, [source_id])
    with connect(settings.db_path, immediate=True) as conn:
        row = conn.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
        failed_run = row["current_run_id"]
        assert row["parse_status"] == "failed"
        assert conn.execute("SELECT run_id FROM session_source_selections WHERE source_id=?", (source_id,)).fetchone()[0] is None
        reading._apply_result(conn, row, ParseResult(
            status="complete", text_available=True,
            segments=[Segment({"line_start": 1, "line_end": 1}, "회사명: 재시도 성공")]))
        selected = preflights.build_sources(conn, sid, [source_id])
        assert len(selected) == 1 and selected[0].segments[0].text == "회사명: 재시도 성공"
        assert conn.execute("SELECT run_id FROM session_source_selections WHERE source_id=?", (source_id,)).fetchone()[0] != failed_run
        assert conn.execute("SELECT status FROM extraction_runs WHERE run_id=?", (failed_run,)).fetchone()[0] == "failed"
        _assert_integrity(conn)


def test_draft_and_manual_edit_record_session_and_exact_preflight(app, settings):
    ctx = Ctx(app, with_photo=False)
    ctx.patch([{"op": "rename_page", "page_id": "page_02", "title": "수동 수정 제목"}])
    with connect(settings.db_path) as conn:
        revisions = conn.execute(
            "SELECT revision, session_id, preflight_id FROM document_revisions WHERE document_id=? ORDER BY revision",
            (ctx.did,),
        ).fetchall()
        assert [tuple(r) for r in revisions] == [(1, ctx.sid, ctx.pf), (2, ctx.sid, None)]
        assert conn.execute("SELECT confirmed_at FROM preflights WHERE preflight_id=?", (ctx.pf,)).fetchone()[0] is not None
        _assert_integrity(conn)


def test_registered_import_creates_history_once_and_links_all_evidence(settings):
    first = registered.import_bundle(settings, INGEST, with_mock=True)
    assert (first.added_sources, first.added_segments, first.added_assets) == (8, 24, 6)
    with connect(settings.db_path) as conn:
        before = {r[0]: r[1] for r in conn.execute("SELECT source_id, current_run_id FROM sources")}
        assert all(before.values())
        assert conn.execute("SELECT COUNT(*) FROM source_versions").fetchone()[0] == 8
        assert conn.execute("SELECT COUNT(*) FROM extraction_runs").fetchone()[0] == 8
        for table in ("segments", "assets"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id IS NULL").fetchone()[0] == 0
        _assert_integrity(conn)
    second = registered.import_bundle(settings, INGEST, with_mock=True)
    assert second.added_sources == second.added_segments == second.added_assets == 0
    assert second.already_present == 8
    with connect(settings.db_path) as conn:
        after = {r[0]: r[1] for r in conn.execute("SELECT source_id, current_run_id FROM sources")}
        assert after == before
        assert conn.execute("SELECT COUNT(*) FROM source_versions").fetchone()[0] == 8
        assert conn.execute("SELECT COUNT(*) FROM extraction_runs").fetchone()[0] == 8
        _assert_integrity(conn)


def test_registered_dry_run_leaves_no_history_or_copied_files(settings):
    summary = registered.run(settings, INGEST, with_mock=True, dry_run=True)
    assert summary.added_sources == 8 and summary.dry_run
    with connect(settings.db_path) as conn:
        for table in ("sources", "segments", "assets", "source_versions", "extraction_runs", "registered_imports"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        _assert_integrity(conn)
    assert not (settings.private_runs_dir / "registered").exists()


def test_source_delete_scrubs_version_and_extraction_private_content(client, settings):
    sid = _session(client)
    source_id = _upload(client, sid, ("private-filename.txt", SOURCE_A))[0]
    revision = _select(client, sid, [source_id])
    with connect(settings.db_path) as conn:
        stored_path = conn.execute("SELECT stored_path FROM sources WHERE source_id=?", (source_id,)).fetchone()[0]
        conn.execute("UPDATE extraction_runs SET summary_json=?, warnings_json=? WHERE source_id=?",
                     ('{"private": "private excerpt"}', '[{"message": "private warning"}]', source_id))
    response = client.delete(f"/api/v1/sessions/{sid}/sources/{source_id}", params={"expected_input_revision": revision})
    assert response.status_code == 200, response.text
    assert not (settings.private_runs_dir / stored_path).exists()
    with connect(settings.db_path) as conn:
        version = conn.execute("SELECT * FROM source_versions WHERE source_id=?", (source_id,)).fetchone()
        assert version["original_name"] == version["stored_path"] == version["provenance"] == ""
        assert version["purged_at"] is not None
        run = conn.execute("SELECT * FROM extraction_runs WHERE source_id=?", (source_id,)).fetchone()
        assert run["summary_json"] == "{}" and run["warnings_json"] == "[]" and run["purged_at"] is not None
        assert conn.execute("SELECT COUNT(*) FROM segments WHERE source_id=?", (source_id,)).fetchone()[0] == 0
        assert preflights.build_sources(conn, sid, [source_id]) == []
        _assert_integrity(conn)


@pytest.mark.parametrize("change", ["document", "input"])
def test_final_confirmation_is_created_once_and_invalidated_with_approval(app, settings, change):
    ctx = Ctx(app, with_photo=False)
    validation = ctx.make_clean_and_validate(settings)
    layout_id = ctx.layout_row(settings)
    declined = ctx.approve(validation["validation_id"], layout_id, confirmed=False)
    assert declined.status_code == 422
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM confirmations").fetchone()[0] == 0
    response = ctx.approve(validation["validation_id"], layout_id, headers={"Idempotency-Key": "orm-approve"})
    assert response.status_code == 201, response.text
    approval = response.json()
    replay = ctx.approve(validation["validation_id"], layout_id, headers={"Idempotency-Key": "orm-approve"})
    assert replay.status_code == 201 and replay.json() == approval
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM confirmations").fetchone()
        assert conn.execute("SELECT COUNT(*) FROM confirmations").fetchone()[0] == 1
        assert row["kind"] == "final_consent" and row["status"] == "active"
        assert row["document_id"] == ctx.did and row["session_id"] == ctx.sid
        assert row["document_revision"] == approval["document_revision"] and row["input_revision"] == ctx.rev_in
        assert row["confirmed_by"] == approval["approved_by"] and row["confirmed_at"] == approval["approved_at"]
        assert row["validation_id"] == validation["validation_id"] and row["layout_check_id"] == layout_id
        confirmation_id = row["confirmation_id"]
        assert conn.execute("SELECT confirmation_id FROM approvals WHERE approval_id=?", (approval["approval_id"],)).fetchone()[0] == confirmation_id
    if change == "document":
        ctx.patch([{"op": "rename_page", "page_id": "page_02", "title": "승인 뒤 변경"}])
    else:
        response = ctx.c.patch(f"/api/v1/sessions/{ctx.sid}/inputs",
                               json={"expected_input_revision": ctx.rev_in, "brief": {**BRIEF, "purpose": "변경"}})
        assert response.status_code == 200, response.text
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM confirmations WHERE confirmation_id=?", (confirmation_id,)).fetchone()
        assert row["status"] == "invalidated" and row["invalidated_at"] is not None
        assert conn.execute("SELECT status FROM approvals WHERE approval_id=?", (approval["approval_id"],)).fetchone()[0] == "invalidated"
        _assert_integrity(conn)


def test_session_close_scrubs_new_history_without_purging_registered_sources(app, settings):
    registered.import_bundle(settings, INGEST, with_mock=True)
    ctx = Ctx(app, with_photo=False)
    validation = ctx.make_clean_and_validate(settings)
    approved = ctx.approve(validation["validation_id"], ctx.layout_row(settings))
    assert approved.status_code == 201, approved.text
    with connect(settings.db_path) as conn:
        conn.execute(
            "INSERT INTO impact_reviews (review_id, session_id, document_id, document_revision, from_input_revision, "
            "to_input_revision, preflight_id, items_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("impact_private", ctx.sid, ctx.did, ctx.rev(), ctx.rev_in, ctx.rev_in, ctx.pf,
             '[{"excerpt":"private excerpt"}]', "pending", "test"),
        )
        conn.execute("UPDATE confirmations SET reasons_json=? WHERE session_id=?", ('["private reason"]', ctx.sid))
    closed = ctx.c.delete(f"/api/v1/sessions/{ctx.sid}")
    assert closed.status_code == 200, closed.text
    with connect(settings.db_path) as conn:
        for row in conn.execute("SELECT * FROM input_revisions WHERE session_id=?", (ctx.sid,)):
            assert row["brief_json"] == "{}" and row["purged_at"] is not None
        for row in conn.execute("SELECT * FROM source_versions WHERE session_id=?", (ctx.sid,)):
            assert row["original_name"] == row["stored_path"] == "" and row["purged_at"] is not None
        for row in conn.execute("SELECT * FROM extraction_runs WHERE session_id=?", (ctx.sid,)):
            assert row["warnings_json"] == "[]" and row["summary_json"] == "{}" and row["purged_at"] is not None
        impact = conn.execute("SELECT * FROM impact_reviews WHERE review_id='impact_private'").fetchone()
        assert impact["items_json"] == "[]" and impact["purged_at"] is not None
        confirmation = conn.execute("SELECT * FROM confirmations WHERE session_id=?", (ctx.sid,)).fetchone()
        assert confirmation["confirmed_by"] == "" and confirmation["reasons_json"] == "[]"
        assert confirmation["status"] == "invalidated" and confirmation["purged_at"] is not None
        registered_version = conn.execute("SELECT * FROM source_versions WHERE source_id='MOCK01'").fetchone()
        assert registered_version["purged_at"] is None and registered_version["original_name"]
        assert conn.execute("SELECT COUNT(*) FROM segments WHERE source_id='MOCK01'").fetchone()[0] > 0
        _assert_integrity(conn)


@pytest.fixture
def published_history(app, settings):
    """실제 API로 만든 가상 문서/승인/출력. PDF·미리보기 바이트만 격리 fixture로 대신한다."""
    from app.services import cleanup
    from test_be08_rules import Flow, _png

    flow = Flow(app, settings, upload_png=False)
    try:
        target = next(b for p in flow.doc()["pages"] for b in p["blocks"] if b["type"] == "paragraph")
        response = flow.c.post(
            f"/api/v1/sessions/{flow.sid}/documents/{flow.did}/proposals",
            json={"expected_revision": flow.rev(), "input_revision": flow.rev_in,
                  "target_block_ids": [target["block_id"]], "instruction": "근거를 유지하며 정리", "kind": "text"},
            headers={"Idempotency-Key": "fk-proposal"},
        )
        assert response.status_code == 202, response.text
        proposal_id = flow.job(response.json()["job_id"])["result_ref"]["proposal_id"]
        approval, artifact = flow.approved()
        exported = flow.export_ready(approval["approval_id"])
        preview_path = artifact["path"].parent / "previews" / "fixture.png"
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_bytes = _png()
        preview_path.write_bytes(preview_bytes)
        with connect(settings.db_path) as conn:
            preflight_id = conn.execute("SELECT preflight_id FROM preflights WHERE session_id=?", (flow.sid,)).fetchone()[0]
            conn.execute(
                "INSERT INTO layout_previews (asset_id,session_id,layout_check_id,artifact_id,page_no,stored_path,sha256,"
                "size_bytes,width,height,mime_type,status,created_at) VALUES ('preview_fk',?,?,?,1,?,?,?,8,6,'image/png','ready','fixture')",
                (flow.sid, artifact["layout_check_id"], artifact["artifact_id"],
                 preview_path.relative_to(settings.private_runs_dir).as_posix(),
                 hashlib.sha256(preview_bytes).hexdigest(), len(preview_bytes)),
            )
            task_id = cleanup.enqueue(conn, flow.sid, "orphan_tmp", f"{flow.sid}/artifacts/tmp_fixture")
            assert task_id is not None
            _assert_integrity(conn)
        yield SimpleNamespace(flow=flow, artifact=artifact, preview_path=preview_path, ids={
            "session": flow.sid, "document": flow.did, "preflight": preflight_id, "proposal": proposal_id,
            "validation": approval["validation_id"], "layout": artifact["layout_check_id"],
            "artifact": artifact["artifact_id"], "approval": approval["approval_id"],
            "export": exported["export_id"], "preview": "preview_fk", "idempotency": "fk-proposal", "cleanup": task_id,
        })
    finally:
        flow.c.close()


def _stored_rows(settings):
    """스키마 모양 대신 트랜잭션 전후의 실제 저장 내용 전체를 비교한다."""
    with connect(settings.db_path) as conn:
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {table: [tuple(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
                for table in tables}


@pytest.mark.parametrize("table,column,key_column,key_name,bad_value,deferred", [
    ("preflights", "input_revision", "preflight_id", "preflight", 99999, False),
    ("document_revisions", "input_revision", "document_id", "document", 99999, False),
    ("proposals", "base_document_revision", "proposal_id", "proposal", 99999, False),
    ("validations", "document_revision", "validation_id", "validation", 99999, False),
    ("artifacts", "document_revision", "artifact_id", "artifact", 99999, False),
    ("layout_checks", "document_revision", "layout_check_id", "layout", 99999, False),
    ("approvals", "validation_id", "approval_id", "approval", "missing_validation", False),
    ("approvals", "layout_check_id", "approval_id", "approval", "missing_layout", False),
    ("approvals", "artifact_id", "approval_id", "approval", "missing_artifact", False),
    ("exports", "approval_id", "export_id", "export", "missing_approval", False),
    ("exports", "artifact_id", "export_id", "export", "missing_artifact", False),
    ("layout_checks", "artifact_id", "layout_check_id", "layout", "missing_artifact", False),
    ("layout_previews", "artifact_id", "asset_id", "preview", "missing_artifact", False),
    ("layout_previews", "layout_check_id", "asset_id", "preview", "missing_layout", True),
    ("idempotency_keys", "session_id", "idem_key", "idempotency", "missing_session", False),
    ("cleanup_queue", "session_id", "task_id", "cleanup", "missing_session", False),
], ids=lambda value: str(value))
def test_broken_history_reference_rejects_write_and_rolls_back_prior_changes(
    published_history, settings, table, column, key_column, key_name, bad_value, deferred,
):
    history = published_history
    before = _stored_rows(settings)
    artifact_bytes = history.artifact["path"].read_bytes()
    update_finished = False
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        with connect(settings.db_path) as conn:
            conn.execute("UPDATE documents SET title='must roll back' WHERE document_id=?", (history.flow.did,))
            changed = conn.execute(f'UPDATE "{table}" SET "{column}"=? WHERE "{key_column}"=?',
                                   (bad_value, history.ids[key_name]))
            assert changed.rowcount > 0
            update_finished = True
    # 대부분은 잘못된 UPDATE에서 즉시 차단한다. preview→layout은 트랜잭션 종료 시 차단한다.
    assert update_finished is deferred
    assert _stored_rows(settings) == before
    assert history.artifact["path"].read_bytes() == artifact_bytes
    with connect(settings.db_path) as conn:
        _assert_integrity(conn)


@pytest.mark.parametrize("table,key_column,key_name", [
    ("preflights", "preflight_id", "preflight"),
    ("document_revisions", "document_id", "document"),
])
def test_input_revision_from_another_session_is_not_a_valid_basis(
    published_history, settings, table, key_column, key_name,
):
    history = published_history
    other_session = _session(history.flow.c)
    with connect(settings.db_path) as conn:
        conn.execute("INSERT INTO input_revisions (session_id,revision,brief_json,created_at) VALUES (?,777,'{}','fixture')",
                     (other_session,))
    before = _stored_rows(settings)
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        with connect(settings.db_path) as conn:
            conn.execute(f'UPDATE "{table}" SET input_revision=777 WHERE "{key_column}"=?', (history.ids[key_name],))
    assert _stored_rows(settings) == before


@pytest.mark.parametrize("table,key_column,key_name,revision_column", [
    ("proposals", "proposal_id", "proposal", "base_document_revision"),
    ("validations", "validation_id", "validation", "document_revision"),
    ("artifacts", "artifact_id", "artifact", "document_revision"),
    ("layout_checks", "layout_check_id", "layout", "document_revision"),
])
def test_existing_document_cannot_borrow_another_documents_revision(
    published_history, app, settings, table, key_column, key_name, revision_column,
):
    history = published_history
    other = Ctx(app, with_photo=False)
    try:
        assert other.rev() == 1 and history.flow.rev() > 1
        before = _stored_rows(settings)
        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            with connect(settings.db_path) as conn:
                # 문서 ID·세션 ID는 각각 존재하지만 그 문서의 해당 revision은 존재하지 않는다.
                conn.execute(f'UPDATE "{table}" SET document_id=?,session_id=? WHERE "{key_column}"=?',
                             (other.did, other.sid, history.ids[key_name]))
        assert _stored_rows(settings) == before
        with connect(settings.db_path) as conn:
            stored_revision = conn.execute(f'SELECT "{revision_column}" FROM "{table}" WHERE "{key_column}"=?',
                                           (history.ids[key_name],)).fetchone()[0]
            assert stored_revision == history.flow.rev()
            _assert_integrity(conn)
    finally:
        other.c.close()


def test_preview_can_precede_its_layout_check_in_one_successful_transaction(published_history, settings):
    history = published_history
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE layout_previews SET layout_check_id='layout_created_later' WHERE asset_id=?",
                     (history.ids["preview"],))
        assert conn.execute("SELECT 1 FROM layout_checks WHERE layout_check_id='layout_created_later'").fetchone() is None
        conn.execute(
            "INSERT INTO layout_checks (layout_check_id,session_id,document_id,document_revision,input_revision,format,"
            "template_version,render_options_hash,asset_manifest_hash,status,actual_pages,created_at,artifact_id) "
            "SELECT 'layout_created_later',session_id,document_id,document_revision,input_revision,format,"
            "template_version,render_options_hash,asset_manifest_hash,status,actual_pages,created_at,artifact_id "
            "FROM layout_checks WHERE layout_check_id=?", (history.ids["layout"],),
        )
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT layout_check_id FROM layout_previews WHERE asset_id=?",
                            (history.ids["preview"],)).fetchone()[0] == "layout_created_later"
        _assert_integrity(conn)


def test_closing_published_session_keeps_references_and_purges_private_content(published_history, settings):
    history = published_history
    flow = history.flow
    with connect(settings.db_path) as conn:
        revision_count = conn.execute("SELECT COUNT(*) FROM document_revisions WHERE document_id=?", (flow.did,)).fetchone()[0]
    response = flow.c.delete(f"/api/v1/sessions/{flow.sid}")
    assert response.status_code == 200 and response.json()["cleanup"] == "done", response.text
    assert not (settings.private_runs_dir / flow.sid).exists()
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT status FROM sessions WHERE session_id=?", (flow.sid,)).fetchone()[0] == "closed"
        assert conn.execute("SELECT COUNT(*) FROM document_revisions WHERE document_id=?", (flow.did,)).fetchone()[0] == revision_count
        assert all(row[0] == "{}" for row in conn.execute("SELECT content_json FROM document_revisions WHERE document_id=?", (flow.did,)))
        assert conn.execute("SELECT facts_json FROM preflights WHERE preflight_id=?", (history.ids["preflight"],)).fetchone()[0] == "[]"
        assert conn.execute("SELECT changes_json FROM proposals WHERE proposal_id=?", (history.ids["proposal"],)).fetchone()[0] == "{}"
        assert conn.execute("SELECT deleted_at FROM layout_previews WHERE asset_id=?", (history.ids["preview"],)).fetchone()[0] is not None
        assert conn.execute("SELECT finalized_reason FROM exports WHERE export_id=?", (history.ids["export"],)).fetchone()[0] == "session_closed"
        assert conn.execute("SELECT response_json FROM idempotency_keys WHERE idem_key='fk-proposal'").fetchone()[0] == "{}"
        assert conn.execute("SELECT COUNT(*) FROM cleanup_queue WHERE session_id=? AND status!='done'", (flow.sid,)).fetchone()[0] == 0
        _assert_integrity(conn)


def _migration_snapshot(path):
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(path)) as conn:
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name!='alembic_version' ORDER BY name")]
        return {name: conn.execute(f'SELECT rowid,* FROM "{name}" ORDER BY rowid').fetchall() for name in names}


def test_populated_relationship_upgrade_and_downgrade_keep_rows_rowids_and_files(published_history, settings):
    from alembic import command
    from app.schema_migrations import BASELINE_REVISION, make_config, upgrade_database, validate_baseline

    history = published_history
    with connect(settings.db_path) as conn:
        # A hole in rowid numbering must survive rebuilds (several queries use it as a tie-breaker).
        conn.execute("UPDATE layout_previews SET rowid=71 WHERE asset_id=?", (history.ids["preview"],))
    before = _migration_snapshot(settings.db_path)
    artifact = history.artifact["path"].read_bytes()
    preview = history.preview_path.read_bytes()
    config = make_config(settings.db_path)
    command.downgrade(config, BASELINE_REVISION)
    assert _migration_snapshot(settings.db_path) == before
    with connect(settings.db_path) as conn:
        validate_baseline(conn.sqlalchemy_connection)
    upgrade_database(settings.db_path)
    assert _migration_snapshot(settings.db_path) == before
    command.downgrade(config, BASELINE_REVISION)
    assert _migration_snapshot(settings.db_path) == before
    assert history.artifact["path"].read_bytes() == artifact
    assert history.preview_path.read_bytes() == preview


def test_old_invalid_relationship_is_rejected_without_changing_rows_or_schema(published_history, settings):
    from alembic import command
    from app.schema_migrations import BASELINE_REVISION, make_config, upgrade_database

    command.downgrade(make_config(settings.db_path), BASELINE_REVISION)
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE preflights SET input_revision=99999 WHERE preflight_id=?",
                     (published_history.ids["preflight"],))
        schema = [tuple(row) for row in conn.execute("SELECT * FROM sqlite_master ORDER BY name")]
    before = _stored_rows(settings)
    with pytest.raises(ValueError, match="invalid existing reference fk_preflights_input_revision"):
        upgrade_database(settings.db_path)
    assert _stored_rows(settings) == before
    with connect(settings.db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
        assert [tuple(row) for row in conn.execute("SELECT * FROM sqlite_master ORDER BY name")] == schema


def test_mid_rebuild_failure_restores_populated_tables_and_version(published_history, settings):
    from alembic import command
    from sqlalchemy import event
    from app.db import get_engine
    from app.schema_migrations import BASELINE_REVISION, make_config, upgrade_database, validate_baseline

    command.downgrade(make_config(settings.db_path), BASELINE_REVISION)
    before = _stored_rows(settings)
    before_rowids = _migration_snapshot(settings.db_path)
    engine = get_engine(settings.db_path)

    def fail_after_first_rebuilt_tables(connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("CREATE TABLE proposals ("):
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM preflights").scalar_one() > 0
            raise RuntimeError("simulated relationship rebuild failure")

    event.listen(engine, "before_cursor_execute", fail_after_first_rebuilt_tables)
    try:
        with pytest.raises(RuntimeError, match="simulated relationship rebuild failure"):
            upgrade_database(settings.db_path)
    finally:
        event.remove(engine, "before_cursor_execute", fail_after_first_rebuilt_tables)
    assert _stored_rows(settings) == before
    assert _migration_snapshot(settings.db_path) == before_rowids
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA user_version").scalar_one() == 10
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        validate_baseline(conn)
