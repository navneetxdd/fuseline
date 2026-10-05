from __future__ import annotations

import sqlite3
from pathlib import Path

from app.parsers.adb_location import AdbLocationParser
from app.parsers.adb_usagestats import AdbUsageStatsParser
from app.parsers.app_usage import AppUsageParser
from app.parsers.base import ArtifactParser, ParseContext, ParseResult
from app.parsers.chrome_history import ChromiumHistoryParser
from app.parsers.firefox_history import FirefoxPlacesParser
from app.parsers.location import LocationParser
from app.parsers.plaso_l2tcsv import PlasoParser

PARSERS: list[ArtifactParser] = [
    ChromiumHistoryParser(),
    FirefoxPlacesParser(),
    AppUsageParser(),
    AdbUsageStatsParser(),
    AdbLocationParser(),
    LocationParser(),
    PlasoParser(),
]

VALID_SOURCES = {p.source for p in PARSERS}

SUPPORTED_FORMATS = [
    {"source": "browsing", "parser": "chromium_history", "label": "Chromium / Chrome / Edge History (SQLite)"},
    {"source": "browsing", "parser": "firefox_places", "label": "Firefox / Fenix places.sqlite"},
    {"source": "app_usage", "parser": "android_app_usage", "label": "Android UsageStats (SQLite, CSV or XML)"},
    {
        "source": "app_usage",
        "parser": "adb_usagestats_dump",
        "label": "adb shell dumpsys usagestats (live device pull)",
    },
    {
        "source": "location",
        "parser": "location",
        "label": "Location fixes (CSV, SQLite, GPX, Google Takeout Records.json)",
    },
    {
        "source": "location",
        "parser": "adb_location_dump",
        "label": "adb shell dumpsys location (live device pull)",
    },
    {"source": "plaso", "parser": "plaso_timeline", "label": "Plaso psort output (L2TCSV or JSON lines)"},
]

NO_MATCH_HELP = (
    "Supported: Chromium/Firefox history databases, Android UsageStats (SQLite/CSV/XML), "
    "location data (CSV/SQLite/GPX/Takeout JSON) and Plaso L2TCSV/JSONL."
)


def detect_parser(path: Path, preferred_source: str | None = None) -> ArtifactParser | None:
    """Pick the parser whose content sniff is most confident (file names are never trusted)."""
    if preferred_source and preferred_source not in VALID_SOURCES:
        raise ValueError(f"Unknown source hint: {preferred_source}")
    candidates = [p for p in PARSERS if not preferred_source or p.source == preferred_source]
    scored = [(p.sniff(path), i, p) for i, p in enumerate(candidates)]
    scored = [item for item in scored if item[0] > 0]
    if not scored:
        if preferred_source:
            raise ValueError(
                f"File does not match source hint '{preferred_source}'. Use auto-detect or upload a matching artifact."
            )
        return None
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][2]


def parse_artifact(
    path: Path,
    preferred_source: str | None = None,
    ctx: ParseContext | None = None,
    *,
    label: str | None = None,
) -> tuple[ArtifactParser, ParseResult]:
    """Detect a parser for ``path`` and run it. ``label`` is the name shown in errors (the examiner's
    file name, not the temporary path the upload happens to sit at)."""
    ctx = ctx or ParseContext()
    name = label or path.name
    parser = detect_parser(path, preferred_source)
    if parser is None:
        raise ValueError(f"No parser recognised the contents of {name}. {NO_MATCH_HELP}")
    try:
        result = parser.parse(path, ctx)
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"Failed to parse {name} as {parser.name}: {exc}") from exc
    return parser, result
