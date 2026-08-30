from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class AccessContext(BaseModel):
    user_id: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=200)
    groups: frozenset[str] = Field(default_factory=frozenset)


class WorkspaceContext(BaseModel):
    """Identity and entitlements resolved by the server, never by UI fields."""

    user_id: str
    tenant_id: str
    groups: list[str]
    auth_mode: Literal["development", "jwt"]
    can_observe: bool


class DocumentIn(BaseModel):
    document_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=1_000_000)
    allowed_groups: frozenset[str] = Field(default_factory=frozenset)
    classification: str = Field(default="internal", max_length=100)
    trust_tier: Literal[
        "authoritative",
        "approved-reference",
        "working-draft",
        "external",
        "untrusted",
        "expired",
    ] = "approved-reference"
    version: str = Field(default="1", max_length=100)
    valid_until: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StoredDocument(DocumentIn):
    tenant_id: str


class StoredChunk(BaseModel):
    chunk_id: str
    document_id: str
    tenant_id: str
    title: str
    text: str
    position: int = Field(ge=0)
    allowed_groups: frozenset[str] = Field(default_factory=frozenset)
    classification: str
    trust_tier: str
    version: str
    valid_until: datetime | None = None
    content_sha256: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def expired(self) -> bool:
        if self.trust_tier == "expired":
            return True
        if self.valid_until is None:
            return False
        limit = self.valid_until
        if limit.tzinfo is None:
            limit = limit.replace(tzinfo=UTC)
        return limit < datetime.now(UTC)


class QueryIn(BaseModel):
    question: str = Field(min_length=1)


class Evidence(BaseModel):
    source_id: str
    document_id: str
    title: str
    text: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    source_id: str
    document_id: str
    title: str
    score: float


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]
    grounded: bool
    model_provider: str
    model_name: str
    retrieval_confidence: float = Field(default=0, ge=0, le=1)
    reflection_attempts: int = Field(default=0, ge=0, le=2)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def grounded_answers_require_citations(self) -> "QueryResponse":
        if self.grounded and not self.citations:
            raise ValueError("grounded answers require at least one citation")
        return self


class IngestResponse(BaseModel):
    document_id: str
    indexed: bool
    chunk_count: int = Field(default=0, ge=0)
    deduplicated: bool = False


class SqlProposalIn(BaseModel):
    question: str = Field(min_length=1, max_length=8_000)
    schema_name: str = Field(min_length=1, max_length=200)


class SqlProposal(BaseModel):
    proposal_id: str = Field(default_factory=lambda: str(uuid4()))
    question: str
    schema_name: str
    sql: str
    status: Literal["pending", "approved", "rejected", "executed"] = "pending"
    requested_by: str
    tenant_id: str
    approved_by: str | None = None
    approval_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SqlApprovalIn(BaseModel):
    approved: bool
    reason: str = Field(min_length=3, max_length=1_000)


class SqlQueryResult(BaseModel):
    proposal_id: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int = Field(ge=0)
    truncated: bool = False
    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    lineage: dict[str, Any] = Field(default_factory=dict)


class ArtifactSource(BaseModel):
    source_id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=500)
    locator: str = Field(min_length=1, max_length=2_000)
    as_of: datetime | None = None


class ArtifactTable(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    columns: list[str] = Field(min_length=1, max_length=8)
    rows: list[list[str | int | float]] = Field(max_length=30)

    @model_validator(mode="after")
    def rows_match_columns(self) -> "ArtifactTable":
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("every table row must match the column count")
        return self


class ArtifactChartSeries(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    values: list[float] = Field(min_length=1, max_length=20)


class ArtifactChart(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    categories: list[str] = Field(min_length=1, max_length=20)
    series: list[ArtifactChartSeries] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def series_match_categories(self) -> "ArtifactChart":
        if any(len(series.values) != len(self.categories) for series in self.series):
            raise ValueError("chart series must match the category count")
        return self


class ArtifactSection(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(default="", max_length=4_000)
    bullets: list[str] = Field(default_factory=list, max_length=8)
    table: ArtifactTable | None = None
    chart: ArtifactChart | None = None
    source_ids: frozenset[str] = Field(default_factory=frozenset)
    speaker_notes: str = Field(default="", max_length=4_000)


class ArtifactSpec(BaseModel):
    kind: Literal["docx", "pptx"]
    filename: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
    title: str = Field(min_length=1, max_length=300)
    subtitle: str = Field(default="", max_length=500)
    audience: str = Field(min_length=1, max_length=300)
    purpose: str = Field(min_length=1, max_length=500)
    classification: str = Field(default="internal", max_length=100)
    sections: list[ArtifactSection] = Field(min_length=1, max_length=25)
    sources: list[ArtifactSource] = Field(default_factory=list, max_length=100)
    template_version: str = Field(default="standard-business-v1", max_length=100)

    @model_validator(mode="after")
    def all_claims_require_known_sources(self) -> "ArtifactSpec":
        known = {source.source_id for source in self.sources}
        for section in self.sections:
            has_claims = bool(section.summary or section.bullets or section.table or section.chart)
            if has_claims and not section.source_ids:
                raise ValueError(f"section '{section.title}' requires source_ids")
            unknown = section.source_ids - known
            if unknown:
                raise ValueError(f"section '{section.title}' has unknown sources: {sorted(unknown)}")
        expected_extension = f".{self.kind}"
        if not self.filename.lower().endswith(expected_extension):
            raise ValueError(f"filename must end with {expected_extension}")
        return self


class ArtifactDraft(BaseModel):
    artifact_id: str = Field(default_factory=lambda: str(uuid4()))
    spec: ArtifactSpec
    tenant_id: str
    requested_by: str
    status: Literal["pending", "approved", "rejected", "rendered"] = "pending"
    approved_by: str | None = None
    approval_reason: str | None = None
    output_path: str | None = None
    output_sha256: str | None = None
    qa_manifest_path: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ArtifactApprovalIn(BaseModel):
    approved: bool
    reason: str = Field(min_length=3, max_length=1_000)
