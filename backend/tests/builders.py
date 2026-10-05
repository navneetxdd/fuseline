"""Small factories for synthetic evidence files used across the test suite."""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

WEBKIT_DELTA = 11_644_473_600
BASE = datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC)


def webkit(dt: datetime) -> int:
    return int((dt.timestamp() + WEBKIT_DELTA) * 1_000_000)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def chromium_db(
    path: Path,
    visits: list[tuple[str, str, datetime]],
    *,
    transition: int = 1,
    search_terms: dict[int, str] | None = None,
    downloads: list[tuple[str, str, datetime]] | None = None,
    raw_visit_times: list[int] | None = None,
) -> Path:
    """``visits`` are (url, title, when); ``downloads`` are (target_path, url, when)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT, visit_count INTEGER, last_visit_time INTEGER);
        CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER, from_visit INTEGER,
                             transition INTEGER, visit_duration INTEGER DEFAULT 0);
        CREATE TABLE keyword_search_terms (keyword_id INTEGER, url_id INTEGER, term TEXT, normalized_term TEXT);
        CREATE TABLE downloads (id INTEGER PRIMARY KEY, target_path TEXT, start_time INTEGER, end_time INTEGER,
                                total_bytes INTEGER, mime_type TEXT, tab_url TEXT);
        CREATE TABLE downloads_url_chains (id INTEGER, chain_index INTEGER, url TEXT);
        """
    )
    for i, (url, title, when) in enumerate(visits, start=1):
        conn.execute("INSERT INTO urls VALUES (?,?,?,?,?)", (i, url, title, 1, webkit(when)))
        conn.execute("INSERT INTO visits VALUES (?,?,?,?,?,?)", (i, i, webkit(when), 0, transition, 1_500_000))
    for offset, raw in enumerate(raw_visit_times or []):
        n = len(visits) + offset + 1
        conn.execute("INSERT INTO urls VALUES (?,?,?,?,?)", (n, f"https://raw{n}.test/", f"raw{n}", 1, 0))
        conn.execute("INSERT INTO visits VALUES (?,?,?,?,?,?)", (n, n, raw, 0, 0, 0))
    for url_id, term in (search_terms or {}).items():
        conn.execute("INSERT INTO keyword_search_terms VALUES (2, ?, ?, ?)", (url_id, term, term.lower()))
    for i, (target, url, when) in enumerate(downloads or [], start=1):
        conn.execute(
            "INSERT INTO downloads VALUES (?,?,?,?,?,?,?)",
            (i, target, webkit(when), webkit(when + timedelta(seconds=5)), 2048, "application/pdf", None),
        )
        conn.execute("INSERT INTO downloads_url_chains VALUES (?,?,?)", (i, 0, url))
    conn.commit()
    conn.close()
    return path


def firefox_db(path: Path, visits: list[tuple[str, str, datetime]]) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT, visit_count INTEGER);
        CREATE TABLE moz_historyvisits (id INTEGER PRIMARY KEY, from_visit INTEGER, place_id INTEGER,
                                        visit_date INTEGER, visit_type INTEGER);
        """
    )
    for i, (url, title, when) in enumerate(visits, start=1):
        conn.execute("INSERT INTO moz_places VALUES (?,?,?,?)", (i, url, title, 1))
        conn.execute(
            "INSERT INTO moz_historyvisits VALUES (?,?,?,?,?)", (i, 0, i, int(when.timestamp() * 1_000_000), 2)
        )
    conn.commit()
    conn.close()
    return path


def app_usage_db(path: Path, events: list[tuple[str, int, datetime]]) -> Path:
    """``events`` are (package, type_code, when)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE packages (_id INTEGER PRIMARY KEY, package_name TEXT UNIQUE);
        CREATE TABLE events (_id INTEGER PRIMARY KEY, timestamp INTEGER, type INTEGER, package_id INTEGER);
        """
    )
    ids: dict[str, int] = {}
    for package, code, when in events:
        if package not in ids:
            ids[package] = len(ids) + 1
            conn.execute("INSERT INTO packages VALUES (?,?)", (ids[package], package))
        conn.execute("INSERT INTO events (timestamp, type, package_id) VALUES (?,?,?)", (ms(when), code, ids[package]))
    conn.commit()
    conn.close()
    return path


def location_csv(
    path: Path, rows: list[tuple[str, float, float]], header: str = "timestamp,latitude,longitude"
) -> Path:
    lines = [header] + [f"{ts},{lat},{lon}" for ts, lat, lon in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def app_usage_csv(path: Path, rows: list[tuple[str, str]]) -> Path:
    lines = ["package,timestamp"] + [f"{pkg},{ts}" for pkg, ts in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def gpx(path: Path, points: list[tuple[float, float, str | None]]) -> Path:
    body = "".join(
        f'<trkpt lat="{lat}" lon="{lon}">' + (f"<time>{t}</time>" if t else "") + "<ele>10</ele></trkpt>"
        for lat, lon, t in points
    )
    path.write_text(
        '<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
        f"<trk><trkseg>{body}</trkseg></trk></gpx>",
        encoding="utf-8",
    )
    return path


def takeout(path: Path, fixes: list[tuple[float, float, str]]) -> Path:
    locations = [
        {"latitudeE7": int(lat * 1e7), "longitudeE7": int(lon * 1e7), "accuracy": 20, "timestamp": ts, "source": "WIFI"}
        for lat, lon, ts in fixes
    ]
    path.write_text(json.dumps({"locations": locations}), encoding="utf-8")
    return path


def plaso_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    fields = ["date", "time", "timezone", "source", "sourcetype", "type", "short", "desc", "message", "url"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})
    return path


def plaso_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def adb_usagestats_dump(
    events: list[tuple[str, str, datetime]],
    *,
    preamble: bool = True,
    modern: bool = False,
) -> str:
    """Synthetic `adb shell dumpsys usagestats` text. ``events`` are (package, TYPE, when).

    ``modern=True`` mimics Android 12+ dumps that use ``Last 24 hour events`` instead of
    the older ``Usage Events:`` section header.
    """
    lines = []
    if preamble:
        lines += [
            "Flags:",
            "    android.app.usage.user_interaction_type_api: true",
            "user=0 ",
            "",
        ]
    lines.append('  Last 24 hour events (timeRange="…")' if modern else "  Usage Events:")
    for package, event_type, when in events:
        ts = when.strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f'    time="{ts}" type={event_type} package={package} class=.MainActivity instanceId=7')
    lines.append("configStatsService:")  # following, unrelated section
    lines.append("  nothing to see here")
    return "\n".join(lines) + "\n"
