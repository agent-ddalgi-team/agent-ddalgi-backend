"""ERD v10 API/이력 회귀. 가상 자료와 mock Agent만 사용하며 브라우저는 실행하지 않는다."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.config import Settings
from app.db import connect, init_orm_db
from app.parsers import ParseResult, Segment
from app.services import preflights, reading, registered
from test_be04 import BRIEF, SOURCE_A, _select, _session, _upload
from test_be06 import Ctx


INGEST = Path(__file__).parent / "fixtures" / "ddalgi_mock_bundle_v1" / "ingest"


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
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


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


@pytest.fixture(params=[9, 10], ids=["legacy-v9", "orm-v10"])
def upload_case(tmp_path, request):
    """구형/ERD DB 양쪽에서 같은 업로드 요청·오류·재전송 규칙을 확인한다."""
    value = Settings(private_runs_dir=tmp_path / "uploads", db_path=tmp_path / "uploads" / "test.sqlite3",
                     agent_mode="mock", cleanup_sweep_interval_s=0, max_files_per_session=3,
                     export_browser_path=str(tmp_path / "no-browser.exe"))
    if request.param == 10:
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
        if conn.execute("PRAGMA user_version").fetchone()[0] == 10:
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
