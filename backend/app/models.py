from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.timeutil import canonical_timezone_name

SourceType = Literal["location", "browsing", "app_usage", "plaso"]
Severity = Literal["pass", "warn", "fail", "info"]

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Examiner = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]


def _check_timezone(value: str | None) -> str | None:
    return None if value is None else canonical_timezone_name(value)


class CaseCreate(BaseModel):
    name: Name
    examiner: Examiner = ""
    timezone: str = Field(default="UTC", max_length=64)
    notes: Notes = ""

    _tz = field_validator("timezone")(_check_timezone)


class CaseUpdate(BaseModel):
    name: Name | None = None
    examiner: Examiner | None = None
    timezone: str | None = Field(default=None, max_length=64)
    notes: Notes | None = None

    _tz = field_validator("timezone")(_check_timezone)


class CaseOut(BaseModel):
    id: str
    name: str
    examiner: str
    timezone: str
    notes: str
    created_at: str
    updated_at: str
    event_count: int = 0
    artifact_count: int = 0
    session_count: int = 0


class ArtifactOut(BaseModel):
    id: str
    source_type: str
    original_name: str
    sha256: str
    ingested_at: str
    row_count: int
    size_bytes: int = 0
    skipped_rows: int = 0
    parser: str = ""
    notes: list[str] = Field(default_factory=list)


class EventOut(BaseModel):
    id: str
    artifact_id: str
    ts_utc: str
    ts_ms: int = 0
    ts_original: str
    tz_assumed: str
    ts_basis: str = "absolute"
    source: str
    event_type: str
    title: str
    detail: dict[str, Any] = Field(default_factory=dict)
    lat: float | None = None
    lon: float | None = None
    package: str | None = None
    url: str | None = None
    domain: str | None = None
    confidence: float = 1.0


class SessionOut(BaseModel):
    id: str
    start_utc: str
    end_utc: str
    score: float
    summary: str
    member_event_ids: list[str]
    sources: list[str]
    event_count: int = 0
    centroid_lat: float | None = None
    centroid_lon: float | None = None
    radius_m: float | None = None


class CorrelationParamsIn(BaseModel):
    window_seconds: int = Field(default=300, ge=1, le=3600)
    max_span_seconds: int = Field(default=1800, ge=30, le=86_400)
    min_sources: int = Field(default=2, ge=1, le=4)


class ValidationFindingOut(BaseModel):
    id: str
    severity: str
    code: str
    message: str
    created_at: str


class TimelineResponse(BaseModel):
    case_id: str
    events: list[EventOut]
    total: int
    offset: int = 0
    limit: int = 0
    has_more: bool = False
    sources: dict[str, int]


class OverviewResponse(BaseModel):
    origin_ms: int
    bucket_ms: int
    bucket_count: int
    total: int
    series: dict[str, list[tuple[int, int]]]


class LocationPoint(BaseModel):
    id: str
    ts_ms: int
    lat: float
    lon: float


class LocationsResponse(BaseModel):
    points: list[LocationPoint]
    total: int
    returned: int


class IngestResult(BaseModel):
    artifact: ArtifactOut
    events_added: int
    sessions_rebuilt: int
    findings: list[ValidationFindingOut]
    duplicate: bool = False


class AuditEntryOut(BaseModel):
    id: int
    ts: str
    case_id: str | None = None
    actor: str = ""
    action: str
    detail: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str
    entry_hash: str


class ArtifactIntegrity(BaseModel):
    artifact_id: str
    original_name: str
    expected_sha256: str
    actual_sha256: str | None = None
    status: Literal["ok", "modified", "missing"]


class IntegrityResult(BaseModel):
    ok: bool
    checked_at: str
    artifacts: list[ArtifactIntegrity]
    audit_chain_ok: bool
    audit_first_bad_entry: int | None = None


class DeviceInfo(BaseModel):
    serial: str
    state: str
    model: str | None = None
    ready: bool


class DeviceExportInfo(BaseModel):
    name: str
    remote_path: str
    display_path: str
    size_bytes: int | None = None


class DeviceBundleItem(BaseModel):
    kind: Literal["app_usage", "location", "export"]
    label: str
    ok: bool
    events_added: int = 0
    duplicate: bool = False
    error: str | None = None
    source_type: str | None = None


class DeviceBundleResult(BaseModel):
    items: list[DeviceBundleItem]
    total_events_added: int
    sessions_rebuilt: int


class FormatInfo(BaseModel):
    source: str
    parser: str
    label: str


class MetaOut(BaseModel):
    version: str
    max_upload_bytes: int
    formats: list[FormatInfo]


class ReportSummary(BaseModel):
    case: CaseOut
    artifacts: list[ArtifactOut]
    event_count: int
    source_counts: dict[str, int]
    session_count: int
    findings: list[ValidationFindingOut]
    sessions: list[SessionOut]
    correlation: CorrelationParamsIn
    audit: list[AuditEntryOut]
    first_event_utc: str | None = None
    last_event_utc: str | None = None
