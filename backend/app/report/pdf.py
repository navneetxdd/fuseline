"""Case report as a PDF. Same facts as the on-screen report, laid out for printing."""

from __future__ import annotations

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

from app.report.build import gather_report_data
from app.report.sections import report_sections

INK = colors.HexColor("#1c2428")
RULE = colors.HexColor("#d5dee1")
HEAD = colors.HexColor("#1a3338")


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
        title="Fuseline — Cyber Forensics Capstone Project",
        author=case.get("examiner") or "Fuseline",
        subject=f"Case report for {case['name']}",
    )
    body = ParagraphStyle("body", fontName="Times-Roman", fontSize=10, leading=13, textColor=INK, spaceAfter=6)
    h1 = ParagraphStyle("h1", fontName="Times-Bold", fontSize=16, leading=20, textColor=INK, spaceAfter=4)
    h2 = ParagraphStyle(
        "h2", fontName="Times-Bold", fontSize=12, leading=15, textColor=INK, spaceBefore=10, spaceAfter=4
    )
    story: list = [
        Paragraph("Fuseline — Cyber Forensics Capstone Project", h1),
        Paragraph(
            f"Case {_esc(case['name'])} · examiner {_esc(case.get('examiner') or 'not recorded')} · "
            f"device timezone {_esc(case.get('timezone') or 'UTC')}",
            body,
        ),
    ]
    for section in report_sections(data):
        story.append(Paragraph(_esc(section["title"]), h2))
        for paragraph in section["paragraphs"]:
            story.append(Paragraph(_esc(paragraph), body))
        if section["id"] == "outcome":
            story.extend(_outcome_tables(data, activity, body))
    doc.build(story)
    return buf.getvalue()


def _outcome_tables(data: dict, activity: dict, body: ParagraphStyle) -> list:
    story: list = []
    story.append(Paragraph("Acquired files", body))
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
        story.append(Paragraph("App usage", body))
        rows = [["Package", "Events", "Foreground"]]
        for pkg in activity["packages"]:
            fg = _duration(pkg["foreground_ms"]) if pkg["foreground_ms"] else "—"
            rows.append([pkg["package"], f"{pkg['events']:,}", fg])
        story.append(_table(rows, [110 * mm, 28 * mm, 28 * mm]))

    if activity["event_types"]:
        story.append(Paragraph("Usage event types", body))
        rows = [["Type", "Count"]]
        rows.extend([[item["label"], f"{item['count']:,}"] for item in activity["event_types"]])
        story.append(_table(rows, [120 * mm, 46 * mm]))

    if activity["domains"]:
        story.append(Paragraph("Browsing", body))
        rows = [["Domain", "Events"]]
        rows.extend([[item["label"], f"{item['count']:,}"] for item in activity["domains"]])
        story.append(_table(rows, [120 * mm, 46 * mm]))

    if activity["location_fixes"]:
        story.append(Paragraph(f"{activity['location_fixes']:,} location fixes include coordinates.", body))

    if data["session_count"]:
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
    return story


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
