"""BE-07 D-03 비교 실험 — 같은 가상 문서 4종을 PDF 후보별로 만들고 비교표를 남긴다. 앱 코드가 import하지 않는다.

실행: PYTHONIOENCODING=utf-8 uv run python scripts/experiments/be07_render_candidates.py [--out private_runs/be07]
후보: chrome_cli(시스템 Chrome/Edge --print-to-pdf, 정식 어댑터 경로) · playwright_chrome(playwright 패키지 + 시스템 Chrome)
      · reportlab · libreoffice(soffice 있을 때만). 설치되지 않은 후보는 '미실행/설치 제약'으로 기록한다.
문서: D1 fixture 1쪽 · D2 fixture 10쪽 · D3 스트레스(넘침·가로/세로/배너 사진·깨진 이미지·placeholder·HTML 문자) · D4 mock 업로드 PDF로 실제 흐름(mock Agent 초안).
출력은 --out 아래(private_runs, 커밋 금지). 실제 회사 자료는 쓰지 않는다. AI를 호출하지 않는다(mock).
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.models import Block, Document, Page  # noqa: E402
from app.services import export_render as er  # noqa: E402
from app.services import layout_checks  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "ddalgi_mock_bundle_v1"
UPLOAD_PDF = ROOT / "tests" / "fixtures" / "mock_upload_pdf"
LONG_UNIT = "[MOCK] 긴 문단의 줄바꿈과 배치 경고를 시험하는 가상 문장입니다."
KOREAN_PROBE = "한글 서체 확인 가나다라 ABC 123"


# ---------------- 문서 4종 ----------------

def _fixture_assets(doc: Document) -> dict[str, er.SnapshotAsset]:
    return {aid: er.asset_from_bytes(aid, (FIX / "ingest" / "images" / f"{aid}.png").read_bytes())
            for aid in layout_checks.image_asset_ids(doc) if (FIX / "ingest" / "images" / f"{aid}.png").is_file()}


def doc_fixture(name: str) -> er.RenderSnapshot:
    doc = Document.model_validate(json.loads((FIX / "ui" / "documents" / f"document_{name}.json").read_text(encoding="utf-8")))
    return er.snapshot_from_document(doc, _fixture_assets(doc))


def doc_stress() -> er.RenderSnapshot:
    def blk(bid, typ, **content):
        return Block(block_id=bid, type=typ, content=content)
    long_text = " ".join([LONG_UNIT] * 270)  # 약 10,800자 → A4 두 쪽 이상
    pages = [
        Page(page_id="s_p1", title="표지", layout_key="text_photo", blocks=[
            blk("s_h1", "heading", text=f"[MOCK] 스트레스 문서 — {KOREAN_PROBE}", level=1),
            blk("s_intro", "paragraph", text="예시 회사는 가상 부품의 표면처리와 검사 과정을 소개하는 테스트 기업입니다."),
            blk("s_list", "list", items=["가상 공정 A", "가상 공정 B", "가상 공정 C"]),
            blk("s_img_land", "image", asset_id="MOCK_IMG02", alt="[MOCK] 가로 사진", caption="[MOCK] 가로 960×640 (contain)", fit="contain"),
        ]),
        Page(page_id="s_p2", title="긴 본문(넘침)", layout_key="text", blocks=[
            blk("s_h2", "heading", text="긴 본문", level=2),
            blk("s_long", "paragraph", text=long_text),
            blk("s_html", "paragraph", text="<script>alert(1)</script> & <b>굵게</b> \"따옴표\" 'single' — 문자 그대로 보여야 함"),
        ]),
        Page(page_id="s_p3", title="사진 3종", layout_key="text_photo", blocks=[
            blk("s_h3", "heading", text="사진 3종", level=2),
            blk("s_img_port", "image", asset_id="MOCK_IMG03", alt="[MOCK] 세로 사진", caption="[MOCK] 세로 640×960 (contain)", fit="contain"),
            blk("s_img_wide", "image", asset_id="MOCK_IMG04", alt="[MOCK] 배너", caption="[MOCK] 가로 배너 1280×420 (crop)", fit="crop"),
            blk("s_img_broken", "image", asset_id="BROKEN_1", alt="[MOCK] 깨진 파일", caption="[MOCK] 깨진 PNG", fit="contain"),
        ]),
        Page(page_id="s_p4", title="사진 자리", layout_key="text", blocks=[
            blk("s_h4", "heading", text="남은 사진 자리", level=2),
            blk("s_ph", "image_placeholder", description="[MOCK] 사진을 추가할 자리"),
            blk("s_img_missing", "image", asset_id="NO_SUCH_ASSET", alt="", caption="[MOCK] 없는 asset", fit="contain"),
            blk("s_tail", "paragraph", text="마지막 문단입니다."),
        ]),
    ]
    doc = Document(document_id="doc_stress", session_id="sess_stress", document_revision=1, input_revision=1,
                   title="[MOCK] 스트레스 문서", target_pages=4, status="draft", pages=pages)
    assets = _fixture_assets(doc)
    assets["BROKEN_1"] = er.asset_from_bytes("BROKEN_1", b"\x89PNG\r\n\x1a\n" + b"broken-bytes" * 40)
    return er.snapshot_from_document(doc, assets)


def doc_flow(out: Path) -> tuple[er.RenderSnapshot, dict]:
    """mock 업로드 PDF + PNG를 실제 API 흐름(세션→업로드→선택→사전 점검→mock 초안)으로 돌려 DB에서 build_snapshot."""
    from fastapi.testclient import TestClient

    from app import create_app
    from app.config import Settings
    from app.db import connect
    from app.services.documents import get_current

    runs = out / "flow" / "runs"
    if runs.exists():
        shutil.rmtree(runs)
    settings = Settings(private_runs_dir=runs, db_path=runs / "t.sqlite3")
    app = create_app(settings)
    c = TestClient(app)
    brief = {"purpose": "테스트", "emphasis": [], "direction": "balanced", "target_pages": 4, "photo_preference": "balanced"}
    sid = c.post("/api/v1/sessions", json={"brief": brief}).json()["session_id"]
    pdf_path = next(UPLOAD_PDF.glob("*MOCK01*.pdf"))
    files = [("files", ("MOCK01.pdf", io.BytesIO(pdf_path.read_bytes()), "application/pdf")),
             ("files", ("MOCK_IMG01.png", io.BytesIO((FIX / "ingest" / "images" / "MOCK_IMG01.png").read_bytes()), "image/png"))]
    up = c.post(f"/api/v1/sessions/{sid}/sources", files=files).json()
    selected = [i["source_id"] for i in up["items"]]
    r = c.patch(f"/api/v1/sessions/{sid}/inputs", json={"expected_input_revision": 1, "selected_source_ids": selected}).json()
    rev_in = r["input_revision"]
    job = c.post(f"/api/v1/sessions/{sid}/preflights", json={"expected_input_revision": rev_in}).json()
    pf = c.get(f"/api/v1/sessions/{sid}/jobs/{job['job_id']}").json()["result_ref"]["preflight_id"]
    job = c.post(f"/api/v1/sessions/{sid}/drafts", json={"preflight_id": pf, "input_revision": rev_in, "confirmed": True}).json()
    did = c.get(f"/api/v1/sessions/{sid}/jobs/{job['job_id']}").json()["result_ref"]["document_id"]
    with connect(settings.db_path) as conn:
        doc = get_current(conn, sid, did)
        snap = er.build_snapshot(conn, settings, sid, doc)
        db_manifest = layout_checks.asset_manifest_hash(conn, doc)
    info = {"session_id": sid, "document_id": did, "pages": len(doc.pages), "blocks": sum(len(p.blocks) for p in doc.pages),
            "manifest_snapshot": snap.asset_manifest_hash, "manifest_db": db_manifest, "manifest_equal": snap.asset_manifest_hash == db_manifest}
    return snap, info


# ---------------- 후보 ----------------

def cand_chrome_cli(snap: er.RenderSnapshot, out: Path) -> dict:
    t = time.perf_counter()
    r = er.render(snap, "pdf", out)
    return {"ok": True, "pages": r.actual_pages, "elapsed_ms": int((time.perf_counter() - t) * 1000), "renderer": r.renderer,
            "fonts": r.details.get("fonts"), "overflow": [f.__dict__ for f in r.findings if f.kind == "overflow"],
            "layout_ok": r.layout_ok, "checks": [c.__dict__ for c in r.checks], "file": str(r.file_path),
            "measure_unit": "dom(px)", "page_count_method": "pypdf"}


def _in_thread(fn):
    """Playwright Sync API는 asyncio 루프가 있는 스레드에서 못 쓴다(TestClient가 루프를 남김). 전용 워커 스레드 1개에서만 실행한다."""
    try:
        return _PW_EXEC.submit(fn).result()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


import concurrent.futures as _cf  # noqa: E402

_PW_EXEC = _cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="be07-playwright")
_PW = {"pw": None, "browser": None}


def cand_playwright(snap: er.RenderSnapshot, out: Path, warm: bool) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "skipped": "미실행/설치 제약: playwright 미설치"}
    html = er.build_html(snap)
    out.mkdir(parents=True, exist_ok=True)
    html_path = out / f"{snap.document_id}.html"
    html_path.write_text(html, encoding="utf-8")
    pdf_path = out / f"{snap.document_id}.pdf"
    t = time.perf_counter()
    try:
        if warm and _PW["browser"] is None:
            _PW["pw"] = sync_playwright().start()
            _PW["browser"] = _PW["pw"].chromium.launch(channel="chrome", headless=True)
        if warm:
            browser = _PW["browser"]
            pw = None
        else:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.goto(html_path.as_uri())
        measure = page.evaluate("() => JSON.parse(document.getElementById('be07-measure').textContent)")
        page.pdf(path=str(pdf_path), prefer_css_page_size=True, print_background=True)
        version = browser.version
        page.close()
        if not warm:
            browser.close()
            pw.stop()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    elapsed = int((time.perf_counter() - t) * 1000)
    info = er.pdf_info(pdf_path)
    over = [p for p in measure["pages"] if p["overflow"]]
    return {"ok": True, "pages": info["pages"], "elapsed_ms": elapsed, "renderer": f"playwright/chrome {version}",
            "fonts": info["fonts"], "overflow": over, "file": str(pdf_path), "measure_unit": "dom(px)", "page_count_method": "pypdf"}


def _pw_close() -> None:
    if _PW["browser"] is not None:
        _PW["browser"].close()
        _PW["pw"].stop()
        _PW["browser"] = None


def _to_plain(snap: er.RenderSnapshot) -> dict:
    return {"title": snap.title,
            "pages": [{"page_id": p.page_id, "title": p.title,
                       "blocks": [{"block_id": b.block_id, "type": b.type, "content": dict(b.content)} for b in p.blocks]} for p in snap.pages],
            "assets": {aid: {"data": a.data, "width": a.width, "height": a.height, "ok": a.ok, "reason": a.reason} for aid, a in snap.assets.items()}}


def cand_reportlab(snap: er.RenderSnapshot, out: Path) -> dict:
    if render_reportlab is None:
        return {"ok": False, "skipped": "미실행/설치 제약: reportlab 미설치 또는 후보 코드 없음"}
    out.mkdir(parents=True, exist_ok=True)
    pdf_path = out / f"{snap.document_id}.pdf"
    r = render_reportlab(_to_plain(snap), str(pdf_path), str(er.FONT_FILES["regular"]), str(er.FONT_FILES["bold"]))
    r["file"] = str(pdf_path)
    r.setdefault("measure_unit", "flowable.wrap(pt)")
    r.setdefault("page_count_method", "pypdf")
    r["fonts"] = r.get("font_names")
    return r


def cand_libreoffice(docx_path: Path, out: Path) -> dict:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    for p in (r"C:\Program Files\LibreOffice\program\soffice.exe", r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        if Path(p).is_file():
            soffice = p
    if not soffice:
        return {"ok": False, "skipped": "미실행/설치 제약: LibreOffice(soffice) 미설치"}
    import subprocess

    out.mkdir(parents=True, exist_ok=True)
    t = time.perf_counter()
    cp = subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out), str(docx_path)], capture_output=True, timeout=180)
    pdf_path = out / (docx_path.stem + ".pdf")
    if cp.returncode != 0 or not pdf_path.is_file():
        return {"ok": False, "error": cp.stderr.decode("utf-8", "replace")[:300]}
    info = er.pdf_info(pdf_path)
    return {"ok": True, "pages": info["pages"], "elapsed_ms": int((time.perf_counter() - t) * 1000), "fonts": info["fonts"], "file": str(pdf_path),
            "overflow": "docx 변환이라 논리 페이지 넘침을 직접 재지 않음", "page_count_method": "pypdf"}


# ---------------- 후처리 ----------------

def rasterize(pdf_path: Path, png_dir: Path, tag: str, scale: float = 1.0) -> list[str]:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return ["미실행: pypdfium2 미설치"]
    png_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    names = []
    for i, page in enumerate(pdf, start=1):
        p = png_dir / f"{tag}_p{i}.png"
        bitmap = page.render(scale=scale)
        bitmap.to_pil().save(p)
        bitmap.close(); page.close()
        names.append(p.name)
    pdf.close()
    return names


def text_probe(pdf_path: Path) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    return {"has_hangul": any("가" <= ch <= "힣" for ch in text), "has_korean_probe": KOREAN_PROBE in text,
            "has_script_literal": "<script>" in text,
            "banned_words": [w for w in ("거산", "케미칼", "Geosan") if w in text], "chars": len(text)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "private_runs" / "be07"))
    ap.add_argument("--skip-playwright", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    docs: dict[str, er.RenderSnapshot] = {"D1_1pages": doc_fixture("1pages"), "D2_10pages": doc_fixture("10pages"), "D3_stress": doc_stress()}
    flow_snap, flow_info = doc_flow(out)
    docs["D4_flow"] = flow_snap
    results: dict = {"flow": flow_info, "docs": {}, "browser": None}
    browser = er.find_browser()
    results["browser"] = f"{browser} → {er.browser_version(browser)}" if browser else None

    for name, snap in docs.items():
        row: dict = {"logical_pages": len(snap.pages), "manifest": snap.asset_manifest_hash, "candidates": {}}
        # DOCX(어댑터)
        r = er.render(snap, "docx", out / "docx")
        row["docx"] = {"file": str(r.file_path), "actual_pages": r.actual_pages, "layout_ok": r.layout_ok,
                       "checks": [c.__dict__ for c in r.checks], "findings": [f.__dict__ for f in r.findings], "details": r.details, "elapsed_ms": r.elapsed_ms}
        docx_path = r.file_path
        # PDF 후보
        try:
            row["candidates"]["chrome_cli"] = cand_chrome_cli(snap, out / "chrome_cli")
        except er.RenderError as exc:
            row["candidates"]["chrome_cli"] = {"ok": False, "error": f"{exc.code}: {exc.message}"}
        if not args.skip_playwright:
            row["candidates"]["playwright_cold"] = _in_thread(lambda: cand_playwright(snap, out / "playwright", warm=False))
            row["candidates"]["playwright_warm"] = _in_thread(lambda: cand_playwright(snap, out / "playwright_warm", warm=True))
        row["candidates"]["reportlab"] = cand_reportlab(snap, out / "reportlab")
        row["candidates"]["libreoffice"] = cand_libreoffice(docx_path, out / "libreoffice")
        for cand, res in row["candidates"].items():
            if res.get("ok") and res.get("file"):
                res["png"] = rasterize(Path(res["file"]), out / "png", f"{cand}_{name}")
                res["text"] = text_probe(Path(res["file"]))
        results["docs"][name] = row
        print(f"[{name}] logical={len(snap.pages)} " + " | ".join(
            f"{c}: {('p=%s %sms' % (v.get('pages'), v.get('elapsed_ms'))) if v.get('ok') else v.get('skipped') or v.get('error')}"
            for c, v in row["candidates"].items()), flush=True)
    _in_thread(_pw_close)

    def _json(o):
        if isinstance(o, bytes):
            return f"<{len(o)} bytes>"
        if isinstance(o, Path):
            return str(o)
        return str(o)
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1, default=_json), encoding="utf-8")
    print("written", out / "results.json")


# --- reportlab 후보 (서브에이전트 초안을 인라인; reportlab이 없으면 후보를 건너뛴다) ---
try:
    import io
    import time
    import traceback
    from typing import Any
    from xml.sax.saxutils import escape as _xml_escape

    from reportlab import rl_config
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        BaseDocTemplate,
        Flowable,
        Frame,
        KeepTogether,
        PageBreak,
        PageTemplate,
        Paragraph,
    )
    from reportlab.platypus import Image as RLImage

    # --------------------------------------------------------------------------
    # Template v0 constants (points unless stated otherwise)
    # --------------------------------------------------------------------------
    PAGE_W, PAGE_H = A4
    MARGIN = 15 * mm
    CONTENT_W = 180 * mm  # 510.24 pt
    CONTENT_H = 267 * mm  # 756.85 pt
    IMG_MAX_W = 180 * mm
    IMG_MAX_H = 120 * mm
    BOX_W = 180 * mm
    BOX_H = 40 * mm

    BASE_FONT = "Pretendard"
    BOLD_FONT = "Pretendard-Bold"
    BASE_SIZE = 10.5
    BASE_LEADING = round(BASE_SIZE * 1.55, 2)  # 16.28 pt
    GREY = colors.Color(0.45, 0.45, 0.45)


    # --------------------------------------------------------------------------
    # Helpers
    # --------------------------------------------------------------------------
    def _esc(text: Any) -> str:
        """Escape untrusted text so Paragraph never interprets it as markup."""
        if text is None:
            return ""
        return _xml_escape(str(text))


    def _register_fonts(font_regular: str, font_bold: str) -> None:
        pdfmetrics.registerFont(TTFont(BASE_FONT, font_regular))
        pdfmetrics.registerFont(TTFont(BOLD_FONT, font_bold))
        pdfmetrics.registerFontFamily(
            BASE_FONT,
            normal=BASE_FONT,
            bold=BOLD_FONT,
            italic=BASE_FONT,  # Pretendard has no italic face
            boldItalic=BOLD_FONT,
        )


    def _build_styles() -> dict[str, ParagraphStyle]:
        body = ParagraphStyle(
            "tv0-body",
            fontName=BASE_FONT,
            fontSize=BASE_SIZE,
            leading=BASE_LEADING,
            wordWrap="CJK",
            alignment=TA_LEFT,
            spaceBefore=0,
            spaceAfter=6,
        )
        styles: dict[str, ParagraphStyle] = {"body": body}
        styles["h1"] = ParagraphStyle(
            "tv0-h1", parent=body, fontName=BOLD_FONT, fontSize=20, leading=26,
            spaceBefore=12, spaceAfter=8,
        )
        styles["h2"] = ParagraphStyle(
            "tv0-h2", parent=body, fontName=BOLD_FONT, fontSize=14, leading=19,
            spaceBefore=10, spaceAfter=5,
        )
        styles["h3"] = ParagraphStyle(
            "tv0-h3", parent=body, fontName=BOLD_FONT, fontSize=12, leading=16.5,
            spaceBefore=8, spaceAfter=4,
        )
        styles["bullet"] = ParagraphStyle(
            "tv0-bullet", parent=body, leftIndent=14, bulletIndent=3,
            bulletFontName=BASE_FONT, bulletFontSize=BASE_SIZE, spaceAfter=2,
        )
        styles["label"] = ParagraphStyle(
            "tv0-label", parent=body, fontSize=8, leading=10, textColor=GREY,
            spaceAfter=6,
        )
        styles["caption"] = ParagraphStyle(
            "tv0-caption", parent=body, fontSize=9, leading=12.5, textColor=GREY,
            alignment=TA_CENTER, spaceBefore=3, spaceAfter=8,
        )
        styles["box"] = ParagraphStyle(
            "tv0-box", parent=body, textColor=GREY, spaceBefore=0, spaceAfter=0,
        )
        styles["note"] = ParagraphStyle(
            "tv0-note", parent=body, fontSize=9, leading=12.5, textColor=GREY,
        )
        return styles


    class DashedBox(Flowable):
        """Fixed-size box with a dashed grey border and grey text inside.

        Used for broken images and image placeholders. Text is drawn top-aligned
        when it is taller than the box and clipped to the box, so untrusted long
        descriptions can never spill outside the 180 x 40 mm area.
        """

        def __init__(self, escaped_text: str, style: ParagraphStyle,
                     width: float = BOX_W, height: float = BOX_H,
                     pad: float = 4 * mm) -> None:
            Flowable.__init__(self)
            self._w = width
            self._h = height
            self._pad = pad
            self._para = Paragraph(escaped_text, style)
            self.hAlign = "CENTER"
            self.spaceBefore = 0
            self.spaceAfter = 8

        def wrap(self, availWidth: float, availHeight: float):  # noqa: N803
            self.width = min(self._w, availWidth)
            self.height = self._h
            return self.width, self.height

        def draw(self) -> None:
            c = self.canv
            c.saveState()
            c.setStrokeColor(GREY)
            c.setLineWidth(0.8)
            c.setDash([4, 3], 0)
            c.rect(0, 0, self.width, self.height, stroke=1, fill=0)

            inner_w = self.width - 2 * self._pad
            inner_h = self.height - 2 * self._pad
            clip = c.beginPath()
            clip.rect(self._pad, self._pad, inner_w, inner_h)
            c.clipPath(clip, stroke=0, fill=0)

            _pw, ph = self._para.wrap(inner_w, inner_h)
            if ph <= inner_h:
                y = self._pad + (inner_h - ph) / 2.0  # vertically centred
            else:
                y = self._pad + inner_h - ph  # top-aligned, rest is clipped
            self._para.drawOn(c, self._pad, y)
            c.restoreState()


    def _build_image(data: bytes, fit: str, notes: list[str]) -> RLImage:
        """Return a reportlab Image sized to fit 180 x 120 mm (contain/crop)."""
        from PIL import Image as PILImage

        pil = PILImage.open(io.BytesIO(data))
        pil.load()
        w, h = pil.size
        if w <= 0 or h <= 0:
            raise ValueError("empty image")

        src: io.BytesIO
        if fit == "crop":
            target = IMG_MAX_W / IMG_MAX_H  # 1.5
            cur = w / h
            if abs(cur - target) > 1e-6:
                if cur > target:
                    new_w = max(1, int(round(h * target)))
                    left = (w - new_w) // 2
                    pil = pil.crop((left, 0, left + new_w, h))
                else:
                    new_h = max(1, int(round(w / target)))
                    top = (h - new_h) // 2
                    pil = pil.crop((0, top, w, top + new_h))
            if pil.mode not in ("RGB", "RGBA", "L", "LA"):
                pil = pil.convert("RGB")
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            buf.seek(0)
            src = buf
            w, h = pil.size
            if "crop=center-crop(Pillow)->180x120mm" not in notes:
                notes.append("crop=center-crop(Pillow)->180x120mm")
        else:
            src = io.BytesIO(data)

        scale = min(IMG_MAX_W / w, IMG_MAX_H / h)  # contain (may upscale)
        dw, dh = w * scale, h * scale
        img = RLImage(src, width=dw, height=dh, kind="direct", lazy=0,
                      hAlign="CENTER")
        img.spaceBefore = 0
        img.spaceAfter = 4
        return img


    def _image_flowables(content: dict, assets: dict, styles: dict,
                         notes: list[str]) -> list[Flowable]:
        asset_id = str(content.get("asset_id", "") or "")
        fit = str(content.get("fit", "contain") or "contain")
        caption = content.get("caption") or ""
        asset = (assets or {}).get(asset_id)

        flows: list[Flowable] = []
        reason: str | None = None
        if not isinstance(asset, dict):
            reason = "asset_not_found"
        elif not asset.get("ok"):
            reason = str(asset.get("reason") or "not_ok")
        elif not asset.get("data"):
            reason = str(asset.get("reason") or "no_data")

        if reason is None:
            try:
                flows.append(_build_image(asset["data"], fit, notes))
            except Exception as exc:  # decode / crop failure -> dashed box
                reason = f"decode_failed: {type(exc).__name__}"

        if reason is not None:
            flows.append(DashedBox(
                _esc(f"[이미지를 열 수 없음] {asset_id} ({reason})"), styles["box"]))

        if caption:
            flows.append(Paragraph(_esc(caption), styles["caption"]))
        if len(flows) > 1:
            # Keep the image (or its fallback box) on the same physical page as
            # its caption. KeepTogether.wrap() returns a sentinel height to force
            # a split, so the overflow pre-pass measures its inner content.
            return [KeepTogether(flows)]
        return flows


    def _block_flowables(block: dict, assets: dict, styles: dict,
                         notes: list[str]) -> list[Flowable]:
        btype = block.get("type")
        content = block.get("content") or {}
        if not isinstance(content, dict):
            content = {}

        if btype == "heading":
            try:
                level = int(content.get("level", 1))
            except (TypeError, ValueError):
                level = 1
            level = min(3, max(1, level))
            return [Paragraph(_esc(content.get("text", "")), styles[f"h{level}"])]

        if btype == "paragraph":
            return [Paragraph(_esc(content.get("text", "")), styles["body"])]

        if btype == "list":
            items = content.get("items") or []
            out: list[Flowable] = []
            for item in items:
                out.append(Paragraph(_esc(item), styles["bullet"], bulletText="\u2022"))
            if out:
                out[-1].spaceAfter = 6  # close the list like a paragraph
            return out

        if btype == "image":
            return _image_flowables(content, assets, styles, notes)

        if btype == "image_placeholder":
            desc = content.get("description", "")
            return [DashedBox(_esc(f"[사진 자리] {desc}"), styles["box"])]

        return [Paragraph(_esc(f"[unsupported block type: {btype}]"), styles["note"])]


    def _measure_logical_page(items: list[tuple[str | None, Flowable]]):
        """Real wrap() pass. Returns (content_height_pt, first_overflow_block_id).

        Mirrors reportlab Frame._add spacing: spaceBefore is ignored for the
        first flowable on the page; with rl_config.overlapAttachedSpace (default
        on) the gap between two flowables is max(prev spaceAfter, spaceBefore);
        the trailing spaceAfter of the last flowable is not counted as content.
        KeepTogether groups are measured through their inner flowables because
        KeepTogether.wrap() returns a sentinel height (0xffffff) by design.
        """
        merge = bool(getattr(rl_config, "overlapAttachedSpace", 1))
        y = 0.0
        bottom_max = 0.0
        prev_sa = 0.0
        at_top = True
        first_overflow: str | None = None
        last_block: str | None = None
        for block_id, fl in items:
            leaves = list(fl._content) if isinstance(fl, KeepTogether) else [fl]
            if block_id is not None:
                last_block = block_id
            for leaf in leaves:
                if at_top:
                    sb = 0.0
                else:
                    sb = float(leaf.getSpaceBefore() or 0.0)
                    if merge:
                        sb = max(sb - prev_sa, 0.0)
                _w, h = leaf.wrap(CONTENT_W, CONTENT_H)
                bottom = y + sb + h
                if first_overflow is None and bottom > CONTENT_H + 1e-6:
                    first_overflow = block_id if block_id is not None else last_block
                bottom_max = max(bottom_max, bottom)
                sa = float(leaf.getSpaceAfter() or 0.0)
                y = bottom + sa
                prev_sa = sa
                at_top = False
        return bottom_max, first_overflow


    def _page1_font_names(reader) -> list[str]:
        page = reader.pages[0]
        node: Any = page
        res = None
        for _ in range(32):  # walk up /Parent chain for inherited resources
            if node is None:
                break
            if "/Resources" in node:
                res = node["/Resources"].get_object()
                break
            parent = node.get("/Parent")
            node = parent.get_object() if parent is not None else None
        names: set[str] = set()
        if res is not None and "/Font" in res:
            fonts = res["/Font"].get_object()
            for key, ref in fonts.items():
                fo = ref.get_object()
                base = fo.get("/BaseFont")
                names.add(str(base if base is not None else key).lstrip("/"))
        return sorted(names)


    # --------------------------------------------------------------------------
    # Public entry point
    # --------------------------------------------------------------------------
    def render_reportlab(snapshot: dict, out_path: str, font_regular: str,
                         font_bold: str) -> dict:
        t0 = time.perf_counter()
        result: dict[str, Any] = {
            "ok": False,
            "pages": None,
            "overflow": [],
            "elapsed_ms": 0,
            "font_names": [],
            "notes": "",
            "error": None,
        }
        notes: list[str] = [
            "reportlab BaseDocTemplate + single Frame(padding=0), A4, 15mm margins",
            "wordWrap=CJK; text escaped (&,<,>) before Paragraph",
            "dashed boxes via custom Flowable (canvas.setDash)",
            "images: contain within 180x120mm (upscales small bitmaps)",
            "PDF /Author set to empty string (reportlab always writes the key)",
            "overflow: pre-pass wrap() against 180x267mm mirroring Frame spacing, "
            "trailing spaceAfter excluded",
            "image/box + caption grouped with KeepTogether (no orphaned captions)",
        ]
        try:
            _register_fonts(font_regular, font_bold)
            styles = _build_styles()
            assets = snapshot.get("assets") or {}
            title = str(snapshot.get("title") or "")

            story: list[Flowable] = []
            overflow: list[dict] = []
            pages = snapshot.get("pages") or []
            for p_idx, page in enumerate(pages):
                page_id = str(page.get("page_id", f"page-{p_idx + 1}"))
                page_title = page.get("title", "") or ""
                items: list[tuple[str | None, Flowable]] = []
                items.append((None, Paragraph(_esc(page_title), styles["label"])))
                for b_idx, block in enumerate(page.get("blocks") or []):
                    block_id = str(block.get("block_id", f"{page_id}-b{b_idx + 1}"))
                    for fl in _block_flowables(block, assets, styles, notes):
                        items.append((block_id, fl))

                content_h, first_overflow = _measure_logical_page(items)
                if content_h > CONTENT_H + 1e-6:
                    overflow.append({
                        "page_id": page_id,
                        "first_overflow_block_id": first_overflow,
                        "excess_mm": round((content_h - CONTENT_H) / mm, 1),
                    })

                if p_idx > 0:
                    story.append(PageBreak())
                story.extend(fl for _bid, fl in items)

            doc = BaseDocTemplate(
                out_path,
                pagesize=A4,
                leftMargin=MARGIN,
                rightMargin=MARGIN,
                topMargin=MARGIN,
                bottomMargin=MARGIN,
                title=title,
                author="",
                subject="",
                creator="candidate_reportlab",
                initialFontName=BASE_FONT,  # keeps Helvetica out of page resources
                initialFontSize=BASE_SIZE,
                initialLeading=BASE_LEADING,
            )
            frame = Frame(
                MARGIN, MARGIN, CONTENT_W, CONTENT_H,
                leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0,
                id="main",
            )
            doc.addPageTemplates([PageTemplate(id="main", frames=[frame])])
            doc.build(story)

            from pypdf import PdfReader

            reader = PdfReader(out_path)
            result["pages"] = len(reader.pages)
            result["font_names"] = _page1_font_names(reader)
            result["overflow"] = overflow
            result["ok"] = True
        except Exception as exc:  # never raise; report instead
            result["error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            result["ok"] = False
        finally:
            result["elapsed_ms"] = int(round((time.perf_counter() - t0) * 1000))
            result["notes"] = "; ".join(notes)
        return result


    # --------------------------------------------------------------------------
    # Demo
    # --------------------------------------------------------------------------
except ImportError:
    render_reportlab = None

if __name__ == "__main__":
    main()
