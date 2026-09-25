"""API 요청·응답 모델. 필드명은 contracts.md 2절 그대로 쓴다.

계약 확인 대기 항목(task_backend.md 6.1-5):
- ① Brief에 company_name_hint 없음 → 계약대로 두었다.
- ② Job.progress 형식 미정 → handoff/api_examples_v1.1.json의 제안 형식.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Brief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str = Field(min_length=1)
    emphasis: list[str] = Field(default_factory=list)
    direction: Literal["balanced", "quality_process", "customer_response"] = "balanced"
    target_pages: Literal[1, 4, 6, 8, 10] = 4
    photo_preference: Literal["none", "balanced", "many"] = "balanced"


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brief: Brief


class DocumentSummary(BaseModel):
    document_id: str
    document_revision: int
    status: str


class SessionOut(BaseModel):
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
    expected_input_revision: int
    brief: Brief | None = None
    selected_source_ids: list[str] | None = None


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
    source_id: str
    source_version: int
    scope: Literal["registered", "session"]
    session_id: str | None
    name: str
    mime_type: str
    size_bytes: int
    kind: Literal["company", "interview", "certificate", "photo", "other"]
    parse_status: Literal["queued", "reading", "complete", "partial", "failed"]
    text_available: bool
    image_available: bool
    usable_segment_ids: list[str] = Field(default_factory=list)
    warnings: list[SourceWarning] = Field(default_factory=list)
    expires_at: str | None


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


class JobOut(BaseModel):
    job_id: str
    kind: str
    status: Literal["queued", "running", "waiting_user", "succeeded", "failed", "cancelled"]
    progress: JobProgress
    result_ref: dict[str, Any] | None = None
    error: JobError | None = None
    created_at: str
    updated_at: str


class JobAccepted(BaseModel):
    job_id: str
    status: Literal["queued"]
    kind: str
    session_id: str
    created_at: str
