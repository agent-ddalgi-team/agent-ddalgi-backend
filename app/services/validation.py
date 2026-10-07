"""검증(Validation) — 서버 일반 검사, 부분 재검증, Issue 정체성·기록, 문서 상태 계산.

AI 의미 검증(Agent validate)과 분리된 서버 검사:
  REQUIRED_MISSING   회사명·주요 사업/공정이 실제 블록에 있는지(fact_ids만으로 통과 안 함)   blocker
  UNSUPPORTED_CLAIM  근거 없는 사실 주장(계약 확인 ㉛)                                           blocker, 확인 클릭 불가
  PLACEHOLDER_TEXT   "추가 확인 필요"/"자료에서 확인되지 않음" 문단                                 warning
  VALUE_CONFLICT     사전 점검의 미해결 충돌을 문서 전체와 참조 블록에 연결                          blocker
  EVIDENCE_INVALID   근거가 지금 세션 자료에 없음                                                   blocker
  MOCK_VALUE         본문/캡션 텍스트·근거 원문·원출처(is_mock)·이미지 원출처 중 하나라도 mock           blocker, excluded·acknowledged 불가

부분 재검증: 마지막 유효 검증(같은 문서·같은 입력 버전·이전 revision)의 블록 지문과 서버가 비교한다.
지문이 같은 블록의 Agent 검사·Issue·해결 기록은 재사용하고, 지문 집합이 같으면 Agent를 부르지 않는다.
Document.status는 저장값이 아니라 compute_document_status()가 현재 검증·승인으로 계산한다.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from app.agent_bridge import SourceIn
from app.db import Connection, Row
from app.models import (Block, Brief, CheckRecord, Document, EvidenceRef, Fact, Issue, IssueOut, PreflightOut,
                        ValidationOut)
from app.services import refs as refs_service
from app.timeutil import now, to_iso

PLACEHOLDERS = {"추가 확인 필요", "자료에서 확인되지 않음"}
MOCK_LABEL = "[MOCK]"
REQUIRED_NAME_KEYS = ("company_name",)
REQUIRED_BUSINESS_KEYS = ("company_summary", "business_areas", "processes", "products_services", "technology")
NON_ACKNOWLEDGEABLE = {"MOCK_VALUE", "UNSUPPORTED_CLAIM", "REQUIRED_MISSING", "VALUE_CONFLICT", "EVIDENCE_INVALID",
                      "UNVERIFIED_SUPERLATIVE", "VALUE_MISMATCH", "CONDITION_LOSS", "CERTIFICATION_MISMATCH",
                      "IMAGE_MISMATCH", "IMAGE_UNVERIFIABLE"}
LAYOUT_ISSUE_CODES = {"LAYOUT_OVERFLOW", "BROKEN_IMAGE", "PLACEHOLDER_REMAINING", "IMAGE_PUBLICATION_UNCONFIRMED"}   # BE-08 ㉜·㊱
NON_EXCLUDABLE = {"MOCK_VALUE", "REQUIRED_MISSING"} | LAYOUT_ISSUE_CODES

# 계약 확인 ㉛ — 비사실 안내·연결 문장의 제한된 규칙: 아래 머리말로 시작하고 서술형으로 끝나며 숫자·%·주장 키워드가 없고 40자 이하.
_CONNECTOR_HEAD = re.compile(r"^(다음은|아래는|이어서|이 장에서는|이 페이지에서는|본 자료는|여기서는|다음 페이지에서는)")
_CONNECTOR_TAIL = re.compile(r"(입니다|합니다|살펴봅니다|소개합니다|안내합니다|정리합니다)\.?$")
# heading·캡션이 '표지/라벨'이 아니라 사실 주장인지 판단하는 키워드(있으면 주장으로 본다).
_CLAIM_KEYWORDS = ("인증", "납기", "최고", "1위", "보장", "특허", "ISO", "최대", "최소", "이상", "이하", "년",
                   "개", "톤", "㎡", "억", "만", "명", "위", "국내", "세계", "최초", "유일", "품질", "정밀")
_DIGIT = re.compile(r"[0-9%]")
LABEL_MAX_LEN = 20
CONNECTOR_MAX_LEN = 40


def sequence_evidence_supported(layout: str, evidence: str,
                                fact_evidence: list[tuple[str, list[str]]]) -> bool:
    """Shared draft/review rule; table years and ordered step headings are evidence too."""
    pattern = (r"(?:\d{4}년|\d{4}[-./]\d{1,2})" if layout == "timeline"
               else r"(?:→|->|\d+[.)]\s|먼저.+다음|후에|이후)")
    if re.search(pattern, evidence):
        return True
    for field, excerpts in fact_evidence:
        if layout == "timeline" and field == "history" and any(
                re.search(r"(?m)^\s*(?:19|20)\d{2}\s*[|｜]\s*\S", text) for text in excerpts):
            return True
        if layout == "process_steps" and field == "processes":
            steps = [int(value) for text in excerpts for value in re.findall(
                r"(?<!\w)(?:제[^\S\r\n]*)?([1-9]\d{0,2})[^\S\r\n]*단계[^\S\r\n]*[:：—–-][^\S\r\n]*\S", text)]
            if len(steps) >= 2 and all(a < b for a, b in zip(steps, steps[1:])):
                return True
    return False


def block_texts(block: Block) -> list[str]:
    c = block.content
    if block.type in ("heading", "paragraph"):
        return [str(c.get("text", ""))]
    if block.type == "list":
        return [str(i) for i in c.get("items", [])]
    if block.type == "image":
        return [str(c.get("caption", "")), str(c.get("alt", ""))]
    return [str(c.get("description", ""))]


def is_placeholder(text: str) -> bool:
    return text.strip() in PLACEHOLDERS


def is_connector(text: str) -> bool:
    """명확한 비사실 안내·연결 문장(㉛). 제한된 규칙이라 애매하면 주장으로 본다."""
    t = text.strip()
    return (len(t) <= CONNECTOR_MAX_LEN and not _DIGIT.search(t) and bool(_CONNECTOR_HEAD.match(t))
            and bool(_CONNECTOR_TAIL.search(t)) and not any(k in t for k in _CLAIM_KEYWORDS))


_SECTION_NUMBER = re.compile(r"^\s*\d+[.)]?\s*|\s+\d+\s*$")   # "2. 개요", "개요 2" 같은 절 번호
# 기존 초안의 비사실 제목 중 주장 키워드(명·개·위 등)와 겹치는 표현만 정확히 허용한다.
# Agent 모듈·mock fixture에 서버 검사를 의존시키지 않는다. 전체 생성 제목은 연결 테스트로 대조한다.
_SECTION_LABELS = frozenset({"회사명", "회사소개서 초안", "회사 개요", "인증·승인·특허", "대응 범위", "납기 조건",
                            # '소개/개요/설명'의 개·명은 수량 단위가 아니다. 정확한 항목명만 허용한다.
                            "회사 소개", "기업 소개", "제품 소개", "서비스 소개", "사업 소개",
                            "사업 개요", "기업 개요", "제품 설명"})


def is_label(text: str) -> bool:
    """절 번호를 뺀 정확한 항목 제목을 허용한다. 나머지는 숫자·주장 키워드·길이를 검사한다."""
    t = _SECTION_NUMBER.sub("", text.strip()).strip()
    # '범위' is a scope noun, not the ranking marker '위'. Other claim words and digits still require evidence.
    claim_text = t.replace("범위", "")
    return t in _SECTION_LABELS or (len(t) <= LABEL_MAX_LEN and not _DIGIT.search(t)
                                    and not any(k in claim_text for k in _CLAIM_KEYWORDS))


def _norm(text: str) -> str:
    return " ".join(text.split())


def quantity_tokens(text: str) -> set[tuple[str, str]]:
    """Literal number/unit pairs; semantic equivalence and unit conversions remain review work."""
    return {(number.replace(",", ""), unit.lower()) for number, unit in re.findall(
        r"(?<![0-9.])(\d+(?:[.,]\d+)*)\s*(영업일|개월|시간|억원|만원|kg|mm|cm|㎡|m²|%|톤|년|월|일|명|개|대|건|회|원|g|m)(?![A-Za-z])",
        text, re.IGNORECASE)}


_MONTHS = {name: n for n, names in enumerate((
    ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
    ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
    ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
    ("december", "dec")), 1) for name in names}
_MONTH_PATTERN = "(?:" + "|".join(_MONTHS) + r")\.?"
_DATE_PATTERNS = (
    re.compile(r"(?<![\w.-])(?P<y>[12]\d{3})(?P<sep>[-./])(?P<m>\d{1,2})(?P=sep)(?P<d>\d{1,2})(?![\dA-Za-z./-])"),
    re.compile(r"(?<![\dA-Za-z])(?P<y>[12]\d{3})\s*년\s*(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일"),
    re.compile(r"(?<![\w.-])(?P<d>\d{1,2})\s+(?P<m>" + _MONTH_PATTERN + r")\s+(?P<y>[12]\d{3})(?!\d)", re.I),
    re.compile(r"\b(?P<m>" + _MONTH_PATTERN + r")\s+(?P<d>\d{1,2}),?\s+(?P<y>[12]\d{3})(?!\d)", re.I),
)
_CALENDAR_YEAR = re.compile(r"(?<![\dA-Za-z.-])([12]\d{3})(?:\s*년|(?=\s*\|))")


# Normalize only explicit formatting relationships inside the cited text. This
# does not establish company identity, date role, subject or contract conditions.
_NUMBER_FORM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_COMPACT_DATE = re.compile(
    r"(?<![A-Za-z가-힣])(?P<label>(?:설립일자?|발행일자?|만료일자?|기준일자?|공시일자?|"
    r"접수일자?|시작일|종료일|[Dd]ate)\s*[:：]?\s*)"
    r"(?P<y>[12]\d{3})(?P<m>\d{2})(?P<d>\d{2})(?![\dA-Za-z])")
_KOREAN_DATE_RANGE = re.compile(
    r"(?<![\dA-Za-z])(?P<y>[12]\d{3})\s*년\s*(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일"
    r"(?P<join>\s*(?:부터|에서|[~∼–-])\s*)(?P<end_m>\d{1,2})\s*월\s*(?P<end_d>\d{1,2})\s*일")
_TABLE_UNIT = re.compile(
    r"(?P<label>[가-힣A-Za-z][가-힣A-Za-z \t]{0,40})[（(]\s*(?P<unit>원|%)\s*[)）]"
    r"\s*[:：]?\s*(?P<number>[+-]?" + _NUMBER_FORM + r")(?![\d.,])")
_PARENTHESIZED_UNIT = re.compile(
    r"[（(](?P<number>[+-]?" + _NUMBER_FORM + r")[)）]\s*(?P<unit>원|%)")
_WON_AMOUNT = re.compile(
    r"(?<![\w.,+-])(?P<sign>[+-]?)(?P<parts>(?:" + _NUMBER_FORM + r"\s*[조억만]\s*)*"
    r"(?:" + _NUMBER_FORM + r"\s*)?)원(?![A-Za-z])")
_WON_PART = re.compile(r"(?P<number>" + _NUMBER_FORM + r")\s*(?P<scale>[조억만]?)")
_WON_SCALES = {"조": 10**12, "억": 10**8, "만": 10**4, "": 1}


def _numeric_format_text(text: str) -> str:
    def compact(match: re.Match) -> str:
        try:
            value = date(int(match['y']), int(match['m']), int(match['d']))
        except ValueError:
            return match[0]
        return match['label'] + value.isoformat()

    def date_range(match: re.Match) -> str:
        try:
            start = date(int(match['y']), int(match['m']), int(match['d']))
            end = date(start.year, int(match['end_m']), int(match['end_d']))
        except ValueError:
            return match[0]
        if end < start:  # A year crossing cannot be inferred from the omission.
            return match[0]
        # Preserve Korean suffix boundaries (e.g. '일부터', '일까지').
        return (f"{start.year}년 {start.month}월 {start.day}일{match['join']}"
                f"{end.year}년 {end.month}월 {end.day}일")

    def table_unit(match: re.Match) -> str:
        return f"{match['label']}({match['unit']}) {match['number']}{match['unit']}"

    def won(match: re.Match) -> str:
        parts = list(_WON_PART.finditer(match['parts']))
        if not parts or len(parts) > 4:
            return match[0]
        previous = 10**13
        with localcontext() as context:
            context.prec = 140
            total = Decimal(0)
            for part in parts:
                number, scale = part['number'], _WON_SCALES[part['scale']]
                if scale >= previous or len(number) > 40:
                    return match[0]
                previous = scale
                total += Decimal(number.replace(',', '')) * scale
            number = format(total, 'f')
            if '.' in number:
                number = number.rstrip('0').rstrip('.')
        return ('-' if match['sign'] == '-' else '') + number + '원'

    text = _COMPACT_DATE.sub(compact, text)
    text = _KOREAN_DATE_RANGE.sub(date_range, text)
    text = _TABLE_UNIT.sub(table_unit, text)
    text = _PARENTHESIZED_UNIT.sub(lambda m: f"({m['number']}{m['unit']})", text)
    return _WON_AMOUNT.sub(won, text)


def numeric_evidence_tokens(text: str) -> set[tuple[str, str]]:
    """Compare quantities and dates after explicit, exact format normalization.

    Korean won scales are converted exactly; other unit conversions stay unsupported.

    Dates stay atomic: matching year/month/day digits in different dates is not evidence.
    Their year can support a year-only history statement; a year cannot support a full date.
    This is a formatting check, not proof of subject, date role, conditions or causality.
    """
    text = _numeric_format_text(text)
    tokens: set[tuple[str, str]] = set()
    # Monetary sign is part of the amount, including after exact scale conversion.
    for amount in re.findall(r"(?<![\d.,A-Za-z])([+-]?" + _NUMBER_FORM + r")\s*원", text):
        tokens.add(("currency", amount.replace(',', '').lstrip('+')))
    for amount in re.findall(r"(?<![\d.,A-Za-z])([+-]?" + _NUMBER_FORM + r")\s*%", text):
        tokens.add(("percent", amount.replace(',', '').lstrip('+')))

    def calendar(match: re.Match) -> str:
        month = match["m"].lower().rstrip(".")
        month = int(month) if month.isdigit() else _MONTHS[month]
        try:
            value = date(int(match["y"]), month, int(match["d"]))
        except ValueError:
            return match[0]  # Invalid dates are not normalized.
        tokens.add(("date", value.isoformat()))
        tokens.add(("year", str(value.year)))
        return " " * len(match[0])

    for pattern in _DATE_PATTERNS:
        text = pattern.sub(calendar, text)

    def year(match: re.Match) -> str:
        tokens.add(("year", match[1]))
        return " " * len(match[0])

    text = _CALENDAR_YEAR.sub(year, text)
    for number in re.findall(r"\d+(?:[.,]\d+)*", text):
        # Only well-formed thousands grouping is presentation, never decimal punctuation.
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", number):
            number = number.replace(",", "")
        tokens.add(("number", number))
    tokens.update(("quantity", number + "|" + unit) for number, unit in quantity_tokens(text))
    return tokens


def value_in_text(value: str | None, text: str) -> bool:
    """사실 값이 본문에 실제로 들어 있는지. 정규화 부분 문자열 또는 2자 이상 토큰의 60% 이상."""
    if not value:
        return False
    v, t = _norm(value), _norm(text)
    if not v:
        return False
    if v in t:
        return True
    tokens = [w for w in re.split(r"[\s,·/()]+", v) if len(w) >= 2]
    if not tokens:
        return False
    return sum(1 for w in tokens if w in t) / len(tokens) >= 0.6


def _required_value_in_text(fact: Fact, text: str) -> bool:
    if fact.field_key == "company_name":
        from app.config import company_name_aliases
        aliases = company_name_aliases(fact.value)
        if aliases:
            return any(re.search(r"(?<![\w])" + re.escape(alias) + r"(?![\w])", text, re.IGNORECASE)
                       for alias in aliases)
    return value_in_text(fact.value, text)


# ---------------- 지문 ----------------

def fingerprint_block(block: Block, seg_texts: dict[str, str]) -> str:
    payload = {
        "type": block.type, "content": block.content, "fact_ids": sorted(block.fact_ids),
        "evidence": sorted((r.source_id, r.source_version, r.segment_id, seg_texts.get(r.segment_id, "")) for r in block.evidence_refs),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def fingerprints(document: Document, seg_texts: dict[str, str]) -> dict[str, str]:
    result = {b.block_id: fingerprint_block(b, seg_texts) for p in document.pages for b in p.blocks}
    for page in document.pages:
        if page.design is not None:
            for heading, body in zip(page.blocks, page.blocks[1:]):
                if heading.type == "heading" and heading.content.get("level") == 2 and body.type == "paragraph":
                    pair = json.dumps([heading.block_id, body.block_id, result[heading.block_id], result[body.block_id]],
                                      separators=(",", ":"))
                    for block in (heading, body):
                        result[block.block_id] = hashlib.sha256((result[block.block_id] + pair).encode()).hexdigest()
        # Ordered visual presentations add meaning. Color/spacing-only edits can reuse content checks.
        if page.design is not None and page.layout_key in {"process_steps", "timeline"}:
            sequence = json.dumps([page.layout_key, [b.block_id for b in page.blocks]], separators=(",", ":"))
            for block in page.blocks:
                result[block.block_id] = hashlib.sha256((result[block.block_id] + sequence).encode()).hexdigest()
    return result


# ---------------- 세션 사실 정보 ----------------

@dataclass
class Context:
    seg_texts: dict[str, str]              # segment_id → 원문
    seg_source: dict[str, str]             # segment_id → source_id
    asset_source: dict[str, str]           # asset_id → source_id
    mock_sources: set[str]                 # is_mock=1
    refs: refs_service.SessionRefs
    facts: dict[str, Fact]
    preflight_issues: list[Issue]
    demo_sources: set[str] = field(default_factory=set)
    demo: bool = False
    asset_captions: dict[str, str] = field(default_factory=dict)
    selected_sources: list[SourceIn] | None = None
    selected_preflight: PreflightOut | None = None
    required_fields: list[str] = field(default_factory=list)
    scope_sources: list[SourceIn] | None = None
    scope_preflight: PreflightOut | None = None


def load_context(conn: Connection, session_id: str, preflight: PreflightOut | None) -> Context:
    seg_texts, seg_source = {}, {}
    for r in conn.execute(
            "SELECT s.segment_id, s.source_id, s.text FROM segments s JOIN sources src ON src.source_id=s.source_id "
            "WHERE src.deleted_at IS NULL AND ((src.scope='session' AND src.session_id=?) OR src.scope='registered')",
            (session_id,)):
        seg_texts[r["segment_id"]] = r["text"]
        seg_source[r["segment_id"]] = r["source_id"]
    asset_source = {r["asset_id"]: r["source_id"] for r in conn.execute(
        "SELECT asset_id, source_id FROM assets WHERE deleted_at IS NULL AND (session_id=? OR scope='registered')", (session_id,))}
    mock_sources = {r["source_id"] for r in conn.execute("SELECT source_id FROM sources WHERE is_mock=1 OR origin_kind='mock'")}
    demo_sources = {r["source_id"] for r in conn.execute("SELECT source_id FROM sources WHERE origin_kind='demo'")}
    session = conn.execute("SELECT demo, input_revision, selected_source_ids, brief_json FROM sessions WHERE session_id=?",
                           (session_id,)).fetchone()
    from app.services import preflights
    sources = preflights.build_sources(conn, session_id, json.loads(session["selected_source_ids"])) if session else []
    selected_sources = None
    if (session is not None and conn.execute("PRAGMA user_version").fetchone()[0] >= 11
            and conn.execute("SELECT 1 FROM impact_reviews WHERE session_id=? "
                             "AND purged_at IS NULL LIMIT 1", (session_id,)).fetchone() is not None):
        # C-05 검토를 시작한 뒤에는 과거 점검·선택 해제 자료를 현재 근거로 되살리지 않는다.
        # 같은 입력 버전의 재점검도 이전 Fact ID를 계속 허용하는 근거가 될 수 없다.
        latest = conn.execute("SELECT preflight_id FROM preflights WHERE session_id=? AND input_revision=? "
                              "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                              (session_id, session["input_revision"])).fetchone()
        preflight = preflights.get(conn, session_id, latest[0]) if latest else None
        selected_sources = sources
    facts = {f.fact_id: f for f in preflight.facts} if preflight else {}
    captions = {aid: meta["caption"] for src in sources for aid, meta in src.asset_descriptions.items()}
    return Context(seg_texts, seg_source, asset_source, mock_sources, refs_service.load(conn, session_id), facts,
                   list(preflight.issues) if preflight else [], demo_sources, bool(session and session["demo"]), captions,
                   selected_sources=selected_sources, selected_preflight=preflight if selected_sources is not None else None,
                   scope_sources=sources, scope_preflight=preflight,
                   required_fields=Brief.model_validate_json(session["brief_json"]).required_fields if session else [])


def image_has_descriptive_caption(block: Block, ctx: Context) -> bool:
    """캡션/alt를 각각 검사한다. 등록 설명은 출처만 제공하며 이미지 의미 검사를 대체하지 않는다."""
    registered = ctx.asset_captions.get(block.content.get("asset_id"))
    for text in block_texts(block):
        # '소개서'의 '개'는 수량 주장이 아니다. 문서 출처 머리말만 분리한다.
        description = text.removeprefix("소개서의 ")
        if not text.strip() or is_label(description):
            continue
        # 선택·공개 허가가 유효한 사진의 동일 설명에만 길이 제한을 완화한다.
        # 인증/성능/수치 등 사실 주장은 등록 캡션이라도 텍스트 근거가 필요하다.
        if (text != registered or len(text) > 160 or _DIGIT.search(text)
                or any(word in description for word in _CLAIM_KEYWORDS)):
            return False
    return True


# ---------------- 서버 일반 검사 ----------------

@dataclass
class IssueDraft:
    scope: str
    code: str
    severity: str
    message: str
    block_ids: list[str] = field(default_factory=list)
    fact_ids: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    origin: str = "server"
    layout_format: str | None = None   # origin=layout일 때 형식(pdf/docx). 공개 허가 Issue는 None(형식 무관)

    @property
    def identity_key(self) -> str:
        # origin이 앞에 온다: 서버 Issue와 Agent Issue는 같은 code·대상이어도 다른 행이다(Agent가 서버 행을 갱신할 수 없다).
        key = "|".join([self.origin, self.scope, self.code, ",".join(sorted(self.block_ids)),
                        ",".join(sorted(self.fact_ids)), ",".join(sorted(self.source_ids))])
        if self.origin == "layout":
            key += f"|{self.layout_format or ''}"   # PDF·DOCX 배치 Issue는 서로 다른 행
        return key


KEY_ORIGINS = ("server", "agent", "preflight", "layout")   # layout: BE-08 배치 검사. 키 마이그레이션도 이 목록으로 판정한다


def migrate_legacy_issue_keys(conn: Connection) -> int:
    """origin이 없는 옛 identity_key('scope|code|…')를 'origin|scope|code|…'로 바꾼다. 재실행 안전. 바꾼 행 수를 돌려준다."""
    changed = 0
    for row in conn.execute("SELECT issue_id, identity_key, origin FROM issues").fetchall():
        if row["identity_key"].split("|", 1)[0] in KEY_ORIGINS:
            continue
        new_key = f"{row['origin']}|{row['identity_key']}"
        if conn.execute("SELECT 1 FROM issues WHERE identity_key=? AND document_id=(SELECT document_id FROM issues WHERE issue_id=?)",
                        (new_key, row["issue_id"])).fetchone():
            continue  # 이미 새 형식 행이 있으면 옛 행은 그대로 둔다(중복 생성 방지)
        conn.execute("UPDATE issues SET identity_key=? WHERE issue_id=?", (new_key, row["issue_id"]))
        changed += 1
    return changed


def _block_is_mock(block: Block, ctx: Context) -> tuple[bool, list[str]]:
    return _block_origin(block, ctx, ctx.mock_sources, MOCK_LABEL)


def _block_origin(block: Block, ctx: Context, source_ids: set[str], label: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if any(label in t for t in block_texts(block)):
        reasons.append("text")
    for ref in block.evidence_refs:
        if label in ctx.seg_texts.get(ref.segment_id, ""):
            reasons.append("evidence_text")
        if ctx.seg_source.get(ref.segment_id) in source_ids or ref.source_id in source_ids:
            reasons.append("evidence_source")
    for fid in block.fact_ids:
        f = ctx.facts.get(fid)
        if f and any(r.source_id in source_ids or label in ctx.seg_texts.get(r.segment_id, "") for r in f.evidence_refs):
            reasons.append("fact_source")
    if block.type == "image":
        aid = block.content.get("asset_id")
        if ctx.asset_source.get(aid) in source_ids:
            reasons.append("image_source")
    return bool(reasons), sorted(set(reasons))


def _required_present(document: Document, ctx: Context, keys: tuple[str, ...]) -> bool:
    """해당 field_key의 supported 사실을 참조하는 블록이 있고, 그 블록 본문에 사실 값이 실제로 들어 있어야 한다."""
    for page in document.pages:
        for block in page.blocks:
            text = " ".join(block_texts(block))
            if is_placeholder(text):
                continue
            for fid in block.fact_ids:
                f = ctx.facts.get(fid)
                if f and fid in ctx.refs.fact_ids and f.status == "supported" and f.field_key in keys and _required_value_in_text(f, text):
                    return True
    return False


def _required_issue(document: Document, ctx: Context, keys: tuple[str, ...], label: str) -> IssueDraft:
    """차단 기준은 유지하고, 표기 누락과 근거 미확인을 구분해 관련 블록을 안내한다."""
    facts = [f for f in ctx.facts.values() if f.field_key in keys]
    matches = [(block, f) for page in document.pages for block in page.blocks
               for f in facts if not is_placeholder(" ".join(block_texts(block)))
               and _required_value_in_text(f, " ".join(block_texts(block)))]
    if matches:
        if any(f.status == "supported" and f.fact_id in ctx.refs.fact_ids for _, f in matches):
            message = (f"{label} 표기는 있지만 해당 문구에 확인된 {label} 근거가 연결되지 않았습니다. "
                       "관련 문구의 근거를 확인하고, 자료 점검 결과를 반영해 수정한 뒤 다시 검증해 주세요.")
        else:
            message = (f"{label} 표기는 있지만 자료 점검에서 사용할 수 있는 확인된 근거가 없습니다. "
                       "자료 점검의 해당 항목과 원문을 확인하고 다시 점검해 주세요. 문구만 반복해서 고쳐도 해결되지 않습니다.")
    else:
        message = (f"문서 전체에서 자료의 {label}과 일치하는 문구를 찾지 못했습니다. "
                   "자료 점검에서 확인된 내용을 제목 또는 본문에 쓰고 해당 근거를 연결해 주세요.")
    return IssueDraft("content", "REQUIRED_MISSING", "blocker", message,
                      block_ids=sorted({block.block_id for block, _ in matches}),
                      fact_ids=sorted(f.fact_id for f in facts))


def preflight_conflicts(ctx: Context) -> list[IssueDraft]:
    """현재 점검의 미해결 blocker. 기존 함수/검사 이름은 호환을 위해 유지한다.

    본문 참조·안내 삭제로 점검 문제를 해결할 수 없다. conflict Fact는 Issue 누락에도
    보존하며 warning 수준의 needs_confirmation을 임의 blocker로 승격하지 않는다.
    """
    conflicts: list[IssueDraft] = []
    covered_facts: set[str] = set()
    for issue in ctx.preflight_issues:
        if issue.scope == "layout" or issue.severity != "blocker" or issue.status != "open":
            continue
        if issue.code == "REQUIRED_MISSING":
            # 필수 회사/사업·사용자 지정 항목은 아래 required_content가 문서 전체에서
            # 재판정한다. 점검의 company_summary 누락을 대체 사업 설명까지 막는
            # 독립 blocker로 고정하지 않는다.
            continue
        sources = set(issue.source_ids)
        for fid in issue.fact_ids:
            if fid in ctx.facts:
                sources.update(ref.source_id for ref in ctx.facts[fid].evidence_refs)
        conflicts.append(IssueDraft(issue.scope, issue.code, "blocker", issue.message,
                                    fact_ids=sorted(set(issue.fact_ids)), source_ids=sorted(sources), origin="preflight"))
        if issue.code == "VALUE_CONFLICT":
            covered_facts.update(issue.fact_ids)
    # 분석 결과에 Issue가 누락되거나 완화돼도 아직 conflict인 Fact를 해결된 것으로 취급하지 않는다.
    for fact in ctx.facts.values():
        if fact.status == "conflict" and fact.fact_id not in covered_facts:
            conflicts.append(IssueDraft("content", "VALUE_CONFLICT", "blocker",
                                        f"{fact.field_key}의 값이 자료마다 다릅니다. 후보 근거를 확인해 주세요.",
                                        fact_ids=[fact.fact_id],
                                        source_ids=sorted({ref.source_id for ref in fact.evidence_refs}), origin="preflight"))
    return conflicts


def server_checks(document: Document, ctx: Context) -> tuple[list[IssueDraft], list[CheckRecord]]:
    drafts: list[IssueDraft] = []
    records: list[CheckRecord] = []
    if ctx.selected_preflight is not None:
        records.append(CheckRecord(check_key=f"preflight:{ctx.selected_preflight.preflight_id}", kind="server", result="ok"))
    if not _required_present(document, ctx, REQUIRED_NAME_KEYS):
        drafts.append(_required_issue(document, ctx, REQUIRED_NAME_KEYS, "회사명"))
    if not _required_present(document, ctx, REQUIRED_BUSINESS_KEYS):
        drafts.append(_required_issue(document, ctx, REQUIRED_BUSINESS_KEYS, "주요 사업/공정 설명"))
    for key in ctx.required_fields:
        if not _required_present(document, ctx, (key,)):
            drafts.append(_required_issue(document, ctx, (key,), key))
    if document.editorial and document.editorial.input_revision == document.input_revision:
        present = {fid for p in document.pages for b in p.blocks
                   if b.type != "heading" for fid in b.fact_ids}
        present.update(fid for p in document.pages for b in p.blocks for fid in b.fact_ids
                       if fid in ctx.facts and ctx.facts[fid].field_key == "company_name")
        missing = [s.fact_id for s in document.editorial.selections if s.disposition == "required"
                   and s.fact_id in ctx.facts and s.fact_id not in present]
        if missing:
            drafts.append(IssueDraft("content", "REQUIRED_MISSING", "blocker",
                                     "구성 계획의 필수 사실이 현재 본문에서 빠졌습니다.", fact_ids=missing))
    records.append(CheckRecord(check_key="required_content", kind="server", result="issue" if drafts else "ok"))

    conflicts = preflight_conflicts(ctx)
    drafts.extend(conflicts)
    records.append(CheckRecord(check_key="preflight_conflicts", kind="server", result="issue" if conflicts else "ok"))
    conflict_by_fact = {fid: issue for issue in conflicts for fid in issue.fact_ids}

    for page in document.pages:
        if page.design and page.layout_key in {"process_steps", "timeline"}:
            page_refs = [r for b in page.blocks for r in b.evidence_refs]
            page_fids = {fid for b in page.blocks for fid in b.fact_ids}
            # Only facts and references actually linked on this page can justify its layout.
            fact_evidence = [(fact.field_key, [r.excerpt for r in fact.evidence_refs if r in page_refs])
                             for fid, fact in ctx.facts.items() if fid in page_fids]
            if not sequence_evidence_supported(page.layout_key, " ".join(r.excerpt for r in page_refs), fact_evidence):
                drafts.append(IssueDraft("content", "UNSUPPORTED_CLAIM", "blocker",
                    "순서·시점 근거가 없는 단계/연혁 배치입니다.", block_ids=[b.block_id for b in page.blocks]))
        for block in page.blocks:
            bid = block.block_id
            texts = block_texts(block)
            joined = " ".join(texts).strip()
            before = len(drafts)
            selected_problem = None
            current_sources = ctx.scope_sources if ctx.scope_sources is not None else ctx.selected_sources
            current_preflight = ctx.scope_preflight or ctx.selected_preflight
            if current_sources is not None:
                selected_problem = (refs_service.selected_problem(
                    [page.model_copy(update={"blocks": [block]})], current_sources, current_preflight)
                    if current_preflight is not None else "현재 입력에 해당하는 사전 점검이 없습니다.")
            if selected_problem:
                drafts.append(IssueDraft("content", "EVIDENCE_INVALID", "blocker",
                                         "현재 선택 자료와 최신 점검에서 사용할 수 없는 참조입니다. " + selected_problem,
                                         block_ids=[bid]))
            elif any(fid not in ctx.refs.fact_ids for fid in block.fact_ids):
                drafts.append(IssueDraft("content", "EVIDENCE_INVALID", "blocker",
                                         "현재 세션에서 근거로 사용할 수 없는 사실 참조입니다.", block_ids=[bid]))

            if document.editorial and block.fact_ids:
                expected_refs = [r for fid in block.fact_ids if fid in ctx.facts for r in ctx.facts[fid].evidence_refs]
                if any(ref not in block.evidence_refs for ref in expected_refs):
                    drafts.append(IssueDraft("content", "EVIDENCE_INVALID", "blocker",
                        "주장의 사실 근거 또는 조건 근거가 빠졌습니다.", block_ids=[bid]))
                if numeric_evidence_tokens(joined) - numeric_evidence_tokens(
                        " ".join(r.excerpt for r in expected_refs)):
                    drafts.append(IssueDraft("content", "VALUE_MISMATCH", "blocker",
                        "현재 문구의 수치·단위 조합이 연결된 원문에 없습니다.", block_ids=[bid]))

            # 근거 없는 사실 주장(㉛). 종류가 아니라 내용으로 판단한다.
            if not block.fact_ids and not is_placeholder(joined):
                if block.type in ("paragraph", "list") and not is_connector(joined):
                    drafts.append(IssueDraft("content", "UNSUPPORTED_CLAIM", "blocker",
                                             "근거(fact_ids·evidence_refs)가 없는 사실 주장입니다. 주장을 지우거나 근거를 연결한 뒤 다시 검증하세요.",
                                             block_ids=[bid]))
                elif (block.type in ("heading", "image") and joined
                      and not (image_has_descriptive_caption(block, ctx) if block.type == "image" else is_label(joined))):
                    drafts.append(IssueDraft("content", "UNSUPPORTED_CLAIM", "blocker",
                                             "제목·캡션에 근거 없는 사실 주장이 있습니다.", block_ids=[bid]))
            if block.type == "paragraph" and is_placeholder(joined):
                drafts.append(IssueDraft("content", "PLACEHOLDER_TEXT", "warning",
                                         "안내 문구가 남아 있습니다. 내용을 채우거나 블록을 지우세요.", block_ids=[bid]))
            for ref in block.evidence_refs:
                if ref.segment_id not in ctx.refs.segment_ids or ctx.refs.source_versions.get(ref.source_id) != ref.source_version:
                    drafts.append(IssueDraft("content", "EVIDENCE_INVALID", "blocker",
                                             "근거가 지금 세션 자료에 없습니다(삭제·제외·버전 변경).",
                                             block_ids=[bid], source_ids=[ref.source_id]))
                    break
            for fid in block.fact_ids:
                if fid in conflict_by_fact:
                    src = conflict_by_fact[fid]
                    drafts.append(IssueDraft("content", "VALUE_CONFLICT", "blocker", src.message,
                                             block_ids=[bid], fact_ids=[fid], source_ids=list(src.source_ids)))
            is_mock, reasons = _block_is_mock(block, ctx)
            if is_mock:
                drafts.append(IssueDraft("content", "MOCK_VALUE", "blocker",
                                         f"가상(mock) 자료에서 온 내용입니다({', '.join(reasons)}). 실제 자료로 바꾼 뒤 다시 검증해야 합니다.",
                                         block_ids=[bid]))
            is_demo, demo_reasons = _block_origin(block, ctx, ctx.demo_sources, "[시연]")
            if is_demo:
                drafts.append(IssueDraft("content", "DEMO_VALUE", "warning" if ctx.demo else "blocker",
                                         "시연용 가상 내용 또는 이미지가 포함되어 있습니다. 실제 회사 실적·제품으로 오해되지 않도록 시연 표시를 확인해 주세요.",
                                         block_ids=[bid]))
            records.append(CheckRecord(check_key=f"block:{bid}", kind="server", block_ids=[bid],
                                       result="issue" if len(drafts) > before else "ok"))
    return drafts, records


def validate_agent_issues(issues: list[Issue], document: Document, ctx: Context, changed: set[str]) -> str | None:
    blocks = {b.block_id for p in document.pages for b in p.blocks}
    for iss in issues:
        if iss.scope != "content":
            return f"agent issue scope는 content만: {iss.issue_id}"
        if any(b not in blocks for b in iss.block_ids):
            return f"문서에 없는 block_id: {iss.issue_id}"
        if any(b not in changed for b in iss.block_ids):
            return f"바뀌지 않은 블록에 대한 agent issue: {iss.issue_id}"
        if any(f not in ctx.facts for f in iss.fact_ids):
            return f"사전 점검에 없는 fact_id: {iss.issue_id}"
        if any(s not in ctx.refs.source_versions for s in iss.source_ids):
            return f"세션에 없는 source_id: {iss.issue_id}"
        if iss.status != "open" or iss.resolution is not None:
            return f"agent는 상태를 정할 수 없음: {iss.issue_id}"
    return None


# ---------------- 마지막 유효 검증·변경 범위 ----------------

def current_preflight_key(conn: Connection, document_id: str, input_revision: int) -> str | None:
    """C-05 이후에는 같은 입력의 재점검도 과거 의미 검증을 재사용할 수 없게 연결한다."""
    from app.services import db_history

    if not db_history.enabled(conn) or conn.execute(
            "SELECT 1 FROM impact_reviews WHERE document_id=? AND purged_at IS NULL LIMIT 1",
            (document_id,)).fetchone() is None:
        return None
    row = conn.execute("SELECT p.preflight_id FROM preflights p JOIN documents d ON d.session_id=p.session_id "
                       "WHERE d.document_id=? AND p.input_revision=? ORDER BY p.created_at DESC, p.rowid DESC LIMIT 1",
                       (document_id, input_revision)).fetchone()
    return f"preflight:{row[0]}" if row else "preflight:missing"


def _matches_preflight(conn: Connection, document_id: str, input_revision: int, row: Row | None) -> Row | None:
    if row is None:
        return None
    key = current_preflight_key(conn, document_id, input_revision)
    return row if key is None or any(c.get("check_key") == key for c in json.loads(row["checks_json"])) else None


def latest_validation(conn: Connection, document_id: str, document_revision: int,
                      input_revision: int) -> Row | None:
    # created_at은 초 단위라 같은 값이 생긴다. 저장 순서(rowid)로 보조 정렬한다(ID 문자열 정렬 금지).
    row = conn.execute(
        "SELECT * FROM validations WHERE document_id=? AND document_revision=? AND input_revision=? "
        "AND status<>'pending' ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (document_id, document_revision, input_revision)).fetchone()
    return _matches_preflight(conn, document_id, input_revision, row)


def base_validation(conn: Connection, document_id: str, document_revision: int,
                    input_revision: int) -> Row | None:
    """재사용 기준: 같은 문서·같은 입력 버전에서 현재보다 낮은 revision의 가장 최근 유효 검증."""
    row = conn.execute(
        "SELECT * FROM validations WHERE document_id=? AND input_revision=? AND document_revision<? "
        "AND status<>'pending' ORDER BY document_revision DESC, created_at DESC, rowid DESC LIMIT 1",
        (document_id, input_revision, document_revision)).fetchone()
    return _matches_preflight(conn, document_id, input_revision, row)


def changed_blocks(current: dict[str, str], base: Row | None) -> tuple[set[str], set[str]]:
    """(바뀐/새 블록, 그대로인 블록). base가 없으면 전부 바뀐 것으로 본다."""
    if base is None:
        return set(current), set()
    old = json.loads(base["fingerprints_json"])
    changed = {b for b, fp in current.items() if old.get(b) != fp}
    return changed, set(current) - changed


# ---------------- Issue 기록 ----------------

def _anchor(draft: IssueDraft, fps: dict[str, str], ctx: Context, input_revision: int) -> str:
    payload = {"blocks": {b: fps.get(b, "") for b in sorted(draft.block_ids or fps)},
               "facts": {f: ctx.facts[f].model_dump() if f in ctx.facts else None for f in sorted(draft.fact_ids)},
               "sources": sorted(draft.source_ids), "code": draft.code, "scope": draft.scope,
               "severity": draft.severity, "message": draft.message,
               "input_revision": input_revision}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def issue_anchor(issue: Row, fps: dict[str, str], ctx: Context, input_revision: int) -> str:
    return _anchor(IssueDraft(issue["scope"], issue["code"], issue["severity"], issue["message"],
                              json.loads(issue["block_ids_json"]), json.loads(issue["fact_ids_json"]),
                              json.loads(issue["source_ids_json"]), issue["origin"]), fps, ctx, input_revision)


def acknowledgeable(issue: Row, *, demo: bool) -> bool:
    """검증기가 warning으로 보냈다는 이유만으로 정확성 문제를 승인 가능한 문제로 바꾸지 않는다."""
    if issue["severity"] != "warning" or issue["scope"] == "layout":
        return False
    if issue["origin"] == "server":
        return issue["code"] == "PLACEHOLDER_TEXT" or (issue["code"] == "DEMO_VALUE" and demo)
    return issue["origin"] == "agent" and issue["code"] in {"REPETITION", "PHOTO_SHORTAGE"}


def validation_in_progress(conn: Connection, document_id: str, revision: int, input_revision: int) -> bool:
    return conn.execute("SELECT 1 FROM jobs WHERE kind='validate' AND status IN ('queued','running') AND target_key=?",
                        (f"{document_id}@{revision}@{input_revision}",)).fetchone() is not None


def warning_ack_valid(conn: Connection, issue: Row, checked: Row, *, demo: bool) -> bool:
    from app.services import db_history

    proof = json.loads(issue["resolution_json"]) if issue["resolution_json"] else {}
    return (issue["status"] == "acknowledged" and acknowledgeable(issue, demo=demo)
            and proof.get("action") == "acknowledged" and bool(proof.get("by")) and bool(proof.get("at"))
            and proof.get("anchor_fingerprint") == issue["anchor_fingerprint"]
            and proof.get("input_revision") == checked["input_revision"]
            and proof.get("validated_document_revision") == checked["document_revision"]
            and proof.get("validated_validation_id") == checked["validation_id"]
            and db_history.warning_record_exists(conn, issue, checked))


def reconcile_acknowledgements(conn: Connection, document: Document, checked: Row, ctx: Context,
                               fps: dict[str, str]) -> None:
    """검증 저장 후 관련 지문을 대조한다. 관련 없는 편집만 원 확인의 재사용 연결을 허용한다."""
    from app.services import db_history

    for issue in conn.execute("SELECT * FROM issues WHERE document_id=? AND status='acknowledged' AND scope<>'layout'",
                              (document.document_id,)).fetchall():
        proof = json.loads(issue["resolution_json"]) if issue["resolution_json"] else {}
        anchor = issue_anchor(issue, fps, ctx, checked["input_revision"])
        if (not acknowledgeable(issue, demo=ctx.demo) or not proof.get("by") or not proof.get("at")
                or proof.get("anchor_fingerprint") != anchor or proof.get("input_revision") != checked["input_revision"]):
            history = json.loads(issue["resolution_history_json"])
            if proof:
                history.append({**proof, "reopened_at": to_iso(now()), "previous_status": "acknowledged",
                                "reopened_by_validation": checked["validation_id"]})
            conn.execute("UPDATE issues SET status='open', resolution_json=NULL, resolution_history_json=?, "
                         "anchor_fingerprint=?, updated_at=? WHERE issue_id=?",
                         (json.dumps(history, ensure_ascii=False), anchor, to_iso(now()), issue["issue_id"]))
            db_history.invalidate_warning(conn, issue["issue_id"])
            continue
        proof.update(validated_document_revision=document.document_revision,
                     validated_validation_id=checked["validation_id"])
        conn.execute("UPDATE issues SET resolution_json=?, last_validation_id=?, updated_at=? WHERE issue_id=?",
                     (json.dumps(proof, ensure_ascii=False), checked["validation_id"], to_iso(now()), issue["issue_id"]))
        db_history.record_warning(conn, issue, proof)


def _covered_by_this_validation(row: Row, agent_covered_blocks: set[str], agent_full: bool) -> bool:
    """이번 검증에서 그 Issue가 속한 검사가 그 범위를 실제로 다시 봤는가.

    서버 검사는 매번 문서 전체를 다시 보므로 항상 True. Agent 검사는 이번에 넘긴 블록(agent_covered_blocks)에 한하고,
    block_ids가 없는 문서 전체 Agent Issue는 전체 검사(agent_full)였을 때만 True.
    """
    if row["origin"] == "layout":
        return False   # 배치 Issue는 내용 Validation이 닫지 않는다. 그 형식의 배치 재검사만 닫는다(BE-08)
    if row["origin"] != "agent":
        return True
    blocks = set(json.loads(row["block_ids_json"]))
    if not blocks:
        return agent_full
    return blocks <= agent_covered_blocks


def _agent_issue_keys(conn: Connection, document_id: str, drafts: list[IssueDraft]) -> dict[tuple[str, str], str]:
    """같은 대상의 독립 지적을 구분하고, 내용이 같은 기존 지적의 ID·이력을 유지한다."""
    incoming: dict[str, set[str]] = {}
    for draft in drafts:
        if draft.origin == "agent":
            incoming.setdefault(draft.identity_key, set()).add(draft.message)
    if not incoming:
        return {}
    existing: dict[str, list[Row]] = {}
    for row in conn.execute("SELECT * FROM issues WHERE document_id=? AND origin='agent' ORDER BY rowid",
                            (document_id,)).fetchall():
        group = IssueDraft(row["scope"], row["code"], row["severity"], row["message"],
                           json.loads(row["block_ids_json"]), json.loads(row["fact_ids_json"]),
                           json.loads(row["source_ids_json"]), origin="agent").identity_key
        existing.setdefault(group, []).append(row)
    keys = {}
    for group, messages in incoming.items():
        old = existing.get(group, [])
        by_message = {row["message"]: row["identity_key"] for row in old}
        for message in messages:
            if message in by_message:
                key = by_message[message]
            elif len(messages) == 1 and (not old or (len(old) == 1 and old[0]["identity_key"] == group)):
                # 기존 단일 지적은 설명이 바뀌어도 ID를 유지한다. anchor 비교가 경고 재확인을 맡는다.
                key = group
            else:
                # 여러 지적의 의미 대응을 추측하지 않는다. 순서 대신 전체 설명으로 새 문제를 식별한다.
                key = group + "|finding:" + hashlib.sha256(message.encode("utf-8")).hexdigest()
            keys[group, message] = key
    return keys


def persist_issues(conn: Connection, session_id: str, document: Document, validation_id: str | None,
                   drafts: list[IssueDraft], fps: dict[str, str], ctx: Context, input_revision: int,
                   agent_covered_blocks: set[str], agent_full: bool, *, resolve_missing: bool = True) -> list[str]:
    """이번 검증이 만든 Issue를 기록한다. 현재 Issue ID 목록을 돌려준다.

    - Agent의 같은 대상·코드에 여러 설명이 있으면 각각 기록한다. 같은 설명의 ID·이력을 재사용한다.
    - 서버·사전 점검·배치 문제는 기존 identity_key(origin 포함)로 갱신한다.
    - resolved/excluded였는데 다시 검출되면 원인이 돌아온 것 → open으로 되돌리고 이전 resolution은 이력으로.
    - acknowledged는 관련 내용·근거·입력(anchor)이 바뀌었을 때만 open으로 재확인.
    - 이번에 검출되지 않은 open Issue는 그 검사가 그 범위를 실제로 다시 봤을 때만 서버가 resolved로 닫는다.
    - 사전 점검 전달은 validation_id=None, resolve_missing=False로 새 충돌만 합친다. 다른 문제를 닫지 않는다.
    """
    stamp = to_iso(now())
    produced: set[str] = set()
    agent_keys = _agent_issue_keys(conn, document.document_id, drafts)
    for d in drafts:
        key = agent_keys[d.identity_key, d.message] if d.origin == "agent" else d.identity_key
        anchor = _anchor(d, fps, ctx, input_revision)
        row = conn.execute("SELECT * FROM issues WHERE document_id=? AND identity_key=?", (document.document_id, key)).fetchone()
        if row is None:
            iid = f"iss_{uuid.uuid4().hex[:16]}"
            conn.execute(
                "INSERT INTO issues (issue_id, session_id, document_id, identity_key, scope, code, severity, status, message, "
                "source_ids_json, fact_ids_json, block_ids_json, origin, anchor_fingerprint, resolution_json, "
                "resolution_history_json, first_validation_id, last_validation_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, NULL, '[]', ?, ?, ?, ?)",
                (iid, session_id, document.document_id, key, d.scope, d.code, d.severity, d.message,
                 json.dumps(sorted(d.source_ids)), json.dumps(sorted(d.fact_ids)), json.dumps(sorted(d.block_ids)),
                 d.origin, anchor, validation_id, validation_id, stamp, stamp))
        else:
            status, resolution, history = row["status"], row["resolution_json"], json.loads(row["resolution_history_json"])
            reopen = (status in ("resolved", "excluded")                       # 다시 검출됨 = 원인이 돌아옴
                      or (status == "acknowledged" and row["anchor_fingerprint"] != anchor))  # 관련 내용이 바뀜
            if reopen:
                from app.services import db_history
                db_history.invalidate_warning(conn, row["issue_id"])
                if resolution:
                    history.append({**json.loads(resolution), "reopened_at": stamp, "reopened_by_validation": validation_id,
                                    "previous_status": status})
                status, resolution = "open", None
            conn.execute(
                "UPDATE issues SET severity=?, message=?, status=?, anchor_fingerprint=?, resolution_json=?, "
                "resolution_history_json=?, first_validation_id=COALESCE(first_validation_id, ?), "
                "last_validation_id=COALESCE(?, last_validation_id), updated_at=? WHERE issue_id=?",
                (d.severity, d.message, status, anchor, resolution, json.dumps(history, ensure_ascii=False),
                 validation_id, validation_id, stamp, row["issue_id"]))
        produced.add(key)

    # 이번에 다시 나오지 않은 open Issue: 그 검사가 그 범위를 실제로 다시 본 경우에만 원인이 사라진 것으로 보고 닫는다.
    previous_open = (conn.execute("SELECT * FROM issues WHERE document_id=? AND status IN ('open','acknowledged')",
                                  (document.document_id,)).fetchall() if resolve_missing else [])
    for row in previous_open:
        if row["identity_key"] in produced:
            continue
        blocks = set(json.loads(row["block_ids_json"]))
        # 대상 블록이 전부 삭제된 지적은 현재 문서에서 존재할 수 없다.
        # 일부 대상이 남거나 문서 전체 지적이면 기존 부분 검증 범위를 유지한다.
        deleted_agent_target = row["origin"] == "agent" and bool(blocks) and blocks.isdisjoint(fps)
        if not deleted_agent_target and not _covered_by_this_validation(row, agent_covered_blocks, agent_full):
            continue  # 재실행하지 않은 검사의 Issue(문서 전체 Issue 포함)는 보존
        from app.services import db_history
        db_history.invalidate_warning(conn, row["issue_id"])
        if row["resolution_json"]:
            history = json.loads(row["resolution_history_json"])
            history.append({**json.loads(row["resolution_json"]), "closed_at": stamp,
                            "closed_by_validation": validation_id, "previous_status": row["status"]})
            conn.execute("UPDATE issues SET resolution_history_json=? WHERE issue_id=?",
                         (json.dumps(history, ensure_ascii=False), row["issue_id"]))
        resolution = {"action": "resolved", "by": "server", "reason": "재검증에서 원인이 더 이상 확인되지 않음",
                      "at": stamp, "document_revision": document.document_revision, "input_revision": input_revision,
                      "validation_id": validation_id}
        conn.execute("UPDATE issues SET status='resolved', resolution_json=?, last_validation_id=?, updated_at=? WHERE issue_id=?",
                     (json.dumps(resolution, ensure_ascii=False), validation_id, stamp, row["issue_id"]))
    return [r["issue_id"] for r in conn.execute("SELECT issue_id FROM issues WHERE document_id=? ORDER BY created_at, rowid",
                                                 (document.document_id,))]


def record_preflight_conflicts(conn: Connection, session_id: str, document: Document,
                              preflight: PreflightOut) -> bool:
    """점검의 미해결 blocker를 현재 문서에 합친다. 다른 문제를 닫거나 검증 완료로 처리하지 않는다."""
    if document.input_revision != preflight.input_revision:
        return False
    ctx = load_context(conn, session_id, preflight)
    conflicts = preflight_conflicts(ctx)
    if not conflicts:
        return False
    persist_issues(conn, session_id, document, None, conflicts, fingerprints(document, ctx.seg_texts), ctx,
                   preflight.input_revision, set(), False, resolve_missing=False)
    latest = latest_validation(conn, document.document_id, document.document_revision, preflight.input_revision)
    if latest is not None:
        refresh_validation_status(conn, latest["validation_id"], document.document_id)
    return True


def compute_validation_status(conn: Connection, document_id: str) -> str:
    # 내용 검증 상태. 배치(scope=layout) Issue는 형식별 LayoutCheck·승인 ⑥에서 판단한다(BE-08).
    rows = conn.execute("SELECT severity FROM issues WHERE document_id=? AND status='open' AND scope<>'layout'", (document_id,)).fetchall()
    if any(r["severity"] == "blocker" for r in rows):
        return "failed"
    if any(r["severity"] == "warning" for r in rows):
        return "needs_review"
    return "passed"


def save_validation(conn: Connection, session_id: str, document: Document, input_revision: int,
                    validation_id: str, status: str, issue_ids: list[str], checks: list[CheckRecord],
                    fps: dict[str, str], base_id: str | None, agent_called: bool) -> None:
    stamp = to_iso(now())
    conn.execute(
        "INSERT INTO validations (validation_id, session_id, document_id, document_revision, input_revision, status, "
        "issue_ids_json, checks_json, fingerprints_json, base_validation_id, agent_called, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (validation_id, session_id, document.document_id, document.document_revision, input_revision, status,
         json.dumps(issue_ids), json.dumps([c.model_dump() for c in checks], ensure_ascii=False),
         json.dumps(fps), base_id, int(agent_called), stamp, stamp))


def refresh_validation_status(conn: Connection, validation_id: str, document_id: str) -> str:
    """Issue 해결 뒤 최종 상태를 현재 문서 전체의 미해결 문제로 다시 합산한다."""
    status = compute_validation_status(conn, document_id)
    issue_ids = [r["issue_id"] for r in conn.execute("SELECT issue_id FROM issues WHERE document_id=? ORDER BY created_at, rowid", (document_id,))]
    conn.execute("UPDATE validations SET status=?, issue_ids_json=?, updated_at=? WHERE validation_id=?",
                 (status, json.dumps(issue_ids), to_iso(now()), validation_id))
    return status


# ---------------- 출력 ----------------

def to_validation_out(row: Row) -> ValidationOut:
    checks = [CheckRecord.model_validate(c) for c in json.loads(row["checks_json"])]
    checked = sorted({b for c in checks if c.kind == "agent" and c.result != "skipped" and c.reused_from_validation_id is None for b in c.block_ids})
    reused = sorted({b for c in checks if c.reused_from_validation_id is not None for b in c.block_ids})
    return ValidationOut(validation_id=row["validation_id"], document_id=row["document_id"],
                         document_revision=row["document_revision"], input_revision=row["input_revision"],
                         status=row["status"], issue_ids=json.loads(row["issue_ids_json"]), checks=checks,
                         agent_called=bool(row["agent_called"]), checked_block_ids=checked, reused_block_ids=reused,
                         base_validation_id=row["base_validation_id"], created_at=row["created_at"])


def issue_to_out(row: Row) -> IssueOut:
    return IssueOut(issue_id=row["issue_id"], scope=row["scope"], code=row["code"], severity=row["severity"],
                    status=row["status"], message=row["message"], source_ids=json.loads(row["source_ids_json"]),
                    fact_ids=json.loads(row["fact_ids_json"]), block_ids=json.loads(row["block_ids_json"]),
                    resolution=json.loads(row["resolution_json"]) if row["resolution_json"] else None,
                    origin=row["origin"], layout_format=(row["layout_format"] if "layout_format" in row.keys() else None),
                    created_at=row["created_at"], updated_at=row["updated_at"])


def list_issues(conn: Connection, document_id: str) -> list[IssueOut]:
    return [issue_to_out(r) for r in conn.execute("SELECT * FROM issues WHERE document_id=? ORDER BY created_at, rowid", (document_id,))]


def compute_document_status(conn: Connection, document_id: str, document_revision: int, input_revision: int) -> str:
    """읽을 때 한 곳에서 계산한다(문서 조회·세션 summary 공용). document_revisions.status는 캐시일 뿐이다."""
    if conn.execute("SELECT 1 FROM approvals WHERE document_id=? AND document_revision=? AND input_revision=? AND status='active'",
                    (document_id, document_revision, input_revision)).fetchone():
        return "approved"
    if conn.execute("SELECT 1 FROM issues WHERE document_id=? AND origin='preflight' AND status='open' AND severity='blocker'",
                    (document_id,)).fetchone():
        return "review_required"
    v = latest_validation(conn, document_id, document_revision, input_revision)
    if v is None:
        if conn.execute("SELECT 1 FROM document_revisions WHERE document_id=? AND revision=? "
                        "AND input_revision=? AND origin='impact_review'",
                        (document_id, document_revision, input_revision)).fetchone() is not None:
            return "review_required"
        return "draft"
    return "ready_for_approval" if v["status"] == "passed" else "review_required"
