"""AGENT_MODE=mock 구현. 실제 LLM 호출 없이 계약 모양의 결과를 만든다.

- 값은 사용자가 올린 구간(segment) 텍스트에서 "회사명: …" 같은 단순 패턴으로만 뽑는다.
- 고정 문구는 전부 가짜("예시 회사", "예시 구성")다. 실제 회사 정보는 어디에도 넣지 않는다.
- 근거 없는 사실을 만들지 않는다. 못 찾은 항목은 missing이다.
- 일부러 async로 만들어 실행기가 awaitable을 다루는지 확인한다.
"""
from __future__ import annotations

import re

from app.agent_bridge import AnalyzeRequest, AnalyzeResult, DraftRequest, DraftResult, SourceIn
from app.models import Block, EvidenceRef, Fact, Issue, Page, Recommendations

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
FALLBACK_TITLE = "예시 회사"
_LABEL_RE = re.compile(r"^\s*([^:：]{1,20})\s*[:：]\s*(.+?)\s*$")


def _evidence(src: SourceIn, seg) -> EvidenceRef:
    return EvidenceRef(source_id=src.source_id, source_version=src.source_version, segment_id=seg.segment_id,
                       locator=seg.locator, excerpt=seg.text[:80])


def _collect(sources: list[SourceIn]) -> dict[str, list[tuple[str, EvidenceRef]]]:
    found: dict[str, list[tuple[str, EvidenceRef]]] = {k: [] for k in FIELD_KEYS}
    for src in sources:
        for seg in src.segments:
            m = _LABEL_RE.match(seg.text)
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
