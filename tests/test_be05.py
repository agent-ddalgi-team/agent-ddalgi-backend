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
from app.db import connect, init_db

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
    assert job["status"] == "failed" and job["error"]["code"] == "UNSUPPORTED_PROPOSAL"


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
    assert blocks[0]["type"] == "image" and blocks[0]["block_id"] == "block_ph_img1"
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
