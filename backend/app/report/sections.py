"""The seven sections the project report has to contain, filled with this case where the facts differ."""

from __future__ import annotations

from app.report.activity import SOURCE_PLAIN

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

REFERENCES = (
    (
        "Ayers, R., Brothers, S., and Jansen, W. (2014). Guidelines on Mobile Device Forensics. "
        "NIST Special Publication 800-101 Revision 1.",
        "https://csrc.nist.gov/pubs/sp/800/101/r1/final",
    ),
    (
        "NIST (2015). Secure Hash Standard (SHS). FIPS 180-4.",
        "https://csrc.nist.gov/pubs/fips/180-4/upd1/final",
    ),
    (
        "Scientific Working Group on Digital Evidence (2025). Best Practices for Mobile Device Evidence "
        "Collection, Preservation, Handling, and Acquisition. SWGDE 18-F-003-2.0.",
        "https://www.swgde.org/wp-content/uploads/2025/09/2025-08-21-Best-Practices-for-Mobile-Device-Evidence-Collection-Preservation-Handling-and-Acquisition-18-F-003-2.0.pdf",
    ),
    (
        "Scientific Working Group on Digital Evidence (2026). Best Practices for Mobile Device Forensic Analysis. "
        "SWGDE 20-F-005-2.0.",
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
        "The Chromium Authors. HistoryDatabase.",
        "https://chromium.googlesource.com/chromium/src/+/refs/heads/main/components/history/core/browser/history_database.h",
    ),
    (
        "Mozilla. Places. Firefox Source Docs.",
        "https://firefox-source-docs.mozilla.org/browser/places/index.html",
    ),
    ("Google. Google Takeout.", "https://takeout.google.com/"),
    ("Topografix. GPX 1.1 schema.", "https://www.topografix.com/GPX/1/1/"),
    ("SQLite. The SQLite Database File Format.", "https://www.sqlite.org/fileformat2.html"),
    ("log2timeline project. Plaso documentation.", "https://plaso.readthedocs.io/en/latest/"),
    ("Brignoni, A. ALEAPP.", "https://github.com/abrignoni/ALEAPP"),
)


def report_sections(data: dict) -> list[dict]:
    activity = data["activity"]
    params = data["correlation"]
    names = sorted(activity["source_counts"])
    window = params["window_seconds"]
    return [
        {"id": "required", "title": "1. Solution required", "paragraphs": _required()},
        {"id": "proposed", "title": "2. Solution proposed", "paragraphs": _proposed()},
        {"id": "algorithm", "title": "3. Algorithm", "paragraphs": _algorithm(window)},
        {"id": "outcome", "title": "4. Outcome", "paragraphs": _outcome(activity, names)},
        {"id": "conclusion", "title": "5. Conclusion", "paragraphs": _conclusion(data, names, window)},
        {"id": "inference", "title": "6. Inference", "paragraphs": _inference(data)},
        {"id": "references", "title": "7. References", "paragraphs": _references()},
    ]


def _required() -> list[str]:
    return [
        "The assigned problem (problem 31, Mobile App Usage Timeline Builder) is to correlate location data, "
        "browsing history, and app usage logs into one forensic timeline. The suggested tools are Python, "
        "SQLite, and Plaso. The work is acquisition, analysis, validation, and report.",
        "Those three sources do not arrive as one log. Location may be a GPS track, a GPX file, or a Google "
        "location export. Browsing may be a Chromium History database or a Firefox places database. App usage "
        "may be an Android usage-stats database, a CSV or XML export, or dumpsys usagestats text. Some clocks "
        "are UTC, some carry an offset, and some are local time with no zone.",
    ]


def _proposed() -> list[str]:
    return [
        "Fuseline is a local program for Android artifacts. The examiner creates a case, records who is "
        "examining it, and sets the device timezone. Evidence is added by uploading files, or by an authorised "
        "USB debugging connection. Each case is its own SQLite database. The parser is chosen by reading the "
        "file, not the file name.",
        "On USB, Fuseline pulls dumpsys usagestats, a dumpsys location snapshot when that text contains timed "
        "coordinates, and supported files in Download and Documents. Browser history can be placed in those "
        "folders or uploaded from the examiner's machine.",
    ]


def _algorithm(window: int) -> list[str]:
    return [
        "Each file is hashed with SHA-256 before it is stored. The stored copy is hashed again and kept "
        "read-only. A file whose digest is already in the case is not parsed again. Rows that cannot be "
        "interpreted are skipped and counted.",
        "Every event stores a UTC time for sorting, the original value, and a basis: absolute, offset, or "
        "assumed. A timestamp with no zone is read in the case timezone and marked assumed.",
        f"A session is built only when events from at least two sources fall inside {window} seconds. "
        "A run longer than 30 minutes is split. A session means the records are close in time. It is not a "
        "claim that one event caused another.",
    ]


def _outcome(activity: dict, names: list[str]) -> list[str]:
    notes = list(activity.get("notes") or [])
    if not notes:
        listed = ", ".join(names) if names else "no sources"
        notes = [f"{activity['event_count']:,} events. Sources: {listed}."]
    notes.append("The tables that follow are the files, apps, and record types in this case.")
    return notes


def _conclusion(data: dict, names: list[str], window: int) -> list[str]:
    if len(names) < 2:
        present = ", ".join(SOURCE_PLAIN.get(name, name.replace("_", " ")) for name in names) or "nothing"
        sessions = (
            f"This case has {present}. No session was built, because a session needs two sources inside "
            f"{window} seconds."
        )
    elif data["session_count"] == 0:
        sessions = f"No pair of events from different sources falls inside {window} seconds."
    else:
        count = data["session_count"]
        sessions = f"{count:,} session{'s' if count != 1 else ''} were built with the current window."
    return [
        "Fuseline places the timestamps from the acquired artifacts on one UTC timeline and keeps each event "
        "tied to the stored file and its SHA-256.",
        sessions,
    ]


def _inference(data: dict) -> list[str]:
    warnings = [finding["message"] for finding in data["findings"] if finding["code"] in WARN_CODES]
    if not warnings:
        warnings = ["No skipped rows, clock warnings, or hash problems were recorded."]
    warnings.append(
        "Event count is not time on screen. Foreground time is the gap from a move-to-foreground or "
        "activity-resumed record to the next background, pause, or stop for the same package. An open "
        "foreground with no later stop is left without a duration."
    )
    return warnings


def _references() -> list[str]:
    return [
        "The course problem statement is an internal brief, so it is described in section 1 and is not listed here.",
        *[f"{index}. {citation} {url}" for index, (citation, url) in enumerate(REFERENCES, start=1)],
    ]
