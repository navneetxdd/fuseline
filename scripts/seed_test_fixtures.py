#!/usr/bin/env python3
"""Regenerate backend/tests/fixtures/evidence for the test suite (not a product feature)."""

from __future__ import annotations

import csv
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "backend" / "tests" / "fixtures" / "evidence"

# Chromium epoch
WEBKIT_EPOCH_DELTA = 11644473600


def utc_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def to_webkit(dt: datetime) -> int:
    return int((dt.timestamp() + WEBKIT_EPOCH_DELTA) * 1_000_000)


def build_app_usage(base: datetime) -> None:
    path = FIXTURES / "app_usage.db"
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE packages (
          _id INTEGER PRIMARY KEY,
          package_name TEXT UNIQUE
        );
        CREATE TABLE events (
          _id INTEGER PRIMARY KEY,
          timestamp INTEGER NOT NULL,
          type INTEGER NOT NULL,
          package_id INTEGER NOT NULL REFERENCES packages(_id)
        );
        """
    )
    packages = [
        (1, "com.google.android.apps.maps"),
        (2, "com.android.chrome"),
        (3, "com.whatsapp"),
        (4, "com.spotify.music"),
    ]
    conn.executemany("INSERT INTO packages (_id, package_name) VALUES (?, ?)", packages)
    events = [
        (1, utc_ms(base + timedelta(minutes=0)), 1, 1),  # maps
        (2, utc_ms(base + timedelta(minutes=1)), 1, 2),  # chrome
        (3, utc_ms(base + timedelta(minutes=12)), 1, 3),  # whatsapp
        (4, utc_ms(base + timedelta(minutes=45)), 1, 4),  # spotify
        (5, utc_ms(base + timedelta(hours=2)), 1, 2),
        (6, utc_ms(base + timedelta(hours=2, minutes=2)), 1, 1),
    ]
    conn.executemany(
        "INSERT INTO events (_id, timestamp, type, package_id) VALUES (?, ?, ?, ?)",
        events,
    )
    conn.commit()
    conn.close()


def build_chrome_history(base: datetime) -> None:
    path = FIXTURES / "History"
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE urls (
          id INTEGER PRIMARY KEY,
          url TEXT,
          title TEXT,
          visit_count INTEGER,
          typed_count INTEGER,
          last_visit_time INTEGER,
          hidden INTEGER DEFAULT 0
        );
        CREATE TABLE visits (
          id INTEGER PRIMARY KEY,
          url INTEGER,
          visit_time INTEGER,
          from_visit INTEGER,
          transition INTEGER,
          segment_id INTEGER,
          visit_duration INTEGER DEFAULT 0
        );
        """
    )
    urls = [
        (1, "https://maps.google.com/directions", "Directions", 3, 1, to_webkit(base + timedelta(minutes=2)), 0),
        (2, "https://news.example.com/local", "Local News", 1, 0, to_webkit(base + timedelta(minutes=20)), 0),
        (3, "https://mail.google.com/", "Gmail", 5, 2, to_webkit(base + timedelta(hours=2, minutes=1)), 0),
        (4, "https://open.spotify.com/", "Spotify Web", 2, 0, to_webkit(base + timedelta(minutes=46)), 0),
    ]
    visits = [
        (1, 1, to_webkit(base + timedelta(minutes=2)), 0, 1, 0, 0),
        (2, 2, to_webkit(base + timedelta(minutes=20)), 0, 1, 0, 0),
        (3, 4, to_webkit(base + timedelta(minutes=46)), 0, 1, 0, 0),
        (4, 3, to_webkit(base + timedelta(hours=2, minutes=1)), 0, 1, 0, 0),
        (5, 1, to_webkit(base + timedelta(hours=2, minutes=3)), 0, 1, 0, 0),
    ]
    conn.executemany(
        "INSERT INTO urls VALUES (?, ?, ?, ?, ?, ?, ?)",
        urls,
    )
    conn.executemany(
        "INSERT INTO visits VALUES (?, ?, ?, ?, ?, ?, ?)",
        visits,
    )
    conn.commit()
    conn.close()


def build_location_csv(base: datetime) -> None:
    path = FIXTURES / "location.csv"
    rows = [
        {
            "timestamp": (base + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "latitude": "12.971600",
            "longitude": "77.594600",
            "accuracy": "12",
            "provider": "gps",
        },
        {
            "timestamp": (base + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "latitude": "12.975000",
            "longitude": "77.599000",
            "accuracy": "18",
            "provider": "network",
        },
        {
            "timestamp": (base + timedelta(hours=2, minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "latitude": "12.980200",
            "longitude": "77.605100",
            "accuracy": "10",
            "provider": "gps",
        },
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["timestamp", "latitude", "longitude", "accuracy", "provider"])
        writer.writeheader()
        writer.writerows(rows)


def build_plaso_sample(base: datetime) -> None:
    path = FIXTURES / "plaso_sample.l2t.csv"
    # Three correlated events covering browsing / app / location lanes
    rows = [
        {
            "date": (base + timedelta(minutes=3)).strftime("%Y-%m-%d"),
            "time": (base + timedelta(minutes=3)).strftime("%H:%M:%S"),
            "timezone": "UTC",
            "source": "WEBHIST",
            "sourcetype": "Chrome History",
            "desc": "Chrome History",
            "message": "Visited https://maps.google.com/search?q=cafe",
            "URL": "https://maps.google.com/search?q=cafe",
            "package": "",
            "latitude": "",
            "longitude": "",
        },
        {
            "date": (base + timedelta(minutes=4, seconds=12)).strftime("%Y-%m-%d"),
            "time": (base + timedelta(minutes=4, seconds=12)).strftime("%H:%M:%S"),
            "timezone": "UTC",
            "source": "LOG",
            "sourcetype": "android:app_usage",
            "desc": "Android App Usage",
            "message": "MOVE_TO_FOREGROUND com.whatsapp",
            "URL": "",
            "package": "com.whatsapp",
            "latitude": "",
            "longitude": "",
        },
        {
            "date": (base + timedelta(minutes=5, seconds=40)).strftime("%Y-%m-%d"),
            "time": (base + timedelta(minutes=5, seconds=40)).strftime("%H:%M:%S"),
            "timezone": "UTC",
            "source": "LOG",
            "sourcetype": "gps",
            "desc": "GPS Fix",
            "message": "Location fix near cafe",
            "URL": "",
            "package": "",
            "latitude": "12.971600",
            "longitude": "77.594600",
        },
    ]
    fields = [
        "date",
        "time",
        "timezone",
        "source",
        "sourcetype",
        "desc",
        "message",
        "URL",
        "package",
        "latitude",
        "longitude",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    base = datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC)
    build_app_usage(base)
    build_chrome_history(base)
    build_location_csv(base)
    build_plaso_sample(base)
    print(f"Seeded test fixtures in {FIXTURES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
