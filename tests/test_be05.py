"""BE-05 확인: 편집 연산 8종, 원자성·버전 충돌·멱등·동시성, Proposal 생성/적용/거부/stale, restore, 출처 보존.

mock Agent·가짜 회사("예시 회사")만. 실제 LLM 호출 없음. Validation(BE-06)은 미연결 — GET documents.validation은 null.
"""
from __future__ import annotations

import io
import json
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.agent_bridge import AgentError
from app.agent_mock import MockAgent
from app.config import Settings
from app.db import connect, init_db, init_orm_db

BRIEF = {"purpose": "테스트", "emphasis": [], "direction": "balanced", "target_pages": 4, "photo_preference": "balanced"}
SOURCE_A = "회사명: 예시 회사\n회사 개요: 예시용 기업입니다.\n사업 분야: 예시 사업 A\n".encode()


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (0, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def settings(tmp_path):
    return Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "t.sqlite3")


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


class Ctx:
    """세션 + 초안 rev.1까지 만든 상태."""

    def __init__(self, client: TestClient, with_photo: bool = True):
        self.c = client
        self.sid = client.post("/api/v1/sessions", json={"brief": BRIEF}).json()["session_id"]
        files = [("files", ("a.txt", io.BytesIO(SOURCE_A)))]
        if with_photo:
            files.append(("files", ("p.png", io.BytesIO(_png()))))
        up = client.post(f"/api/v1/sessions/{self.sid}/sources", files=files).json()
        self.src = [i["source_id"] for i in up["items"]]
        self.rev_in = client.patch(f"/api/v1/sessions/{self.sid}/inputs",
                                   json={"expected_input_revision": 1, "selected_source_ids": self.src}).json()["input_revision"]
        r = client.post(f"/api/v1/sessions/{self.sid}/preflights", json={"expected_input_revision": self.rev_in})
        job = client.get(f"/api/v1/sessions/{self.sid}/jobs/{r.json()['job_id']}").json()
        self.pf = job["result_ref"]["preflight_id"]
        r = client.post(f"/api/v1/sessions/{self.sid}/drafts",
                        json={"preflight_id": self.pf, "input_revision": self.rev_in, "confirmed": True})
        job = client.get(f"/api/v1/sessions/{self.sid}/jobs/{r.json()['job_id']}").json()
        assert job["status"] == "succeeded", job
        self.did = job["result_ref"]["document_id"]

    def doc(self) -> dict:
        return self.c.get(f"/api/v1/sessions/{self.sid}/documents/{self.did}").json()["document"]

    def patch(self, ops, expected=None, headers=None):
        expected = self.doc()["document_revision"] if expected is None else expected
        return self.c.patch(f"/api/v1/sessions/{self.sid}/documents/{self.did}",
                            json={"expected_revision": expected, "operations": ops}, headers=headers or {})

    def propose(self, target_ids, kind="text", instruction="정리해줘", expected=None) -> dict:
        d = self.doc()
        r = self.c.post(f"/api/v1/sessions/{self.sid}/documents/{self.did}/proposals",
                        json={"expected_revision": d["document_revision"] if expected is None else expected,
                              "input_revision": self.rev_in, "target_block_ids": target_ids,
                              "instruction": instruction, "kind": kind})
        assert r.status_code == 202, r.text
        return self.c.get(f"/api/v1/sessions/{self.sid}/jobs/{r.json()['job_id']}").json()

    def apply(self, pid, expected=None, candidate=None, headers=None):
        expected = self.doc()["document_revision"] if expected is None else expected
        body = {"expected_revision": expected}
        if candidate:
            body["selected_candidate_id"] = candidate
        return self.c.post(f"/api/v1/sessions/{self.sid}/proposals/{pid}/apply", json=body, headers=headers or {})

    def proposal(self, pid) -> dict:
        return self.c.get(f"/api/v1/sessions/{self.sid}/proposals/{pid}").json()


def _blocks(doc, page=0):
    return doc["pages"][page]["blocks"]


def _find(doc, type_):
    return next(b for p in doc["pages"] for b in p["blocks"] if b["type"] == type_)


# ================= 편집 연산 8종 (각각) =================

def test_op_replace_block_content_keeps_ids_and_refs(client):
    ctx = Ctx(client)
    before = ctx.doc()
    para = next(b for b in _blocks(before) if b["type"] == "paragraph" and b["fact_ids"])
    r = ctx.patch([{"op": "replace_block_content", "block_id": para["block_id"], "content": {"text": "직접 고친 문장"}}])
    assert r.status_code == 200 and r.json() == {"document_id": ctx.did, "document_revision": 2,
                                                 "input_revision": ctx.rev_in, "status": "draft", "validation_job_id": None}
    after = ctx.doc()
    new = next(b for b in _blocks(after) if b["block_id"] == para["block_id"])
    assert new["content"] == {"text": "직접 고친 문장"} and new["type"] == "paragraph"
    assert new["fact_ids"] == para["fact_ids"] and new["evidence_refs"] == para["evidence_refs"]
    # 안 건드린 블록은 그대로
    others_before = [b for b in _blocks(before) if b["block_id"] != para["block_id"]]
    others_after = [b for b in _blocks(after) if b["block_id"] != para["block_id"]]
    assert others_before == others_after
    assert client.get(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}").json()["validation"] is None


def test_op_insert_block(client):
    ctx = Ctx(client)
    d = ctx.doc()
    first = _blocks(d)[0]["block_id"]
    r = ctx.patch([{"op": "insert_block", "page_id": "page_01", "after_block_id": first,
                    "block": {"block_id": "block_new", "type": "list", "content": {"items": ["항목 1", "항목 2"]},
                              "fact_ids": [], "evidence_refs": []}}])
    assert r.status_code == 200
    ids = [b["block_id"] for b in _blocks(ctx.doc())]
    assert ids[0] == first and ids[1] == "block_new"
    r = ctx.patch([{"op": "insert_block", "page_id": "page_01", "after_block_id": None,
                    "block": {"block_id": "block_front", "type": "paragraph", "content": {"text": "맨 앞"}}}])
    assert r.status_code == 200 and _blocks(ctx.doc())[0]["block_id"] == "block_front"


def test_op_delete_block(client):
    ctx = Ctx(client)
    ph = _find(ctx.doc(), "image")["block_id"]
    r = ctx.patch([{"op": "delete_block", "block_id": ph}])
    assert r.status_code == 200
    assert ph not in [b["block_id"] for p in ctx.doc()["pages"] for b in p["blocks"]]


def test_op_move_block_across_pages(client):
    ctx = Ctx(client)
    d = ctx.doc()
    para = next(b for b in _blocks(d) if b["type"] == "paragraph")["block_id"]
    target_first = _blocks(d, 1)[0]["block_id"]
    r = ctx.patch([{"op": "move_block", "block_id": para, "target_page_id": "page_02", "after_block_id": target_first}])
    assert r.status_code == 200
    after = ctx.doc()
    assert para not in [b["block_id"] for b in _blocks(after, 0)]
    assert [b["block_id"] for b in _blocks(after, 1)][1] == para
    r = ctx.patch([{"op": "move_block", "block_id": para, "target_page_id": "page_02", "after_block_id": None}])
    assert r.status_code == 200 and _blocks(ctx.doc(), 1)[0]["block_id"] == para


def test_op_insert_page(client):
    ctx = Ctx(client)
    page = {"page_id": "page_new", "title": "새 페이지", "layout_key": "text",
            "blocks": [{"block_id": "block_np1", "type": "heading", "content": {"text": "새 제목", "level": 2}}]}
    r = ctx.patch([{"op": "insert_page", "after_page_id": "page_01", "page": page}])
    assert r.status_code == 200
    assert [p["page_id"] for p in ctx.doc()["pages"]][:2] == ["page_01", "page_new"]
    r = ctx.patch([{"op": "insert_page", "after_page_id": None,
                    "page": {**page, "page_id": "page_front", "blocks": [{"block_id": "block_f", "type": "paragraph", "content": {"text": "x"}}]}}])
    assert r.status_code == 200 and ctx.doc()["pages"][0]["page_id"] == "page_front"


def test_op_rename_page(client):
    ctx = Ctx(client)
    r = ctx.patch([{"op": "rename_page", "page_id": "page_02", "title": "바뀐 제목"}])
    assert r.status_code == 200 and ctx.doc()["pages"][1]["title"] == "바뀐 제목"


def test_op_move_page(client):
    ctx = Ctx(client)
    r = ctx.patch([{"op": "move_page", "page_id": "page_04", "after_page_id": None}])
    assert r.status_code == 200 and [p["page_id"] for p in ctx.doc()["pages"]] == ["page_04", "page_01", "page_02", "page_03"]
    r = ctx.patch([{"op": "move_page", "page_id": "page_04", "after_page_id": "page_02"}])
    assert r.status_code == 200 and [p["page_id"] for p in ctx.doc()["pages"]] == ["page_01", "page_02", "page_04", "page_03"]


def test_op_delete_page(client):
    ctx = Ctx(client)
    r = ctx.patch([{"op": "delete_page", "page_id": "page_03"}])
    assert r.status_code == 200 and [p["page_id"] for p in ctx.doc()["pages"]] == ["page_01", "page_02", "page_04"]
    for pid in ("page_01", "page_02"):
        assert ctx.patch([{"op": "delete_page", "page_id": pid}]).status_code == 200
    r = ctx.patch([{"op": "delete_page", "page_id": "page_04"}])
    assert r.status_code == 422 and "마지막" in r.json()["error"]["details"]["reason"]


# ================= 실패·원자성·충돌 =================

@pytest.mark.parametrize("ops, reason_part", [
    ([{"op": "delete_block", "block_id": "block_nope"}], "블록이 없습니다"),
    ([{"op": "move_block", "block_id": "block_001", "target_page_id": "page_01", "after_block_id": "block_001"}], "자기 자신"),
    ([{"op": "move_block", "block_id": "block_001", "target_page_id": "page_02", "after_block_id": "block_001"}], "자기 자신"),
    ([{"op": "insert_block", "page_id": "page_02", "after_block_id": "block_001",
       "block": {"block_id": "b_x", "type": "paragraph", "content": {"text": "x"}}}], "그 페이지에 없습니다"),
    ([{"op": "insert_block", "page_id": "page_01", "after_block_id": None,
       "block": {"block_id": "block_001", "type": "paragraph", "content": {"text": "x"}}}], "이미 있는 block_id"),
    ([{"op": "replace_block_content", "block_id": "block_001", "content": {"text": "no level"}}], "heading content"),
    ([{"op": "move_page", "page_id": "page_01", "after_page_id": "page_01"}], "자기 자신"),
    ([{"op": "rename_page", "page_id": "page_99", "title": "x"}], "페이지가 없습니다"),
])
def test_invalid_operation_422_and_nothing_changes(client, ops, reason_part):
    ctx = Ctx(client)
    before = ctx.doc()
    r = ctx.patch(ops)
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_OPERATION", r.text
    assert reason_part in r.json()["error"]["details"]["reason"]
    assert ctx.doc() == before


def test_batch_is_atomic_first_ops_rolled_back_when_later_fails(client, settings):
    ctx = Ctx(client)
    before = ctx.doc()
    r = ctx.patch([{"op": "rename_page", "page_id": "page_01", "title": "바꿈"},
                   {"op": "delete_block", "block_id": "block_nope"}])
    assert r.status_code == 422 and r.json()["error"]["details"]["index"] == 1
    assert ctx.doc() == before
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM document_revisions WHERE document_id=?", (ctx.did,)).fetchone()[0] == 1


def test_insert_block_with_foreign_refs_rejected(client):
    ctx = Ctx(client)
    before = ctx.doc()
    r = ctx.patch([{"op": "insert_block", "page_id": "page_01", "after_block_id": None,
                    "block": {"block_id": "b_img", "type": "image",
                              "content": {"asset_id": "asset_other", "alt": "", "caption": "", "fit": "contain"}}}])
    assert r.status_code == 422 and "asset_id" in r.json()["error"]["details"]["reason"]
    r = ctx.patch([{"op": "insert_block", "page_id": "page_01", "after_block_id": None,
                    "block": {"block_id": "b_p", "type": "paragraph", "content": {"text": "x"},
                              "fact_ids": ["fact_999"], "evidence_refs": []}}])
    assert r.status_code == 422 and "fact_id" in r.json()["error"]["details"]["reason"]
    assert ctx.doc() == before


def test_revision_conflict_and_input_conflict(client):
    ctx = Ctx(client)
    r = ctx.patch([{"op": "rename_page", "page_id": "page_01", "title": "x"}], expected=99)
    assert r.status_code == 409 and r.json()["error"]["code"] == "DOCUMENT_REVISION_CONFLICT"
    assert r.json()["error"]["details"] == {"expected_revision": 99, "current_revision": 1}
    # 입력이 바뀐 뒤(문서 input_revision < 세션) 편집은 409 INPUT_REVISION_CONFLICT
    client.patch(f"/api/v1/sessions/{ctx.sid}/inputs", json={"expected_input_revision": ctx.rev_in, "brief": BRIEF})
    r = ctx.patch([{"op": "rename_page", "page_id": "page_01", "title": "x"}])
    assert r.status_code == 409 and r.json()["error"]["code"] == "INPUT_REVISION_CONFLICT"


def test_patch_idempotent_replay_does_not_bump_twice(client):
    ctx = Ctx(client)
    ops = [{"op": "rename_page", "page_id": "page_01", "title": "멱등"}]
    r1 = ctx.patch(ops, expected=1, headers={"Idempotency-Key": "k-patch"})
    r2 = ctx.patch(ops, expected=1, headers={"Idempotency-Key": "k-patch"})
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
    assert ctx.doc()["document_revision"] == 2
    r3 = ctx.patch([{"op": "rename_page", "page_id": "page_01", "title": "다른 본문"}], expected=1,
                   headers={"Idempotency-Key": "k-patch"})
    assert r3.status_code == 409 and r3.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_concurrent_patch_only_one_wins(settings):
    app = create_app(settings)
    client = TestClient(app)
    ctx = Ctx(client)
    results = []

    def worker(title):
        c = TestClient(app)
        c.cookies = client.cookies
        results.append(c.patch(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}",
                               json={"expected_revision": 1, "operations": [{"op": "rename_page", "page_id": "page_01", "title": title}]}).status_code)

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200, 409, 409, 409] and ctx.doc()["document_revision"] == 2


# ================= Proposal =================

def test_proposal_text_creates_without_changing_document(client):
    ctx = Ctx(client)
    before = ctx.doc()
    para = next(b for b in _blocks(before) if b["type"] == "paragraph")
    job = ctx.propose([para["block_id"]])
    assert job["status"] == "succeeded" and job["result_ref"]["type"] == "proposal" and job["result_ref"]["status"] == "proposed"
    prop = ctx.proposal(job["result_ref"]["proposal_id"])
    assert prop["status"] == "proposed" and prop["base_document_revision"] == 1 and prop["base_input_revision"] == ctx.rev_in
    assert prop["kind"] == "text" and prop["candidates"] is None
    assert prop["changes"] == [{"op": "replace_block_content", "block_id": para["block_id"],
                                "content": {"text": "(정리) " + " ".join(para["content"]["text"].split())}}]
    assert ctx.doc() == before  # 적용 전 문서 불변
    assert ctx.doc()["document_revision"] == 1


def test_proposal_apply_new_revision_preserves_untouched_and_refs(client, settings):
    ctx = Ctx(client)
    before = ctx.doc()
    para = next(b for b in _blocks(before) if b["type"] == "paragraph" and b["fact_ids"])
    pid = ctx.propose([para["block_id"]])["result_ref"]["proposal_id"]
    r = ctx.apply(pid)
    assert r.status_code == 200 and r.json()["document_revision"] == 2
    after = ctx.doc()
    changed = next(b for b in _blocks(after) if b["block_id"] == para["block_id"])
    assert changed["content"]["text"].startswith("(정리) ")
    assert changed["fact_ids"] == para["fact_ids"] and changed["evidence_refs"] == para["evidence_refs"]
    assert [b for b in _blocks(after) if b["block_id"] != para["block_id"]] == [b for b in _blocks(before) if b["block_id"] != para["block_id"]]
    assert after["pages"][1:] == before["pages"][1:]
    prop = ctx.proposal(pid)
    assert prop["status"] == "applied" and prop["applied_revision"] == 2
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT origin, source_ref FROM document_revisions WHERE document_id=? AND revision=2", (ctx.did,)).fetchone()
        assert (row["origin"], row["source_ref"]) == ("proposal_apply", pid)


def test_proposal_apply_twice(client):
    ctx = Ctx(client)
    para = next(b for b in _blocks(ctx.doc()) if b["type"] == "paragraph")
    pid = ctx.propose([para["block_id"]])["result_ref"]["proposal_id"]
    r1 = ctx.apply(pid, expected=1, headers={"Idempotency-Key": "k-apply"})
    r2 = ctx.apply(pid, expected=1, headers={"Idempotency-Key": "k-apply"})
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
    assert ctx.doc()["document_revision"] == 2
    r3 = ctx.apply(pid, expected=2)  # 키 없이 재전송 → 이미 적용됨
    assert r3.status_code == 409 and r3.json()["error"]["code"] == "PROPOSAL_STALE" and r3.json()["error"]["details"]["status"] == "applied"
    r4 = ctx.apply(pid, expected=1, headers={"Idempotency-Key": "k-apply-other"})
    assert r4.status_code == 409


def test_concurrent_apply_only_one_wins(settings):
    app = create_app(settings)
    client = TestClient(app)
    ctx = Ctx(client)
    para = next(b for b in _blocks(ctx.doc()) if b["type"] == "paragraph")
    pid = ctx.propose([para["block_id"]])["result_ref"]["proposal_id"]
    results = []

    def worker():
        c = TestClient(app)
        c.cookies = client.cookies
        results.append(c.post(f"/api/v1/sessions/{ctx.sid}/proposals/{pid}/apply", json={"expected_revision": 1}).status_code)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200, 409, 409] and ctx.doc()["document_revision"] == 2
    assert ctx.proposal(pid)["applied_revision"] == 2


def test_stale_proposal_rejected_and_stays_stale(client, settings):
    ctx = Ctx(client)
    para = next(b for b in _blocks(ctx.doc()) if b["type"] == "paragraph")
    pid = ctx.propose([para["block_id"]])["result_ref"]["proposal_id"]
    # 다른 편집으로 문서가 바뀜 → 훅이 proposed를 stale로
    assert ctx.patch([{"op": "rename_page", "page_id": "page_02", "title": "다른 편집"}]).status_code == 200
    assert ctx.proposal(pid)["status"] == "stale"
    r = ctx.apply(pid, expected=2)
    assert r.status_code == 409 and r.json()["error"]["code"] == "PROPOSAL_STALE"
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT status FROM proposals WHERE proposal_id=?", (pid,)).fetchone()[0] == "stale"
    assert ctx.doc()["document_revision"] == 2


def test_stale_by_input_change_set_during_apply(client, settings):
    """훅이 못 잡는 경우(입력 버전만 바뀜): apply 시점에 stale로 바꾸고 그 상태가 커밋된다."""
    ctx = Ctx(client)
    para = next(b for b in _blocks(ctx.doc()) if b["type"] == "paragraph")
    pid = ctx.propose([para["block_id"]])["result_ref"]["proposal_id"]
    client.patch(f"/api/v1/sessions/{ctx.sid}/inputs", json={"expected_input_revision": ctx.rev_in, "brief": BRIEF})
    assert ctx.proposal(pid)["status"] == "proposed"
    r = ctx.apply(pid, expected=1)
    assert r.status_code == 409 and r.json()["error"]["details"]["status"] == "stale"
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT status FROM proposals WHERE proposal_id=?", (pid,)).fetchone()[0] == "stale"
    assert ctx.doc()["document_revision"] == 1


def test_reject_leaves_document_unchanged(client):
    ctx = Ctx(client)
    before = ctx.doc()
    para = next(b for b in _blocks(before) if b["type"] == "paragraph")
    pid = ctx.propose([para["block_id"]])["result_ref"]["proposal_id"]
    r = client.post(f"/api/v1/sessions/{ctx.sid}/proposals/{pid}/reject")
    assert r.status_code == 200 and r.json() == {"proposal_id": pid, "status": "rejected", "document_id": ctx.did, "document_revision": 1}
    assert ctx.doc() == before
    assert ctx.apply(pid).status_code == 409
    assert client.post(f"/api/v1/sessions/{ctx.sid}/proposals/{pid}/reject").status_code == 200  # 멱등


def test_proposal_create_validations(client):
    ctx = Ctx(client)
    d = ctx.doc()
    para = next(b for b in _blocks(d) if b["type"] == "paragraph")["block_id"]
    base = {"input_revision": ctx.rev_in, "target_block_ids": [para], "instruction": "x", "kind": "text"}
    r = client.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/proposals", json={**base, "expected_revision": 9})
    assert r.status_code == 409 and r.json()["error"]["code"] == "DOCUMENT_REVISION_CONFLICT"
    r = client.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/proposals", json={**base, "expected_revision": 1, "input_revision": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INPUT_REVISION_CONFLICT"
    r = client.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/proposals", json={**base, "expected_revision": 1, "target_block_ids": ["block_zzz"]})
    assert r.status_code == 422 and r.json()["error"]["details"]["missing_block_ids"] == ["block_zzz"]


def test_mock_unsupported_kinds_fail_not_empty_success(client):
    ctx = Ctx(client)
    d = ctx.doc()
    para = next(b for b in _blocks(d) if b["type"] == "paragraph")["block_id"]
    image = _find(d, "image")["block_id"]
    job = ctx.propose([para], kind="structure")
    assert job["status"] == "failed" and job["error"]["code"] == "UNSUPPORTED_PROPOSAL"
    job = ctx.propose([image], kind="text")
    assert job["status"] == "failed" and job["error"]["code"] == "UNSUPPORTED_PROPOSAL"
    job = ctx.propose([para], kind="image")
    assert job["status"] == "succeeded"  # 텍스트는 보존하고 바로 뒤에 사진을 추가하는 후보를 지원한다.
    assert ctx.doc() == d


def test_image_proposal_candidates_and_selection(client):
    ctx = Ctx(client)
    d = ctx.doc()
    image = _find(d, "image")
    job = ctx.propose([image["block_id"]], kind="image")
    assert job["status"] == "succeeded"
    pid = job["result_ref"]["proposal_id"]
    prop = ctx.proposal(pid)
    assert prop["changes"] == [] and len(prop["candidates"]) == 1 and prop["candidates"][0]["candidate_id"] == "cand_01"
    assert ctx.doc() == d
    r = ctx.apply(pid)
    assert r.status_code == 422 and r.json()["error"]["code"] == "CANDIDATE_REQUIRED"
    r = ctx.apply(pid, candidate="cand_99")
    assert r.status_code == 422
    r = ctx.apply(pid, candidate="cand_01")
    assert r.status_code == 200 and ctx.doc()["document_revision"] == 2
    assert _find(ctx.doc(), "image")["content"]["asset_id"] == image["content"]["asset_id"]


def test_image_proposal_on_placeholder_without_assets_fails(client):
    ctx = Ctx(client, with_photo=False)
    ph = _find(ctx.doc(), "image_placeholder")["block_id"]
    job = ctx.propose([ph], kind="image")
    assert job["status"] == "failed" and job["error"]["code"] == "NO_IMAGE_CANDIDATES"


def test_image_proposal_on_placeholder_with_asset_replaces_block(client):
    ctx = Ctx(client)
    # 사진을 지워 placeholder 상태로 만든 뒤 후보 적용
    img = _find(ctx.doc(), "image")
    r = ctx.patch([{"op": "delete_block", "block_id": img["block_id"]},
                   {"op": "insert_block", "page_id": "page_01", "after_block_id": None,
                    "block": {"block_id": "block_ph", "type": "image_placeholder", "content": {"description": "자리"}}}])
    assert r.status_code == 200
    job = ctx.propose(["block_ph"], kind="image")
    assert job["status"] == "succeeded", job
    pid = job["result_ref"]["proposal_id"]
    r = ctx.apply(pid, candidate="cand_01")
    assert r.status_code == 200
    blocks = _blocks(ctx.doc())
    assert blocks[0]["type"] == "image" and blocks[0]["block_id"].startswith("block_ph_img_")
    assert "block_ph" not in [b["block_id"] for b in blocks]


def test_agent_ops_outside_target_are_rejected(client, monkeypatch):
    ctx = Ctx(client)
    d = ctx.doc()
    paras = [b for b in _blocks(d) if b["type"] == "paragraph"]
    heading = _blocks(d)[0]["block_id"]
    original = MockAgent.propose

    async def tampered(self, request):
        result = await original(self, request)
        from app.models import OpDeleteBlock
        result.changes.append(OpDeleteBlock(op="delete_block", block_id=heading))
        return result

    monkeypatch.setattr(MockAgent, "propose", tampered)
    job = ctx.propose([paras[0]["block_id"]])
    assert job["status"] == "failed" and job["error"]["code"] == "AGENT_OUTPUT_INVALID"
    assert ctx.doc() == d


def test_proposal_made_while_document_changed_is_saved_stale(client, monkeypatch):
    ctx = Ctx(client)
    para = next(b for b in _blocks(ctx.doc()) if b["type"] == "paragraph")
    original = MockAgent.propose

    async def edit_then_propose(self, request):
        ctx.patch([{"op": "rename_page", "page_id": "page_02", "title": "중간 편집"}], expected=1)
        return await original(self, request)

    monkeypatch.setattr(MockAgent, "propose", edit_then_propose)
    job = ctx.propose([para["block_id"]], expected=1)
    assert job["status"] == "succeeded" and job["result_ref"]["status"] == "stale"
    assert ctx.proposal(job["result_ref"]["proposal_id"])["status"] == "stale"


# ================= C-05 자료 변경 후 편집 복귀 =================

@pytest.fixture
def impact_ctx(tmp_path):
    settings = Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "impact.sqlite3",
                        cleanup_sweep_interval_s=0)
    init_orm_db(settings.db_path, settings.private_runs_dir)
    with TestClient(create_app(settings)) as client:
        yield Ctx(client), settings


def _impact_preflight(ctx, *, selected=None, change_input=True):
    if change_input:
        payload = {"expected_input_revision": ctx.rev_in, "brief": {**BRIEF, "purpose": "보완 자료로 편집 계속"}}
        if selected is not None:
            payload["selected_source_ids"] = selected
        response = ctx.c.patch(f"/api/v1/sessions/{ctx.sid}/inputs", json=payload)
        assert response.status_code == 200, response.text
        ctx.rev_in = response.json()["input_revision"]
    response = ctx.c.post(f"/api/v1/sessions/{ctx.sid}/preflights",
                          json={"expected_input_revision": ctx.rev_in})
    assert response.status_code == 202, response.text
    job = ctx.c.get(f"/api/v1/sessions/{ctx.sid}/jobs/{response.json()['job_id']}").json()
    assert job["status"] == "succeeded", job
    return ctx.c.get(f"/api/v1/sessions/{ctx.sid}/preflights/{job['result_ref']['preflight_id']}").json()


def _impact_route(ctx):
    return f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/impact-reviews"


def _impact_create(ctx, pf, *, headers=None):
    body = {"expected_revision": ctx.doc()["document_revision"], "input_revision": ctx.rev_in,
            "preflight_id": pf["preflight_id"], "confirmed": True}
    response = ctx.c.post(_impact_route(ctx), json=body, headers=headers or {})
    assert response.status_code == 201, response.text
    return response.json(), body


def _impact_body(ctx, **extra):
    return {"expected_revision": ctx.doc()["document_revision"], "input_revision": ctx.rev_in,
            "keep_reason": "변경 자료와 기존 편집을 대조해 유지할 내용을 확인했습니다.", **extra}


def _impact_state(settings):
    """세션 접근 시각을 제외하고 원자성이 필요한 행을 비교한다."""
    with connect(settings.db_path) as conn:
        return {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("documents", "document_revisions", "impact_reviews", "confirmations", "jobs",
                              "idempotency_keys")}


def test_impact_review_preserves_edits_photos_and_rebinds_exact_facts(impact_ctx, monkeypatch):
    ctx, settings = impact_ctx
    para = next(b for b in _blocks(ctx.doc()) if b["type"] == "paragraph" and b["fact_ids"])
    assert ctx.patch([{"op": "replace_block_content", "block_id": para["block_id"],
                       "content": {"text": "사용자가 직접 다듬은 회사 소개입니다."}},
                      {"op": "move_page", "page_id": "page_04", "after_page_id": None}]).status_code == 200
    before = ctx.doc()
    original_analyze = MockAgent.analyze

    async def new_fact_ids(self, request):
        result = await original_analyze(self, request)
        for fact in result.facts:
            fact.fact_id = "latest_" + fact.fact_id
        for issue in result.issues:
            issue.fact_ids = ["latest_" + fid for fid in issue.fact_ids]
        return result

    monkeypatch.setattr(MockAgent, "analyze", new_fact_ids)
    monkeypatch.setattr(MockAgent, "draft", lambda *a: pytest.fail("기존 문서를 초안으로 덮어쓰면 안 됩니다."))
    pf = _impact_preflight(ctx)
    preserved = ctx.doc()
    review, _ = _impact_create(ctx, pf)
    assert ctx.doc() == preserved
    assert review["status"] == "pending" and review["completed_at"] is None
    assert review["document_revision"] == before["document_revision"]
    assert review["from_input_revision"] == before["input_revision"]
    assert review["to_input_revision"] == ctx.rev_in and review["preflight_id"] == pf["preflight_id"]
    assert ctx.c.get(_impact_route(ctx) + "/" + review["review_id"]).json() == review
    expected_ids = {fid for p in before["pages"] for b in p["blocks"] for fid in b["fact_ids"]}
    assert all(review["fact_rebindings"][fid] == "latest_" + fid for fid in expected_ids)
    response = ctx.c.post(_impact_route(ctx) + f"/{review['review_id']}/apply", json=_impact_body(ctx))
    assert response.status_code == 200, response.text
    result = response.json()
    after = ctx.doc()
    assert after["document_revision"] == before["document_revision"] + 1
    assert after["input_revision"] == ctx.rev_in
    expected_pages = json.loads(json.dumps(before["pages"]))
    for page in expected_pages:
        for block in page["blocks"]:
            block["fact_ids"] = ["latest_" + fid for fid in block["fact_ids"]]
    assert after["pages"] == expected_pages and after["title"] == before["title"]
    assert result["validation_job_id"]
    job = ctx.c.get(f"/api/v1/sessions/{ctx.sid}/jobs/{result['validation_job_id']}").json()
    assert job["status"] == "succeeded", job
    completed = ctx.c.get(_impact_route(ctx) + "/" + review["review_id"]).json()
    assert completed["status"] == "applied" and completed["completed_at"]
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT input_revision, preflight_id FROM document_revisions WHERE document_id=? AND revision=?",
                           (ctx.did, after["document_revision"])).fetchone()
        assert tuple(row) == (ctx.rev_in, pf["preflight_id"])
        assert conn.execute("SELECT COUNT(*) FROM confirmations WHERE kind='impact_keep' AND document_id=?",
                            (ctx.did,)).fetchone()[0] == 1
    assert ctx.patch([{"op": "rename_page", "page_id": "page_02", "title": "복귀 후 편집"}]).status_code == 200


def test_impact_replaced_source_requires_explicit_current_references(impact_ctx):
    ctx, settings = impact_ctx
    before = ctx.doc()
    upload = ctx.c.post(f"/api/v1/sessions/{ctx.sid}/sources",
                        files=[("files", ("replacement.txt", SOURCE_A))])
    assert upload.status_code == 202, upload.text
    new_source = upload.json()["items"][0]["source_id"]
    pf = _impact_preflight(ctx, selected=[new_source, ctx.src[1]])
    review, _ = _impact_create(ctx, pf)
    route = _impact_route(ctx) + f"/{review['review_id']}/apply"
    saved = _impact_state(settings)
    refused = ctx.c.post(route, json=_impact_body(ctx))
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "IMPACT_REFERENCE_INVALID"
    assert _impact_state(settings) == saved
    facts = {f["fact_id"]: f for f in pf["facts"] if f["status"] == "supported"}
    updates = []
    for page in before["pages"]:
        for block in page["blocks"]:
            if block["fact_ids"]:
                updates.append({"block_id": block["block_id"], "fact_ids": block["fact_ids"],
                                "evidence_refs": [ref for fid in block["fact_ids"] for ref in facts[fid]["evidence_refs"]]})
    response = ctx.c.post(route, json=_impact_body(ctx, reference_updates=updates))
    assert response.status_code == 200, response.text
    after = ctx.doc()
    assert [[b["content"] for b in p["blocks"]] for p in after["pages"]] == [
        [b["content"] for b in p["blocks"]] for p in before["pages"]]
    assert all(ref["source_id"] == new_source for p in after["pages"] for b in p["blocks"] for ref in b["evidence_refs"])


def test_impact_excluded_photo_requires_selected_change_and_failure_is_atomic(impact_ctx):
    ctx, settings = impact_ctx
    before = ctx.doc()
    image = _find(before, "image")
    pf = _impact_preflight(ctx, selected=[ctx.src[0]])
    review, _ = _impact_create(ctx, pf)
    assert any(item["block_id"] == image["block_id"] and item["requires_change"] for item in review["items"])
    route = _impact_route(ctx) + f"/{review['review_id']}/apply"
    saved = _impact_state(settings)
    refused = ctx.c.post(route, json=_impact_body(ctx, operations=[
        {"op": "rename_page", "page_id": "page_01", "title": "실패하면 저장하지 않을 제목"}]))
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "IMPACT_REFERENCE_INVALID"
    assert _impact_state(settings) == saved
    response = ctx.c.post(route, json=_impact_body(ctx, operations=[{"op": "delete_block", "block_id": image["block_id"]}]))
    assert response.status_code == 200, response.text
    expected = json.loads(json.dumps(before["pages"]))
    for page in expected:
        page["blocks"] = [b for b in page["blocks"] if b["block_id"] != image["block_id"]]
    assert ctx.doc()["pages"] == expected


@pytest.mark.parametrize("bad", ["unknown_fact", "unselected_evidence", "unsupported_fact"])
def test_impact_reference_updates_must_use_latest_supported_selected_facts(impact_ctx, bad):
    ctx, settings = impact_ctx
    extra = ctx.c.post(f"/api/v1/sessions/{ctx.sid}/sources", files=[("files", ("unselected.txt", SOURCE_A))]).json()
    unselected_id = extra["items"][0]["source_id"]
    pf = _impact_preflight(ctx)
    review, _ = _impact_create(ctx, pf)
    block = next(b for p in ctx.doc()["pages"] for b in p["blocks"] if b["fact_ids"])
    update = {"block_id": block["block_id"], "fact_ids": block["fact_ids"], "evidence_refs": block["evidence_refs"]}
    if bad == "unknown_fact":
        update["fact_ids"] = ["fact_not_returned_by_latest_preflight"]
    elif bad == "unsupported_fact":
        update["fact_ids"] = [next(f["fact_id"] for f in pf["facts"] if f["status"] == "missing")]
    else:
        with connect(settings.db_path) as conn:
            segment = conn.execute("SELECT segment_id FROM segments WHERE source_id=? LIMIT 1", (unselected_id,)).fetchone()[0]
        update["evidence_refs"] = [{**block["evidence_refs"][0], "source_id": unselected_id,
                                    "source_version": 1, "segment_id": segment}]
    saved = _impact_state(settings)
    response = ctx.c.post(_impact_route(ctx) + f"/{review['review_id']}/apply",
                          json=_impact_body(ctx, reference_updates=[update]))
    assert response.status_code == 422 and response.json()["error"]["code"] == "IMPACT_REFERENCE_INVALID"
    assert _impact_state(settings) == saved


@pytest.mark.parametrize("field,value,code", [
    ("confirmed", False, "PREFLIGHT_NOT_CONFIRMED"),
    ("expected_revision", 99, "DOCUMENT_REVISION_CONFLICT"),
    ("input_revision", 1, "INPUT_REVISION_CONFLICT"),
])
def test_impact_create_requires_explicit_confirmation_and_current_versions(impact_ctx, field, value, code):
    ctx, settings = impact_ctx
    pf = _impact_preflight(ctx)
    body = {"expected_revision": ctx.doc()["document_revision"], "input_revision": ctx.rev_in,
            "preflight_id": pf["preflight_id"], "confirmed": True, field: value}
    saved = _impact_state(settings)
    response = ctx.c.post(_impact_route(ctx), json=body)
    assert response.status_code == (422 if field == "confirmed" else 409)
    assert response.json()["error"]["code"] == code
    assert _impact_state(settings) == saved


def test_impact_old_preflight_cannot_confirm_new_input_or_superseded_preflight(impact_ctx):
    ctx, _ = impact_ctx
    original_pf = ctx.pf
    pf = _impact_preflight(ctx)
    body = {"expected_revision": ctx.doc()["document_revision"], "input_revision": ctx.rev_in,
            "preflight_id": original_pf, "confirmed": True}
    response = ctx.c.post(_impact_route(ctx), json=body)
    assert response.status_code == 409 and response.json()["error"]["code"] == "INPUT_REVISION_CONFLICT"
    _impact_preflight(ctx, change_input=False)
    response = ctx.c.post(_impact_route(ctx), json={**body, "preflight_id": pf["preflight_id"]})
    assert response.status_code == 409 and response.json()["error"]["code"] == "PREFLIGHT_STALE"


@pytest.mark.parametrize("changed", ["input", "preflight"])
def test_impact_apply_rejects_changes_after_review(impact_ctx, changed):
    ctx, settings = impact_ctx
    pf = _impact_preflight(ctx)
    review, _ = _impact_create(ctx, pf)
    body = _impact_body(ctx)
    _impact_preflight(ctx, change_input=changed == "input")
    before = ctx.doc()
    response = ctx.c.post(_impact_route(ctx) + f"/{review['review_id']}/apply", json=body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] in {"INPUT_REVISION_CONFLICT", "IMPACT_REVIEW_STALE"}
    assert ctx.doc() == before
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM confirmations WHERE kind='impact_keep'").fetchone()[0] == 0


def test_impact_create_and_apply_replay_do_not_duplicate_revision_or_validation(impact_ctx):
    ctx, settings = impact_ctx
    pf = _impact_preflight(ctx)
    review, create_body = _impact_create(ctx, pf, headers={"Idempotency-Key": "impact-create"})
    replay = ctx.c.post(_impact_route(ctx), json=create_body, headers={"Idempotency-Key": "impact-create"})
    assert replay.status_code == 201 and replay.json() == review
    route = _impact_route(ctx) + f"/{review['review_id']}/apply"
    body = _impact_body(ctx)
    response = ctx.c.post(route, json=body, headers={"Idempotency-Key": "impact-apply"})
    assert response.status_code == 200, response.text
    saved = _impact_state(settings)
    replay = ctx.c.post(route, json=body, headers={"Idempotency-Key": "impact-apply"})
    assert replay.status_code == 200 and replay.json() == response.json()
    assert _impact_state(settings) == saved
    conflict = ctx.c.post(route, json={**body, "keep_reason": "다른 확인 사유"}, headers={"Idempotency-Key": "impact-apply"})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_impact_access_checked_before_replay_and_foreign_review_rejected(impact_ctx):
    ctx, settings = impact_ctx
    pf = _impact_preflight(ctx)
    review, body = _impact_create(ctx, pf, headers={"Idempotency-Key": "impact-owner"})
    item_url = _impact_route(ctx) + "/" + review["review_id"]
    with TestClient(create_app(settings)) as other:
        foreign = Ctx(other)
        assert other.get(item_url).status_code == 404
        assert other.post(_impact_route(ctx), json=body, headers={"Idempotency-Key": "impact-owner"}).status_code == 404
        assert other.post(item_url + "/apply", json=_impact_body(ctx)).status_code == 404
        assert other.get(_impact_route(foreign) + "/" + review["review_id"]).status_code == 404
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE sessions SET status='expired' WHERE session_id=?", (ctx.sid,))
    for response in (ctx.c.get(item_url),
                     ctx.c.post(_impact_route(ctx), json=body, headers={"Idempotency-Key": "impact-owner"}),
                     ctx.c.post(item_url + "/apply", json={"expected_revision": 1, "input_revision": ctx.rev_in,
                                                           "keep_reason": "확인"})):
        assert response.status_code == 410 and response.json()["error"]["code"] == "SESSION_EXPIRED"


def test_impact_restore_cannot_bypass_review_or_reintroduce_old_input(impact_ctx):
    ctx, _ = impact_ctx
    assert ctx.patch([{"op": "rename_page", "page_id": "page_02", "title": "보존할 편집"}]).status_code == 200
    pf = _impact_preflight(ctx)
    before = ctx.doc()
    route = f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/restore"
    response = ctx.c.post(route, json={"expected_revision": 2, "restore_from_revision": 1})
    assert response.status_code == 409 and response.json()["error"]["code"] == "INPUT_REVISION_CONFLICT"
    assert ctx.doc() == before
    review, _ = _impact_create(ctx, pf)
    applied = ctx.c.post(_impact_route(ctx) + f"/{review['review_id']}/apply", json=_impact_body(ctx))
    assert applied.status_code == 200, applied.text
    response = ctx.c.post(route, json={"expected_revision": 3, "restore_from_revision": 1})
    assert response.status_code == 409 and response.json()["error"]["code"] == "INPUT_REVISION_CONFLICT"
    assert ctx.doc()["document_revision"] == 3


def test_impact_concurrent_apply_commits_one_revision(impact_ctx):
    ctx, settings = impact_ctx
    review, _ = _impact_create(ctx, _impact_preflight(ctx))
    body = _impact_body(ctx)
    route = _impact_route(ctx) + f"/{review['review_id']}/apply"
    barrier = threading.Barrier(2)
    responses, errors = [], []

    def apply():
        try:
            with TestClient(ctx.c.app) as client:
                client.cookies = ctx.c.cookies
                barrier.wait(timeout=10)
                responses.append(client.post(route, json=body))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=apply) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads) and errors == []
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert ctx.doc()["document_revision"] == body["expected_revision"] + 1
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM confirmations WHERE kind='impact_keep'").fetchone()[0] == 1


@pytest.mark.parametrize("failure_at", ["confirmation", "validation_job"])
def test_impact_apply_rolls_back_late_write_failure_and_can_retry(impact_ctx, monkeypatch, failure_at):
    from app.db import DatabaseConnection
    from app.services import jobs

    ctx, settings = impact_ctx
    review, _ = _impact_create(ctx, _impact_preflight(ctx))
    route = _impact_route(ctx) + f"/{review['review_id']}/apply"
    body, headers = _impact_body(ctx), {"Idempotency-Key": "impact-late-write"}
    before = _impact_state(settings)
    reached = []
    original_execute, original_create = DatabaseConnection.execute, jobs.create

    def written_before_failure(conn):
        revision = original_execute(conn, "SELECT current_revision FROM documents WHERE document_id=?",
                                    (ctx.did,)).fetchone()[0]
        status = original_execute(conn, "SELECT status FROM impact_reviews WHERE review_id=?",
                                  (review["review_id"],)).fetchone()[0]
        count = original_execute(conn, "SELECT COUNT(*) FROM confirmations WHERE impact_review_id=?",
                                 (review["review_id"],)).fetchone()[0]
        reached.append((revision, status, count))
        raise RuntimeError("injected impact write failure")

    def failing_confirmation(conn, statement, parameters=()):
        result = original_execute(conn, statement, parameters)
        if (isinstance(statement, str) and statement.startswith("INSERT INTO confirmations ")
                and "'impact_keep'" in statement):
            written_before_failure(conn)
        return result

    def failing_job(conn, *args, **kwargs):
        result = original_create(conn, *args, **kwargs)
        if result.kind == "validate":
            written_before_failure(conn)
        return result

    with monkeypatch.context() as fail:
        if failure_at == "confirmation":
            fail.setattr(DatabaseConnection, "execute", failing_confirmation)
        else:
            fail.setattr(jobs, "create", failing_job)
        with pytest.raises(RuntimeError, match="injected impact write failure"):
            ctx.c.post(route, json=body, headers=headers)
    assert reached == [(body["expected_revision"] + 1, "applied", 1)]
    assert _impact_state(settings) == before
    response = ctx.c.post(route, json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert ctx.doc()["document_revision"] == body["expected_revision"] + 1
    with connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM confirmations WHERE impact_review_id=?",
                            (review["review_id"],)).fetchone()[0] == 1


@pytest.mark.parametrize("changed", ["segment_text", "photo_permission", "selected_read_run"])
def test_impact_snapshot_changes_without_new_input_make_review_stale(impact_ctx, changed):
    ctx, settings = impact_ctx
    if changed == "photo_permission":
        # 격리 DB의 가짜 사진을 공개 허가가 필요한 등록 자료로 만든다.
        with connect(settings.db_path) as conn:
            conn.execute("UPDATE sources SET scope='registered', session_id=NULL, use_as_company_evidence=1 "
                         "WHERE source_id=?", (ctx.src[1],))
            conn.execute("UPDATE assets SET scope='registered', session_id=NULL, approved_for_external_use=1 "
                         "WHERE source_id=?", (ctx.src[1],))
    review, _ = _impact_create(ctx, _impact_preflight(ctx))
    body = _impact_body(ctx)
    with connect(settings.db_path) as conn:
        if changed == "segment_text":
            conn.execute("UPDATE segments SET text=text || ' 변경된 원문 조건' WHERE source_id=?", (ctx.src[0],))
        elif changed == "photo_permission":
            conn.execute("UPDATE assets SET approved_for_external_use=0 WHERE source_id=?", (ctx.src[1],))
        else:
            conn.execute("UPDATE session_source_selections SET run_id=NULL "
                         "WHERE session_id=? AND input_revision=? AND source_id=?",
                         (ctx.sid, ctx.rev_in, ctx.src[0]))
    saved = _impact_state(settings)
    url = _impact_route(ctx) + "/" + review["review_id"]
    assert ctx.c.get(url).json()["status"] == "stale"
    response = ctx.c.post(url + "/apply", json=body)
    assert response.status_code == 409 and response.json()["error"]["code"] == "IMPACT_REVIEW_STALE"
    assert _impact_state(settings) == saved


@pytest.mark.parametrize("changed", ["conditions", "evidence", "source_version"])
def test_impact_same_fact_id_with_changed_meaning_or_evidence_is_not_rebound(impact_ctx, changed):
    ctx, settings = impact_ctx
    old_block = next(block for block in _blocks(ctx.doc()) if block["fact_ids"])
    fact_id = old_block["fact_ids"][0]
    pf = _impact_preflight(ctx)
    fact = next(fact for fact in pf["facts"] if fact["fact_id"] == fact_id)
    if changed == "conditions":
        fact["conditions"] = {"scope": "별도 확인이 필요한 적용 범위"}
    elif changed == "evidence":
        assert len(fact["evidence_refs"][0]["excerpt"]) > 1
        fact["evidence_refs"][0]["excerpt"] = fact["evidence_refs"][0]["excerpt"][1:]
    else:
        fact["evidence_refs"][0]["source_version"] += 1
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE preflights SET facts_json=? WHERE preflight_id=?",
                     (json.dumps(pf["facts"], ensure_ascii=False), pf["preflight_id"]))
    review, _ = _impact_create(ctx, pf)
    assert fact_id not in review["fact_rebindings"]
    assert any(item["code"] == "FACT_REVIEW_REQUIRED" and item["requires_change"]
               and item["block_id"] == old_block["block_id"] for item in review["items"])
    saved = _impact_state(settings)
    response = ctx.c.post(_impact_route(ctx) + f"/{review['review_id']}/apply", json=_impact_body(ctx))
    assert response.status_code == 422 and response.json()["error"]["code"] == "IMPACT_REFERENCE_INVALID"
    assert _impact_state(settings) == saved


def test_impact_two_identical_candidates_require_explicit_reference_choice(impact_ctx):
    ctx, settings = impact_ctx
    old_block = next(block for block in _blocks(ctx.doc()) if block["fact_ids"])
    fact_id = old_block["fact_ids"][0]
    pf = _impact_preflight(ctx)
    candidate = next(fact for fact in pf["facts"] if fact["fact_id"] == fact_id)
    pf["facts"].append({**candidate, "fact_id": "second_candidate_same_evidence"})
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE preflights SET facts_json=? WHERE preflight_id=?",
                     (json.dumps(pf["facts"], ensure_ascii=False), pf["preflight_id"]))
    review, _ = _impact_create(ctx, pf)
    assert fact_id not in review["fact_rebindings"]
    assert any(item["code"] == "FACT_REVIEW_REQUIRED" and item["block_id"] == old_block["block_id"]
               for item in review["items"])
    saved = _impact_state(settings)
    response = ctx.c.post(_impact_route(ctx) + f"/{review['review_id']}/apply", json=_impact_body(ctx))
    assert response.status_code == 422 and response.json()["error"]["code"] == "IMPACT_REFERENCE_INVALID"
    assert _impact_state(settings) == saved


@pytest.mark.parametrize("phase", ["create", "apply"])
def test_impact_waits_for_active_preflight_before_create_or_apply(impact_ctx, phase):
    from app.services import jobs

    ctx, settings = impact_ctx
    pf = _impact_preflight(ctx)
    if phase == "apply":
        review, _ = _impact_create(ctx, pf)
        route, body = _impact_route(ctx) + f"/{review['review_id']}/apply", _impact_body(ctx)
    else:
        route = _impact_route(ctx)
        body = {"expected_revision": ctx.doc()["document_revision"], "input_revision": ctx.rev_in,
                "preflight_id": pf["preflight_id"], "confirmed": True}
    with connect(settings.db_path) as conn:
        pending = jobs.create(conn, ctx.sid, "preflight", "가짜 점검 대기", input_revision=ctx.rev_in)
    saved = _impact_state(settings)
    response = ctx.c.post(route, json=body)
    expected = "PREFLIGHT_STALE" if phase == "create" else "IMPACT_REVIEW_STALE"
    assert response.status_code == 409 and response.json()["error"]["code"] == expected
    assert _impact_state(settings) == saved
    with connect(settings.db_path) as conn:
        assert jobs.get(conn, ctx.sid, pending.job_id).status == "queued"
        if phase == "create":
            assert conn.execute("SELECT confirmed_at FROM preflights WHERE preflight_id=?",
                                (pf["preflight_id"],)).fetchone()[0] is None


# ================= restore =================

def test_restore_creates_new_revision_and_never_restores_approval(client, settings):
    ctx = Ctx(client)
    rev1 = ctx.doc()
    ctx.patch([{"op": "rename_page", "page_id": "page_01", "title": "2판"}])
    ctx.patch([{"op": "rename_page", "page_id": "page_01", "title": "3판"}])
    with connect(settings.db_path) as conn:  # 과거 버전이 승인 상태였다고 가정
        conn.execute("UPDATE document_revisions SET status='approved' WHERE document_id=? AND revision=1", (ctx.did,))
    r = client.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/restore",
                    json={"expected_revision": 3, "restore_from_revision": 1})
    assert r.status_code == 200 and r.json()["document_revision"] == 4 and r.json()["status"] == "draft"
    now = ctx.doc()
    assert now["document_revision"] == 4 and now["pages"] == rev1["pages"] and now["title"] == rev1["title"]
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT origin, source_ref FROM document_revisions WHERE document_id=? AND revision=4", (ctx.did,)).fetchone()
        assert (row["origin"], row["source_ref"]) == ("restore", "1")
        assert conn.execute("SELECT COUNT(*) FROM document_revisions WHERE document_id=?", (ctx.did,)).fetchone()[0] == 4
    r = client.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/restore", json={"expected_revision": 3, "restore_from_revision": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "DOCUMENT_REVISION_CONFLICT"
    r = client.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/restore", json={"expected_revision": 4, "restore_from_revision": 4})
    assert r.status_code == 422
    r = client.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/restore", json={"expected_revision": 4, "restore_from_revision": 77})
    assert r.status_code == 404


def test_restore_rejected_when_referenced_source_deleted(client):
    ctx = Ctx(client)
    img = _find(ctx.doc(), "image")
    ctx.patch([{"op": "delete_block", "block_id": img["block_id"]}])  # rev 2: 사진 없음
    photo_src = ctx.src[1]
    r = client.delete(f"/api/v1/sessions/{ctx.sid}/sources/{photo_src}", params={"expected_input_revision": ctx.rev_in})
    assert r.status_code == 200
    new_in = r.json()["input_revision"]
    # 입력이 바뀌었으니 편집은 막히지만 복원 요청 자체는 참조 검사에서 거부돼야 한다
    r = client.post(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}/restore", json={"expected_revision": 2, "restore_from_revision": 1})
    assert r.status_code == 422 and r.json()["error"]["code"] == "RESTORE_REFERENCE_INVALID"
    assert ctx.doc()["document_revision"] == 2 and new_in == ctx.rev_in + 1


# ================= 접근·멱등 순서 =================

def test_idempotent_replay_requires_access_first(client, settings):
    ctx = Ctx(client)
    ops = [{"op": "rename_page", "page_id": "page_01", "title": "x"}]
    assert ctx.patch(ops, expected=1, headers={"Idempotency-Key": "k1"}).status_code == 200
    other = TestClient(create_app(settings))
    other.post("/api/v1/sessions", json={"brief": BRIEF})
    r = other.patch(f"/api/v1/sessions/{ctx.sid}/documents/{ctx.did}", json={"expected_revision": 1, "operations": ops},
                    headers={"Idempotency-Key": "k1"})
    assert r.status_code == 404
    with connect(settings.db_path) as conn:
        conn.execute("UPDATE sessions SET status='expired' WHERE session_id=?", (ctx.sid,))
    r = ctx.patch(ops, expected=1, headers={"Idempotency-Key": "k1"})
    assert r.status_code == 410 and r.json()["error"]["code"] == "SESSION_EXPIRED"
    r = client.post(f"/api/v1/sessions/{ctx.sid}/preflights", json={"expected_input_revision": ctx.rev_in}, headers={"Idempotency-Key": "any"})
    assert r.status_code == 410


def test_other_owner_cannot_touch_proposals(client, settings):
    ctx = Ctx(client)
    para = next(b for b in _blocks(ctx.doc()) if b["type"] == "paragraph")
    pid = ctx.propose([para["block_id"]])["result_ref"]["proposal_id"]
    other = TestClient(create_app(settings))
    other.post("/api/v1/sessions", json={"brief": BRIEF})
    assert other.get(f"/api/v1/sessions/{ctx.sid}/proposals/{pid}").status_code == 404
    assert other.post(f"/api/v1/sessions/{ctx.sid}/proposals/{pid}/apply", json={"expected_revision": 1}).status_code == 404
    assert other.post(f"/api/v1/sessions/{ctx.sid}/proposals/{pid}/reject").status_code == 404
    assert ctx.proposal(pid)["status"] == "proposed"


# ================= 마이그레이션 v3 → v4 =================

def test_v3_db_gets_v4_columns_and_table(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    db = runs / "app.sqlite3"
    init_db(db, runs)
    with sqlite3.connect(db) as conn:  # v4 흔적 제거해 v3처럼 만든 뒤 다시 init
        conn.execute("DROP TABLE proposals")
        conn.execute("ALTER TABLE document_revisions DROP COLUMN origin")
        conn.execute("ALTER TABLE document_revisions DROP COLUMN source_ref")
        conn.execute("PRAGMA user_version=3")
    init_db(db, runs)
    with sqlite3.connect(db) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(document_revisions)")]
        assert "origin" in cols and "source_ref" in cols
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='proposals'").fetchone()
        from app.db import SCHEMA_VERSION
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_mock_source_has_no_real_company_terms():
    import inspect
    import app.agent_mock as m
    for banned in ("거산", "케미칼", "Geosan"):
        assert banned not in inspect.getsource(m)


def test_photo_validation_routes_send_selected_bytes_and_recheck_images(client, settings, monkeypatch):
    from dataclasses import replace
    from app.agent_llm import LlmAgent
    from app.services import ai_jobs
    ctx = Ctx(client)
    client.app.state.settings = replace(settings, agent_mode="llm")
    calls = []
    mismatch = True
    def respond(instructions, payload, schema, name, *, images):
        assert name == "content_review" and len(images) == 1 and images[0].data == _png()
        assert payload["images"][0]["asset_id"] == images[0].asset_id
        calls.append(payload)
        bid = payload["images"][0]["block_ids"][0]
        return {"checked_block_ids": payload["changed_block_ids"], "checked_image_ids": [images[0].asset_id],
                "findings": [{"kind": "image_mismatch", "block_ids": [bid], "fact_ids": [],
                    "reason": "검은 사진을 푸른 원으로 설명하고 있습니다.", "action": "사진 설명을 고쳐 주세요.",
                    "evidence": []}] if mismatch else []}
    monkeypatch.setattr(ai_jobs, "get_bridge", lambda config: LlmAgent(respond, max_input_chars=40000))
    base = f"/api/v1/sessions/{ctx.sid}"
    url = base + f"/documents/{ctx.did}"
    def check():
        r = client.post(url + "/validate", json={"expected_revision": ctx.doc()["document_revision"],
                                                 "input_revision": ctx.rev_in})
        assert r.status_code == 202, r.text
        job = client.get(base + "/jobs/" + r.json()["job_id"]).json()
        assert job["status"] == "succeeded", job
    check()
    issues = client.get(url + "/issues").json()["issues"]
    issue, = [i for i in issues if i["code"] == "IMAGE_MISMATCH"]
    assert issue["severity"] == "blocker" and issue["status"] == "open"
    assert client.get(url).json()["approval"] is None
    # 다른 문단만 수정해도 이미지 검사를 생략하거나 이전 사진 문제를 조용히 닫지 않는다.
    paragraph = _find(ctx.doc(), "paragraph")
    ctx.patch([{"op": "replace_block_content", "block_id": paragraph["block_id"],
                "content": {"text": paragraph["content"]["text"] + " "}}])
    mismatch = False
    check()
    assert len(calls) == 2
    assert _find(ctx.doc(), "image")["block_id"] in calls[-1]["changed_block_ids"]
    assert all(i["status"] == "resolved" for i in client.get(url + "/issues").json()["issues"] if i["code"] == "IMAGE_MISMATCH")


@pytest.mark.parametrize("bad", ["unselected", "tampered", "missing", "oversize", "changed_during_review"])
def test_photo_validation_refuses_inaccessible_or_changed_files(client, settings, monkeypatch, bad):
    from dataclasses import replace
    from app.db import connect
    from app.models import Document
    from app.services import ai_jobs, preflights, sources
    from app.agent_bridge import AgentError
    from app.agent_llm import LlmAgent
    ctx = Ctx(client)
    aid = _find(ctx.doc(), "image")["content"]["asset_id"]
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM assets WHERE asset_id=?", (aid,)).fetchone()
        path = sources.resolve_path(settings, row["stored_path"])
        selected = preflights.build_sources(conn, ctx.sid, ctx.src)
    if bad == "unselected":
        selected = [s for s in selected if aid not in s.asset_ids]
    elif bad == "tampered": path.write_bytes(b"changed")
    elif bad == "missing": path.unlink()
    elif bad == "oversize": path.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    if bad != "changed_during_review":
        with connect(settings.db_path) as conn, pytest.raises(AgentError):
            ai_jobs.review_images(conn, settings, ctx.sid, Document.model_validate(ctx.doc()), selected)
        return
    client.app.state.settings = replace(settings, agent_mode="llm")
    def respond(instructions, payload, schema, name, *, images):
        path.write_bytes(b"changed-after-send")
        return {"checked_block_ids": payload["changed_block_ids"], "checked_image_ids": [aid], "findings": []}
    monkeypatch.setattr(ai_jobs, "get_bridge", lambda config: LlmAgent(respond, max_input_chars=40000))
    base = f"/api/v1/sessions/{ctx.sid}"
    url = base + f"/documents/{ctx.did}"
    r = client.post(url + "/validate", json={"expected_revision": ctx.doc()["document_revision"], "input_revision": ctx.rev_in})
    job = client.get(base + "/jobs/" + r.json()["job_id"]).json()
    assert job["status"] == "failed" and job["error"]["code"] == "INVALID_REQUEST"
    assert client.get(url).json()["validation"] is None


@pytest.mark.parametrize("raw, expected", [
    ('{"slide":11,"page":2,"original_path":"private/file","original_sha256":"private"}', {"slide":11,"page":2}),
    ('{"slide":true,"page":-1}', {}),
    ('{"slide":"11","page":1000001}', {}),
    ('[]', {}), ('not-json', {}), (None, {}),
])
def test_photo_locator_exposes_only_valid_page_numbers(raw, expected):
    from app.services.preflights import photo_locator
    assert photo_locator(raw) == expected


def test_photo_location_reaches_candidate_and_vision_without_original_path(client, settings):
    from app.services import ai_jobs, preflights
    from app.agent_bridge import ProposeRequest
    from app.agent_llm import LlmAgent
    from app.models import Brief, Document
    ctx = Ctx(client)
    doc = Document.model_validate(ctx.doc())
    aid = _find(ctx.doc(), "image")["content"]["asset_id"]
    with connect(settings.db_path, immediate=True) as conn:
        conn.execute("UPDATE assets SET photo_locator_json=? WHERE asset_id=?",
                     (json.dumps({"slide":11,"original_path":"private/original-secret.jpg","original_sha256":"hidden"}), aid))
        selected = preflights.build_sources(conn, ctx.sid, ctx.src)
        pictures = ai_jobs.review_images(conn, settings, ctx.sid, doc, selected)
    assert pictures[0].locator == {"slide":11}
    assert next(s for s in selected if aid in s.asset_ids).asset_locators == {aid:{"slide":11}}
    request = ProposeRequest(ctx.sid, ctx.rev_in, Brief.model_validate(BRIEF), selected, doc,
                              [doc.pages[0].blocks[0].block_id], "사진 후보", "image")
    result = LlmAgent(lambda *a, **k: pytest.fail("candidate listing must not call AI")).propose(request)
    assert "PPT 11쪽" in result.candidates[0].label
    assert "private" not in result.candidates[0].label and "secret" not in repr(pictures[0])
