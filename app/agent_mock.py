"""AGENT_MODE=mock 구현. 실제 LLM 호출 없이 계약 모양의 결과를 만든다.

- 값은 사용자가 올린 구간(segment) 텍스트에서 "회사명: …" 같은 단순 패턴으로만 뽑는다.
- 고정 문구는 전부 가짜("예시 회사", "예시 구성")다. 실제 회사 정보는 어디에도 넣지 않는다.
- 근거 없는 사실을 만들지 않는다. 못 찾은 항목은 missing이다.
- 일부러 async로 만들어 실행기가 awaitable을 다루는지 확인한다.
"""
from __future__ import annotations

import re

from app.agent_bridge import (AgentError, AnalyzeRequest, AnalyzeResult, DraftRequest, DraftResult, ProposeRequest,
                              ProposeResult, SourceIn)
from app.models import (Block, Candidate, EvidenceRef, Fact, Issue, OpDeleteBlock, OpInsertBlock,
                        OpReplaceBlockContent, Page, Recommendations)

# 옛 상세 회사정보 14개 키(contracts/profile.schema.json). D-05 변환표를 만들 때 이름을 그대로 쓴다.
FIELD_KEYS = ("company_name", "company_summary", "business_areas", "products_services", "technology",
              "strengths", "customers_markets", "certifications", "history", "processes", "process_count",
              "capabilities", "lead_time", "other_info")
# 구간 텍스트의 "라벨: 값" 패턴을 키로 잇는다. mock 전용의 단순 규칙이다.
LABELS = {
    "회사명": "company_name", "회사 개요": "company_summary", "사업 분야": "business_areas",
    "제품": "products_services", "기술": "technology", "강점": "strengths", "고객": "customers_markets",
    "인증": "certifications", "연혁": "history", "공정 목록": "processes", "공정 수": "process_count",
    "대응 범위": "capabilities", "납기": "lead_time",
}
REQUIRED = ("company_name", "company_summary")  # prd 6절: 최소 필수 = 회사명 + 주요 사업/공정 설명
MOCK_PREFIX = "[MOCK] "
FALLBACK_TITLE = "예시 회사"
_LABEL_RE = re.compile(r"^\s*([^:：]{1,20})\s*[:：]\s*(.+?)\s*$")


def _evidence(src: SourceIn, seg) -> EvidenceRef:
    return EvidenceRef(source_id=src.source_id, source_version=src.source_version, segment_id=seg.segment_id,
                       locator=seg.locator, excerpt=seg.text[:80])


def _collect(sources: list[SourceIn]) -> dict[str, list[tuple[str, EvidenceRef]]]:
    found: dict[str, list[tuple[str, EvidenceRef]]] = {k: [] for k in FIELD_KEYS}
    for src in sources:
        for seg in src.segments:
            # 등록 mock 자료의 "[MOCK] 회사명: …" — 라벨을 찾을 때만 접두어를 벗긴다. 저장된 text·excerpt는 그대로다.
            m = _LABEL_RE.match(seg.text.removeprefix(MOCK_PREFIX))
            if not m:
                continue
            label, value = m.group(1), m.group(2)
            for prefix, key in LABELS.items():
                if label.startswith(prefix):
                    found[key].append((value, _evidence(src, seg)))
                    break
    return found


class MockAgent:
    async def analyze(self, request: AnalyzeRequest) -> AnalyzeResult:
        found = _collect(request.sources)
        facts: list[Fact] = []
        issues: list[Issue] = []
        n = 0
        for key in FIELD_KEYS:
            hits = found[key]
            n += 1
            fact_id = f"fact_{n:03d}"
            if not hits:
                facts.append(Fact(fact_id=fact_id, field_key=key, value=None, status="missing"))
                continue
            values = {v for v, _ in hits}
            if len(values) > 1:
                facts.append(Fact(fact_id=fact_id, field_key=key, value=None, status="conflict",
                                  evidence_refs=[e for _, e in hits],
                                  alternatives=[{"value": v, "evidence_refs": [e.model_dump()]} for v, e in hits]))
                issues.append(Issue(issue_id=f"iss_{len(issues) + 1:03d}", scope="content", code="VALUE_CONFLICT",
                                    severity="blocker", message=f"{key} 값이 자료마다 다릅니다.",
                                    source_ids=sorted({e.source_id for _, e in hits}), fact_ids=[fact_id]))
                continue
            value = hits[0][0]
            if key == "lead_time" and not re.search(r"\d", value):
                facts.append(Fact(fact_id=fact_id, field_key=key, value=value, status="needs_confirmation",
                                  evidence_refs=[e for _, e in hits]))
                issues.append(Issue(issue_id=f"iss_{len(issues) + 1:03d}", scope="content",
                                    code="LEAD_TIME_UNQUANTIFIED", severity="warning",
                                    message="납기가 정성 표현뿐입니다. 기간·조건을 확인하거나 수치형 주장을 제외하세요.",
                                    source_ids=[hits[0][1].source_id], fact_ids=[fact_id]))
                continue
            facts.append(Fact(fact_id=fact_id, field_key=key, value=value, status="supported",
                              evidence_refs=[e for _, e in hits]))

        by_key = {f.field_key: f for f in facts}
        for key in REQUIRED:
            if by_key[key].status == "missing":
                issues.append(Issue(issue_id=f"iss_{len(issues) + 1:03d}", scope="content", code="REQUIRED_MISSING",
                                    severity="blocker", message=f"필수 항목({key})을 자료에서 찾지 못했습니다.",
                                    fact_ids=[by_key[key].fact_id]))
        for src in request.sources:
            if not src.segments and src.asset_ids:
                issues.append(Issue(issue_id=f"iss_{len(issues) + 1:03d}", scope="source", code="IMAGE_ONLY_SOURCE",
                                    severity="info", message=f"{src.name}은(는) 이미지만 있어 사실 근거로 쓰지 않았습니다.",
                                    source_ids=[src.source_id]))
        needed = [k for k in REQUIRED if by_key[k].status == "missing"]
        has_photo = any(src.asset_ids for src in request.sources)
        return AnalyzeResult(
            facts=facts, issues=issues,
            recommendations=Recommendations(
                suggested_pages=request.brief.target_pages,
                reason="(mock) 요청한 쪽수를 그대로 제안합니다. 실제 분석이 아닙니다.",
                needed=needed + ([] if has_photo or request.brief.photo_preference == "none" else ["제품·공정 사진"]),
            ),
        )

    async def draft(self, request: DraftRequest) -> DraftResult:
        supported = [f for f in request.preflight.facts if f.status == "supported"]
        by_key = {f.field_key: f for f in supported}
        title = by_key["company_name"].value if "company_name" in by_key else FALLBACK_TITLE
        asset_ids = [a for src in request.sources for a in src.asset_ids]
        pages: list[Page] = []
        n_blocks = 0

        def block(type_: str, content: dict, fact=None) -> Block:
            nonlocal n_blocks
            n_blocks += 1
            return Block(block_id=f"block_{n_blocks:03d}", type=type_, content=content,
                         fact_ids=[fact.fact_id] if fact else [],
                         evidence_refs=list(fact.evidence_refs) if fact else [])

        per_page = max(1, -(-len(supported) // request.brief.target_pages))  # 올림 나눗셈
        for p in range(request.brief.target_pages):
            blocks: list[Block] = []
            if p == 0:
                blocks.append(block("heading", {"text": title, "level": 1},
                                    by_key.get("company_name")))
            else:
                blocks.append(block("heading", {"text": f"예시 구성 {p + 1}", "level": 2}))
            chunk = supported[p * per_page:(p + 1) * per_page]
            for fact in chunk:
                blocks.append(block("paragraph", {"text": f"(mock) {fact.field_key}: {fact.value}"}, fact))
            if not chunk:
                # 근거 없는 문단은 만들지 않는다. 자리만 표시한다.
                blocks.append(block("paragraph", {"text": "추가 확인 필요"}))
            if p == 0 and request.brief.photo_preference != "none":
                if asset_ids:
                    blocks.append(block("image", {"asset_id": asset_ids[0], "alt": "자료 사진", "caption": "자료 사진",
                                                  "fit": "contain"}))
                else:
                    blocks.append(block("image_placeholder", {"description": "회사·제품 사진 자리"}))
            pages.append(Page(page_id=f"page_{p + 1:02d}", title=blocks[0].content["text"],
                              layout_key="text_photo" if p == 0 else "text", blocks=blocks))
        return DraftResult(title=title, pages=pages)

    # ---------------- 편집 보조 (BE-05 연결 규격용 mock) ----------------
    # text: 선택한 텍스트 블록마다 "(정리) " 표시 + 공백 정돈, 60자 넘으면 축약 표시. fact_ids·evidence_refs는 서버가 보존.
    # structure: mock 미지원 → AgentError. 빈 수정안으로 성공 처리하지 않는다.
    # image: 세션 asset마다 후보 1개. 후보 선택 전 문서는 바뀌지 않는다. asset이 없으면 AgentError.
    TEXT_TYPES = {"heading", "paragraph", "list"}
    IMAGE_TYPES = {"image", "image_placeholder"}
    SHORTEN_AT = 60

    async def propose(self, request: ProposeRequest) -> ProposeResult:
        blocks = {b.block_id: (page, b) for page in request.document.pages for b in page.blocks}
        targets = [blocks[bid] for bid in request.target_block_ids if bid in blocks]
        if len(targets) != len(request.target_block_ids):
            raise AgentError("UNSUPPORTED_PROPOSAL", "선택한 블록이 문서에 없습니다.")

        if request.kind == "structure":
            raise AgentError("UNSUPPORTED_PROPOSAL", "mock은 구성(structure) 편집안을 만들지 않습니다. 실제 Agent 연결(AG-05/06) 필요.")

        if request.kind == "text":
            if any(b.type not in self.TEXT_TYPES for _, b in targets):
                raise AgentError("UNSUPPORTED_PROPOSAL", "문구 편집은 heading·paragraph·list 블록에만 요청할 수 있습니다.")
            changes = []
            for _, b in targets:
                if b.type == "list":
                    items = [self._tidy(i) for i in b.content["items"]]
                    changes.append(OpReplaceBlockContent(op="replace_block_content", block_id=b.block_id,
                                                         content={"items": items}))
                elif b.type == "heading":
                    changes.append(OpReplaceBlockContent(op="replace_block_content", block_id=b.block_id,
                                                         content={"text": self._tidy(b.content["text"]),
                                                                  "level": b.content["level"]}))
                else:
                    changes.append(OpReplaceBlockContent(op="replace_block_content", block_id=b.block_id,
                                                         content={"text": self._tidy(b.content["text"])}))
            return ProposeResult(changes=changes,
                                 rationale="(mock) 문장 공백을 정돈하고 긴 문장은 축약 표시했습니다. 새 사실은 넣지 않았습니다.")

        # kind == image
        if any(b.type not in self.IMAGE_TYPES for _, b in targets):
            raise AgentError("UNSUPPORTED_PROPOSAL", "사진 편집은 image·image_placeholder 블록에만 요청할 수 있습니다.")
        asset_ids = [a for src in request.sources for a in src.asset_ids]
        if not asset_ids:
            raise AgentError("NO_IMAGE_CANDIDATES", "이 세션에 쓸 수 있는 사진이 없습니다. 사진을 올리거나 자리를 비워 두세요.")
        candidates: list[Candidate] = []
        for n, asset_id in enumerate(asset_ids, start=1):
            ops = []
            for page, b in targets:
                image_content = {"asset_id": asset_id, "alt": "자료 사진", "caption": "자료 사진", "fit": "contain"}
                if b.type == "image":
                    ops.append(OpReplaceBlockContent(op="replace_block_content", block_id=b.block_id, content=image_content))
                else:
                    # placeholder → image는 type이 바뀌므로 지우고 같은 자리에 넣는다.
                    idx = page.blocks.index(b)
                    after = page.blocks[idx - 1].block_id if idx > 0 else None
                    ops.append(OpDeleteBlock(op="delete_block", block_id=b.block_id))
                    ops.append(OpInsertBlock(op="insert_block", page_id=page.page_id, after_block_id=after,
                                             block=Block(block_id=f"{b.block_id}_img{n}", type="image", content=image_content)))
            candidates.append(Candidate(candidate_id=f"cand_{n:02d}", label=f"사진 후보 {n}", changes=ops))
        return ProposeResult(changes=[], rationale="(mock) 세션 사진마다 후보를 만들었습니다. 고르기 전에는 문서가 바뀌지 않습니다.",
                             candidates=candidates)

    def _tidy(self, text: str) -> str:
        tidy = " ".join(text.split())
        if tidy.startswith("(정리) "):
            tidy = tidy[len("(정리) "):]
        if len(tidy) > self.SHORTEN_AT:
            tidy = tidy[: self.SHORTEN_AT - 1] + "…"
        return "(정리) " + tidy
