"""BE-07 확인: 렌더 어댑터(PDF/DOCX)·스냅샷 고정·findings/not_checked·출력 식별값 공유(㉖)·템플릿 가드.

모든 자료는 가상(fixture mock 묶음·Pillow 생성 PNG). 실제 회사 자료·AI 호출 없음.
PDF는 시스템 Chromium 계열 브라우저가 있을 때만 실제로 만든다. 없으면 해당 테스트는 skip으로 표시된다(통과가 아니다).
"""
from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.config import Settings
from app.db import connect
from app.models import Block, Document, Page
from app.services import export_render as er
from app.services import layout_checks
from app.services.documents import get_current

FIX = Path(__file__).parent / "fixtures" / "ddalgi_mock_bundle_v1"
BRIEF = {"purpose": "테스트", "emphasis": [], "direction": "balanced", "target_pages": 4, "photo_preference": "balanced"}
CLEAN_TXT = "회사명: 예시 회사\n회사 개요: 예시 회사는 가상 부품 표면처리와 검사를 하는 테스트 기업입니다.\n사업 분야: 가상 부품 표면처리\n".encode()
LONG_UNIT = "[MOCK] 긴 문단의 줄바꿈과 배치 경고를 시험하는 가상 문장입니다."
BANNED = ("거산", "케미칼", "Geosan")

# 템플릿·폰트·DOCX 배치 상수·PDF 렌더 상수의 sha256(줄바꿈 정규화). 이 중 하나라도 바꾸면 TEMPLATE_VERSION을 올리고 여기 값을 갱신한다.
TEMPLATE_FINGERPRINTS = {
    "template_v11": "b63a7d29a4f7476d317bcbeffb90538f39aca3e2d1aef7c72ccfeaa4c66e4b7b",
    "template_v10": "c002f3bb5cf786bf59e3d0d05bb18079b6832cd82701dbe64e9514fc40b062fb",
    "template_v9": "576799643934d2178e3a03e6e569604c36e6859a51fa3e498caa1be15a8edad1",
    "template_v8": "77bee728ad40c81e24e5abb5b12c24fcaa282399a7da8224f14a9ec9bf82ac88",
    "template_v7": "4be75f1522c12f656c964872f89797c24e5b3396138ed237eeabaa2757562134",
    "template_v6": "20677eaf1d7238e726d7a9b13e7648ad79440be189dc17b8203cfa8ad0229950",
    "template_v5": "f8f7d40b6c886b14eb813bbdf561bf1c540d6483c336a78bee377bac46b60fa9",
    "template_v4": "538839538005c76d91e68a091398ce4cd20842499f62d147c52852339d98b237",
    "template_v3": "e0e3f49e66c8564353bc357022845e40c936e273147883ce49dde3cc500a3512",
    "template_v2": "f7a692ddd4ee706e3bb93daec8dce2ce07548d608f1c6009962d1ff049905e4a",
    "template_v0": "7b1b3eaabcad9c23078f68a09fd2ccba89a372cbaec9b13643ebfc3abbbe8096",
    "template_v1": "2bb7dcf9660011db9d907f7ead738cf0859fe5b64a3beef7736ff07711f60781",
}

# 브라우저 탐색은 어댑터와 같은 규칙(EXPORT_BROWSER_PATH 우선). 테스트 settings에도 같은 값을 넣는다.
BROWSER_PATH_ENV = (os.environ.get("EXPORT_BROWSER_PATH") or "").strip() or None
BROWSER = er.find_browser(Settings(private_runs_dir=Path("."), db_path=Path("."), export_browser_path=BROWSER_PATH_ENV))
needs_browser = pytest.mark.skipif(BROWSER is None, reason="Chromium 계열 브라우저 없음 — PDF 렌더 테스트 미실행")
LIBREOFFICE = next((str(p) for p in [Path(os.environ.get("TEST_LIBREOFFICE_PATH") or
    r"C:\Program Files\LibreOffice\program\soffice.com")] if p.is_file()), None)
needs_libreoffice = pytest.mark.skipif(LIBREOFFICE is None, reason="LibreOffice 없음 — DOCX 실제 배치 검사 미실행")


def _png(width: int = 8, height: int = 6, color=(10, 20, 30)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(width: int, height: int, orientation: int | None = None, color=(200, 40, 40)) -> bytes:
    """왼쪽 위 사분면을 다른 색으로 칠한 JPEG. orientation을 주면 EXIF Orientation 태그를 넣는다(픽셀은 그대로)."""
    from PIL import Image

    img = Image.new("RGB", (width, height), color)
    for x in range(width // 2):
        for y in range(height // 2):
            img.putpixel((x, y), (20, 20, 200))
    buf = io.BytesIO()
    if orientation:
        exif = Image.Exif()
        exif[er.EXIF_ORIENTATION_TAG] = orientation
        img.save(buf, format="JPEG", quality=90, exif=exif.tobytes())
    else:
        img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _blk(bid: str, typ: str, **content) -> Block:
    return Block(block_id=bid, type=typ, content=content)


def _doc(pages: list[Page], *, did: str = "doc_t", rev: int = 1, title: str = "[MOCK] 테스트 문서") -> Document:
    return Document(document_id=did, session_id="sess_t", document_revision=rev, input_revision=1, title=title,
                    target_pages=4, status="draft", pages=pages)


def _fixture_doc(name: str) -> Document:
    return Document.model_validate(json.loads((FIX / "ui" / "documents" / f"document_{name}.json").read_text(encoding="utf-8")))


def _fixture_snapshot(name: str) -> er.RenderSnapshot:
    doc = _fixture_doc(name)
    assets = {aid: er.asset_from_bytes(aid, (FIX / "ingest" / "images" / f"{aid}.png").read_bytes())
              for aid in layout_checks.image_asset_ids(doc)}
    return er.snapshot_from_document(doc, assets)


def _stress_snapshot() -> er.RenderSnapshot:
    """넘침·가로/세로 사진·깨진 이미지·없는 asset·사진 자리·HTML 문자를 한 문서에."""
    pages = [
        Page(page_id="p1", title="표지", layout_key="text_photo", blocks=[
            _blk("h1", "heading", text="[MOCK] 스트레스 문서 한글 서체 확인", level=1),
            _blk("para", "paragraph", text="<script>alert(1)</script> & <img src=x onerror=alert(2)> \"따옴표\""),
            _blk("lst", "list", items=["가상 공정 A", "가상 공정 B"]),
            _blk("img_land", "image", asset_id="LAND", alt="가로", caption="[MOCK] 가로", fit="contain"),
        ]),
        Page(page_id="p2", title="긴 본문", layout_key="text", blocks=[
            _blk("h2", "heading", text="긴 본문", level=2),
            _blk("long", "paragraph", text=" ".join([LONG_UNIT] * 150)),
        ]),
        Page(page_id="p3", title="사진", layout_key="text_photo", blocks=[
            _blk("img_port", "image", asset_id="PORT", alt="세로", caption="[MOCK] 세로", fit="contain"),
            _blk("img_crop", "image", asset_id="WIDE", alt="배너", caption="[MOCK] 배너", fit="crop"),
            _blk("img_broken", "image", asset_id="BROKEN", alt="", caption="[MOCK] 깨진 파일", fit="contain"),
            _blk("img_missing", "image", asset_id="NOPE", alt="", caption="", fit="contain"),
            _blk("ph", "image_placeholder", description="[MOCK] 사진을 추가할 자리"),
        ]),
    ]
    assets = {"LAND": er.asset_from_bytes("LAND", _png(96, 64)), "PORT": er.asset_from_bytes("PORT", _png(64, 96)),
              "WIDE": er.asset_from_bytes("WIDE", _png(128, 42)), "BROKEN": er.asset_from_bytes("BROKEN", b"\x89PNG\r\n\x1a\nbroken" * 5)}
    return er.snapshot_from_document(_doc(pages, did="doc_stress"), assets)


def _ok_assets(snap: er.RenderSnapshot) -> int:
    return sum(1 for a in snap.assets.values() if a.ok)


@pytest.fixture
def settings(tmp_path):
    return Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "t.sqlite3", export_browser_path=BROWSER_PATH_ENV)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def out_dir(tmp_path):
    return tmp_path / "runs" / "exports"


class Flow:
    """세션 + 가상 TXT/PNG 업로드 + mock 초안 rev.1(이미지 블록 포함)."""

    def __init__(self, app, *, png: bytes | None = None):
        self.c = TestClient(app)
        self.sid = self.c.post("/api/v1/sessions", json={"brief": BRIEF}).json()["session_id"]
        files = [("files", ("a.txt", io.BytesIO(CLEAN_TXT))), ("files", ("p.png", io.BytesIO(png or _png())))]
        up = self.c.post(f"/api/v1/sessions/{self.sid}/sources", files=files).json()
        selected = [i["source_id"] for i in up["items"]]
        r = self.c.patch(f"/api/v1/sessions/{self.sid}/inputs", json={"expected_input_revision": 1, "selected_source_ids": selected})
        assert r.status_code == 200, r.text
        self.rev_in = r.json()["input_revision"]
        job = self.c.post(f"/api/v1/sessions/{self.sid}/preflights", json={"expected_input_revision": self.rev_in}).json()
        pf = self._job(job["job_id"])["result_ref"]["preflight_id"]
        job = self.c.post(f"/api/v1/sessions/{self.sid}/drafts", json={"preflight_id": pf, "input_revision": self.rev_in, "confirmed": True}).json()
        self.did = self._job(job["job_id"])["result_ref"]["document_id"]

    def _job(self, jid):
        job = self.c.get(f"/api/v1/sessions/{self.sid}/jobs/{jid}").json()
        assert job["status"] == "succeeded", job
        return job


# ================= 출력 식별값 공유(㉖) =================

@needs_browser
@pytest.mark.parametrize("layout", ["product_grid", "fact_sheet", "certification_summary"])
def test_editorial_labeled_pdf_preserves_conditions_and_block_order(layout, out_dir, settings):
    from app import agent_llm
    from test_agent_llm import build_editorial_request, editorial_response, EDITORIAL_CASES
    from pypdf import PdfReader
    request = build_editorial_request("manufacturing")
    draft = agent_llm.LlmAgent(lambda i, p, s, n: editorial_response(p)).draft(request)
    draft.pages[0].layout_key = layout
    document = _doc(draft.pages)
    before = document.model_copy(deep=True)
    result = er.render(er.snapshot_from_document(document, {}), "pdf", out_dir, settings)
    assert result.layout_ok and result.actual_pages == 1
    assert document == before
    text = "".join(p.extract_text() or "" for p in PdfReader(result.file_path).pages)
    compact = "".join(text.split())
    assert all("".join(term.split()) in compact for term in EDITORIAL_CASES["manufacturing"]["must_keep"])
    for heading, body in zip(document.pages[0].blocks, document.pages[0].blocks[1:]):
        if heading.type == "heading" and heading.content.get("level") == 2:
            assert compact.index("".join(heading.content["text"].split())) < compact.index("".join(body.content["text"].split()))


def _editorial_photo_snapshot(layout="text_photo", *, fit="contain", long=False, sparse=False):
    from app.models import PageDesign
    blocks = [_blk("photo_title", "heading", text="시연 제조공정", level=1),
              _blk("photo_lead", "paragraph", text="시험용 부품의 표면 처리와 피막 처리를 소개하며, 공정별 적용 소재와 가능한 용도를 구분해 설명합니다.")]
    texts = (["시연 회사", "EXAMPLE COMPANY"] if layout == "cover_photo" else [
        "시연 공정은 시험용 금속 표면에 적용하며, 적용 소재와 처리 조건에 따라 결과를 확인합니다."
    ] * 4)
    if sparse:
        texts = []
    if long:
        texts = [LONG_UNIT * 80]
    for i, text in enumerate(texts):
        blocks.extend([_blk(f"photo_label_{i}", "heading", text="공정 적용 조건", level=2),
                       _blk(f"photo_body_{i}", "paragraph", text=text)])
    blocks.append(_blk("photo", "image", asset_id="PHOTO", fit=fit, alt="시연 사진", caption="[MOCK] 시험용 사진"))
    doc = _doc([Page(page_id="photo_page", title="시연 사진 배치", layout_key=layout,
                    design=PageDesign(), blocks=blocks)])
    return er.snapshot_from_document(doc, {"PHOTO": er.asset_from_bytes("PHOTO", _png(320, 240))}, demo=True)


@needs_browser
@pytest.mark.parametrize("layout", ["cover_photo", "text_photo"])
def test_editorial_photo_fit_keeps_text_image_and_logical_page(layout, out_dir, settings, monkeypatch):
    from pypdf import PdfReader
    snap = _editorial_photo_snapshot(layout)
    before = [p.model_dump() for p in snap.pages]
    build = er.build_html
    with monkeypatch.context() as patch:
        # Reproduce the old fixed-height behavior with exactly the same content.
        patch.setattr(er, "build_html", lambda s: build(s).replace("var photoFits = fitPhotos();", "var photoFits = [];"))
        old = er.render(snap, "pdf", out_dir / "fixed", settings)
    assert not old.layout_ok and old.actual_pages > 1
    result = er.render(snap, "pdf", out_dir / "fitted", settings)
    assert result.layout_ok and result.actual_pages == 1 and not result.findings
    fits = result.details["measure"]["photo_fits"]
    assert len(fits) == 1 and fits[0]["block_id"] == "photo"
    assert 36 * 96 / 25.4 - 0.1 <= fits[0]["height_px"] < fits[0]["original_height_px"]
    def content(path):
        text = "".join("".join((p.extract_text() or "").split()) for p in PdfReader(path).pages)
        return text.replace("".join(er.DEMO_FOOTER_TEXT.split()), "")
    assert content(old.file_path) == content(result.file_path)
    reader = PdfReader(result.file_path)
    assert sum(len(p.images) for p in reader.pages) == 1
    assert [p.model_dump() for p in snap.pages] == before


@needs_browser
@pytest.mark.parametrize("case", ["text_too_long", "crop", "already_fits"])
def test_editorial_photo_fit_does_not_mask_overflow_or_change_other_images(case, out_dir, settings):
    from pypdf import PdfReader
    snap = _editorial_photo_snapshot(fit="crop" if case == "crop" else "contain",
                                    long=case == "text_too_long", sparse=case == "already_fits")
    result = er.render(snap, "pdf", out_dir, settings)
    fits = result.details["measure"]["photo_fits"]
    if case == "already_fits":
        assert result.layout_ok and result.actual_pages == 1 and not fits
    else:
        assert not result.layout_ok and any(f.kind == "overflow" for f in result.findings)
        if case == "crop":
            assert not fits
        else:
            assert fits and abs(fits[0]["height_px"] - 36 * 96 / 25.4) < 0.1
            text = "".join("".join((p.extract_text() or "").split()) for p in PdfReader(result.file_path).pages)
            assert ("".join(LONG_UNIT.split()) * 80) in text.replace("".join(er.DEMO_FOOTER_TEXT.split()), "")


@needs_browser
def test_editorial_group_overflow_is_detected_without_hiding_text(out_dir, settings):
    from app.models import PageDesign
    doc = _doc([Page(page_id="p_labeled", title="긴 조건", layout_key="fact_sheet", design=PageDesign(), blocks=[
        _blk("label", "heading", level=2, text="적용 조건"),
        _blk("body", "paragraph", text=LONG_UNIT * 180),
    ])])
    result = er.render(er.snapshot_from_document(doc, {}), "pdf", out_dir, settings)
    assert not result.layout_ok and any(f.kind == "overflow" for f in result.findings)


@needs_browser
@pytest.mark.parametrize("case", ["groups", "oversized_paragraph"])
def test_draft_pagination_preserves_content_and_fits_actual_pdf(case, out_dir, settings):
    from app.models import EvidenceRef, PageDesign
    from pypdf import PdfReader
    blocks = [_blk("title", "heading", text="자동 분량 확인", level=1),
              _blk("lead", "paragraph", text="가상 제조 공정의 확인 조건을 소개합니다.")]
    for n in range(10 if case == "groups" else 1):
        blocks.extend([_blk(f"label_{n}", "heading", text=f"확인 항목 {n + 1}", level=2),
                       _blk(f"body_{n}", "paragraph", text=LONG_UNIT * (4 if case == "groups" else 90))])
    blocks.append(_blk("photo", "image", asset_id="PHOTO", fit="contain", caption="가상 사진", alt="가상 사진"))
    ref = EvidenceRef(source_id="source_test", source_version=1, segment_id="segment_test", locator={"line": 1}, excerpt=LONG_UNIT)
    for block in blocks:
        if block.type != "image":
            block.fact_ids, block.evidence_refs = ["fact_test"], [ref]
    page = Page(page_id="page_overfull", title="자동 분량 확인", layout_key="text_photo", design=PageDesign(), blocks=blocks)
    snap = er.snapshot_from_document(_doc([page]), {"PHOTO": er.asset_from_bytes("PHOTO", _png(320, 240))}, demo=True)
    before = [p.model_dump() for p in snap.pages]
    prepared = er.paginate_draft(snap, out_dir, settings)
    assert prepared.outcome == "passed" and 1 < len(prepared.pages) <= 10
    assert prepared.attempts > 1
    flattened = [b for p in prepared.pages for b in p.blocks]
    assert "".join(b.content.get("text", "") for b in flattened) == "".join(b.content.get("text", "") for b in blocks)
    assert len({b.block_id for b in flattened}) == len(flattened)
    for block in flattened:
        if block.type != "image":
            assert block.fact_ids == ["fact_test"] and block.evidence_refs == [ref]
    assert [b.model_dump() for b in flattened if b.type == "image"] == [blocks[-1].model_dump()]
    if case == "groups":
        assert len(prepared.pages) <= 3  # Refill continuations instead of making sparse pages.
        assert [b.model_dump() for b in flattened] == [b.model_dump() for b in blocks]
        for p in prepared.pages:
            for i, b in enumerate(p.blocks):
                if b.block_id.startswith("label_"):
                    assert p.blocks[i + 1].block_id == b.block_id.replace("label_", "body_")
    else:
        assert len(flattened) > len(blocks)  # One paragraph really needed splitting.
    reader = PdfReader(out_dir / f"{snap.document_id}_rev{snap.document_revision}.pdf")
    assert len(reader.pages) == len(prepared.pages)
    assert sum(len(p.images) for p in reader.pages) == 1
    text = ""
    for n, (pdf_page, logical_page) in enumerate(zip(reader.pages, prepared.pages), 1):
        content = "".join((pdf_page.extract_text() or "").split())
        for label in (er.DEMO_FOOTER_TEXT, "COMPANY PROFILE", f"{n} / {logical_page.title}"):
            content = content.replace("".join(label.split()), "")
        text += content
    expected = "".join("".join(b.content.get("text", b.content.get("caption", "")).split()) for b in blocks)
    assert text == expected  # Includes sentence fragments spanning a page boundary.
    assert [p.model_dump() for p in snap.pages] == before


@pytest.mark.parametrize("with_heading", [False, True])
@pytest.mark.parametrize("photo_count", [1, 2])
def test_draft_split_keeps_explanation_with_trailing_photo(with_heading, photo_count):
    blocks = [_blk("intro", "paragraph", text="소개 문장"),
              _blk("other", "paragraph", text="앞 설명")]
    if with_heading:
        blocks.append(_blk("heading", "heading", text="설비 설명", level=2))
    blocks.extend([_blk("body", "paragraph", text="사진과 연결된 설명", fact_ids=["fact_photo"]),
                   _blk("photo", "image", asset_id="image", caption="설비 사진", fit="contain")])
    page = Page(page_id="p", title="설비", layout_key="text_photo", blocks=blocks)
    if photo_count == 2:
        page.blocks.append(_blk("photo2", "image", asset_id="image", caption="다른 설비 사진", fit="contain"))
    before = page.model_dump()
    split = er._split_draft_page(page, "photo2" if photo_count == 2 else "photo")
    assert split is not None and len(split) == 2
    assert [b.model_dump() for p in split for b in p.blocks] == before["blocks"]
    assert [b.block_id for b in split[1].blocks] == (["heading"] if with_heading else []) + ["body", "photo"] + (["photo2"] if photo_count == 2 else [])
    assert split[0].blocks and page.model_dump() == before


@needs_browser
def test_draft_pagination_leaves_a_fitting_document_unchanged(out_dir, settings):
    snap = _editorial_photo_snapshot(sparse=True)
    prepared = er.paginate_draft(snap, out_dir, settings)
    assert prepared.outcome == "passed" and prepared.attempts == 1
    assert prepared.pages == snap.pages


def test_draft_pagination_unavailable_preserves_every_block(out_dir, settings, monkeypatch):
    snap = _editorial_photo_snapshot(long=True)
    def unavailable(*args):
        raise er.RenderError("browser_not_found", "No browser")
    monkeypatch.setattr(er, "render", unavailable)
    prepared = er.paginate_draft(snap, out_dir, settings)
    assert prepared.outcome == "unavailable" and prepared.pages == snap.pages


def test_draft_pagination_stops_without_hiding_unresolvable_content(out_dir, settings, monkeypatch):
    from app.models import PageDesign
    page = Page(page_id="p", title="긴 제목", layout_key="cover_text", design=PageDesign(),
                blocks=[_blk("h", "heading", text="가" * 3000, level=1)])
    snap = er.snapshot_from_document(_doc([page]), {})
    def overflow(candidate, *args):
        return er.RenderResult("pdf", out_dir / "unused.pdf", 2,
            [er.LayoutCheckRecord("overflow", True, "finding")],
            [er.Finding("overflow", "p", "h", "overflow", {})], False, "v", "r", "a", "test", 0,
            {"measure": {"fonts_ready": True, "pages": [{"page_id": "p", "overflow": True,
                "height_px": 2000, "excess_px": 1000, "first_overflow_block_id": "h"}]}})
    monkeypatch.setattr(er, "render", overflow)
    prepared = er.paginate_draft(snap, out_dir, settings)
    assert prepared.outcome == "unresolved" and prepared.pages == snap.pages and prepared.attempts == 1


def test_draft_pagination_bounds_repeated_overflow_without_losing_content(out_dir, settings, monkeypatch):
    from app.models import PageDesign
    page = Page(page_id="p", title="긴 본문", layout_key="text_photo", design=PageDesign(),
                blocks=[_blk("b", "paragraph", text=("조건을 모두 유지합니다. " * 10000))])
    snap = er.snapshot_from_document(_doc([page]), {})
    calls = []
    def overflow(candidate, *args):
        calls.append(len(candidate.pages))
        return er.RenderResult("pdf", out_dir / "unused.pdf", len(candidate.pages) + 1,
            [er.LayoutCheckRecord("overflow", True, "finding")],
            [er.Finding("overflow", p.page_id, p.blocks[0].block_id, "overflow") for p in candidate.pages],
            False, "v", "r", "a", "test", 0, {"measure": {"fonts_ready": True, "pages": [
                {"page_id": p.page_id, "overflow": True, "height_px": 2000, "excess_px": 1000,
                 "first_overflow_block_id": p.blocks[0].block_id} for p in candidate.pages]}})
    monkeypatch.setattr(er, "render", overflow)
    prepared = er.paginate_draft(snap, out_dir, settings)
    assert prepared.outcome == "limit" and len(calls) <= 8 and len(prepared.pages) <= 40
    assert "".join(b.content["text"] for p in prepared.pages for b in p.blocks) == page.blocks[0].content["text"]


def test_identity_values_come_from_layout_checks(out_dir):
    r = er.render(_fixture_snapshot("1pages"), "docx", out_dir)
    assert r.template_version == layout_checks.TEMPLATE_VERSION == "template_v11"
    assert r.render_options_hash == layout_checks.RENDER_OPTIONS_HASH == layout_checks.render_options_hash(layout_checks.DEFAULT_RENDER_OPTIONS)
    assert len(r.render_options_hash) == 16 and int(r.render_options_hash, 16) >= 0
    changed = dict(layout_checks.DEFAULT_RENDER_OPTIONS, margin_mm=20)
    assert layout_checks.render_options_hash(changed) != r.render_options_hash
    # 옵션 내용이 실제 값(용지·여백·폰트)인지 — 'default' 자리표시자가 아니다
    assert layout_checks.DEFAULT_RENDER_OPTIONS["font_family"] == "Pretendard" and layout_checks.DEFAULT_RENDER_OPTIONS["page_size"] == "A4"


def test_adapter_reads_layout_checks_at_call_time(out_dir, monkeypatch):
    """어댑터가 값을 복사해 두지 않고 layout_checks의 현재 값을 쓴다(한쪽만 올리면 바로 드러남)."""
    monkeypatch.setattr(layout_checks, "TEMPLATE_VERSION", "template_vX")
    monkeypatch.setattr(layout_checks, "RENDER_OPTIONS_HASH", "0123456789abcdef")
    r = er.render(_fixture_snapshot("1pages"), "docx", out_dir)
    assert r.template_version == "template_vX" and r.render_options_hash == "0123456789abcdef"


def test_manifest_hash_pure_function_matches_legacy_formula():
    items = [("b", "h2"), ("a", "h1"), ("a", "")]
    legacy = hashlib.sha256(json.dumps(sorted(items)).encode()).hexdigest()[:16]
    assert layout_checks.manifest_hash(items) == legacy
    assert layout_checks.manifest_hash([]) == hashlib.sha256(b"[]").hexdigest()[:16]


def test_template_fingerprint_pinned_per_version():
    """템플릿·폰트·DOCX 배치 상수·PDF 렌더 상수를 바꾸면서 TEMPLATE_VERSION을 올리지 않으면 실패한다."""
    pinned = TEMPLATE_FINGERPRINTS.get(layout_checks.TEMPLATE_VERSION)
    assert pinned, f"TEMPLATE_VERSION {layout_checks.TEMPLATE_VERSION}의 지문이 TEMPLATE_FINGERPRINTS에 없습니다"
    actual = er.template_fingerprint()
    assert actual == pinned, ("템플릿/폰트/DOCX 배치 상수/PDF 렌더 상수가 바뀌었습니다. layout_checks.TEMPLATE_VERSION을 올리고 "
                              f"TEMPLATE_FINGERPRINTS[새 버전] = '{actual}' 을 추가하세요.")
    # 줄바꿈(autocrlf)에 흔들리지 않는다
    raw = er.TEMPLATE_FILE.read_bytes()
    assert b"\r\n" not in raw
    src = inspect.getsource(er.template_fingerprint)
    assert "PDF_RENDER_CONSTANTS" in src and "DOCX_LAYOUT_CONSTANTS" in src


def test_render_takes_no_caller_options():
    params = inspect.signature(er.render).parameters
    assert list(params) == ["snapshot", "fmt", "out_dir", "settings"]
    assert all(p.kind == p.POSITIONAL_OR_KEYWORD for p in params.values())


# ================= 스냅샷 고정 =================

def test_snapshot_manifest_matches_db_and_tracks_content_hash(app, settings, out_dir):
    flow = Flow(app)
    with connect(settings.db_path) as conn:
        doc = get_current(conn, flow.sid, flow.did)
        assert layout_checks.image_asset_ids(doc), "mock 초안에 image 블록이 있어야 한다"
        snap = er.build_snapshot(conn, settings, flow.sid, doc)
        assert snap.asset_manifest_hash == layout_checks.asset_manifest_hash(conn, doc)
        aid = layout_checks.image_asset_ids(doc)[0]
        asset = snap.assets[aid]
        assert asset.ok and asset.data and asset.width == 8 and asset.height == 6 and len(asset.content_hash) == 64
        # 같은 이름으로 파일을 바꿔치기한 상황: DB content_hash와 파일이 어긋남 → 스냅샷은 hash_mismatch, manifest는 DB값 기준으로 둘 다 동일하게 변함
        conn.execute("UPDATE assets SET content_hash='changed' WHERE asset_id=?", (aid,))
        snap2 = er.build_snapshot(conn, settings, flow.sid, doc)
        assert snap2.assets[aid].ok is False and snap2.assets[aid].reason == "hash_mismatch"
        assert snap2.asset_manifest_hash == layout_checks.asset_manifest_hash(conn, doc) != snap.asset_manifest_hash
    r = er.render(snap2, "docx", out_dir)
    assert r.asset_manifest_hash == snap2.asset_manifest_hash
    assert [f.kind for f in r.findings] == ["broken_image"] and r.findings[0].details["reason"] == "hash_mismatch"


def test_snapshot_access_rules_missing_file_and_not_ready(app, settings):
    flow_a, flow_b = Flow(app), Flow(app)
    with connect(settings.db_path) as conn:
        doc_a = get_current(conn, flow_a.sid, flow_a.did)
        doc_b = get_current(conn, flow_b.sid, flow_b.did)
        aid_b = layout_checks.image_asset_ids(doc_b)[0]
        # A 문서가 B 세션의 asset을 가리키면 not_accessible(존재를 쓰지 않는다). manifest는 DB content_hash로 계산돼 승인 검사와 같다
        pages = [Page(page_id="p", title="t", layout_key="text_photo", blocks=[
            _blk("x", "image", asset_id=aid_b, alt="", caption="", fit="contain")])]
        foreign = doc_a.model_copy(update={"pages": pages})
        snap = er.build_snapshot(conn, settings, flow_a.sid, foreign)
        assert snap.assets[aid_b].ok is False and snap.assets[aid_b].reason == "not_accessible" and snap.assets[aid_b].data is None
        assert snap.asset_manifest_hash == layout_checks.asset_manifest_hash(conn, foreign)
        # 아직 준비되지 않은 asset → not_ready, 바이트 없음
        aid_a = layout_checks.image_asset_ids(doc_a)[0]
        conn.execute("UPDATE assets SET status='processing' WHERE asset_id=?", (aid_a,))
        snap_nr = er.build_snapshot(conn, settings, flow_a.sid, doc_a)
        assert snap_nr.assets[aid_a].reason == "not_ready" and snap_nr.assets[aid_a].data is None
        conn.execute("UPDATE assets SET status='ready' WHERE asset_id=?", (aid_a,))
        # 파일이 사라진 asset → missing
        stored = conn.execute("SELECT stored_path FROM assets WHERE asset_id=?", (aid_a,)).fetchone()[0]
        (settings.private_runs_dir / stored).unlink()
        snap_a = er.build_snapshot(conn, settings, flow_a.sid, doc_a)
        assert snap_a.assets[aid_a].reason == "missing"
        # 아예 없는 asset_id → not_found, content_hash ""
        ghost = doc_a.model_copy(update={"pages": [Page(page_id="p", title="t", layout_key="text", blocks=[
            _blk("g", "image", asset_id="asset_ghost", alt="", caption="", fit="contain")])]})
        snap_g = er.build_snapshot(conn, settings, flow_a.sid, ghost)
        assert snap_g.assets["asset_ghost"].reason == "not_found" and snap_g.manifest_items == [("asset_ghost", "")]


def test_render_uses_snapshot_bytes_not_disk(app, settings, out_dir):
    """규칙 ③: 스냅샷을 만든 뒤 파일·DB 행이 사라져도 렌더는 스냅샷 바이트로 이미지를 넣는다."""
    flow = Flow(app)
    with connect(settings.db_path) as conn:
        doc = get_current(conn, flow.sid, flow.did)
        snap = er.build_snapshot(conn, settings, flow.sid, doc)
        aid = layout_checks.image_asset_ids(doc)[0]
        stored = conn.execute("SELECT stored_path FROM assets WHERE asset_id=?", (aid,)).fetchone()[0]
        conn.execute("DELETE FROM assets WHERE asset_id=?", (aid,))
    (settings.private_runs_dir / stored).unlink()
    r = er.render(snap, "docx", out_dir)
    assert r.findings == [] and er.docx_info(r.file_path)["inline_shapes"] == 1
    if BROWSER is not None:
        rp = er.render(snap, "pdf", out_dir, settings)
        from pypdf import PdfReader

        assert rp.findings == [] and sum(len(p.images) for p in PdfReader(str(rp.file_path)).pages) >= 1


def test_asset_from_bytes_verifies_hash_decodes_and_restricts_format():
    data = _png(5, 7)
    ok = er.asset_from_bytes("a", data)
    assert ok.ok and (ok.width, ok.height) == (5, 7) and ok.content_hash == hashlib.sha256(data).hexdigest() and ok.mime_type == "image/png"
    assert er.asset_from_bytes("a", data, content_hash="0" * 64).reason == "hash_mismatch"
    assert er.asset_from_bytes("a", b"not an image").reason == "decode_failed"
    assert er.asset_from_bytes("a", None).reason == "missing"
    # Pillow는 열지만 두 렌더러가 모두 지원한다고 볼 수 없는 형식(BMP/WebP 등) → unsupported_format, 바이트 없음
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (3, 3)).save(buf, format="BMP")
    bmp = er.asset_from_bytes("b", buf.getvalue())
    assert bmp.ok is False and bmp.reason == "unsupported_format" and bmp.data is None
    snap = er.snapshot_from_document(_doc([Page(page_id="p", title="t", layout_key="text", blocks=[
        _blk("i", "image", asset_id="unknown", alt="", caption="", fit="contain")])]), {})
    assert snap.assets["unknown"].reason == "not_found"


def test_embed_bytes_downscales_only_large_images():
    small = er.asset_from_bytes("s", _png(300, 200))
    data, w, h = er.embed_bytes(small)
    assert data is small.data and (w, h) == (300, 200)          # 상한 이하: 검증한 바이트 그대로
    big = er.asset_from_bytes("b", _png(er.IMAGE_MAX_PX * 2, 400))
    data, w, h = er.embed_bytes(big)
    assert w == er.IMAGE_MAX_PX and h == 200 and data != big.data
    assert big.content_hash == hashlib.sha256(big.data).hexdigest()   # 스냅샷 자체는 바뀌지 않는다
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        assert img.format == "PNG" and img.size == (er.IMAGE_MAX_PX, 200)


# ================= DOCX =================

@pytest.mark.parametrize("size,expected_mm", [((441, 236), (74.676, 39.9626667)),
                                             ((236, 441), (39.9626667, 74.676)),
                                             ((1600, 1000), (180, 112.5))])
def test_docx_picture_size_preserves_source_bytes_and_limits_enlargement(out_dir, size, expected_mm):
    import zipfile
    import docx

    source = _png(*size)
    page = Page(page_id="p", title="사진", layout_key="text_photo",
                blocks=[_blk("photo", "image", asset_id="image", caption="원자료의 시험장비", fit="contain")])
    snapshot = er.snapshot_from_document(_doc([page]), {"image": er.asset_from_bytes("image", source)})
    result = er.render(snapshot, "docx", out_dir)
    shape = docx.Document(result.file_path).inline_shapes[0]
    assert shape.width.mm == pytest.approx(expected_mm[0], abs=0.01)
    assert shape.height.mm == pytest.approx(expected_mm[1], abs=0.01)
    assert shape.width / shape.height == pytest.approx(size[0] / size[1], rel=1e-4)
    with zipfile.ZipFile(result.file_path) as archive:
        media = [name for name in archive.namelist() if name.startswith("word/media/")]
        assert len(media) == 1 and archive.read(media[0]) == source

def test_docx_render_structure_checks_and_font(out_dir):
    snap = _fixture_snapshot("10pages")
    r = er.render(snap, "docx", out_dir)
    assert r.format == "docx" and r.file_path.is_file() and r.file_path.stat().st_size > 0
    assert r.actual_pages is None                       # 2.3절: 논리 페이지 수·PDF 쪽수를 대신 넣지 않는다
    assert r.layout_ok is False and r.not_checked == ["overflow"]
    overflow = next(c for c in r.checks if c.check_key == "overflow")
    assert overflow.required is True and overflow.result == "not_checked" and overflow.reason == "docx_no_layout_engine"
    assert {c.check_key: c.result for c in r.checks if c.check_key != "overflow"} == {"broken_image": "ok", "placeholder_remaining": "ok"}
    assert r.findings == [] and r.renderer.startswith("python-docx/")
    # 구조만 다시 센다(쪽수 판단에 쓰지 않는다)
    info = er.docx_info(r.file_path)
    total_blocks = sum(len(p.blocks) for p in snap.pages)
    assert info["inline_shapes"] == _ok_assets(snap) == 2 and info["paragraphs"] >= total_blocks and info["sections"] == 1
    assert r.details["font_embedding"] == "embedded_regular_bold"
    assert sorted(p.name for p in out_dir.iterdir()) == [r.file_path.name]   # 임시 파일이 남지 않는다
    # 글꼴 이름: Normal·Heading·Caption 스타일에 eastAsia 포함 지정, 테마 글꼴 속성 제거
    import docx
    from docx.oxml.ns import qn

    d = docx.Document(str(r.file_path))
    for name in ("Normal", "Heading 1", "Heading 2", "Caption", "List Bullet"):
        rfonts = d.styles[name].element.rPr.find(qn("w:rFonts"))
        assert rfonts.get(qn("w:eastAsia")) == "Pretendard" == rfonts.get(qn("w:ascii")), name
        assert rfonts.get(qn("w:eastAsiaTheme")) is None and rfonts.get(qn("w:asciiTheme")) is None, name
    # Portable regular/bold fonts must recover to the exact PDF font bytes.
    import uuid
    from zipfile import ZipFile
    from lxml import etree
    with ZipFile(r.file_path) as package:
        table = etree.fromstring(package.read("word/fontTable.xml"))
        font = next(f for f in table if f.get(qn("w:name")) == "Pretendard")
        rels = etree.fromstring(package.read("word/_rels/fontTable.xml.rels"))
        targets = {rel.get("Id"): rel.get("Target") for rel in rels}
        for weight, tag in (("regular", "w:embedRegular"), ("bold", "w:embedBold")):
            item = font.find(qn(tag))
            assert item is not None and item.get(qn("w:subsetted")) == "false"
            mask = uuid.UUID(item.get(qn("w:fontKey")).strip("{}")).bytes[::-1]
            data = bytearray(package.read("word/" + targets[item.get(qn("r:id"))]))
            for n in range(32):
                data[n] ^= mask[n % 16]
            assert data == er.FONT_FILES[weight].read_bytes()
        settings = etree.fromstring(package.read("word/settings.xml"))
        assert settings.find(qn("w:embedTrueTypeFonts")).get(qn("w:val")) == "true"
        assert settings.find(qn("w:saveSubsetFonts")).get(qn("w:val")) == "false"
    assert d.core_properties.title == snap.title and d.core_properties.author == ""
    sec = d.sections[0]
    assert round(sec.page_width.mm) == 210 and round(sec.page_height.mm) == 297 and round(sec.left_margin.mm) == 15
    assert "[MOCK] 예시 회사 소개" in "\n".join(p.text for p in d.paragraphs)


def test_docx_findings_broken_missing_placeholder_and_escaping(out_dir):
    snap = _stress_snapshot()
    r = er.render(snap, "docx", out_dir)
    kinds = sorted((f.kind, f.block_id) for f in r.findings)
    assert kinds == [("broken_image", "img_broken"), ("broken_image", "img_missing"), ("placeholder_remaining", "ph")]
    by_key = {c.check_key: c for c in r.checks}
    assert by_key["broken_image"].result == "finding" and by_key["broken_image"].block_ids == ["img_broken", "img_missing"]
    assert by_key["placeholder_remaining"].result == "finding" and by_key["placeholder_remaining"].page_ids == ["p3"]
    assert r.layout_ok is False
    import docx

    d = docx.Document(str(r.file_path))
    text = "\n".join(p.text for p in d.paragraphs) + "\n" + "\n".join(c.text for t in d.tables for row in t.rows for c in row.cells)
    assert "[사진 자리]" in text and "[이미지를 열 수 없음]" in text and "decode_failed" in text and "not_found" in text
    assert "<script>alert(1)</script>" in text          # 문자 그대로. 실행·해석되지 않는다
    assert er.docx_info(r.file_path)["inline_shapes"] == _ok_assets(snap) == 3   # 정상 이미지만 삽입(가로·세로·배너)
    assert not any(w in text for w in BANNED)


def test_docx_xml_incompatible_chars_are_stripped_not_crashing(out_dir):
    """U+FFFE/U+FFFF·서로게이트는 lxml이 거부한다. 제거해서 만들고, 그래도 실패하면 RenderError로만 알린다."""
    pages = [Page(page_id="p", title="t\ufffe", layout_key="text", blocks=[
        _blk("h", "heading", text="제목\uffff", level=1), _blk("q", "paragraph", text="a\ufffeb\x00c")])]
    snap = er.snapshot_from_document(_doc(pages, title="제목\ufffe"), {})
    r = er.render(snap, "docx", out_dir)
    import docx

    d = docx.Document(str(r.file_path))
    text = "\n".join(p.text for p in d.paragraphs)
    assert "abc" in text and "제목" in text and "\ufffe" not in text and d.core_properties.title == "제목"


def test_docx_build_failure_is_render_error_without_partial_file(out_dir, monkeypatch):
    def boom(*a, **k):
        raise ValueError("boom")

    monkeypatch.setattr(er, "_build_docx", boom)
    with pytest.raises(er.RenderError) as e:
        er.render(_fixture_snapshot("1pages"), "docx", out_dir)
    assert e.value.code == "render_failed" and not any(out_dir.iterdir())
    monkeypatch.undo()
    import docx.document

    def disk_full(self, path):
        raise OSError("disk full")

    monkeypatch.setattr(docx.document.Document, "save", disk_full)
    with pytest.raises(er.RenderError) as e:
        er.render(_fixture_snapshot("1pages"), "docx", out_dir)
    assert e.value.code == "save_failed" and not any(out_dir.iterdir())


def test_docx_concurrent_renders_of_same_revision_do_not_collide(out_dir):
    from dataclasses import replace
    import docx

    snap = _fixture_snapshot("1pages")
    results, errors = [], []

    def run(index):
        try:
            title = f"동시 출력 {index}"
            result = er.render(replace(snap, title=title), "docx", out_dir)
            results.append((title, result))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=run, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(results) == 3
    assert len({result.file_path for _, result in results}) == 3
    assert set(out_dir.iterdir()) == {result.file_path for _, result in results}
    for title, result in results:
        document = docx.Document(str(result.file_path))
        assert document.core_properties.title == title
        assert len(document.inline_shapes) == 1
    # 후속 렌더가 앞서 반환한 출력 파일을 바꾸지 않아야 검사·다운로드 대상이 유지된다.
    originals = {result.file_path: result.file_path.read_bytes() for _, result in results}
    er.render(snap, "docx", out_dir)
    assert all(path.read_bytes() == data for path, data in originals.items())


def test_renderer_child_does_not_inherit_open_server_stdin():
    """실제 서버의 stdin 파이프가 열려 있어도 렌더 자식은 EOF를 받아 종료해야 한다."""
    import subprocess

    script = "from app.services.export_render import _run; import sys; r=_run([sys.executable,'-c','import sys; print(len(sys.stdin.read()))'],3,'stdin-probe'); print(r.stdout.decode().strip())"
    parent = subprocess.Popen([sys.executable, "-X", "utf8", "-B", "-c", script], cwd=Path(__file__).resolve().parent.parent,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        # communicate보다 먼저 wait: communicate는 부모 stdin을 닫아 오류를 숨길 수 있다.
        assert parent.wait(timeout=10) == 0
        output, error = parent.communicate(timeout=2)
        assert output.strip() == b"0", error.decode(errors="replace")
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.communicate(timeout=2)


def test_docx_configured_missing_engine_does_not_pass(settings, out_dir):
    from dataclasses import replace

    result = er.render(_fixture_snapshot("1pages"), "docx", out_dir,
                       replace(settings, export_libreoffice_path=str(out_dir / "missing-soffice.exe")))
    assert result.actual_pages is None and result.layout_ok is False and result.preview_path is None
    assert result.not_checked == ["overflow"]
    assert result.checks[0].reason == "docx_layout_engine_unavailable"


@pytest.mark.parametrize("failure", ["version", "conversion", "missing_pdf", "invalid_pdf", "timeout"])
def test_docx_engine_failure_never_passes_and_removes_profile(settings, out_dir, monkeypatch, failure):
    from dataclasses import replace

    # Existing executable is only an identity placeholder; the runner is replaced before any invocation.
    engine = Path(sys.executable)
    calls = []
    def run(command, timeout, what):
        calls.append(command)
        if failure == "timeout":
            raise er.RenderError("render_timeout", "simulated timeout")
        if "--version" in command:
            return subprocess.CompletedProcess(command, 1 if failure == "version" else 0, b"LibreOffice 26.8.0.3", b"")
        if failure in {"invalid_pdf", "conversion"}:
            (Path(command[command.index("--outdir") + 1]) / "document.pdf").write_bytes(b"not a PDF")
        return subprocess.CompletedProcess(command, 1 if failure == "conversion" else 0, b"", b"PRIVATE_SENTINEL")
    monkeypatch.setattr(er, "_run", run)
    with pytest.raises(er.RenderError) as exc:
        er.render(_fixture_snapshot("1pages"), "docx", out_dir,
                  replace(settings, export_libreoffice_path=str(engine)))
    assert exc.value.code == ("render_timeout" if failure == "timeout" else "render_failed")
    assert "PRIVATE_SENTINEL" not in str(exc.value)
    assert calls and not list(out_dir.glob("docx_check_*")) and not list(out_dir.glob("*.preview.pdf"))


@needs_libreoffice
@pytest.mark.parametrize("pages", [1, 4, 6, 8, 10])
def test_docx_actual_pages_text_photos_and_editability(settings, out_dir, pages):
    from dataclasses import replace
    import docx

    contents = [Page(page_id=f"p{i}", title=f"회사 정보 {i}", layout_key="text", blocks=[
        _blk(f"h{i}", "heading", text=f"한글 회사 소개 {i}", level=1),
        _blk(f"b{i}", "paragraph", text=f"시험 본문 {i}: 수량 12개, 적용 기간 2026년 10월."),
        _blk(f"l{i}", "list", items=["검사 후 출하합니다.", "납기는 확인 후 안내합니다."])] ) for i in range(pages)]
    contents[0].blocks.append(_blk("photo", "image", asset_id="image", caption="검사용 가상 사진", fit="contain"))
    snapshot = er.snapshot_from_document(_doc(contents), {"image": er.asset_from_bytes("image", _png(320, 160))})
    result = er.render(snapshot, "docx", out_dir, replace(settings, export_libreoffice_path=LIBREOFFICE))
    assert result.layout_ok is True and result.actual_pages == pages and result.findings == []
    assert result.preview_path.is_file() and ";libreoffice/" in result.renderer
    assert result.details["images_per_page"] == [1] + [0] * (pages - 1)
    assert all(check.result == "ok" for check in result.checks)
    editable = docx.Document(str(result.file_path))
    assert any("시험 본문 0" in paragraph.text for paragraph in editable.paragraphs)
    assert len(editable.inline_shapes) == 1 and not list(out_dir.glob("docx_check_*"))


@needs_libreoffice
def test_docx_actual_overflow_remains_blocked(settings, out_dir):
    from dataclasses import replace

    snapshot = er.snapshot_from_document(_doc([Page(page_id="p1", title="긴 본문", layout_key="text", blocks=[
        _blk("long", "paragraph", text=LONG_UNIT * 250)])]), {})
    result = er.render(snapshot, "docx", out_dir, replace(settings, export_libreoffice_path=LIBREOFFICE))
    assert result.layout_ok is False and result.actual_pages > 1 and result.preview_path.is_file()
    assert result.checks[0].result == "finding" and not result.not_checked
    assert any(f.kind == "overflow" for f in result.findings)


@needs_libreoffice
@pytest.mark.parametrize("pages", [4, 8])
def test_docx_dense_photo_pages_have_no_blank_break_pages(settings, out_dir, pages):
    from dataclasses import replace
    import docx

    contents = []
    body = "표면 처리 공정은 확인된 조건과 검사 순서를 유지하며 고객의 요청에 따라 작업합니다. "
    for i in range(pages):
        blocks = [_blk(f"h{i}", "heading", text=f"공정 소개 {i}", level=1),
                  _blk(f"intro{i}", "paragraph", text=body)]
        for j in range(4):
            blocks.extend([_blk(f"h{i}_{j}", "heading", text=f"검사 항목 {j}", level=2),
                           _blk(f"b{i}_{j}", "paragraph", text=body + "수량 12개, 적용 기간은 2026년 10월입니다.")])
        blocks.append(_blk(f"photo{i}", "image", asset_id="image", caption="확인한 공정 사진", fit="contain"))
        contents.append(Page(page_id=f"p{i}", title=f"공정 소개 {i}", layout_key="text_photo", blocks=blocks))
    snapshot = er.snapshot_from_document(_doc(contents), {"image": er.asset_from_bytes("image", _png(320, 210))}, demo=True)
    result = er.render(snapshot, "docx", out_dir, replace(settings, export_libreoffice_path=LIBREOFFICE))
    assert result.actual_pages == pages and result.layout_ok is True, result.findings
    assert result.details["images_per_page"] == [1] * pages
    editable = docx.Document(str(result.file_path))
    # Text and picture instances survive; the 320x210 source is no longer enlarged below 150ppi.
    text = "\n".join(p.text for p in editable.paragraphs)
    assert text.count(body) == pages * 5 and len(editable.inline_shapes) == pages
    assert all(abs(shape.height.mm - 35.56) < 0.01 for shape in editable.inline_shapes)


# ================= HTML(템플릿) 안전성 — 브라우저 없이 확인 =================

def test_html_escapes_untrusted_text_and_has_no_external_resources():
    html = er.build_html(_stress_snapshot())
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<img src=x" not in html and "&lt;img src=x onerror=alert(2)&gt;" in html   # 태그가 아니라 문자로만 남는다
    assert html.count("<script") == 1 and 'nonce="' in html          # 측정 스크립트 하나뿐
    assert "http://" not in html and "https://" not in html and "file://" not in html   # 외부 리소스 없음(폰트·이미지는 data URI)
    assert "default-src 'none'" in html and 'font-family: "Pretendard"' in html
    assert "[사진 자리]" in html and "[이미지를 열 수 없음]" in html
    assert 'data-block-id="long"' in html and 'data-page-id="p2"' in html
    assert "document.fonts.ready" in html   # 측정은 폰트 로드 뒤


def test_measure_json_validation_rejects_forged_or_foreign_ids():
    snap = _fixture_snapshot("1pages")
    good = {"fonts_ready": True, "pages": [{"page_id": "page_mock_single", "height_px": 10.0, "overflow": False, "excess_px": 0, "first_overflow_block_id": None}]}
    assert er._valid_measure(good, snap) is True
    bad_cases = [
        dict(good, fonts_ready=False),                                                        # 폰트 미로드
        {"fonts_ready": True, "pages": []},                                                   # 페이지 누락
        {"fonts_ready": True, "pages": [dict(good["pages"][0], page_id="other")]},            # 스냅샷에 없는 page_id
        {"fonts_ready": True, "pages": [dict(good["pages"][0], overflow="false")]},           # 타입 위조
        {"fonts_ready": True, "pages": [dict(good["pages"][0], first_overflow_block_id="nope")]},  # 없는 block_id
        {"fonts_ready": True, "pages": good["pages"] + good["pages"]},                        # 중복
        "[]", None,
    ]
    for bad in bad_cases:
        assert er._valid_measure(bad, snap) is False, bad
    # print_pdf_and_measure는 unescape하지 않는다(ID 안의 &…; 로 JSON 구조를 위조하지 못하게)
    assert "unescape(" not in inspect.getsource(er.print_pdf_and_measure)


# ================= PDF (브라우저 필요) =================

@needs_browser
def test_pdf_render_pages_fonts_text(settings, out_dir):
    r = er.render(_fixture_snapshot("1pages"), "pdf", out_dir, settings)
    assert r.file_path.suffix == ".pdf" and r.file_path.stat().st_size > 0
    assert r.actual_pages == 1 and r.layout_ok is True and r.not_checked == [] and r.findings == []
    assert all(c.required and c.result == "ok" for c in r.checks)
    assert r.renderer.split("/")[0] in ("chrome", "edge", "chromium") and r.renderer.split("/")[1] not in ("", "unknown")
    assert any("Pretendard" in f for f in r.details["fonts"]), r.details["fonts"]
    assert r.details["measure"]["fonts_ready"] is True
    info = er.pdf_info(r.file_path)
    assert "예시 회사" in info["first_page_text"] and "가상 공정 A" in info["first_page_text"]
    assert not any(w in info["first_page_text"] for w in BANNED)
    # 임시 HTML·프로필은 남지 않는다
    assert sorted(p.name for p in out_dir.iterdir()) == [r.file_path.name]
    r10 = er.render(_fixture_snapshot("10pages"), "pdf", out_dir, settings)
    assert r10.actual_pages == 10 and r10.layout_ok is True


@needs_browser
def test_pdf_overflow_broken_placeholder_images(settings, out_dir):
    snap = _stress_snapshot()
    r = er.render(snap, "pdf", out_dir, settings)
    by_kind = {}
    for f in r.findings:
        by_kind.setdefault(f.kind, []).append(f)
    assert [f.page_id for f in by_kind["overflow"]] == ["p2"] and by_kind["overflow"][0].block_id == "long"
    assert by_kind["overflow"][0].details["excess_mm"] > 0
    assert sorted(f.block_id for f in by_kind["broken_image"]) == ["img_broken", "img_missing"]
    assert [f.block_id for f in by_kind["placeholder_remaining"]] == ["ph"]
    assert r.layout_ok is False and r.not_checked == []
    assert r.actual_pages > len(snap.pages)              # 넘친 페이지는 잘리지 않고 다음 물리 쪽으로 흐른다
    checks = {c.check_key: c for c in r.checks}
    assert checks["overflow"].result == "finding" and checks["overflow"].page_ids == ["p2"]
    from pypdf import PdfReader

    reader = PdfReader(str(r.file_path))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    assert "<script>alert(1)</script>" in text and "[사진 자리]" in text and "[이미지를 열 수 없음]" in text
    images = sum(len(p.images) for p in reader.pages)
    assert images >= _ok_assets(snap) == 3                # 정상 이미지(가로·세로·배너)는 모두 실제 이미지로 삽입
    measured = {p["page_id"]: p for p in r.details["measure"]["pages"]}
    assert measured["p1"]["overflow"] is False and measured["p2"]["overflow"] is True


@needs_browser
def test_pdf_measure_failed_is_not_checked_but_file_and_pages_are_real(settings, out_dir, monkeypatch):
    original = er.print_pdf_and_measure

    def no_measure(*a, **k):
        original(*a, **k)
        return None
    monkeypatch.setattr(er, "print_pdf_and_measure", no_measure)
    r = er.render(_fixture_snapshot("1pages"), "pdf", out_dir, settings)
    assert r.actual_pages == 1 and r.file_path.is_file()
    assert r.not_checked == ["overflow"] and r.layout_ok is False
    assert next(c for c in r.checks if c.check_key == "overflow").reason == "measure_failed"

    def bad_measure(*a, **k):
        original(*a, **k)
        return {"fonts_ready": True, "pages": []}   # 위조·불일치 → measure_invalid
    monkeypatch.setattr(er, "print_pdf_and_measure", bad_measure)
    r2 = er.render(_fixture_snapshot("1pages"), "pdf", out_dir, settings)
    assert r2.layout_ok is False and next(c for c in r2.checks if c.check_key == "overflow").reason == "measure_invalid"


@needs_browser
def test_pdf_render_errors_leave_no_partial_file(settings, out_dir, monkeypatch):
    def timeout(cmd, timeout, what):
        raise er.RenderError("render_timeout", "t")

    monkeypatch.setattr(er, "_run", timeout)
    with pytest.raises(er.RenderError) as e:
        er.render(_fixture_snapshot("1pages"), "pdf", out_dir, settings)
    assert e.value.code == "render_timeout"
    assert not out_dir.exists() or list(out_dir.iterdir()) == []
    # 브라우저가 0이 아닌 코드로 끝나고 PDF가 없으면 render_failed
    monkeypatch.setattr(er, "_run", lambda cmd, timeout, what: subprocess.CompletedProcess(cmd, 1, b"", b"crash"))
    with pytest.raises(er.RenderError) as e:
        er.render(_fixture_snapshot("1pages"), "pdf", out_dir, settings)
    assert e.value.code == "render_failed" and list(out_dir.iterdir()) == []


@pytest.fixture
def pdf_cli_process(tmp_path, monkeypatch):
    """완료 신호와 종료를 독립적으로 제어하는 실제 자식 프로세스. PDF 파서는 별도로 검사한다."""
    if not hasattr(os, "pread"):
        pytest.skip("macOS 출력 프로세스용 POSIX 검사")
    processes = []
    original = subprocess.Popen

    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(er.subprocess, "Popen", start)
    script = """
import sys, time
from pathlib import Path
mode, code = sys.argv[1:3]
profile = Path(next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--user-data-dir=')))
pdf = Path(next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--print-to-pdf=')))
pdf.write_bytes(b'fake PDF process fixture')
print('<html></html>' if mode != 'partial_dom' else '<html>', flush=True)
if mode != 'missing_marker':
    size = pdf.stat().st_size + (1 if mode == 'wrong_size' else 0)
    print(f'{size} bytes written to file {pdf}', file=sys.stderr, flush=True)
while not (profile / 'close').exists():
    time.sleep(0.01)
sys.exit(int(code))
"""

    def command(mode="complete", code=0):
        return [sys.executable, "-u", "-c", script, mode, str(code),
                f"--user-data-dir={tmp_path}", f"--print-to-pdf={tmp_path / 'output.pdf'}", "about:blank"]

    yield command, processes
    for process in processes:
        if process.poll() is None:
            er._kill_tree(process)
            process.wait(timeout=5)


@pytest.mark.parametrize("returncode", [0, 3])
def test_mac_pdf_waits_for_actual_exit(pdf_cli_process, tmp_path, monkeypatch, returncode):
    command, processes = pdf_cli_process
    closed = []

    def close(profile, deadline):
        assert processes[0].poll() is None
        closed.append(profile)
        (profile / "close").touch()

    monkeypatch.setattr(er, "_close_pdf_browser", close)
    result = er._run_mac_pdf(command(code=returncode), 3)
    assert result.returncode == returncode and processes[0].poll() == returncode
    assert result.stdout == b"<html></html>\n" and closed == [tmp_path]
    # 파일이 있더라도 비정상 종료는 상위 렌더러에서 성공으로 바꾸지 않는다.
    monkeypatch.setattr(er, "_run", lambda *args: result)
    if returncode:
        with pytest.raises(er.RenderError) as error:
            er.print_pdf_and_measure(Path("chrome"), tmp_path / "input.html", tmp_path / "output.pdf", tmp_path, 3)
        assert error.value.code == "render_failed"
    else:
        # 완료된 DOM도 측정 JSON이 없으면 배치 검사 성공으로 취급하지 않는다.
        assert er.print_pdf_and_measure(Path("chrome"), tmp_path / "input.html", tmp_path / "output.pdf", tmp_path, 3) is None


@pytest.mark.parametrize("mode", ["partial_dom", "missing_marker", "wrong_size"])
def test_mac_pdf_incomplete_output_times_out(pdf_cli_process, tmp_path, monkeypatch, mode):
    command, processes = pdf_cli_process
    closed = []
    monkeypatch.setattr(er, "_close_pdf_browser", lambda *args: closed.append(True))
    with pytest.raises(er.RenderError) as error:
        er._run_mac_pdf(command(mode), 1)
    assert error.value.code == "render_timeout"
    assert (tmp_path / "output.pdf").is_file() and not closed
    assert processes[0].poll() is not None  # 존재하는 PDF를 성공 처리하지 않고 프로세스 정리


@pytest.mark.parametrize("close_fails", [True, False])
def test_mac_pdf_close_failure_or_exit_timeout_stays_failed(pdf_cli_process, monkeypatch, close_fails):
    command, processes = pdf_cli_process

    def close(*args):
        if close_fails:
            raise OSError("browser control unavailable")
        # 종료 명령 성공 뒤에도 프로세스가 끝나지 않는 상황

    monkeypatch.setattr(er, "_close_pdf_browser", close)
    with pytest.raises(er.RenderError) as error:
        er._run_mac_pdf(command(), 1)
    assert error.value.code == ("render_failed" if close_fails else "render_timeout")
    assert processes[0].poll() is not None


def test_mac_pdf_natural_exit_during_close(pdf_cli_process, monkeypatch):
    command, processes = pdf_cli_process

    def close(profile, deadline):
        (profile / "close").touch()
        processes[0].wait(timeout=2)
        raise OSError("browser already exited before CDP connection")

    monkeypatch.setattr(er, "_close_pdf_browser", close)
    result = er._run_mac_pdf(command(), 3)
    assert result.returncode == 0 and result.stdout == b"<html></html>\n"


@pytest.mark.parametrize("endpoint", ["0\n/devtools/browser/id", "65536\n/devtools/browser/id",
                                      "1234\nws://example.com/", "1234\n/devtools/browser/id\nextra"])
def test_mac_pdf_rejects_invalid_control_endpoint(tmp_path, endpoint):
    (tmp_path / "DevToolsActivePort").write_text(endpoint, encoding="utf-8")
    with pytest.raises(ValueError, match="invalid browser control endpoint"):
        er._close_pdf_browser(tmp_path, time.monotonic() + 3)


def test_render_errors_without_browser(tmp_path, out_dir):
    with pytest.raises(er.RenderError) as e:
        er.render(_fixture_snapshot("1pages"), "pptx", out_dir)
    assert e.value.code == "unsupported_format"
    no_browser = Settings(private_runs_dir=tmp_path / "runs", db_path=tmp_path / "runs" / "t.sqlite3",
                          export_browser_path=str(tmp_path / "nope" / "chrome.exe"))
    assert er.find_browser(no_browser) is None
    with pytest.raises(er.RenderError) as e:
        er.render(_fixture_snapshot("1pages"), "pdf", out_dir, no_browser)
    assert e.value.code == "browser_not_found"
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


# ================= 규칙 =================

def test_required_not_checked_is_never_layout_ok():
    findings: list = []
    checks = [er._record("overflow", "docx", findings, not_checked_reason="docx_no_layout_engine"),
              er._record("broken_image", "docx", findings), er._record("placeholder_remaining", "docx", findings)]
    assert er._layout_ok(checks) is False and all(c.required for c in checks)
    assert er._layout_ok([er._record(k, "pdf", findings) for k in er.CHECK_KEYS]) is True
    assert er.REQUIRED_CHECKS["pdf"] == er.REQUIRED_CHECKS["docx"] == frozenset(er.CHECK_KEYS)


def test_browser_args_have_no_shell_and_layout_flags_are_fingerprinted(tmp_path):
    args = er._browser_args(Path("C:/x/chrome.exe"), tmp_path / "prof")
    assert Path(args[0]) == Path("C:/x/chrome.exe") and "--headless=new" in args and any(a.startswith("--user-data-dir=") for a in args)
    assert f"--window-size={er.PDF_RENDER_CONSTANTS['window_size']}" in args
    assert f"--virtual-time-budget={er.PDF_RENDER_CONSTANTS['virtual_time_budget_ms']}" in args
    assert "shell=True" not in inspect.getsource(er._run)


def test_no_real_company_terms_in_export_code_and_template():
    sources = [inspect.getsource(er), inspect.getsource(layout_checks), er.TEMPLATE_FILE.read_text(encoding="utf-8"),
               (er.FONT_DIR / "SOURCE.md").read_text(encoding="utf-8")]
    for src in sources:
        for banned in BANNED:
            assert banned not in src


# ================= 회귀: 스냅샷 격리·전체 디코딩·EXIF 방향·샌드박스 =================

def test_snapshot_is_isolated_from_document_mutation(app, settings, out_dir):
    """규칙: 스냅샷을 만든 뒤 원본 Document(페이지·블록·content dict)를 바꿔도 스냅샷·해시·출력은 그대로다."""
    flow = Flow(app)
    with connect(settings.db_path) as conn:
        doc = get_current(conn, flow.sid, flow.did)
        snap_db = er.build_snapshot(conn, settings, flow.sid, doc)
    doc_local = _fixture_doc("1pages")
    snap_local = er.snapshot_from_document(doc_local, {aid: er.asset_from_bytes(aid, (FIX / "ingest" / "images" / f"{aid}.png").read_bytes())
                                                       for aid in layout_checks.image_asset_ids(doc_local)})
    for snap, original in ((snap_db, doc), (snap_local, doc_local)):
        before_pages = [pg.model_dump() for pg in snap.pages]
        before_hash, before_manifest = snap.content_hash, snap.asset_manifest_hash
        before_html = er.build_html(snap)
        # 원본을 여러 방식으로 변경: content dict 직접 수정, 블록 추가, 페이지 제목 변경, 페이지 삭제, image asset_id 교체
        original.pages[0].blocks[0].content["text"] = "변경된 제목"
        original.pages[0].blocks.append(_blk("added", "paragraph", text="추가 블록"))
        original.pages[0].title = "바뀐 페이지"
        for b in original.pages[0].blocks:
            if b.type == "image":
                b.content["asset_id"] = "asset_swapped"
        del original.pages[-1:]
        assert [pg.model_dump() for pg in snap.pages] == before_pages
        assert snap.content_hash == before_hash and snap.asset_manifest_hash == before_manifest
        after_html = er.build_html(snap)
        assert "변경된 제목" not in after_html and "추가 블록" not in after_html and "asset_swapped" not in after_html
        assert len(after_html) == len(before_html)
    r = er.render(snap_local, "docx", out_dir)
    import docx

    text = "\n".join(pg.text for pg in docx.Document(str(r.file_path)).paragraphs)
    assert "[MOCK] 예시 회사 소개" in text and "변경된 제목" not in text and "추가 블록" not in text
    assert r.findings == [] and er.docx_info(r.file_path)["inline_shapes"] == 1


def test_truncated_jpeg_is_decode_failed_and_broken_image(out_dir):
    """헤더는 정상이지만 본문이 잘린 JPEG: 전체 픽셀 디코딩에서 걸려 decode_failed → 출력에는 깨진 이미지 상자."""
    full = _jpeg(240, 160)
    truncated = full[: int(len(full) * 0.6)]
    ok = er.asset_from_bytes("full", full)
    assert ok.ok and (ok.width, ok.height) == (240, 160) and ok.mime_type == "image/jpeg"
    bad = er.asset_from_bytes("cut", truncated)
    assert bad.ok is False and bad.reason == "decode_failed" and bad.data is None
    # 잘린 파일도 Pillow.open은 성공한다(헤더만) — 검증이 헤더에 그치면 놓치는 사례임을 같이 확인
    from PIL import Image

    with Image.open(io.BytesIO(truncated)) as img:
        assert img.size == (240, 160)
    pages = [Page(page_id="p", title="t", layout_key="text_photo", blocks=[
        _blk("i_cut", "image", asset_id="cut", alt="", caption="[MOCK] 잘린 JPEG", fit="contain"),
        _blk("i_ok", "image", asset_id="full", alt="", caption="[MOCK] 정상 JPEG", fit="contain")])]
    snap = er.snapshot_from_document(_doc(pages), {"cut": bad, "full": ok})
    r = er.render(snap, "docx", out_dir)
    assert [(f.kind, f.block_id, f.details["reason"]) for f in r.findings] == [("broken_image", "i_cut", "decode_failed")]
    assert er.docx_info(r.file_path)["inline_shapes"] == 1 and r.layout_ok is False
    html = er.build_html(snap)
    assert html.count("data:image/jpeg;base64,") == 1 and "[이미지를 열 수 없음]" in html and "decode_failed" in html


@pytest.mark.parametrize("size", [(400, 300), (3200, 2400)], ids=["under_limit", "over_limit"])
def test_embed_bytes_applies_exif_orientation_and_keeps_snapshot_bytes(size):
    """EXIF Orientation=6(시계 방향 90°)인 가로 JPEG는 세로로 삽입된다. 상한 이하·초과 모두 비율 유지, 스냅샷 바이트·해시 보존."""
    from PIL import Image

    width, height = size
    raw = _jpeg(width, height, orientation=6)
    asset = er.asset_from_bytes("exif", raw)
    assert asset.ok and (asset.width, asset.height) == (width, height)       # 스냅샷 크기는 파일의 픽셀 크기 그대로(DB와 같은 기준)
    data, w, h = er.embed_bytes(asset)
    expect_w, expect_h = (height, width) if max(size) <= er.IMAGE_MAX_PX else (int(height * er.IMAGE_MAX_PX / max(size)), er.IMAGE_MAX_PX)
    assert (w, h) == (expect_w, expect_h) and abs(w / h - height / width) < 0.01   # 세로 방향, 비율 유지
    assert data != raw and asset.data == raw and asset.content_hash == hashlib.sha256(raw).hexdigest()   # 원본 바이트·해시 보존
    with Image.open(io.BytesIO(data)) as img:
        assert img.format == "JPEG" and img.size == (w, h)
        assert img.getexif().get(er.EXIF_ORIENTATION_TAG, 1) == 1                # 돌린 사본에는 방향 태그가 남지 않는다(이중 회전 방지)
        # 원본의 왼쪽 위(파란 사분면)는 90° 회전 뒤 오른쪽 위로 간다
        assert img.getpixel((w - 2, 1))[2] > 150 and img.getpixel((1, h - 2))[0] > 150
    # 같은 입력을 두 번 처리하면 출력 바이트가 같다
    assert hashlib.sha256(er.embed_bytes(asset)[0]).hexdigest() == hashlib.sha256(data).hexdigest()


def test_embed_bytes_without_exif_keeps_bytes_and_is_deterministic():
    small = er.asset_from_bytes("s", _jpeg(400, 300))
    data, w, h = er.embed_bytes(small)
    assert data is small.data and (w, h) == (400, 300)
    big_png = er.asset_from_bytes("b", _png(er.IMAGE_MAX_PX * 2, 400))
    d1, w1, h1 = er.embed_bytes(big_png)
    d2, w2, h2 = er.embed_bytes(big_png)
    assert (w1, h1) == (w2, h2) == (er.IMAGE_MAX_PX, 200) and hashlib.sha256(d1).hexdigest() == hashlib.sha256(d2).hexdigest()
    assert big_png.data != d1 and big_png.content_hash == hashlib.sha256(big_png.data).hexdigest()
    big_jpg = er.asset_from_bytes("j", _jpeg(er.IMAGE_MAX_PX * 2, 800))
    j1, _, _ = er.embed_bytes(big_jpg)
    j2, _, _ = er.embed_bytes(big_jpg)
    assert hashlib.sha256(j1).hexdigest() == hashlib.sha256(j2).hexdigest() and big_jpg.data != j1


def test_browser_args_never_disable_sandbox(tmp_path, monkeypatch):
    args = er._browser_args(Path("/usr/bin/chromium"), tmp_path / "prof")
    assert not any(a.startswith("--no-sandbox") or a.startswith("--disable-setuid-sandbox") for a in args)
    if hasattr(os, "geteuid"):
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        args_root = er._browser_args(Path("/usr/bin/chromium"), tmp_path / "prof")
        assert "--no-sandbox" not in args_root
    assert '"--no-sandbox"' not in inspect.getsource(er._browser_args)   # 문자열 인자로 존재하지 않는다(주석 언급만 허용)


def test_demo_snapshot_identity_and_docx_footer(out_dir):
    import docx

    document = _doc([Page(page_id="demo_p", title="가상 안내", layout_key="text", blocks=[
        _blk("demo_b", "paragraph", text="예시 회사 시연용 임시 설명")])])
    normal = er.snapshot_from_document(document, {})
    demo = er.snapshot_from_document(document, {}, demo=True)
    assert not normal.demo and demo.demo and normal.content_hash != demo.content_hash
    assert er.DEMO_FOOTER_TEXT not in er.build_html(normal)
    html = er.build_html(demo)
    assert er.DEMO_FOOTER_TEXT in html and "@bottom-center" in html
    assert "margin-bottom: 24mm" in html and "var limit = 258 * PX_PER_MM" in html
    result = er.render(demo, "docx", out_dir / "demo")
    assert result.demo and result.to_dict()["demo"]
    doc = docx.Document(result.file_path)
    assert er.DEMO_FOOTER_TEXT in "\n".join(p.text for p in doc.sections[0].footer.paragraphs)
    assert round(doc.sections[0].bottom_margin.mm) == 24
    normal_doc = docx.Document(er.render(normal, "docx", out_dir / "normal").file_path)
    assert er.DEMO_FOOTER_TEXT not in "\n".join(p.text for p in normal_doc.sections[0].footer.paragraphs)


def test_instruction_image_is_not_accessible_in_render_snapshot(app, settings, out_dir):
    from app.services import assets

    flow = Flow(app)
    with connect(settings.db_path) as conn:
        document = get_current(conn, flow.sid, flow.did)
        aid = layout_checks.image_asset_ids(document)[0]
        source_id = conn.execute("SELECT source_id FROM assets WHERE asset_id=?", (aid,)).fetchone()[0]
        conn.execute("UPDATE sources SET role='instruction' WHERE source_id=?", (source_id,))
        assert assets.get_ready(conn, flow.sid, aid, settings=settings)["asset_id"] == aid  # 원본 첨부 조회는 유지
        snapshot = er.build_snapshot(conn, settings, flow.sid, document)
    assert not snapshot.assets[aid].ok and snapshot.assets[aid].reason == "not_accessible"
    result = er.render(snapshot, "docx", out_dir)
    assert any(f.kind == "broken_image" and f.details.get("reason") == "not_accessible" for f in result.findings)


@needs_browser
def test_demo_pdf_footer_on_every_physical_page_and_measured_space(out_dir, settings):
    from pypdf import PdfReader

    document = _doc([
        Page(page_id="demo_long", title="긴 본문", layout_key="text", blocks=[
            _blk("long_demo", "paragraph", text=" ".join([LONG_UNIT] * 150))]),
        Page(page_id="demo_last", title="마지막", layout_key="text", blocks=[
            _blk("last_demo", "paragraph", text="예시 회사 시연 마무리")]),
    ])
    result = er.render(er.snapshot_from_document(document, {}, demo=True), "pdf", out_dir, settings)
    pdf = PdfReader(result.file_path)
    assert result.demo and result.actual_pages == len(pdf.pages) and len(pdf.pages) > len(document.pages)
    footer = "".join(er.DEMO_FOOTER_TEXT.split())
    for page in pdf.pages:
        assert footer in "".join((page.extract_text() or "").split())
    assert result.details["measure"]["limit_px"] == pytest.approx(258 * 96 / 25.4)
    assert any(f.kind == "overflow" for f in result.findings)


def test_demo_pdf_missing_footer_is_rejected_before_publication(out_dir, monkeypatch):
    from pypdf import PdfWriter

    document = _doc([Page(page_id="demo_p", title="가상 안내", layout_key="text", blocks=[
        _blk("demo_b", "paragraph", text="예시 회사 시연용 임시 설명")])])
    monkeypatch.setattr(er, "find_browser", lambda *args: Path("fake-browser"))
    monkeypatch.setattr(er, "browser_version", lambda *args: "test/unsupported-margin-box")

    def print_without_footer(browser, html_path, pdf_path, profile_dir, timeout):
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.write(pdf_path)
        return None

    monkeypatch.setattr(er, "print_pdf_and_measure", print_without_footer)
    with pytest.raises(er.RenderError) as exc:
        er.render(er.snapshot_from_document(document, {}, demo=True), "pdf", out_dir)
    assert exc.value.code == "demo_footer_missing" and exc.value.details["page_numbers"] == [1]
    assert not list(out_dir.glob("*.pdf"))


def _brochure_snapshot(*, long_text=False):
    layouts = ["cover_photo", "text_photo", "process_steps", "product_grid", "contact_photo"]
    pages = [Page(page_id=f"page_{i}", title="가상 제품 소개", layout_key=key, blocks=[
        _blk(f"h_{i}", "heading", text="제품과 공정을 소개합니다", level=1),
        _blk(f"p_{i}", "paragraph", text=(LONG_UNIT * 100 if long_text else "가상 자료를 사용한 배치 시험입니다. 사진과 설명의 순서를 유지합니다.")),
        _blk(f"im_{i}", "image", asset_id="photo", alt="가상 이미지", caption="가상 제품 사진", fit="contain"),
        *([_blk(f"im2_{i}", "image", asset_id="photo", alt="가상 이미지", caption="두 번째 사진", fit="contain")]
          if key in {"process_steps", "product_grid"} else []),
        *([_blk(f"list_{i}", "list", items=["가상 품목 — 요청 수량 300개", "가상 검사 — 샘플 측정값이며 전량 결과 아님",
                                           "가상 도면 — 개정 A-01 확인", "확인 상태 — 외관 기준 합의 대기"])]
          if key in {"process_steps", "product_grid"} else []),
    ]) for i, key in enumerate(layouts)]
    return er.snapshot_from_document(_doc(pages), {"photo": er.asset_from_bytes("photo", _png(600, 400))}, demo=True)


def test_brochure_html_preserves_block_order_and_escapes_unknown_layout():
    snapshot = _brochure_snapshot()
    html = er.build_html(snapshot)
    for p in snapshot.pages:
        positions = [html.index(f'data-block-id="{b.block_id}"') for b in p.blocks]
        assert positions == sorted(positions)
        assert f'layout-{p.layout_key}' in html
    snapshot.pages[0].layout_key = '\"><script>alert(1)</script>'
    assert '<script>alert(1)</script>' not in er.build_html(snapshot)


@needs_browser
def test_brochure_pdf_all_layouts_keep_text_images_footer_and_page_count(out_dir):
    from pypdf import PdfReader
    result = er.render(_brochure_snapshot(), "pdf", out_dir)
    assert result.layout_ok and result.actual_pages == 5
    pdf = PdfReader(result.file_path)
    for p in pdf.pages:
        text = ''.join((p.extract_text() or '').split())
        assert '제품과공정을소개합니다' in text
        assert ''.join(er.DEMO_FOOTER_TEXT.split()) in text
        assert p.images
    all_text = ''.join(''.join((p.extract_text() or '').split()) for p in pdf.pages)
    assert '요청수량300개' in all_text and '전량결과아님' in all_text and '외관기준합의대기' in all_text


@needs_browser
def test_brochure_layout_never_hides_long_content_to_pass_overflow(out_dir):
    result = er.render(_brochure_snapshot(long_text=True), "pdf", out_dir)
    assert not result.layout_ok
    assert any(f.kind == "overflow" for f in result.findings)


@pytest.mark.parametrize("actual,matched", [
    ("Town-si 2026\nQty 12", True),
    ("Town\x02si 2026 Qty 12", False),
    ("Townsi 2026 Qty 12", False),
    ("Town-si 2025 Qty 12", False),
    ("Qty 12 Town-si 2026", False),
    ("Town-si 2026", False),
])
def test_docx_exact_page_text_keeps_hyphens_numbers_and_order(actual, matched):
    assert er._docx_page_text_matches(["Town-si 2026", "Qty 12"], actual) is matched


@pytest.mark.skipif(not LIBREOFFICE, reason="LibreOffice not installed")
def test_docx_long_process_page_keeps_photo_caption_and_address(settings, out_dir, monkeypatch):
    from dataclasses import replace
    import docx

    import pypdfium2 as pdfium
    original_text = pdfium.PdfTextPage.get_text_bounded

    def pdfium_line_end_hyphen(self, *args, **kwargs):
        return original_text(self, *args, **kwargs).replace("Town-si", "Town\x02si")

    monkeypatch.setattr(pdfium.PdfTextPage, "get_text_bounded", pdfium_line_end_hyphen)
    text = "가상 제조 공정은 소재와 표면 상태를 확인하고 지정한 작업 순서와 조건에 따라 검사합니다. "
    blocks = [_blk("title", "heading", text="제조 공정과 검사", level=1),
              _blk("intro", "paragraph", text=text)]
    for i in range(6):
        blocks.extend([_blk(f"h{i}", "heading", text=f"공정별 확인 항목 {i}", level=2),
                       _blk(f"t{i}", "paragraph", text=text + "주소는 Town-si, Example-gu이며 수량은 12개입니다.")])
    blocks.append(_blk("photo", "image", asset_id="image", caption="확인한 가상 시설 사진"))
    snapshot = er.snapshot_from_document(_doc([Page(page_id="p1", title="제조 공정", layout_key="text_photo", blocks=blocks)]),
                                        {"image": er.asset_from_bytes("image", _png(320, 210))}, demo=True)
    result = er.render(snapshot, "docx", out_dir, replace(settings, export_libreoffice_path=LIBREOFFICE))
    assert result.actual_pages == 1 and result.layout_ok, result.findings
    assert result.details["images_per_page"] == [1]
    editable = docx.Document(str(result.file_path))
    full_text = "\n".join(p.text for p in editable.paragraphs)
    assert full_text.count(text) == 7
    assert result.details["text_extraction_fallback_pages"] == [1]
    assert full_text.count("Town-si, Example-gu") == 6
    assert "확인한 가상 시설 사진" in full_text
    assert len(editable.inline_shapes) == 1
    assert abs(editable.inline_shapes[0].height.mm - 35.56) < 0.01


@needs_browser
def test_eval_saved_render_keeps_input_and_reports_target_difference(tmp_path, monkeypatch):
    from scripts.experiments import agent_quality_comparison as q
    doc = _doc([Page(page_id='p', title='가상 품질', layout_key='text', blocks=[
        _blk('h','heading',text='가상 품질',level=1),
        _blk('b','paragraph',text='가상 부품은 주문 조건을 확인한 뒤 검사합니다.')])])
    assets = tmp_path/'assets'; assets.mkdir()
    (assets/'asset_manifest.json').write_text('[]',encoding='utf-8')
    source = tmp_path/'document.json'; source.write_text(doc.model_dump_json(),encoding='utf-8')
    manifest = tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'documents':[{'label':'sample','document_path':'document.json','assets_dir':'assets'}]}),encoding='utf-8')
    before = source.read_bytes()
    monkeypatch.setattr(q.agent_llm,'LlmOptions',lambda *a,**kw: pytest.fail('offline replay requested model setup'))
    results = q.render_saved_comparison(manifest, tmp_path/'comparison')
    assert source.read_bytes() == before and results[0]['missing_text_blocks'] == []
    assert results[0]['layout_ok'] and results[0]['pages'] == 1 and not results[0]['target_pages_match']
    with pytest.raises(FileExistsError): q.render_saved_comparison(manifest, tmp_path/'comparison')


@needs_browser
def test_stage_runner_current_render_resume_and_regression_dispatch(tmp_path, monkeypatch):
    from scripts.experiments import stage_runner as runner
    doc = _doc([Page(page_id='p', title='가상 검사', layout_key='text', blocks=[
        _blk('h','heading',text='가상 검사',level=1),
        _blk('b','paragraph',text='가상 품목의 수량과 검사 조건을 기록합니다.')])])
    assets = tmp_path/'assets'; assets.mkdir()
    (assets/'asset_manifest.json').write_text('[]',encoding='utf-8')
    source = tmp_path/'document.json'; source.write_text(doc.model_dump_json(),encoding='utf-8')
    manifest = tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'documents':[{'label':'sample','document_path':'document.json','assets_dir':'assets'}]}),encoding='utf-8')
    before = source.read_bytes(); executions=[]
    # Actual regression is run by the outer suite; verify nested dispatch without recursive pytest.
    monkeypatch.setattr(pytest,'main',lambda args: executions.append(args) or 0)
    monkeypatch.chdir(Path.cwd())
    for name in ('TEMP','TMP','PYTHON_DOTENV_DISABLED','PYTHONDONTWRITEBYTECODE','PYTHONIOENCODING'):
        monkeypatch.setenv(name,os.environ.get(name,''))
    monkeypatch.setattr(sys,'dont_write_bytecode',sys.dont_write_bytecode)
    monkeypatch.setattr(sys,'path',list(sys.path))
    root=tmp_path/'run'
    common=['--manifest',str(manifest),'--run-dir',str(root),'--expected-template',layout_checks.TEMPLATE_VERSION]
    assert runner.main(['stage1',*common,'--docx-label','sample']) == 0
    record=json.loads((root/'baseline/results.json').read_text(encoding='utf-8'))
    assert record['api_calls'] == 0 and record['results'][0]['docx_smoke']['text_and_images_preserved']
    assert not record['results'][0]['target_pages_match']
    assert runner.main(['stage1',*common,'--docx-label','sample','--resume']) == 0
    assert runner.main(['stage2',*common]) == 0
    assert len(executions)==3 and all(set(runner.REGRESSION)<=set(args) for args in executions)
    assert source.read_bytes()==before
    (root/'baseline/sample/document.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='artifact'):
        runner.main(['stage1',*common,'--docx-label','sample','--resume'])
