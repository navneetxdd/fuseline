"""Case report as a PDF. Same facts as the on-screen report, laid out for printing."""

from __future__ import annotations

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

from app.report.build import gather_report_data

INK = colors.HexColor("#1c2428")
RULE = colors.HexColor("#d5dee1")
HEAD = colors.HexColor("#1a3338")
WARN_CODES = frozenset(
    {
        "NO_EVENTS",
        "ROWS_SKIPPED",
        "TZ_ASSUMED",
        "LARGE_GAP",
        "NO_TIME_OVERLAP",
        "FUTURE_TS",
        "EARLY_TS",
        "DUPLICATE_HASH",
        "NO_SOURCES",
    }
)


def _duration(ms: int) -> str:
    seconds = max(0, ms // 1000)
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minute = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {minute:02d}m"
    days, hour = divmod(hours, 24)
    return f"{days}d {hour}h"


def render_case_pdf(case_id: str) -> bytes:
    data = gather_report_data(case_id)
    case = data["case"]
    activity = data["activity"]
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Fuseline case report — {case['name']}",
        author=case.get("examiner") or "Fuseline",
    )
    body = ParagraphStyle("body", fontName="Times-Roman", fontSize=10, leading=13, textColor=INK, spaceAfter=6)
    h1 = ParagraphStyle("h1", fontName="Times-Bold", fontSize=16, leading=20, textColor=INK, spaceAfter=4)
    h2 = ParagraphStyle(
        "h2", fontName="Times-Bold", fontSize=12, leading=15, textColor=INK, spaceBefore=8, spaceAfter=4
    )
    story: list = [
        Paragraph("Fuseline case report", h1),
        Paragraph(
            f"{_esc(case['name'])} · examiner {_esc(case.get('examiner') or 'not recorded')} · "
            f"device timezone {_esc(case.get('timezone') or 'UTC')}",
            body,
        ),
    ]
    if activity.get("notes"):
        story.append(Paragraph("What the log shows", h2))
        for note in activity["notes"]:
            story.append(Paragraph(_esc(note), body))
    else:
        sources = ", ".join(
            f"{name.replace('_', ' ')} {count:,}" for name, count in sorted(activity["source_counts"].items())
        )
        story.append(Paragraph(f"{activity['event_count']:,} events. {sources or 'No events.'}", body))

    story.append(Paragraph("Acquired files", h2))
    if data["artifacts"]:
        rows = [["File", "Parser", "Events", "SHA-256"]]
        for art in data["artifacts"]:
            rows.append(
                [
                    art["original_name"],
                    art["parser"],
                    f"{art['row_count']:,}",
                    art["sha256"],
                ]
            )
        story.append(_table(rows, [42 * mm, 32 * mm, 22 * mm, 70 * mm]))
    else:
        story.append(Paragraph("No files have been acquired.", body))

    if activity["packages"]:
        story.append(Paragraph("App usage", h2))
        story.append(
            Paragraph(
                "Foreground time is measured from a move-to-foreground event to the next background or stop "
                "for the same package. A foreground event with no later stop is listed with no duration.",
                body,
            )
        )
        rows = [["Package", "Events", "Foreground"]]
        for pkg in activity["packages"]:
            fg = _duration(pkg["foreground_ms"]) if pkg["foreground_ms"] else "—"
            rows.append([pkg["package"], f"{pkg['events']:,}", fg])
        story.append(_table(rows, [110 * mm, 28 * mm, 28 * mm]))

    if activity["event_types"]:
        story.append(Paragraph("Usage event types", h2))
        rows = [["Type", "Count"]]
        rows.extend([[item["label"], f"{item['count']:,}"] for item in activity["event_types"]])
        story.append(_table(rows, [120 * mm, 46 * mm]))

    if activity["domains"]:
        story.append(Paragraph("Browsing", h2))
        rows = [["Domain", "Events"]]
        rows.extend([[item["label"], f"{item['count']:,}"] for item in activity["domains"]])
        story.append(_table(rows, [120 * mm, 46 * mm]))

    if activity["location_fixes"]:
        story.append(Paragraph("Location", h2))
        story.append(Paragraph(f"{activity['location_fixes']:,} fixes with coordinates.", body))

    story.append(Paragraph("Correlation", h2))
    params = data["correlation"]
    n_sources = len(activity["source_counts"])
    if n_sources < 2:
        present = ", ".join(sorted(activity["source_counts"])) or "nothing"
        story.append(
            Paragraph(
                f"A session needs events from at least two sources inside {params['window_seconds']} seconds. "
                f"This case has {present}.",
                body,
            )
        )
    elif data["session_count"] == 0:
        story.append(
            Paragraph(
                f"No pair of events from different sources falls inside {params['window_seconds']} seconds.",
                body,
            )
        )
    else:
        story.append(
            Paragraph(
                f"{data['session_count']:,} sessions. Window {params['window_seconds']} seconds, "
                f"at least {params['min_sources']} sources, split after {params['max_span_seconds'] // 60} minutes.",
                body,
            )
        )
        rows = [["Start (UTC)", "Duration", "Sources", "What overlaps"]]
        for session in data["sessions"][:15]:
            rows.append(
                [
                    session["start_utc"],
                    _duration(_ms(session["end_utc"]) - _ms(session["start_utc"])),
                    ", ".join(session["sources"]),
                    session["summary"],
                ]
            )
        story.append(_table(rows, [42 * mm, 22 * mm, 32 * mm, 70 * mm]))

    warnings = [f for f in data["findings"] if f["code"] in WARN_CODES]
    story.append(Paragraph("Checks", h2))
    if warnings:
        for finding in warnings:
            story.append(Paragraph(_esc(finding["message"]), body))
    else:
        story.append(Paragraph("No skipped rows, clock warnings, or hash problems were recorded.", body))

    doc.build(story)
    return buf.getvalue()


def _ms(iso: str) -> int:
    from datetime import datetime

    text = iso.replace("Z", "+00:00")
    return int(datetime.fromisoformat(text).timestamp() * 1000)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _table(rows: list[list[str]], widths: list[float]) -> Table:
    head = ParagraphStyle("headcell", fontName="Times-Bold", fontSize=8, leading=10, textColor=colors.white)
    cell = ParagraphStyle("cell", fontName="Times-Roman", fontSize=8, leading=10, textColor=INK)
    wrapped = []
    for index, row in enumerate(rows):
        style = head if index == 0 else cell
        wrapped.append([Paragraph(_esc(str(value)), style) for value in row])
    table = Table(wrapped, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HEAD),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.3, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table
