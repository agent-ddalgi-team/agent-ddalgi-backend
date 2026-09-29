"""요청·응답 계약 회귀. 임시 ORM DB, 가상 자료와 mock Agent만 사용한다."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import create_app
from app import models
from app.config import Settings
from app.db import connect, init_orm_db
from test_be04 import BRIEF, SOURCE_A
from test_be06 import Ctx


@pytest.fixture
def settings(tmp_path):
    value = Settings(
        private_runs_dir=tmp_path / "runs",
        db_path=tmp_path / "runs" / "contract.sqlite3",
        agent_mode="mock",
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


def _snapshot(settings):
    """세션·Job·버전·승인·멱등 기록 등 모든 행의 무변경을 확인한다."""
    with sqlite3.connect(settings.db_path.as_uri() + "?mode=ro", uri=True) as conn:
        return tuple(conn.iterdump())


def _invalid(response, field=None):
    assert response.status_code == 400, response.text
    error = models.ApiErrorOut.model_validate(response.json()).error
    assert error.code == "INVALID_REQUEST" and error.retryable is False
    assert error.request_id == response.headers["x-request-id"]
    if field is not None:
        assert field in error.details["fields"]
    return error


# Every public JSON request with a revision participates in the same rule.
REVISION_REQUESTS = [
    (models.InputsPatch, {"expected_input_revision": 1, "selected_source_ids": []}),
    (models.PreflightCreate, {"expected_input_revision": 1}),
    (models.DraftCreate, {"preflight_id": "pf_example", "input_revision": 1, "confirmed": True}),
    (models.DocumentPatch, {"expected_revision": 1,
                            "operations": [{"op": "delete_block", "block_id": "block_example"}]}),
    (models.RestoreBody, {"expected_revision": 1, "restore_from_revision": 2}),
    (models.ProposalCreate, {"expected_revision": 1, "input_revision": 1,
                             "target_block_ids": ["block_example"], "instruction": "정리", "kind": "text"}),
    (models.ApplyBody, {"expected_revision": 1}),
    (models.ValidateBody, {"expected_revision": 1, "input_revision": 1}),
    (models.IssueResolveBody, {"expected_revision": 1, "input_revision": 1, "validation_id": "val_example",
                               "resolution": {"action": "acknowledged", "reason": "내용 확인"}}),
    (models.LayoutCheckCreate, {"expected_revision": 1, "format": "pdf"}),
    (models.ApprovalCreate, {"expected_revision": 1, "input_revision": 1, "format": "pdf",
                            "validation_id": "val_example", "layout_check_id": "lc_example", "confirmed": True}),
]


@pytest.mark.parametrize("model,payload", REVISION_REQUESTS, ids=lambda value: getattr(value, "__name__", None))
def test_request_revisions_reject_coercion_and_out_of_range(model, payload):
    assert model.model_validate(payload)
    for field in (name for name in payload if "revision" in name):
        for invalid in (True, "1", 1.0, 0, -1, 2**63):
            with pytest.raises(ValidationError):
                model.model_validate({**payload, field: invalid})


@pytest.mark.parametrize("model,payload,field", [
    (models.Brief, {"purpose": "정상 목적"}, "purpose"),
    (models.ProposalCreate, {"expected_revision": 1, "input_revision": 1, "target_block_ids": ["b"],
                             "instruction": "정상 요청", "kind": "text"}, "instruction"),
    (models.Resolution, {"action": "acknowledged", "reason": "정상 이유"}, "reason"),
    (models.OpRenamePage, {"op": "rename_page", "page_id": "p", "title": "정상 제목"}, "title"),
])
def test_required_text_rejects_whitespace_without_rewriting_valid_text(model, payload, field):
    for invalid in ("", " \t\r\n"):
        with pytest.raises(ValidationError):
            model.model_validate({**payload, field: invalid})
    text = "  사용자가 입력한 문장\n"
    assert getattr(model.model_validate({**payload, field: text}), field) == text


@pytest.mark.parametrize("model,payload,field", [
    (models.InputsPatch, {"expected_input_revision": 1}, "selected_source_ids"),
    (models.ProposalCreate, {"expected_revision": 1, "input_revision": 1,
                             "instruction": "수정", "kind": "text"}, "target_block_ids"),
])
def test_selection_ids_reject_duplicates_and_blank_ids(model, payload, field):
    for invalid in (["id", "id"], [""], [" \t"]):
        with pytest.raises(ValidationError):
            model.model_validate({**payload, field: invalid})
    # IDs remain opaque, rather than requiring a prefix or UUID format.
    assert getattr(model.model_validate({**payload, field: ["불투명한-식별자"]}), field) == ["불투명한-식별자"]


@pytest.mark.parametrize("kind,reference", [
    ("read", {"type": "sources", "source_ids": ["src_example"]}),
    ("preflight", {"type": "preflight", "preflight_id": "pf_example"}),
    ("draft", {"type": "document", "document_id": "doc_example", "document_revision": 1}),
    ("propose", {"type": "proposal", "proposal_id": "prop_example", "status": "proposed"}),
    ("validate", {"type": "validation", "validation_id": "val_example", "status": "needs_review"}),
    ("layout_check", {"layout_check_id": "lc_example", "format": "pdf", "status": "passed",
                      "layout_ok": True, "publication_policy_ok": True, "actual_pages": 4,
                      "artifact_id": "art_example", "preview_asset_ids": [], "preview_basis": "pdf", "warnings": []}),
    ("export", {"export_id": "exp_example", "artifact_id": "art_example", "format": "pdf"}),
])
def test_job_result_references_keep_public_dictionary_shape(kind, reference):
    payload = {"job_id": "job_example", "kind": kind, "status": "succeeded",
               "progress": {"stage": "done"}, "result_ref": reference,
               "created_at": "2026-09-28T00:00:00Z", "updated_at": "2026-09-28T00:00:01Z"}
    result = models.JobOut.model_validate(payload)
    assert isinstance(result.result_ref, dict)
    assert result.model_dump()["result_ref"] == reference
    with pytest.raises(ValidationError):
        models.JobOut.model_validate({**payload, "result_ref": {"unknown_result": "not-an-api-result"}})


@pytest.mark.parametrize("brief", [
    {**BRIEF, "purpose": " \t"},
    {**BRIEF, "target_pages": True},
    {**BRIEF, "target_pages": 4.0},
    {**BRIEF, "target_pages": "4"},
    {**BRIEF, "unknown_setting": "PRIVATE_SENTINEL"},
])
def test_invalid_session_request_does_not_create_session_or_echo_input(client, settings, brief):
    before = _snapshot(settings)
    response = client.post("/api/v1/sessions", json={"brief": brief})
    _invalid(response)
    assert "PRIVATE_SENTINEL" not in response.text
    assert _snapshot(settings) == before


def test_invalid_patch_preserves_approval_versions_jobs_and_history(app, settings):
    ctx = Ctx(app, with_photo=False)
    validation = ctx.make_clean_and_validate(settings)
    layout_id = ctx.layout_row(settings)
    approval = ctx.approve(validation["validation_id"], layout_id)
    assert approval.status_code == 201, approval.text
    source_ids = ctx.c.get(f"/api/v1/sessions/{ctx.sid}").json()["selected_source_ids"]
    before = _snapshot(settings)
    for payload in (
        {"expected_input_revision": ctx.rev_in},
        {"expected_input_revision": ctx.rev_in, "brief": None, "selected_source_ids": None},
        {"expected_input_revision": ctx.rev_in, "selected_source_ids": source_ids * 2},
        {"expected_input_revision": str(ctx.rev_in), "selected_source_ids": []},
    ):
        response = ctx.c.patch(f"/api/v1/sessions/{ctx.sid}/inputs", json=payload)
        _invalid(response)
        assert _snapshot(settings) == before
    assert ctx.get()["approval"]["status"] == "active"
    # An explicitly empty selection is a real change and still invalidates approval.
    cleared = ctx.c.patch(f"/api/v1/sessions/{ctx.sid}/inputs",
                          json={"expected_input_revision": ctx.rev_in, "selected_source_ids": []})
    assert cleared.status_code == 200 and cleared.json()["selected_source_ids"] == []
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT status FROM approvals").fetchone()[0] == "invalidated"


def test_s01_response_chain_and_strict_draft_confirmation(client, settings):
    created = client.post("/api/v1/sessions", json={"brief": BRIEF})
    assert created.status_code == 201
    session = models.SessionOut.model_validate(created.json())
    sid = session.session_id
    uploaded = client.post(f"/api/v1/sessions/{sid}/sources",
                           files=[("files", ("company.txt", SOURCE_A, "text/plain"))])
    assert uploaded.status_code == 202
    upload = models.UploadOut.model_validate(uploaded.json())
    read = models.JobOut.model_validate(client.get(f"/api/v1/sessions/{sid}/jobs/{upload.job_id}").json())
    source_id = upload.items[0].source_id
    assert read.result_ref == {"type": "sources", "source_ids": [source_id]}
    assert isinstance(read.result_ref, dict)
    selected = client.patch(f"/api/v1/sessions/{sid}/inputs",
                             json={"expected_input_revision": 1, "selected_source_ids": [source_id]})
    inputs = models.InputsOut.model_validate(selected.json())
    response = client.post(f"/api/v1/sessions/{sid}/preflights",
                           json={"expected_input_revision": inputs.input_revision})
    accepted = models.JobAccepted.model_validate(response.json())
    job = models.JobOut.model_validate(client.get(f"/api/v1/sessions/{sid}/jobs/{accepted.job_id}").json())
    assert job.status == "succeeded" and job.result_ref["type"] == "preflight"
    pfid = job.result_ref["preflight_id"]
    preflight = models.PreflightOut.model_validate(client.get(f"/api/v1/sessions/{sid}/preflights/{pfid}").json())
    assert preflight.can_generate and preflight.confirmed_at is None
    body = {"preflight_id": pfid, "input_revision": inputs.input_revision, "confirmed": True}
    before = _snapshot(settings)
    for invalid in ("true", "yes", 1):
        _invalid(client.post(f"/api/v1/sessions/{sid}/drafts", json={**body, "confirmed": invalid}), "confirmed")
        assert _snapshot(settings) == before
    declined = client.post(f"/api/v1/sessions/{sid}/drafts", json={**body, "confirmed": False})
    assert declined.status_code == 422 and declined.json()["error"]["code"] == "PREFLIGHT_NOT_CONFIRMED"
    assert _snapshot(settings) == before
    drafted = client.post(f"/api/v1/sessions/{sid}/drafts", json=body)
    assert drafted.status_code == 202
    accepted = models.JobAccepted.model_validate(drafted.json())
    job = models.JobOut.model_validate(client.get(f"/api/v1/sessions/{sid}/jobs/{accepted.job_id}").json())
    assert job.status == "succeeded" and job.result_ref["type"] == "document"
    did = job.result_ref["document_id"]
    document = models.DocumentOut.model_validate(client.get(f"/api/v1/sessions/{sid}/documents/{did}").json())
    assert document.document.document_revision == job.result_ref["document_revision"] == 1
    assert document.document.input_revision == inputs.input_revision
    assert "stored_path" not in document.model_dump_json()


def test_approval_confirmation_rejects_coercion_and_keeps_false_domain_error(app, settings):
    ctx = Ctx(app, with_photo=False)
    validation = ctx.make_clean_and_validate(settings)
    layout_id = ctx.layout_row(settings)
    before = _snapshot(settings)
    for invalid in ("true", 1):
        _invalid(ctx.approve(validation["validation_id"], layout_id, confirmed=invalid), "confirmed")
        assert _snapshot(settings) == before
    declined = ctx.approve(validation["validation_id"], layout_id, confirmed=False)
    assert declined.status_code == 422 and declined.json()["error"]["code"] == "APPROVAL_NOT_CONFIRMED"
    assert _snapshot(settings) == before
    accepted = ctx.approve(validation["validation_id"], layout_id, confirmed=True)
    assert accepted.status_code == 201


def test_oversized_restore_revision_returns_client_error_without_sqlite_overflow(app, settings):
    ctx = Ctx(app, with_photo=False)
    before = _snapshot(settings)
    response = ctx.c.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/restore",
                          json={"expected_revision": ctx.rev(), "restore_from_revision": 10**30})
    _invalid(response, "restore_from_revision")
    assert _snapshot(settings) == before


def test_query_revision_bounds_and_registered_kind(client, settings):
    sid = client.post("/api/v1/sessions", json={"brief": BRIEF}).json()["session_id"]
    uploaded = client.post(f"/api/v1/sessions/{sid}/sources",
                           files=[("files", ("a.txt", SOURCE_A, "text/plain"))]).json()
    source_id = uploaded["items"][0]["source_id"]
    before = _snapshot(settings)
    for invalid in ("0", "-1", str(2**63), "true"):
        _invalid(client.delete(f"/api/v1/sessions/{sid}/sources/{source_id}",
                               params={"expected_input_revision": invalid}))
        assert _snapshot(settings) == before
    _invalid(client.get("/api/v1/sources", params={"kind": "unknown_kind"}))
    assert client.get("/api/v1/sources", params={"kind": "company"}).status_code == 200
    # URL query parameters are strings on the wire, unlike JSON numbers.
    assert client.delete(f"/api/v1/sessions/{sid}/sources/{source_id}",
                         params={"expected_input_revision": "1"}).status_code == 200


def test_framework_errors_use_common_envelope_and_preserve_allow_header(client):
    missing = client.get("/api/v1/no-such-route")
    assert missing.status_code == 404
    assert models.ApiErrorOut.model_validate(missing.json()).error.request_id == missing.headers["x-request-id"]
    wrong_method = client.put("/api/v1/sessions", json={})
    assert wrong_method.status_code == 405
    models.ApiErrorOut.model_validate(wrong_method.json())
    assert "POST" in wrong_method.headers["allow"]
    assert "detail" not in wrong_method.json()


def test_unhandled_error_has_matching_request_id_and_hides_exception_text(app, settings, monkeypatch):
    from app.services import sessions

    def fail_creation(*args, **kwargs):
        raise RuntimeError("PRIVATE_SENTINEL: internal database details")

    monkeypatch.setattr(sessions, "create", fail_creation)
    before = _snapshot(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/sessions", json={"brief": BRIEF})
    assert response.status_code == 500
    error = models.ApiErrorOut.model_validate(response.json()).error
    assert error.code == "INTERNAL_ERROR" and error.retryable is True
    assert error.request_id == response.headers["x-request-id"]
    assert "PRIVATE_SENTINEL" not in response.text and "internal database details" not in response.text
    assert _snapshot(settings) == before


def test_openapi_describes_common_errors_export_statuses_and_binary_responses(app):
    schema = app.openapi()
    operations = [operation for path, item in schema["paths"].items() if path.startswith("/api/v1/")
                  for method, operation in item.items() if method in {"get", "post", "patch", "delete"}]
    assert len(operations) == 27
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    for operation in operations:
        for status in ("400", "422"):
            error_schema = operation["responses"][status]["content"]["application/json"]["schema"]
            assert error_schema["$ref"].endswith("/ApiErrorOut")
    exports = schema["paths"]["/api/v1/sessions/{sid}/exports"]["post"]["responses"]
    for status in ("200", "202"):
        assert exports[status]["content"]["application/json"]["schema"]["$ref"].endswith("/ExportAccepted")
    download = schema["paths"]["/api/v1/sessions/{sid}/exports/{eid}/download"]["get"]["responses"]["200"]["content"]
    assert {"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"} <= download.keys()
    image = schema["paths"]["/api/v1/sessions/{sid}/assets/{asset_id}"]["get"]["responses"]["200"]["content"]
    assert {"image/png", "image/jpeg"} <= image.keys()
    for media in (download, image):
        assert "application/json" not in media
        assert all(value["schema"] == {"type": "string", "format": "binary"} for value in media.values())


def test_handoff_json_response_examples_match_route_models(app):
    examples = json.loads((Path(__file__).parents[1] / "handoff" / "api_examples_v1.1.json").read_text(encoding="utf-8-sig"))
    paths = app.openapi()["paths"]

    def abbreviated(value):
        if isinstance(value, dict):
            return "..." in value or any(abbreviated(item) for item in value.values())
        return isinstance(value, list) and any(abbreviated(item) for item in value)

    def assert_no_fields_dropped(original, validated, label):
        if isinstance(original, dict) and isinstance(validated, dict):
            assert original.keys() <= validated.keys(), label
            for key in original:
                assert_no_fields_dropped(original[key], validated[key], f"{label}.{key}")
        elif isinstance(original, list) and isinstance(validated, list):
            for index, (left, right) in enumerate(zip(original, validated)):
                assert_no_fields_dropped(left, right, f"{label}.{index}")

    checked = 0
    for example in examples["examples"]:
        response = example.get("response", {})
        body = response.get("body")
        status = response.get("http_status", response.get("status"))
        if not isinstance(body, dict) or not isinstance(status, int) or "request" not in example or abbreviated(body):
            continue  # Binary, abbreviated and standalone object descriptions are not full route responses.
        request = example["request"]
        path = urlsplit(request["path"]).path
        if not path.startswith(examples["base_path"] + "/"):
            path = examples["base_path"] + path
        method = request["method"].lower()
        operation = next((item[method] for template, item in paths.items()
                          if re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), path)
                          and method in item), None)
        assert operation is not None, example["name"]
        if status >= 400:
            model = models.ApiErrorOut
        else:
            definition = operation["responses"][str(status)]["content"]["application/json"]["schema"]
            model = getattr(models, definition["$ref"].rsplit("/", 1)[1])
        try:
            validated = model.model_validate(body).model_dump(mode="json")
            assert_no_fields_dropped(body, validated, example["name"])
        except ValidationError as exc:
            pytest.fail(f"{example['name']}: {exc}")
        checked += 1
    assert checked >= 60
