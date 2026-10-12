from __future__ import annotations

import hashlib

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from app import audit
from app.api.deps import require_case_id
from app.models import (
    ActivityOut,
    ArtifactOut,
    AuditEntryOut,
    CaseOut,
    CorrelationParamsIn,
    ReportSummary,
    SessionOut,
    ValidationFindingOut,
)
from app.pipeline.ingest import get_case
from app.report.build import (
    export_events_csv,
    export_events_json,
    gather_report_data,
    render_html_report,
)
from app.report.pdf import render_case_pdf

router = APIRouter(prefix="/api/cases/{case_id}/report", tags=["report"])

# The report is evidence-derived HTML: forbid scripts, network access and framing outright.
REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"


def _record_export(case_id: str, kind: str, content: str | bytes, actor: str) -> None:
    raw = content if isinstance(content, bytes) else content.encode("utf-8")
    audit.record(
        "report.export",
        case_id=case_id,
        actor=actor,
        detail={"kind": kind, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)},
    )


@router.get("", response_model=ReportSummary)
def report_summary(case_id: str) -> ReportSummary:
    case_id = require_case_id(case_id)
    data = gather_report_data(case_id)
    return ReportSummary(
        case=CaseOut(**data["case"]),
        artifacts=[ArtifactOut(**a) for a in data["artifacts"]],
        event_count=data["event_count"],
        source_counts=data["source_counts"],
        session_count=data["session_count"],
        findings=[ValidationFindingOut(**f) for f in data["findings"]],
        sessions=[SessionOut(**s) for s in data["sessions"]],
        correlation=CorrelationParamsIn(**data["correlation"]),
        audit=[AuditEntryOut(**a) for a in data["audit"]],
        first_event_utc=data["first_event_utc"],
        last_event_utc=data["last_event_utc"],
        activity=ActivityOut(**data["activity"]),
    )


@router.get("/pdf")
def report_pdf(case_id: str) -> Response:
    case_id = require_case_id(case_id)
    content = render_case_pdf(case_id)
    _record_export(case_id, "pdf", content, get_case(case_id)["examiner"])
    name = case_id[:8]
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="fuseline_{name}.pdf"'},
    )


@router.get("/html")
def report_html(case_id: str) -> HTMLResponse:
    case_id = require_case_id(case_id)
    html = render_html_report(case_id)
    _record_export(case_id, "html", html, get_case(case_id)["examiner"])
    return HTMLResponse(content=html, headers={"Content-Security-Policy": REPORT_CSP})


@router.get("/csv")
def report_csv(case_id: str, raw: bool = False) -> Response:
    case_id = require_case_id(case_id)
    content = export_events_csv(case_id, raw=raw)
    _record_export(case_id, "csv-raw" if raw else "csv", content, get_case(case_id)["examiner"])
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="fuseline_{case_id}.csv"'},
    )


@router.get("/json")
def report_json(case_id: str) -> PlainTextResponse:
    case_id = require_case_id(case_id)
    content = export_events_json(case_id)
    _record_export(case_id, "json", content, get_case(case_id)["examiner"])
    return PlainTextResponse(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="fuseline_{case_id}.json"'},
    )
