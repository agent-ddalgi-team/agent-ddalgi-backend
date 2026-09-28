"""ERD v2의 25개 테이블 ORM. API용 Pydantic 모델(app/models.py)과 구분한다.

기존 19개 테이블의 필드/JSON·UTC TEXT 형식을 유지하며 신규 이력 테이블을 추가한다.
이 모듈은 DB에 연결하거나 테이블을 생성하지 않는다. DB 생성은 init_orm_db에서 명시한다.
신규 영향 검토/개별 경고 확인 UI와 정책은 이 모델 선언만으로 활성화되지 않는다.
"""
from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Integer, REAL, Text, UniqueConstraint, text as sql_text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass



class WorkSession(Base):
    __tablename__ = 'sessions'
    session_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    owner_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    input_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    brief_json: Mapped[str] = mapped_column(Text, nullable=False)
    selected_source_ids: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_activity_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    closed_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    cleanup_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    purged_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    demo: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text('0'))
    __table_args__ = (
        CheckConstraint('demo IN (0,1)'),
        Index('ix_sessions_owner', 'owner_id'),
        Index('ix_sessions_status_expires', 'status', 'expires_at'),
    )


class Source(Base):
    __tablename__ = 'sources'
    source_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    parse_status: Mapped[str] = mapped_column(Text, nullable=False)
    text_available: Mapped[int] = mapped_column(Integer, nullable=False)
    image_available: Mapped[int] = mapped_column(Integer, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'[]'"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin_group: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_date_verified: Mapped[int | None] = mapped_column(Integer, nullable=True)
    date_from_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    use_as_company_evidence: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text('1'))
    hash_verified: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hash_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_mock: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text('0'))
    imported_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin_kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'real'"))
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'evidence'"))
    current_run_id: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(['session_id'], ['sessions.session_id']),
        CheckConstraint("origin_kind IN ('real','mock','demo')"),
        CheckConstraint("role IN ('evidence','instruction')"),
        CheckConstraint("(scope = 'session' AND session_id IS NOT NULL) OR (scope = 'registered' AND session_id IS NULL)"),
        Index('ix_sources_session', 'session_id'),
        ForeignKeyConstraint(['current_run_id', 'source_id', 'source_version'], ['extraction_runs.run_id', 'extraction_runs.source_id', 'extraction_runs.source_version']),
    )


class Segment(Base):
    __tablename__ = 'segments'
    segment_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    locator_json: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_id: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(['source_id'], ['sources.source_id']),
        Index('ix_segments_source', 'source_id', 'ordinal'),
        ForeignKeyConstraint(['run_id', 'source_id', 'source_version'], ['extraction_runs.run_id', 'extraction_runs.source_id', 'extraction_runs.source_version']),
    )


class Asset(Base):
    __tablename__ = 'assets'
    asset_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption_candidate: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_as_candidate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_for_external_use: Mapped[int | None] = mapped_column(Integer, nullable=True)
    photo_locator_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_id: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(['source_id'], ['sources.source_id']),
        Index('ix_assets_session', 'session_id'),
        ForeignKeyConstraint(['run_id', 'source_id', 'source_version'], ['extraction_runs.run_id', 'extraction_runs.source_id', 'extraction_runs.source_version']),
    )


class Job(Base):
    __tablename__ = 'jobs'
    job_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    progress_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_ref_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    input_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(['session_id'], ['sessions.session_id']),
        Index('ix_jobs_session', 'session_id'),
    )


class Preflight(Base):
    __tablename__ = 'preflights'
    preflight_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    input_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    usable_source_ids: Mapped[str] = mapped_column(Text, nullable=False)
    facts_json: Mapped[str] = mapped_column(Text, nullable=False)
    issues_json: Mapped[str] = mapped_column(Text, nullable=False)
    recommendations_json: Mapped[str] = mapped_column(Text, nullable=False)
    can_generate: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(['session_id'], ['sessions.session_id']),
        Index('ix_preflights_session', 'session_id', 'input_revision'),
    )


class DocumentRecord(Base):
    __tablename__ = 'documents'
    document_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    target_pages: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(['session_id'], ['sessions.session_id']),
        Index('ix_documents_session', 'session_id'),
    )


class DocumentRevision(Base):
    __tablename__ = 'document_revisions'
    document_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    input_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_id: Mapped[str | None] = mapped_column(Text)
    preflight_id: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(['document_id'], ['documents.document_id']),
        ForeignKeyConstraint(['session_id'], ['sessions.session_id']),
        ForeignKeyConstraint(['preflight_id'], ['preflights.preflight_id']),
        UniqueConstraint('document_id', 'revision', 'session_id'),
    )


class Proposal(Base):
    __tablename__ = 'proposals'
    proposal_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[str] = mapped_column(Text, nullable=False)
    base_document_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    base_input_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    target_block_ids: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    changes_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    applied_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(['document_id'], ['documents.document_id']),
        ForeignKeyConstraint(['session_id'], ['sessions.session_id']),
        Index('ix_proposals_document', 'document_id', 'status'),
    )


class ValidationRecord(Base):
    __tablename__ = 'validations'
    validation_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    input_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    issue_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    checks_json: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprints_json: Mapped[str] = mapped_column(Text, nullable=False)
    base_validation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_called: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text('0'))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(['document_id'], ['documents.document_id']),
        ForeignKeyConstraint(['session_id'], ['sessions.session_id']),
        Index('ix_validations_document', 'document_id', 'document_revision', 'input_revision'),
    )


class Issue(Base):
    __tablename__ = 'issues'
    issue_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[str] = mapped_column(Text, nullable=False)
    identity_key: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    fact_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    block_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_history_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'[]'"))
    first_validation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_validation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    layout_format: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(['document_id'], ['documents.document_id']),
        ForeignKeyConstraint(['session_id'], ['sessions.session_id']),
        UniqueConstraint('document_id', 'identity_key'),
        Index('ix_issues_document', 'document_id', 'status'),
    )


class LayoutCheck(Base):
    __tablename__ = 'layout_checks'
    layout_check_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    input_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    format: Mapped[str] = mapped_column(Text, nullable=False)
    template_version: Mapped[str] = mapped_column(Text, nullable=False)
    render_options_hash: Mapped[str] = mapped_column(Text, nullable=False)
    asset_manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    actual_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issue_ids_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'[]'"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    layout_ok: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publication_policy_ok: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checks_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    findings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    fail_reasons_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    renderer: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_checked_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_blocks_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    demo: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text('0'))
    __table_args__ = (
        ForeignKeyConstraint(['document_id'], ['documents.document_id']),
        ForeignKeyConstraint(['session_id'], ['sessions.session_id']),
        CheckConstraint('demo IN (0,1)'),
        Index('ix_layout_checks_document', 'document_id', 'document_revision', 'format'),
    )


class Approval(Base):
    __tablename__ = 'approvals'
    approval_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    input_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    format: Mapped[str] = mapped_column(Text, nullable=False)
    validation_id: Mapped[str] = mapped_column(Text, nullable=False)
    layout_check_id: Mapped[str] = mapped_column(Text, nullable=False)
    template_version: Mapped[str] = mapped_column(Text, nullable=False)
    render_options_hash: Mapped[str] = mapped_column(Text, nullable=False)
    asset_manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    approved_at: Mapped[str] = mapped_column(Text, nullable=False)
    approved_by: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    invalidated_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    invalidated_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    renderer: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_checked_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    demo: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text('0'))
    confirmation_id: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(['document_id'], ['documents.document_id']),
        ForeignKeyConstraint(['session_id'], ['sessions.session_id']),
        CheckConstraint('demo IN (0,1)'),
        Index('ix_approvals_document', 'document_id', 'status'),
        ForeignKeyConstraint(['confirmation_id'], ['confirmations.confirmation_id']),
    )


class Artifact(Base):
    __tablename__ = 'artifacts'
    artifact_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    input_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    format: Mapped[str] = mapped_column(Text, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    template_version: Mapped[str] = mapped_column(Text, nullable=False)
    render_options_hash: Mapped[str] = mapped_column(Text, nullable=False)
    asset_manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    renderer: Mapped[str] = mapped_column(Text, nullable=False)
    actual_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    layout_check_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    demo: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text('0'))
    __table_args__ = (
        ForeignKeyConstraint(['document_id'], ['documents.document_id']),
        ForeignKeyConstraint(['session_id'], ['sessions.session_id']),
        CheckConstraint('demo IN (0,1)'),
        Index('ix_artifacts_session', 'session_id'),
    )


class Export(Base):
    __tablename__ = 'exports'
    export_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    approval_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    input_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    format: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    reuse_key: Mapped[str] = mapped_column(Text, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text('1'))
    job_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    renderer: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_checked_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    finalized_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    demo: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text('0'))
    __table_args__ = (
        ForeignKeyConstraint(['session_id'], ['sessions.session_id']),
        CheckConstraint('demo IN (0,1)'),
        Index('ix_exports_session', 'session_id', 'status'),
        Index('ux_exports_active', 'reuse_key', unique=True, sqlite_where=sql_text("status IN ('queued', 'generating', 'ready')")),
    )


class LayoutPreview(Base):
    __tablename__ = 'layout_previews'
    asset_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    layout_check_id: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'ready'"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(['session_id'], ['sessions.session_id']),
        Index('ix_layout_previews_session', 'session_id'),
    )


class IdempotencyKey(Base):
    __tablename__ = 'idempotency_keys'
    idem_key: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    owner_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    path: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    body_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    purged_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (
        Index('ix_idempotency_session', 'session_id'),
    )


class CleanupTask(Base):
    __tablename__ = 'cleanup_queue'
    task_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    target_rel: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text('0'))
    next_retry_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    done_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (
        Index('ix_cleanup_queue_status', 'status', 'next_retry_at'),
        Index('ux_cleanup_queue_active', 'session_id', 'kind', 'target_rel', unique=True, sqlite_where=sql_text("status IN ('pending', 'running')")),
    )


class RegisteredImport(Base):
    __tablename__ = 'registered_imports'
    import_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    bundle_label: Mapped[str] = mapped_column(Text, nullable=False)
    with_mock: Mapped[int] = mapped_column(Integer, nullable=False)
    dry_run: Mapped[int] = mapped_column(Integer, nullable=False)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class SourceVersion(Base):
    """원본 파일 버전의 메타데이터. 정리 시 식별자와 해시만 남긴다."""

    __tablename__ = 'source_versions'
    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str | None] = mapped_column(Text)
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str | None] = mapped_column(Text)
    purged_at: Mapped[str | None] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(['source_id'], ['sources.source_id']),
        ForeignKeyConstraint(['session_id'], ['sessions.session_id']),
        CheckConstraint('version >= 1'),
        CheckConstraint('size_bytes >= 0'),
        Index('ix_source_versions_session', 'session_id'),
    )


class ExtractionRun(Base):
    """동일 파일을 다시 읽어도 이전 근거 구간을 구분할 수 있는 실행 기록."""

    __tablename__ = 'extraction_runs'
    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[str | None] = mapped_column(Text)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'[]'"))
    summary_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'{}'"))
    page_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(Text)
    purged_at: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(['source_id', 'source_version'], ['source_versions.source_id', 'source_versions.version']),
        ForeignKeyConstraint(['session_id'], ['sessions.session_id']),
        UniqueConstraint('run_id', 'source_id', 'source_version'),
        CheckConstraint('page_count IS NULL OR page_count >= 0'),
        Index('ix_extraction_runs_source', 'source_id', 'source_version', 'created_at'),
        Index('ix_extraction_runs_session', 'session_id'),
    )


class InputRevision(Base):
    """작성 조건을 덮어쓰지 않고 입력 버전별로 저장하는 스냅샷."""

    __tablename__ = 'input_revisions'
    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    brief_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    provenance: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'request'"))
    purged_at: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(['session_id'], ['sessions.session_id']),
        CheckConstraint('revision >= 1'),
    )


class SessionSourceSelection(Base):
    """입력 버전마다 선택한 원본 버전과 추출 실행을 고정한다."""

    __tablename__ = 'session_source_selections'
    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
    input_revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(['session_id', 'input_revision'], ['input_revisions.session_id', 'input_revisions.revision']),
        ForeignKeyConstraint(['source_id', 'source_version'], ['source_versions.source_id', 'source_versions.version']),
        ForeignKeyConstraint(['run_id', 'source_id', 'source_version'], ['extraction_runs.run_id', 'extraction_runs.source_id', 'extraction_runs.source_version']),
        Index('ix_session_source_selections_source', 'source_id', 'source_version'),
    )


class ImpactReview(Base):
    """자료 변경 영향 검토의 저장 기반. 생성·확인 정책은 별도 기능에서 구현한다."""

    __tablename__ = 'impact_reviews'
    review_id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    from_input_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    to_input_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    preflight_id: Mapped[str | None] = mapped_column(Text)
    items_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'[]'"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(Text)
    purged_at: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(['document_id', 'document_revision', 'session_id'], ['document_revisions.document_id', 'document_revisions.revision', 'document_revisions.session_id']),
        ForeignKeyConstraint(['session_id', 'from_input_revision'], ['input_revisions.session_id', 'input_revisions.revision']),
        ForeignKeyConstraint(['session_id', 'to_input_revision'], ['input_revisions.session_id', 'input_revisions.revision']),
        ForeignKeyConstraint(['preflight_id'], ['preflights.preflight_id']),
        Index('ix_impact_reviews_session', 'session_id', 'status'),
        Index('ix_impact_reviews_document', 'document_id', 'document_revision'),
    )


class Confirmation(Base):
    """서버가 확인한 사용자의 명시적 확인과 그 대상 버전을 기록한다."""

    __tablename__ = 'confirmations'
    confirmation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    input_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[str] = mapped_column(Text, nullable=False)
    issue_id: Mapped[str | None] = mapped_column(Text)
    impact_review_id: Mapped[str | None] = mapped_column(Text)
    validation_id: Mapped[str | None] = mapped_column(Text)
    layout_check_id: Mapped[str | None] = mapped_column(Text)
    artifact_id: Mapped[str | None] = mapped_column(Text)
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'[]'"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'active'"))
    invalidated_at: Mapped[str | None] = mapped_column(Text)
    purged_at: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(['document_id', 'document_revision', 'session_id'], ['document_revisions.document_id', 'document_revisions.revision', 'document_revisions.session_id']),
        ForeignKeyConstraint(['session_id', 'input_revision'], ['input_revisions.session_id', 'input_revisions.revision']),
        ForeignKeyConstraint(['issue_id'], ['issues.issue_id']),
        ForeignKeyConstraint(['impact_review_id'], ['impact_reviews.review_id']),
        ForeignKeyConstraint(['validation_id'], ['validations.validation_id']),
        ForeignKeyConstraint(['layout_check_id'], ['layout_checks.layout_check_id']),
        ForeignKeyConstraint(['artifact_id'], ['artifacts.artifact_id']),
        CheckConstraint("kind IN ('final_consent', 'warning_ack', 'impact_keep')"),
        CheckConstraint("status IN ('active', 'invalidated')"),
        Index('ix_confirmations_session', 'session_id', 'status'),
        Index('ix_confirmations_document', 'document_id', 'document_revision', 'input_revision'),
    )
