"""검증(Validation) — 서버 일반 검사, 부분 재검증, Issue 정체성·기록, 문서 상태 계산.

AI 의미 검증(Agent validate)과 분리된 서버 검사:
  REQUIRED_MISSING   회사명·주요 사업/공정이 실제 블록에 있는지(fact_ids만으로 통과 안 함)   blocker
  UNSUPPORTED_CLAIM  근거 없는 사실 주장(계약 확인 ㉛)                                           blocker, 확인 클릭 불가
  PLACEHOLDER_TEXT   "추가 확인 필요"/"자료에서 확인되지 않음" 문단                                 warning
  VALUE_CONFLICT     사전 점검의 자료 간 충돌을 현재 블록이 참조하는 사실에 연결                     blocker
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
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.models import (Block, CheckRecord, Document, EvidenceRef, Fact, Issue, IssueOut, PreflightOut,
                        ValidationOut)
from app.services import refs as refs_service
from app.timeutil import now, to_iso

PLACEHOLDERS = {"추가 확인 필요", "자료에서 확인되지 않음"}
MOCK_LABEL = "[MOCK]"
REQUIRED_NAME_KEYS = ("company_name",)
REQUIRED_BUSINESS_KEYS = ("company_summary", "business_areas", "processes", "products_services", "technology")
NON_ACKNOWLEDGEABLE = {"MOCK_VALUE", "UNSUPPORTED_CLAIM"}   # blocker는 원래 확인 클릭 불가. 명시적으로도 막는다
NON_EXCLUDABLE = {"MOCK_VALUE", "REQUIRED_MISSING"}

# 계약 확인 ㉛ — 비사실 안내·연결 문장의 제한된 규칙: 아래 머리말로 시작하고 서술형으로 끝나며 숫자·%·주장 키워드가 없고 40자 이하.
_CONNECTOR_HEAD = re.compile(r"^(다음은|아래는|이어서|이 장에서는|이 페이지에서는|본 자료는|여기서는|다음 페이지에서는)")
_CONNECTOR_TAIL = re.compile(r"(입니다|합니다|살펴봅니다|소개합니다|안내합니다|정리합니다)\.?$")
# heading·캡션이 '표지/라벨'이 아니라 사실 주장인지 판단하는 키워드(있으면 주장으로 본다).
_CLAIM_KEYWORDS = ("인증", "납기", "최고", "1위", "보장", "특허", "ISO", "최대", "최소", "이상", "이하", "년",
                   "개", "톤", "㎡", "억", "만", "명", "위", "국내", "세계", "최초", "유일", "품질", "정밀")
_DIGIT = re.compile(r"[0-9%]")
LABEL_MAX_LEN = 20
CONNECTOR_MAX_LEN = 40


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


def is_label(text: str) -> bool:
    """heading·캡션이 단순 표지/라벨인지. 절 번호를 뺀 뒤 숫자·주장 키워드가 있거나 길면 사실 주장으로 검사한다."""
    t = _SECTION_NUMBER.sub("", text.strip()).strip()
    return len(t) <= LABEL_MAX_LEN and not _DIGIT.search(t) and not any(k in t for k in _CLAIM_KEYWORDS)


def _norm(text: str) -> str:
    return " ".join(text.split())


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


# ---------------- 지문 ----------------

def fingerprint_block(block: Block, seg_texts: dict[str, str]) -> str:
    payload = {
        "type": block.type, "content": block.content, "fact_ids": sorted(block.fact_ids),
        "evidence": sorted((r.source_id, r.source_version, r.segment_id, seg_texts.get(r.segment_id, "")) for r in block.evidence_refs),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def fingerprints(document: Document, seg_texts: dict[str, str]) -> dict[str, str]:
    return {b.block_id: fingerprint_block(b, seg_texts) for p in document.pages for b in p.blocks}


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


def load_context(conn: sqlite3.Connection, session_id: str, preflight: PreflightOut | None) -> Context:
    seg_texts, seg_source = {}, {}
    for r in conn.execute(
            "SELECT s.segment_id, s.source_id, s.text FROM segments s JOIN sources src ON src.source_id=s.source_id "
            "WHERE src.deleted_at IS NULL AND ((src.scope='session' AND src.session_id=?) OR src.scope='registered')",
            (session_id,)):
        seg_texts[r["segment_id"]] = r["text"]
        seg_source[r["segment_id"]] = r["source_id"]
    asset_source = {r["asset_id"]: r["source_id"] for r in conn.execute(
        "SELECT asset_id, source_id FROM assets WHERE deleted_at IS NULL AND (session_id=? OR scope='registered')", (session_id,))}
    mock_sources = {r["source_id"] for r in conn.execute("SELECT source_id FROM sources WHERE is_mock=1")}
    facts = {f.fact_id: f for f in preflight.facts} if preflight else {}
    return Context(seg_texts, seg_source, asset_source, mock_sources, refs_service.load(conn, session_id), facts,
                   list(preflight.issues) if preflight else [])


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

    @property
    def identity_key(self) -> str:
        # origin이 앞에 온다: 서버 Issue와 Agent Issue는 같은 code·대상이어도 다른 행이다(Agent가 서버 행을 갱신할 수 없다).
        return "|".join([self.origin, self.scope, self.code, ",".join(sorted(self.block_ids)),
                         ",".join(sorted(self.fact_ids)), ",".join(sorted(self.source_ids))])


KEY_ORIGINS = ("server", "agent", "preflight")


def migrate_legacy_issue_keys(conn: sqlite3.Connection) -> int:
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
    reasons: list[str] = []
    if any(MOCK_LABEL in t for t in block_texts(block)):
        reasons.append("text")
    for ref in block.evidence_refs:
        if MOCK_LABEL in ctx.seg_texts.get(ref.segment_id, ""):
            reasons.append("evidence_text")
        if ctx.seg_source.get(ref.segment_id) in ctx.mock_sources or ref.source_id in ctx.mock_sources:
            reasons.append("evidence_source")
    for fid in block.fact_ids:
        f = ctx.facts.get(fid)
        if f and any(r.source_id in ctx.mock_sources for r in f.evidence_refs):
            reasons.append("fact_source")
    if block.type == "image":
        aid = block.content.get("asset_id")
        if ctx.asset_source.get(aid) in ctx.mock_sources:
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
                if f and f.status == "supported" and f.field_key in keys and value_in_text(f.value, text):
                    return True
    return False


def server_checks(document: Document, ctx: Context) -> tuple[list[IssueDraft], list[CheckRecord]]:
    drafts: list[IssueDraft] = []
    records: list[CheckRecord] = []
    name_fact_ids = [f.fact_id for f in ctx.facts.values() if f.field_key in REQUIRED_NAME_KEYS]
    biz_fact_ids = [f.fact_id for f in ctx.facts.values() if f.field_key in REQUIRED_BUSINESS_KEYS]
    if not _required_present(document, ctx, REQUIRED_NAME_KEYS):
        drafts.append(IssueDraft("content", "REQUIRED_MISSING", "blocker",
                                 "회사명이 실제 문서 블록에 없습니다(사실 참조만으로는 통과하지 않습니다).",
                                 fact_ids=sorted(name_fact_ids)))
    if not _required_present(document, ctx, REQUIRED_BUSINESS_KEYS):
        drafts.append(IssueDraft("content", "REQUIRED_MISSING", "blocker",
                                 "주요 사업/공정 설명이 실제 문서 블록에 없습니다.", fact_ids=sorted(biz_fact_ids)))
    records.append(CheckRecord(check_key="required_content", kind="server", result="issue" if drafts else "ok"))

    conflict_by_fact: dict[str, Issue] = {}
    for iss in ctx.preflight_issues:
        if iss.code == "VALUE_CONFLICT" and iss.severity == "blocker":
            for fid in iss.fact_ids:
                conflict_by_fact[fid] = iss

    for page in document.pages:
        for block in page.blocks:
            bid = block.block_id
            texts = block_texts(block)
            joined = " ".join(texts).strip()
            before = len(drafts)

            # 근거 없는 사실 주장(㉛). 종류가 아니라 내용으로 판단한다.
            if not block.fact_ids and not is_placeholder(joined):
                if block.type in ("paragraph", "list") and not is_connector(joined):
                    drafts.append(IssueDraft("content", "UNSUPPORTED_CLAIM", "blocker",
                                             "근거(fact_ids·evidence_refs)가 없는 사실 주장입니다. 주장을 지우거나 근거를 연결한 뒤 다시 검증하세요.",
                                             block_ids=[bid]))
                elif block.type in ("heading", "image") and joined and not is_label(joined):
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

def latest_validation(conn: sqlite3.Connection, document_id: str, document_revision: int,
                      input_revision: int) -> sqlite3.Row | None:
    # created_at은 초 단위라 같은 값이 생긴다. 저장 순서(rowid)로 보조 정렬한다(ID 문자열 정렬 금지).
    return conn.execute(
        "SELECT * FROM validations WHERE document_id=? AND document_revision=? AND input_revision=? "
        "AND status<>'pending' ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (document_id, document_revision, input_revision)).fetchone()


def base_validation(conn: sqlite3.Connection, document_id: str, document_revision: int,
                    input_revision: int) -> sqlite3.Row | None:
    """재사용 기준: 같은 문서·같은 입력 버전에서 현재보다 낮은 revision의 가장 최근 유효 검증."""
    return conn.execute(
        "SELECT * FROM validations WHERE document_id=? AND input_revision=? AND document_revision<? "
        "AND status<>'pending' ORDER BY document_revision DESC, created_at DESC, rowid DESC LIMIT 1",
        (document_id, input_revision, document_revision)).fetchone()


def changed_blocks(current: dict[str, str], base: sqlite3.Row | None) -> tuple[set[str], set[str]]:
    """(바뀐/새 블록, 그대로인 블록). base가 없으면 전부 바뀐 것으로 본다."""
    if base is None:
        return set(current), set()
    old = json.loads(base["fingerprints_json"])
    changed = {b for b, fp in current.items() if old.get(b) != fp}
    return changed, set(current) - changed


# ---------------- Issue 기록 ----------------

def _anchor(draft: IssueDraft, fps: dict[str, str], ctx: Context, input_revision: int) -> str:
    payload = {"blocks": [fps.get(b, "") for b in sorted(draft.block_ids)],
               "facts": [ (ctx.facts[f].value if f in ctx.facts else None) for f in sorted(draft.fact_ids)],
               "input_revision": input_revision}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def _covered_by_this_validation(row: sqlite3.Row, agent_covered_blocks: set[str], agent_full: bool) -> bool:
    """이번 검증에서 그 Issue가 속한 검사가 그 범위를 실제로 다시 봤는가.

    서버 검사는 매번 문서 전체를 다시 보므로 항상 True. Agent 검사는 이번에 넘긴 블록(agent_covered_blocks)에 한하고,
    block_ids가 없는 문서 전체 Agent Issue는 전체 검사(agent_full)였을 때만 True.
    """
    if row["origin"] != "agent":
        return True
    blocks = set(json.loads(row["block_ids_json"]))
    if not blocks:
        return agent_full
    return blocks <= agent_covered_blocks


def persist_issues(conn: sqlite3.Connection, session_id: str, document: Document, validation_id: str,
                   drafts: list[IssueDraft], fps: dict[str, str], ctx: Context, input_revision: int,
                   agent_covered_blocks: set[str], agent_full: bool) -> list[str]:
    """이번 검증이 만든 Issue를 기록한다. 현재 Issue ID 목록을 돌려준다.

    - 같은 identity_key(origin 포함)의 기존 행을 갱신한다(새 행 X).
    - resolved/excluded였는데 다시 검출되면 원인이 돌아온 것 → open으로 되돌리고 이전 resolution은 이력으로.
    - acknowledged는 관련 내용·근거·입력(anchor)이 바뀌었을 때만 open으로 재확인.
    - 이번에 검출되지 않은 open Issue는 그 검사가 그 범위를 실제로 다시 봤을 때만 서버가 resolved로 닫는다.
    """
    stamp = to_iso(now())
    produced: set[str] = set()
    for d in drafts:
        key = d.identity_key
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
                if resolution:
                    history.append({**json.loads(resolution), "reopened_at": stamp, "reopened_by_validation": validation_id,
                                    "previous_status": status})
                status, resolution = "open", None
            conn.execute(
                "UPDATE issues SET severity=?, message=?, status=?, anchor_fingerprint=?, resolution_json=?, "
                "resolution_history_json=?, last_validation_id=?, updated_at=? WHERE issue_id=?",
                (d.severity, d.message, status, anchor, resolution, json.dumps(history, ensure_ascii=False),
                 validation_id, stamp, row["issue_id"]))
        produced.add(key)

    # 이번에 다시 나오지 않은 open Issue: 그 검사가 그 범위를 실제로 다시 본 경우에만 원인이 사라진 것으로 보고 닫는다.
    for row in conn.execute("SELECT * FROM issues WHERE document_id=? AND status='open'", (document.document_id,)).fetchall():
        if row["identity_key"] in produced:
            continue
        if not _covered_by_this_validation(row, agent_covered_blocks, agent_full):
            continue  # 재실행하지 않은 검사의 Issue(문서 전체 Issue 포함)는 보존
        resolution = {"action": "resolved", "by": "server", "reason": "재검증에서 원인이 더 이상 확인되지 않음",
                      "at": stamp, "document_revision": document.document_revision, "input_revision": input_revision,
                      "validation_id": validation_id}
        conn.execute("UPDATE issues SET status='resolved', resolution_json=?, last_validation_id=?, updated_at=? WHERE issue_id=?",
                     (json.dumps(resolution, ensure_ascii=False), validation_id, stamp, row["issue_id"]))
    return [r["issue_id"] for r in conn.execute("SELECT issue_id FROM issues WHERE document_id=? ORDER BY created_at, rowid",
                                                 (document.document_id,))]


def compute_validation_status(conn: sqlite3.Connection, document_id: str) -> str:
    rows = conn.execute("SELECT severity FROM issues WHERE document_id=? AND status='open'", (document_id,)).fetchall()
    if any(r["severity"] == "blocker" for r in rows):
        return "failed"
    if any(r["severity"] == "warning" for r in rows):
        return "needs_review"
    return "passed"


def save_validation(conn: sqlite3.Connection, session_id: str, document: Document, input_revision: int,
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


def refresh_validation_status(conn: sqlite3.Connection, validation_id: str, document_id: str) -> str:
    """Issue 해결 뒤 최종 상태를 현재 문서 전체의 미해결 문제로 다시 합산한다."""
    status = compute_validation_status(conn, document_id)
    issue_ids = [r["issue_id"] for r in conn.execute("SELECT issue_id FROM issues WHERE document_id=? ORDER BY created_at, rowid", (document_id,))]
    conn.execute("UPDATE validations SET status=?, issue_ids_json=?, updated_at=? WHERE validation_id=?",
                 (status, json.dumps(issue_ids), to_iso(now()), validation_id))
    return status


# ---------------- 출력 ----------------

def to_validation_out(row: sqlite3.Row) -> ValidationOut:
    checks = [CheckRecord.model_validate(c) for c in json.loads(row["checks_json"])]
    checked = sorted({b for c in checks if c.kind == "agent" and c.result != "skipped" and c.reused_from_validation_id is None for b in c.block_ids})
    reused = sorted({b for c in checks if c.reused_from_validation_id is not None for b in c.block_ids})
    return ValidationOut(validation_id=row["validation_id"], document_id=row["document_id"],
                         document_revision=row["document_revision"], input_revision=row["input_revision"],
                         status=row["status"], issue_ids=json.loads(row["issue_ids_json"]), checks=checks,
                         agent_called=bool(row["agent_called"]), checked_block_ids=checked, reused_block_ids=reused,
                         base_validation_id=row["base_validation_id"], created_at=row["created_at"])


def issue_to_out(row: sqlite3.Row) -> IssueOut:
    return IssueOut(issue_id=row["issue_id"], scope=row["scope"], code=row["code"], severity=row["severity"],
                    status=row["status"], message=row["message"], source_ids=json.loads(row["source_ids_json"]),
                    fact_ids=json.loads(row["fact_ids_json"]), block_ids=json.loads(row["block_ids_json"]),
                    resolution=json.loads(row["resolution_json"]) if row["resolution_json"] else None,
                    origin=row["origin"], created_at=row["created_at"], updated_at=row["updated_at"])


def list_issues(conn: sqlite3.Connection, document_id: str) -> list[IssueOut]:
    return [issue_to_out(r) for r in conn.execute("SELECT * FROM issues WHERE document_id=? ORDER BY created_at, rowid", (document_id,))]


def compute_document_status(conn: sqlite3.Connection, document_id: str, document_revision: int, input_revision: int) -> str:
    """읽을 때 한 곳에서 계산한다(문서 조회·세션 summary 공용). document_revisions.status는 캐시일 뿐이다."""
    if conn.execute("SELECT 1 FROM approvals WHERE document_id=? AND document_revision=? AND input_revision=? AND status='active'",
                    (document_id, document_revision, input_revision)).fetchone():
        return "approved"
    v = latest_validation(conn, document_id, document_revision, input_revision)
    if v is None:
        return "draft"
    return "ready_for_approval" if v["status"] == "passed" else "review_required"
