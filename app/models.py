"""API 요청·응답 모델. contracts.md 계약 1.3과 OpenAPI의 원본이다.

DB 테이블은 orm_models.py에, 요청 값의 형식 검사는 여기에 둔다.
소유권·현재 버전·근거의 유효성 같은 업무 검사는 서비스에서 수행한다.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import NotRequired, TypedDict


MAX_SQLITE_INTEGER = 2**63 - 1
RequestRevision = Annotated[int, Field(strict=True, ge=1, le=MAX_SQLITE_INTEGER)]
NonBlankText = Annotated[str, Field(min_length=1, pattern=r"\S")]
SourceKind = Literal["company", "interview", "certificate", "photo", "other"]
DocumentStatus = Literal["draft", "review_required", "ready_for_approval", "approved"]
JobKind = Literal["read", "preflight", "draft", "propose", "validate", "layout_check", "export"]


class ApiErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str


class ApiErrorOut(BaseModel):
    error: ApiErrorDetail


class Brief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: NonBlankText
    emphasis: list[str] = Field(default_factory=list)
    direction: Literal["balanced", "quality_process", "customer_response"] = "balanced"
    target_pages: Literal[1, 4, 6, 8, 10] = 4
    photo_preference: Literal["none", "balanced", "many"] = "balanced"

    @field_validator("target_pages", mode="before")
    @classmethod
    def require_integer_pages(cls, value):
        # Python equality would otherwise let True and 1.0 match Literal[1].
        if type(value) is not int:
            raise ValueError("target_pages must be an integer")
        return value


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brief: Brief
    demo: bool = Field(default=False, strict=True)


class DocumentSummary(BaseModel):
    demo: bool = False
    document_id: str
    document_revision: int
    status: DocumentStatus


class SessionOut(BaseModel):
    demo: bool = False
    session_id: str
    status: Literal["active", "closed", "expired"]
    input_revision: int
    created_at: str
    last_activity_at: str
    expires_at: str
    brief: Brief
    selected_source_ids: list[str]
    document_summary: DocumentSummary | None = None


class SessionDeleteOut(BaseModel):
    session_id: str
    status: Literal["closed"]
    cleanup: Literal["done", "pending"]


class InputsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_input_revision: RequestRevision
    brief: Brief | None = None
    selected_source_ids: list[NonBlankText] | None = Field(
        default=None, json_schema_extra={"uniqueItems": True},
    )

    @field_validator("selected_source_ids")
    @classmethod
    def unique_selection(cls, value):
        if value is not None and len(value) != len(set(value)):
            raise ValueError("selected_source_ids must not contain duplicates")
        return value

    @model_validator(mode="after")
    def require_change(self):
        if self.brief is None and self.selected_source_ids is None:
            raise ValueError("brief or selected_source_ids is required; [] clears the selection")
        return self


class InputsOut(BaseModel):
    session_id: str
    input_revision: int
    selected_source_ids: list[str]
    preflight_invalidated: bool


class SourceWarning(BaseModel):
    locator: dict[str, Any] | None = None
    code: str
    message: str
    action: str | None = None


class SourceOut(BaseModel):
    role: Literal["evidence", "instruction"] = "evidence"
    origin_kind: Literal["real", "mock", "demo"] = "real"
    source_id: str
    source_version: int
    scope: Literal["registered", "session"]
    session_id: str | None
    name: str
    mime_type: str
    size_bytes: int
    kind: SourceKind
    parse_status: Literal["queued", "reading", "complete", "partial", "failed"]
    text_available: bool
    image_available: bool
    usable_segment_ids: list[str] = Field(default_factory=list)
    # 자료의 ready 이미지와 GET assets를 연결한다(계약 1.3).
    asset_ids: list[str] = Field(default_factory=list)
    warnings: list[SourceWarning] = Field(default_factory=list)
    expires_at: str | None
    # 등록 자료 메타. 세션 업로드 자료는 document_date=None, is_mock=False, evidence=True.
    document_date: str | None = None          # "2017"처럼 연도만 있는 값도 문자열 그대로
    use_as_company_evidence: bool = True      # False면 선택·검색·근거에서 제외(목록에는 표시)
    is_mock: bool = False


class SourceListOut(BaseModel):
    items: list[SourceOut]


class UploadOut(BaseModel):
    job_id: str
    items: list[SourceOut]


class SourceDeleteOut(BaseModel):
    source_id: str
    deleted: bool
    input_revision: int


class JobProgress(BaseModel):
    stage: str
    message: str | None = None


class JobError(BaseModel):
    code: str
    message: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class SourcesResultRef(TypedDict):
    type: Literal["sources"]
    source_ids: list[str]


class PreflightResultRef(TypedDict):
    type: Literal["preflight"]
    preflight_id: str


class DocumentResultRef(TypedDict):
    type: Literal["document"]
    document_id: str
    document_revision: int


class ProposalResultRef(TypedDict):
    type: Literal["proposal"]
    proposal_id: str
    status: Literal["proposed", "stale"]


class ValidationResultRef(TypedDict):
    type: Literal["validation"]
    validation_id: str
    status: Literal["passed", "needs_review", "failed"]


class LayoutCheckResultRef(TypedDict):
    layout_check_id: str
    format: Literal["pdf", "docx"]
    status: Literal["passed", "failed"]
    layout_ok: bool
    publication_policy_ok: bool
    actual_pages: int | None
    artifact_id: str
    preview_asset_ids: list[str]
    preview_basis: Literal["pdf"]
    warnings: list[str]
    demo: NotRequired[bool]  # 시연 구분 도입 전에 저장된 Job도 읽을 수 있다.


class ExportResultRef(TypedDict):
    export_id: str
    artifact_id: str
    format: Literal["pdf", "docx"]


JobResultRef = (SourcesResultRef | PreflightResultRef | DocumentResultRef | ProposalResultRef
                | ValidationResultRef | LayoutCheckResultRef | ExportResultRef)


class JobOut(BaseModel):
    job_id: str
    kind: JobKind
    status: Literal["queued", "running", "waiting_user", "succeeded", "failed", "cancelled"]
    progress: JobProgress
    result_ref: JobResultRef | None = None
    error: JobError | None = None
    created_at: str
    updated_at: str


class JobAccepted(BaseModel):
    job_id: str
    status: Literal["queued", "running"]
    kind: JobKind
    session_id: str
    created_at: str


# ---------------- 사실·근거·문제 (contracts.md 2절) ----------------

class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    source_version: int
    segment_id: str
    locator: dict[str, Any]
    excerpt: str


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: str
    field_key: str
    value: str | None
    status: Literal["supported", "needs_confirmation", "conflict", "missing"]
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    conditions: dict[str, Any] | None = None
    alternatives: list[dict[str, Any]] | None = None


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: str
    scope: Literal["source", "content", "layout"]
    code: str
    severity: Literal["blocker", "warning", "info"]
    status: Literal["open", "resolved", "excluded", "acknowledged"] = "open"
    message: str
    source_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)
    resolution: dict[str, Any] | None = None


class Recommendations(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suggested_pages: Literal[1, 4, 6, 8, 10]
    reason: str
    needed: list[str] = Field(default_factory=list)


class PreflightCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_input_revision: RequestRevision


class PreflightOut(BaseModel):
    preflight_id: str
    session_id: str
    input_revision: int
    usable_source_ids: list[str]
    facts: list[Fact]
    issues: list[Issue]
    recommendations: Recommendations
    can_generate: bool
    confirmed_at: str | None


# ---------------- 문서 (contracts.md Document) ----------------

class Block(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_id: str
    type: Literal["heading", "paragraph", "list", "image", "image_placeholder"]
    content: dict[str, Any]
    fact_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class Page(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_id: str
    title: str
    layout_key: str
    blocks: list[Block]


class Document(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    document_id: str
    session_id: str
    document_revision: int
    input_revision: int
    title: str
    target_pages: Literal[1, 4, 6, 8, 10]
    status: DocumentStatus
    pages: list[Page]


# ---------------- 검증·문제·승인 (BE-06) ----------------

class CheckRecord(BaseModel):
    check_key: str
    kind: Literal["server", "agent"]
    block_ids: list[str] = Field(default_factory=list)
    result: Literal["ok", "issue", "skipped"]
    reused_from_validation_id: str | None = None


class ValidationOut(BaseModel):
    validation_id: str
    document_id: str
    document_revision: int
    input_revision: int
    status: Literal["pending", "passed", "needs_review", "failed"]
    issue_ids: list[str]
    # 계약 확인 ㉓: 재사용/신규 검사 연결(백엔드 제안)
    checks: list[CheckRecord] = Field(default_factory=list)
    agent_called: bool = False
    checked_block_ids: list[str] = Field(default_factory=list)
    reused_block_ids: list[str] = Field(default_factory=list)
    base_validation_id: str | None = None
    created_at: str


class IssueOut(Issue):
    origin: Literal["server", "agent", "preflight", "layout"]   # layout = 배치 검사(BE-08, 계약 확인 ㊳)
    layout_format: Literal["pdf", "docx"] | None = None          # scope=layout Issue의 형식. 공개 허가 Issue는 None(형식 무관)
    created_at: str
    updated_at: str


class IssueListOut(BaseModel):
    document_id: str
    document_revision: int
    validation_id: str | None
    issues: list[IssueOut]


class ValidateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: RequestRevision
    input_revision: RequestRevision


class Resolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["resolved", "excluded", "acknowledged"]
    reason: NonBlankText


class IssueResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: RequestRevision
    resolution: Resolution
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    input_revision: RequestRevision | None = None
    validation_id: NonBlankText | None = None

    @model_validator(mode="after")
    def acknowledgement_context(self):
        if self.resolution.action == "acknowledged" and (self.input_revision is None or self.validation_id is None):
            raise ValueError("경고 확인에는 input_revision과 validation_id가 필요합니다.")
        return self


class IssueResolveOut(BaseModel):
    issue: IssueOut
    validation: ValidationOut | None
    document_status: DocumentStatus


class ApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: RequestRevision
    input_revision: RequestRevision
    format: Literal["pdf", "docx"]
    validation_id: NonBlankText
    layout_check_id: NonBlankText
    confirmed: bool = Field(strict=True)


class ApprovalOut(BaseModel):
    demo: bool = False
    approval_id: str
    document_id: str
    document_revision: int
    input_revision: int
    format: Literal["pdf", "docx"]
    validation_id: str
    layout_check_id: str
    template_version: str
    render_options_hash: str
    asset_manifest_hash: str
    approved_at: str
    approved_by: str
    status: Literal["active", "invalidated"]
    invalidated_at: str | None = None
    invalidated_reason: str | None = None   # document_changed / input_changed / superseded / artifact_invalid / publication_changed / preflight_conflict
    renderer: str | None = None             # BE-08(㉝ 근거): 검사한 렌더러. 식별값 아님
    artifact_id: str | None = None          # BE-08: 검사한 불변 산출물


# ---------------- 배치 검사·출력 (BE-08, 계약 확인 ㉜·㉟·㊲) ----------------

class LayoutCheckCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: RequestRevision
    format: Literal["pdf", "docx"]


class LayoutCheckRecordOut(BaseModel):
    check_key: Literal["overflow", "broken_image", "placeholder_remaining"]
    required: bool
    result: Literal["ok", "finding", "not_checked"]
    block_ids: list[str] = Field(default_factory=list)
    page_ids: list[str] = Field(default_factory=list)
    reason: str | None = None


class FindingOut(BaseModel):
    kind: Literal["overflow", "broken_image", "placeholder_remaining"]
    page_id: str
    block_id: str | None = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class PublicationBlockOut(BaseModel):
    asset_id: str
    block_ids: list[str] = Field(default_factory=list)
    reason: Literal["not_decided", "denied"]


class LayoutCheckOut(BaseModel):
    demo: bool = False
    layout_check_id: str
    document_id: str
    document_revision: int
    input_revision: int
    format: Literal["pdf", "docx"]
    status: Literal["pending", "passed", "failed"]
    template_version: str
    render_options_hash: str
    asset_manifest_hash: str
    actual_pages: int | None
    issue_ids: list[str] = Field(default_factory=list)
    layout_ok: bool
    publication_policy_ok: bool
    publication_blocks: list[PublicationBlockOut] = Field(default_factory=list)
    checks: list[LayoutCheckRecordOut] = Field(default_factory=list)
    findings: list[FindingOut] = Field(default_factory=list)
    fail_reasons: list[str] = Field(default_factory=list)
    renderer: str | None = None
    artifact_id: str | None = None
    preview_asset_ids: list[str] = Field(default_factory=list)
    preview_basis: Literal["pdf"] | None = None    # DOCX도 같은 스냅샷의 PDF 렌더로 미리보기(검사 증거 아님)
    warnings: list[str] = Field(default_factory=list)
    created_at: str


class LayoutChecksByFormat(TypedDict):
    pdf: LayoutCheckOut | None
    docx: LayoutCheckOut | None


class DocumentOut(BaseModel):
    demo: bool = False
    document: Document
    validation: ValidationOut | None = None   # 현재 문서·입력 버전의 최신 검증. 없으면 null
    approval: ApprovalOut | None = None       # 현재 문서·입력 버전의 active 승인(어느 형식이든). 없으면 null
    layout_checks: LayoutChecksByFormat = Field(default_factory=lambda: {"pdf": None, "docx": None})


class ExportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: NonBlankText
    format: Literal["pdf", "docx"]


class ExportOut(BaseModel):
    demo: bool = False
    export_id: str
    approval_id: str
    format: Literal["pdf", "docx"]
    status: Literal["queued", "generating", "ready", "failed"]
    artifact_id: str | None
    expires_at: str
    error: JobError | None = None
    attempt: int = 1
    warnings: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ExportAccepted(BaseModel):
    export: ExportOut
    job_id: str | None


class DraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preflight_id: NonBlankText
    input_revision: RequestRevision
    confirmed: bool = Field(strict=True)


# ---------------- 문서 수정 연산 8종 (contracts.md Document절 허용 목록) ----------------

class OpReplaceBlockContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["replace_block_content"]
    block_id: str
    content: dict[str, Any]


class OpInsertBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["insert_block"]
    page_id: str
    after_block_id: str | None
    block: Block


class OpDeleteBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["delete_block"]
    block_id: str


class OpMoveBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["move_block"]
    block_id: str
    target_page_id: str
    after_block_id: str | None


class OpInsertPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["insert_page"]
    after_page_id: str | None
    page: Page


class OpRenamePage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["rename_page"]
    page_id: str
    title: NonBlankText


class OpMovePage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["move_page"]
    page_id: str
    after_page_id: str | None


class OpDeletePage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["delete_page"]
    page_id: str


Operation = Annotated[
    OpReplaceBlockContent | OpInsertBlock | OpDeleteBlock | OpMoveBlock
    | OpInsertPage | OpRenamePage | OpMovePage | OpDeletePage,
    Field(discriminator="op"),
]


class DocumentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: RequestRevision
    operations: list[Operation] = Field(min_length=1)


class DocumentChangeOut(BaseModel):
    document_id: str
    document_revision: int
    input_revision: int
    status: DocumentStatus
    validation_job_id: str | None = None   # BE-06에서 채운다


class RestoreBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: RequestRevision
    restore_from_revision: RequestRevision


# ---------------- Proposal (contracts.md Proposal절 + 계약 확인 ⑰ rationale·candidates) ----------------

class ProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: RequestRevision
    input_revision: RequestRevision
    target_block_ids: list[NonBlankText] = Field(min_length=1, json_schema_extra={"uniqueItems": True})
    instruction: NonBlankText
    kind: Literal["text", "structure", "image"]

    @field_validator("target_block_ids")
    @classmethod
    def unique_targets(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("target_block_ids must not contain duplicates")
        return value


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    label: str
    changes: list[Operation]


class ProposalOut(BaseModel):
    proposal_id: str
    document_id: str
    base_document_revision: int
    base_input_revision: int
    target_block_ids: list[str]
    kind: Literal["text", "structure", "image"]
    instruction: str
    changes: list[Operation]
    rationale: str
    candidates: list[Candidate] | None = None
    status: Literal["proposed", "applied", "rejected", "stale"]
    applied_revision: int | None = None
    created_at: str
    updated_at: str


class ApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: RequestRevision
    selected_candidate_id: NonBlankText | None = None


class ProposalStatusOut(BaseModel):
    proposal_id: str
    status: Literal["proposed", "applied", "rejected", "stale"]
    document_id: str
    document_revision: int
