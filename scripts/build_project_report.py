#!/usr/bin/env python3
"""Generate the Fuseline capstone project report as a PDF.

Writes docs/Fuseline-Capstone-Report.pdf. The text describes the software in
this repository. It is not a per-case forensic export.
"""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "Fuseline-Capstone-Report.pdf"

PAGE_W, PAGE_H = A4
MARGIN_L = 20 * mm
MARGIN_R = 18 * mm
MARGIN_T = 18 * mm
MARGIN_B = 16 * mm

INK = HexColor("#1C2428")
NAVY = HexColor("#16323C")
TEAL = HexColor("#1F5C57")
BROWN = HexColor("#8A5A2A")
BLUE = HexColor("#2E4A68")
MUTED = HexColor("#5C6A70")
RULE = HexColor("#D5DDE0")
BAND = HexColor("#F3F6F6")
LINK = HexColor("#1A4E57")

FONT = "Body"
FONT_B = "Body-Bold"
FONT_I = "Body-Italic"
FONT_M = "Mono"


def _register_fonts() -> None:
    global FONT, FONT_B, FONT_I, FONT_M
    windows = Path(r"C:\Windows\Fonts")
    pairs = {
        FONT: "segoeui.ttf",
        FONT_B: "segoeuib.ttf",
        FONT_I: "segoeuii.ttf",
        FONT_M: "consola.ttf",
    }
    if all((windows / name).is_file() for name in pairs.values()):
        for key, name in pairs.items():
            pdfmetrics.registerFont(TTFont(key, str(windows / name)))
        return
    # Helvetica is built in. Alias the names used by the styles.
    from reportlab.pdfbase.pdfmetrics import registerFontFamily

    FONT, FONT_B, FONT_I, FONT_M = "Times-Roman", "Times-Bold", "Times-Italic", "Courier"
    registerFontFamily(FONT, normal=FONT, bold=FONT_B, italic=FONT_I, boldItalic="Times-BoldItalic")


def styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle(
        "body",
        fontName=FONT,
        fontSize=10.5,
        leading=15,
        textColor=INK,
        alignment=TA_LEFT,
        spaceAfter=8,
    )
    return {
        "body": base,
        "h1": ParagraphStyle(
            "h1",
            fontName=FONT_B,
            fontSize=14,
            leading=18,
            textColor=NAVY,
            spaceBefore=2,
            spaceAfter=8,
            alignment=TA_LEFT,
        ),
        "h2": ParagraphStyle(
            "h2",
            fontName=FONT_B,
            fontSize=11.5,
            leading=15,
            textColor=NAVY,
            spaceBefore=10,
            spaceAfter=4,
            alignment=TA_LEFT,
        ),
        "caption": ParagraphStyle(
            "caption",
            fontName=FONT_I,
            fontSize=8.5,
            leading=11,
            textColor=MUTED,
            spaceBefore=3,
            spaceAfter=10,
            alignment=TA_LEFT,
        ),
        "toc": ParagraphStyle(
            "toc",
            fontName=FONT,
            fontSize=11,
            leading=16,
            textColor=INK,
            alignment=TA_LEFT,
        ),
        "toc_page": ParagraphStyle(
            "toc_page",
            fontName=FONT,
            fontSize=11,
            leading=16,
            textColor=MUTED,
            alignment=TA_RIGHT,
        ),
        "cell": ParagraphStyle(
            "cell",
            fontName=FONT,
            fontSize=9,
            leading=12,
            textColor=INK,
            alignment=TA_LEFT,
        ),
        "cell_h": ParagraphStyle(
            "cell_h",
            fontName=FONT_B,
            fontSize=8.5,
            leading=11,
            textColor=white,
            alignment=TA_LEFT,
        ),
        "ref": ParagraphStyle(
            "ref",
            fontName=FONT,
            fontSize=9.5,
            leading=13,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=2,
            leftIndent=14,
            firstLineIndent=-14,
        ),
        "url": ParagraphStyle(
            "url",
            fontName=FONT_M,
            fontSize=8,
            leading=10.5,
            textColor=LINK,
            alignment=TA_LEFT,
            leftIndent=14,
            spaceAfter=8,
        ),
        "footer": ParagraphStyle(
            "footer",
            fontName=FONT,
            fontSize=8,
            leading=10,
            textColor=MUTED,
        ),
    }


S = {}  # filled in main


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wrap_url(url: str) -> str:
    """Break a URL on slashes or hyphens so a mono line cannot overflow the frame."""
    # Frame padding is 6pt each side, and the URL style indents 14pt.
    limit = PAGE_W - MARGIN_L - MARGIN_R - 6 - 6 - 14 - 10
    size = 8
    chunks: list[str] = []
    rest = url
    while rest and pdfmetrics.stringWidth(rest, FONT_M, size) > limit:
        cut = len(rest)
        while cut > 8 and pdfmetrics.stringWidth(rest[:cut], FONT_M, size) > limit:
            cut -= 1
        slash = rest.rfind("/", 8, cut + 1)
        hyphen = rest.rfind("-", 8, cut + 1)
        break_at = slash if slash >= 8 else hyphen
        if break_at < 8:
            break_at = cut
        if len(rest) - break_at < 8:
            earlier = max(rest.rfind("/", 8, break_at), rest.rfind("-", 8, break_at))
            if earlier >= 8:
                break_at = earlier
        chunks.append(esc(rest[:break_at]))
        rest = rest[break_at:]
    if rest:
        chunks.append(esc(rest))
    inner = "<br/>".join(chunks)
    return f'<link href="{esc(url)}" color="#1A4E57">{inner}</link>'


class TocMark(Flowable):
    def __init__(self, title: str, sink: dict[str, int]) -> None:
        super().__init__()
        self.title = title
        self.sink = sink
        self.width = 0
        self.height = 0

    def draw(self) -> None:
        self.sink[self.title] = self.canv.getPageNumber()


class PipelineFigure(Flowable):
    """Vertical process diagram. Height is fixed so it cannot collide with text."""

    HEIGHT = 232

    def __init__(self, width: float) -> None:
        super().__init__()
        self.box_w = width
        self.width = width
        self.height = self.HEIGHT

    def draw(self) -> None:
        c = self.canv
        labels = [
            "Evidence file, or an authorised Android USB pull",
            "SHA-256, then a read-only stored copy",
            "Format detected from content; unreadable rows counted",
            "Timestamps normalised to UTC, with a recorded basis",
            "Proximity sessions, plus validation findings",
            "Timeline, location plot, HTML / CSV / JSON",
        ]
        n = len(labels)
        gap = 8
        box_h = (self.HEIGHT - gap * (n - 1)) / n
        y = self.HEIGHT
        for i, label in enumerate(labels):
            y -= box_h
            c.setFillColor(BAND if i % 2 == 0 else white)
            c.setStrokeColor(NAVY)
            c.setLineWidth(0.6)
            c.roundRect(0, y, self.box_w, box_h - 2, 3, fill=1, stroke=1)
            c.setFillColor(TEAL)
            c.rect(0, y, 3.2, box_h - 2, fill=1, stroke=0)
            c.setFillColor(INK)
            c.setFont(FONT, 9)
            c.drawString(12, y + (box_h - 2) / 2 - 3, label)
            if i < n - 1:
                mid = self.box_w / 2
                c.setStrokeColor(MUTED)
                c.setFillColor(MUTED)
                c.setLineWidth(0.7)
                top = y - 1
                bot = y - gap + 1
                c.line(mid, top, mid, bot + 2)
                path = c.beginPath()
                path.moveTo(mid - 3, bot + 3.5)
                path.lineTo(mid + 3, bot + 3.5)
                path.lineTo(mid, bot)
                path.close()
                c.drawPath(path, fill=1, stroke=0)
            y -= gap


class SessionFigure(Flowable):
    """Schematic of the 300-second chain. Not a real case."""

    HEIGHT = 128

    def __init__(self, width: float) -> None:
        super().__init__()
        self.width = width
        self.height = self.HEIGHT

    def draw(self) -> None:
        c = self.canv
        c.setFillColor(BAND)
        c.setStrokeColor(RULE)
        c.roundRect(0, 0, self.width, self.HEIGHT, 3, fill=1, stroke=1)

        left = 78
        right = self.width - 16
        span = right - left
        lanes = [("Location", TEAL, 86), ("Browsing", BLUE, 62), ("App usage", BROWN, 38)]
        # Marks as a fraction of a 12-minute sketch.
        marks = {
            "Location": [0.08, 0.78],
            "Browsing": [0.16],
            "App usage": [0.24],
        }
        c.setStrokeColor(HexColor("#C5D0D3"))
        c.setLineWidth(0.4)
        for name, color, y in lanes:
            c.setFillColor(MUTED)
            c.setFont(FONT, 8)
            c.drawRightString(left - 8, y - 3, name)
            c.line(left, y, right, y)
            c.setFillColor(color)
            for frac in marks[name]:
                x = left + span * frac
                c.circle(x, y, 3.1, fill=1, stroke=0)

        # Window bracket over the first cluster.
        x0 = left + span * 0.05
        x1 = left + span * 0.30
        yb = 102
        c.setStrokeColor(NAVY)
        c.setLineWidth(0.8)
        c.line(x0, yb, x1, yb)
        c.line(x0, yb, x0, yb - 4)
        c.line(x1, yb, x1, yb - 4)
        c.setFillColor(NAVY)
        c.setFont(FONT, 8)
        c.drawString(x0, yb + 3, "One cluster: each step is within 300 seconds of the previous event")

        c.setFillColor(MUTED)
        c.setFont(FONT, 7.5)
        c.drawRightString(right, 18, "Right-hand fix is outside the window")
        c.drawString(left, 6, "Long clusters are split at the widest internal gap.")


def heading(title: str, pages: dict[str, int]) -> list:
    return [TocMark(title, pages), Paragraph(esc(title), S["h1"])]


def table(headers: list[str], rows: list[list[str]], col_widths: list[float]) -> Table:
    head = [Paragraph(esc(h), S["cell_h"]) for h in headers]
    body = [[Paragraph(esc(cell), S["cell"]) for cell in row] for row in rows]
    tbl = Table([head, *body], colWidths=col_widths, repeatRows=1)
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), white),
                ("BACKGROUND", (0, 1), (-1, -1), white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, BAND]),
                ("FONTNAME", (0, 0), (-1, 0), FONT_B),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, 0), 0, NAVY),
                ("LINEBELOW", (0, 1), (-1, -2), 0.2, RULE),
                ("BOX", (0, 0), (-1, -1), 0.4, RULE),
            ]
        )
    )
    return tbl


def references() -> list[tuple[str, str]]:
    return [
        (
            "Ayers, R., Brothers, S., and Jansen, W. (2014). Guidelines on Mobile Device Forensics. NIST Special Publication 800-101 Revision 1.",
            "https://csrc.nist.gov/pubs/sp/800/101/r1/final",
        ),
        (
            "NIST (2015). Secure Hash Standard (SHS). FIPS 180-4.",
            "https://csrc.nist.gov/pubs/fips/180-4/upd1/final",
        ),
        (
            "Scientific Working Group on Digital Evidence (2025). Best Practices for Mobile Device Evidence Collection &amp; Preservation, Handling, and Acquisition. SWGDE 18-F-003-2.0.",
            "https://www.swgde.org/wp-content/uploads/2025/09/2025-08-21-Best-Practices-for-Mobile-Device-Evidence-Collection-Preservation-Handling-and-Acquisition-18-F-003-2.0.pdf",
        ),
        (
            "Scientific Working Group on Digital Evidence (2026). Best Practices for Mobile Device Forensic Analysis. SWGDE 20-F-005-2.0.",
            "https://www.swgde.org/wp-content/uploads/2026/06/2026-06-23-Best-Practices-for-Mobile-Device-Forensic-Analysis-20-F-005-2.0.pdf",
        ),
        (
            "Klyne, G., and Newman, C. (2002). Date and Time on the Internet: Timestamps. RFC 3339.",
            "https://www.rfc-editor.org/rfc/rfc3339",
        ),
        (
            "Android Open Source Project. UsageStatsManager.",
            "https://developer.android.com/reference/android/app/usage/UsageStatsManager",
        ),
        (
            "Android Open Source Project. UsageEvents.",
            "https://developer.android.com/reference/android/app/usage/UsageEvents",
        ),
        (
            "Android Open Source Project. Android Debug Bridge (adb).",
            "https://developer.android.com/tools/adb",
        ),
        (
            "Android Open Source Project. dumpsys.",
            "https://developer.android.com/tools/dumpsys",
        ),
        (
            "The Chromium Authors. HistoryDatabase (Chromium source).",
            "https://chromium.googlesource.com/chromium/src/+/refs/heads/main/components/history/core/browser/history_database.h",
        ),
        (
            "Mozilla. Places. Firefox Source Docs.",
            "https://firefox-source-docs.mozilla.org/browser/places/index.html",
        ),
        (
            "Google. Google Takeout.",
            "https://takeout.google.com/",
        ),
        (
            "Topografix. GPX 1.1 schema.",
            "https://www.topografix.com/GPX/1/1/",
        ),
        (
            "SQLite. The SQLite Database File Format.",
            "https://www.sqlite.org/fileformat2.html",
        ),
        (
            "log2timeline project. Plaso documentation.",
            "https://plaso.readthedocs.io/en/latest/",
        ),
        (
            "log2timeline project. Plaso source repository.",
            "https://github.com/log2timeline/plaso",
        ),
        (
            "Brignoni, A. ALEAPP (Android Logs Events And Protobuf Parser).",
            "https://github.com/abrignoni/ALEAPP",
        ),
    ]


SECTIONS = [
    "1. Solution required",
    "2. Solution proposed",
    "3. Algorithm",
    "4. Outcome",
    "5. Conclusion",
    "6. Inference",
    "7. References",
]


def story(width: float, pages: dict[str, int]) -> list:
    body = S["body"]
    out: list = [NextPageTemplate("body"), PageBreak()]

    out.append(Paragraph("Contents", S["h1"]))
    toc_rows = []
    for title in SECTIONS:
        num = pages.get(title, "")
        label = str(num) if num else "—"
        toc_rows.append(
            [
                Paragraph(esc(title), S["toc"]),
                Paragraph(label, S["toc_page"]),
            ]
        )
    toc = Table(toc_rows, colWidths=[width - 18 * mm, 18 * mm])
    toc.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LINEBELOW", (0, 0), (-1, -2), 0.2, RULE),
            ]
        )
    )
    out.append(toc)
    out.append(Spacer(1, 8))
    out.append(
        Paragraph(
            "This document describes Fuseline version 1.1.0 as implemented in the project repository. "
            "It is the project report. It is not the per-case evidence report the tool exports after a case is built.",
            body,
        )
    )
    out.append(Spacer(1, 10))

    # 1
    out += heading(SECTIONS[0], pages)
    out.append(
        Paragraph(
            "The assigned problem, from the Cyber Forensics Capstone brief (problem 31, Mobile App Usage Timeline Builder), is to correlate location data, browsing history, and app usage logs into one forensic timeline. The suggested path is to extract timestamps from those artifacts, put them on one time base, relate events that sit together in time, and present that view. The named technology is Python, SQLite, and Plaso. The work is organised as acquisition, analysis, validation, and report.",
            body,
        )
    )
    out.append(
        Paragraph(
            f"The practical difficulty is that the three sources do not arrive as one log. A location file may be a GPS track, a GPX file, or a Google location export. Browsing may be a Chromium History database or a Firefox places database. App usage may be an Android usage-stats database, a CSV or XML export, or the text printed by <font face='{FONT_M}'>dumpsys usagestats</font>. The clocks differ. Some values are UTC. Some carry an offset. Some are local time with no zone. Sorting the raw strings does not produce a reliable order.",
            body,
        )
    )
    out.append(
        Paragraph(
            "What the brief asks for is a chronological view an examiner can review, with each point traceable to the artifact it came from. NIST's mobile forensics guide and the SWGDE mobile collection guide treat acquisition and later examination as separate steps. Fuseline follows that split: collect the Android artifacts, then build the timeline from what was collected.",
            body,
        )
    )

    # 2
    out += heading(SECTIONS[1], pages)
    out.append(
        Paragraph(
            "Fuseline is a local program for Android artifacts. The examiner creates a case, records who is examining it, and sets the device timezone. Evidence is added by uploading files, or by an authorised USB debugging connection. A Python API does the parsing and storage. Each case is its own SQLite database. The browser interface is served with that API.",
            body,
        )
    )
    out.append(Paragraph("Figure 1. Path from evidence to the examiner's view.", S["caption"]))
    out.append(PipelineFigure(width))
    out.append(Spacer(1, 8))
    out.append(
        Paragraph(
            f"The parser is chosen by reading the file, not by trusting the file name. The formats recognised are listed below. Plaso is optional. If an examiner already has a Plaso storage file, <font face='{FONT_M}'>psort</font> can write L2TCSV or JSON lines, and Fuseline maps rows it understands onto the location, browsing, and app-usage lanes.",
            body,
        )
    )
    out.append(
        table(
            ["Source", "Accepted input"],
            [
                ["Browsing", "Chromium, Chrome, or Edge History; Firefox or Firefox for Android places.sqlite"],
                ["App usage", "Android UsageStats SQLite, CSV, or XML; adb dumpsys usagestats text"],
                [
                    "Location",
                    "CSV or SQLite with coordinates and time; GPX; Google Takeout Records.json; adb dumpsys location text when the dump contains timed fixes",
                ],
                ["Plaso", "psort L2TCSV or JSON lines, optional"],
            ],
            [28 * mm, width - 28 * mm],
        )
    )
    out.append(Spacer(1, 8))
    out.append(
        Paragraph(
            f"On an authorised USB debugging connection, Fuseline pulls <font face='{FONT_M}'>dumpsys usagestats</font>, a <font face='{FONT_M}'>dumpsys location</font> snapshot when that text contains timed coordinates, and supported files in shared storage (Download and Documents, including a nested Takeout folder). Browser history and other exports can be placed in those folders, or uploaded from the examiner's machine.",
            body,
        )
    )
    out.append(
        Paragraph(
            "The API accepts requests for the host names it has been configured to allow. The default is loopback, which suits an examiner workstation. Case files and evidence copies stay on the machine that runs the API.",
            body,
        )
    )
    out.append(
        Paragraph(
            f"Usage-stats event numbers follow the Android <font face='{FONT_M}'>UsageEvents</font> constants. The CSV layout is the one used by common Android artifact parsers, including ALEAPP. Plaso is used as an import format, not as a library embedded in the program. No third-party forensic engine is shipped inside the repository.",
            body,
        )
    )

    # 3
    out += heading(SECTIONS[2], pages)
    out.append(Paragraph("3.1 Ingest", S["h2"]))
    out.append(
        Paragraph(
            "Each uploaded file is hashed with SHA-256, as specified in FIPS 180-4. The bytes are copied into the case store, the copy is hashed again, and the copy is made read-only. If that digest is already in the case, the stored copy is kept and the file is not parsed again. Rows that cannot be interpreted are skipped and counted, with a short reason stored on the artifact.",
            body,
        )
    )
    out.append(Paragraph("3.2 Time", S["h2"]))
    out.append(
        Paragraph(
            "Every event stores the UTC time used for sorting, the original source value, and a basis. Sorting and correlation use the UTC time. The basis stays on the event so the examiner can see which times were inferred. RFC 3339 is the profile used for the UTC timestamps the tool writes.",
            body,
        )
    )
    out.append(
        table(
            ["Basis", "Meaning"],
            [
                ["absolute", "The source was an epoch number, or a string already marked as UTC."],
                ["offset", "The source carried an explicit offset, such as -05:00, and was converted."],
                ["assumed", "The source had no zone. It was read in the case timezone and flagged."],
            ],
            [28 * mm, width - 28 * mm],
        )
    )
    out.append(Spacer(1, 6))
    out.append(
        Paragraph(
            f"When a timestamp has no zone, it is read in the case timezone and the basis is recorded as assumed. The validation step records <font face='{FONT_M}'>TZ_ASSUMED</font> for those events.",
            body,
        )
    )

    out.append(Paragraph("3.3 Proximity sessions", S["h2"]))
    out.append(
        Paragraph(
            "Events are ordered by UTC time and then by event identifier. An event joins the current cluster when its time is within the window of the immediately previous event. The default window is 300 seconds. This is single linkage along that order. Each event is in at most one cluster. Starting the scan from a different record does not change the clusters, because the sort is fixed.",
            body,
        )
    )
    out.append(
        Paragraph(
            "A cluster is kept when it contains at least two distinct sources. The default sources that count are location, browsing, app usage, and a Plaso row that has been mapped onto one of those lanes. A cluster whose first and last events are more than 30 minutes apart is cut at the widest gap between neighbours. If two gaps are equal, the cut nearer the middle of the cluster is used. The pieces are tested again against the same rules.",
            body,
        )
    )
    out.append(SessionFigure(width))
    out.append(
        Paragraph(
            "Figure 2. Schematic of the default window. The marks are not taken from a case.",
            S["caption"],
        )
    )
    out.append(
        Paragraph(
            "The session identifier is a UUID version 5 computed from the member event identifiers, in order. The same evidence therefore produces the same session identifier. An event identifier is itself a UUID version 5 of the artifact SHA-256 and the row index inside that artifact.",
            body,
        )
    )
    out.append(
        Paragraph(
            "The score used to rank sessions is the number of distinct sources, plus 0.5 if a location source is present, plus the smaller of 1 and (event count / 10). Sessions are ordered by that score.",
            body,
        )
    )
    out.append(
        Paragraph(
            "If the cluster contains coordinates, the session stores their centroid and the radius of those points about the centroid. The radius is the spread of the points in that session.",
            body,
        )
    )
    out.append(
        table(
            ["Parameter", "Default", "Role"],
            [
                ["Window", "300 seconds", "Maximum gap between one event and the next inside a cluster"],
                ["Minimum sources", "2", "A cluster with fewer sources is discarded"],
                ["Maximum length", "30 minutes", "Longer clusters are split at the widest pause"],
            ],
            [38 * mm, 32 * mm, width - 70 * mm],
        )
    )
    out.append(Spacer(1, 8))

    out.append(Paragraph("3.4 Validation", S["h2"]))
    out.append(
        Paragraph(
            "After ingest, the case is checked and each check is stored as a finding. The codes below are the ones the program actually emits.",
            body,
        )
    )
    out.append(
        table(
            ["Code", "When it appears"],
            [
                ["NO_EVENTS / EVENTS_PRESENT", "The case has no normalised events, or it has some."],
                ["SINGLE_SOURCE / MULTI_SOURCE / NO_SOURCES", "How many source types were stored."],
                ["DUPLICATE_HASH / HASH_INVENTORY", "Whether two artifacts share a SHA-256, or each hash is recorded."],
                ["ROWS_SKIPPED", "One or more source rows could not be parsed."],
                ["TZ_ASSUMED", "Some timestamps had no zone and were read in the case timezone."],
                ["TIME_SPAN", "The duration from the earliest event to the latest."],
                ["LARGE_GAP / GAP_OK", "The largest gap between neighbouring events is over 7 days, or not."],
                ["NO_TIME_OVERLAP", "Two sources never occupy the same time range, so they cannot meet in a session."],
                ["SESSIONS_FOUND / NO_SESSIONS", "Whether any cluster met the source rule."],
                ["FUTURE_TS", "Events dated more than one day ahead of the clock used at validation."],
                ["EARLY_TS", "Events dated before 1 January 2008, often a zero or epoch placeholder."],
            ],
            [58 * mm, width - 58 * mm],
        )
    )
    out.append(Spacer(1, 8))

    out.append(Paragraph("3.5 Chain of custody", S["h2"]))
    out.append(
        Paragraph(
            "An append-only audit log records case creation, ingest, device pulls, session rebuilds, and report exports. Each entry includes the hash of the previous entry. The verify action re-hashes every stored evidence file and walks that chain. A match shows that the stored copy still equals the bytes that were ingested.",
            body,
        )
    )

    # 4
    out += heading(SECTIONS[3], pages)
    out.append(
        Paragraph(
            "Given artifacts the parsers recognise, the examiner gets one timeline in UTC. Location, browsing, and app usage are drawn on separate lanes. A session is a band across the lanes it contains. Selecting the session shows its member events. Selecting an event shows the original timestamp, the basis, the source file, and the parser that read it.",
            body,
        )
    )
    out.append(
        Paragraph(
            "Location fixes that were parsed are plotted on a local grid. The drawing is produced on the examiner's machine. On a large case the plot may show a subset of the points, always including the first and the last, while every parsed fix remains stored.",
            body,
        )
    )
    out.append(
        Paragraph(
            "The case report is separate from this project document. From a case the examiner can export an HTML report (findings, sessions, and the audit chain), a CSV of events, or a JSON document that includes the audit trail. Exporting writes an audit entry that records the kind of export and the SHA-256 of the exported text.",
            body,
        )
    )
    out.append(
        Paragraph(
            "The repository contains automated tests for the parsers, the session rules, the API, and the interface.",
            body,
        )
    )

    # 5
    out += heading(SECTIONS[4], pages)
    out.append(
        Paragraph(
            "Fuseline carries out the assigned task. It takes location, browsing, and app-usage artifacts, places their timestamps on UTC, and shows where those sources fall close together in time. Acquisition, analysis, validation, and reporting are separate steps. Each event can be traced to a stored file and a hash.",
            body,
        )
    )
    out.append(
        Paragraph(
            "The correlation is a time window with an explicit length and an explicit rule for splitting long runs. Changing the window, the minimum number of sources, or the maximum length changes which sessions appear, and the examiner can see the values in use. The result is repeatable for the same bytes and the same settings.",
            body,
        )
    )
    out.append(
        Paragraph(
            "The USB path collects Android usage statistics, a location snapshot when timed fixes are present, and files the examiner has placed in shared storage.",
            body,
        )
    )

    # 6
    out += heading(SECTIONS[5], pages)
    out.append(
        Paragraph(
            "A session means that events from at least two sources lie inside the configured window, after the long-cluster split. The examiner can state that these records are close in time, which files they came from, and how each clock was read.",
            body,
        )
    )
    out.append(
        Paragraph(
            f"<font face='{FONT_M}'>LARGE_GAP</font> records a gap of more than seven days between neighbouring events. <font face='{FONT_M}'>NO_TIME_OVERLAP</font> records two sources whose time ranges do not meet.",
            body,
        )
    )
    out.append(
        Paragraph(
            "Times marked assumed were read in the case timezone. The TZ_ASSUMED finding records that on the case.",
            body,
        )
    )
    out.append(
        Paragraph(
            "A successful integrity check means the stored copies and the audit chain still match what the tool wrote. A location radius is the spread of the coordinates inside that session. A large radius means those points are far apart. Where a source file includes its own accuracy figure, that figure stays on the individual event.",
            body,
        )
    )

    # 7
    out += heading(SECTIONS[6], pages)
    out.append(
        Paragraph(
            "The course problem statement is an internal brief, so it is described in section 1 and is not listed here. The works below are the public sources used for the methods in this report.",
            body,
        )
    )
    for index, (citation, url) in enumerate(references(), start=1):
        out.append(Paragraph(f"{index}. {citation}", S["ref"]))
        out.append(Paragraph(wrap_url(url), S["url"]))

    return out


def draw_cover(c, doc) -> None:
    c.saveState()
    c.setFillColor(NAVY)
    c.rect(0, PAGE_H - 86 * mm, PAGE_W, 86 * mm, fill=1, stroke=0)
    c.setFillColor(TEAL)
    c.rect(0, PAGE_H - 88 * mm, PAGE_W, 2 * mm, fill=1, stroke=0)

    c.setFillColor(HexColor("#D5E4E2"))
    c.setFont(FONT, 9)
    c.drawString(MARGIN_L, PAGE_H - 22 * mm, "CYBER FORENSICS CAPSTONE")
    c.setFillColor(white)
    c.setFont(FONT, 11)
    c.drawString(MARGIN_L, PAGE_H - 30 * mm, "Problem 31  ·  Mobile App Usage Timeline Builder")
    c.setFont(FONT_B, 36)
    c.drawString(MARGIN_L, PAGE_H - 52 * mm, "Fuseline")
    c.setFont(FONT, 13)
    c.setFillColor(HexColor("#E4EEF0"))
    c.drawString(MARGIN_L, PAGE_H - 64 * mm, "Cyber Forensics Capstone Project")
    c.setFont(FONT, 11)
    c.drawString(MARGIN_L, PAGE_H - 76 * mm, "Project report")

    y = PAGE_H - 108 * mm
    c.setFillColor(INK)
    c.setFont(FONT_B, 11)
    c.drawString(MARGIN_L, y, "Prepared by")
    y -= 8 * mm
    c.setStrokeColor(RULE)
    c.setLineWidth(0.4)
    c.line(MARGIN_L, y, PAGE_W - MARGIN_R, y)
    y -= 10 * mm

    people = [
        ("Navneet Nanda", "CH.SC.U4CYS24039"),
        ("Roshan Srikanth", "CH.SC.U4CYS24057"),
    ]
    for name, roll in people:
        c.setFillColor(NAVY)
        c.setFont(FONT_B, 12)
        c.drawString(MARGIN_L, y, name)
        c.setFillColor(MUTED)
        c.setFont(FONT, 10)
        c.drawString(MARGIN_L + 58 * mm, y, roll)
        y -= 8 * mm

    y -= 8 * mm
    c.setFillColor(INK)
    c.setFont(FONT_B, 11)
    c.drawString(MARGIN_L, y, "Assigned task")
    y -= 6 * mm
    c.setStrokeColor(RULE)
    c.line(MARGIN_L, y, PAGE_W - MARGIN_R, y)
    y -= 8 * mm
    c.setFillColor(INK)
    c.setFont(FONT, 10.5)
    text = (
        "Correlate location data, browsing history, and app usage logs",
        "into a unified forensic timeline.",
    )
    for line in text:
        c.drawString(MARGIN_L, y, line)
        y -= 5.5 * mm
    y -= 4 * mm
    c.setFillColor(MUTED)
    c.setFont(FONT, 9.5)
    for line in (
        "Suggested tools in the brief: Python, SQLite, Plaso.",
        "Structure: acquisition, analysis, validation, report.",
        "Software version described: 1.1.0.",
    ):
        c.drawString(MARGIN_L, y, line)
        y -= 5 * mm

    c.setFillColor(MUTED)
    c.setFont(FONT, 8)
    c.drawString(MARGIN_L, 14 * mm, "Generated from the Fuseline repository  ·  scripts/build_project_report.py")
    c.restoreState()


def draw_body(c, doc) -> None:
    c.saveState()
    c.setFillColor(NAVY)
    c.rect(0, PAGE_H - 9 * mm, PAGE_W, 9 * mm, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont(FONT, 8)
    c.drawString(MARGIN_L, PAGE_H - 6 * mm, "Fuseline")
    c.setFont(FONT, 8)
    c.drawRightString(PAGE_W - MARGIN_R, PAGE_H - 6 * mm, "Capstone project report")

    c.setStrokeColor(RULE)
    c.setLineWidth(0.4)
    c.line(MARGIN_L, 12 * mm, PAGE_W - MARGIN_R, 12 * mm)
    c.setFillColor(MUTED)
    c.setFont(FONT, 8)
    c.drawString(MARGIN_L, 7 * mm, "Problem 31  ·  Mobile App Usage Timeline Builder")
    c.drawRightString(PAGE_W - MARGIN_R, 7 * mm, str(doc.page))
    c.restoreState()


def main() -> int:
    global S
    _register_fonts()
    S = styles()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    frame = Frame(
        MARGIN_L,
        16 * mm,
        PAGE_W - MARGIN_L - MARGIN_R,
        PAGE_H - 28 * mm,
        id="body",
        showBoundary=0,
    )
    cover_frame = Frame(0, 0, PAGE_W, PAGE_H, id="cover", showBoundary=0)
    doc = BaseDocTemplate(
        str(OUT),
        pagesize=A4,
        title="Fuseline — Cyber Forensics Capstone Project",
        author="Navneet Nanda; Roshan Srikanth",
        subject="Project report for problem 31, Mobile App Usage Timeline Builder",
    )
    doc.addPageTemplates(
        [
            PageTemplate(id="cover", frames=[cover_frame], onPage=draw_cover),
            PageTemplate(id="body", frames=[frame], onPage=draw_body),
        ]
    )

    pages: dict[str, int] = {}
    width = PAGE_W - MARGIN_L - MARGIN_R
    # Two passes so the contents list can show the page numbers collected on the first pass.
    doc.build(story(width, pages))
    doc.build(story(width, dict(pages)))
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
