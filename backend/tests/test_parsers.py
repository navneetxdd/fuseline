from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from builders import (
    BASE,
    adb_usagestats_dump,
    app_usage_csv,
    app_usage_db,
    chromium_db,
    firefox_db,
    gpx,
    iso,
    location_csv,
    plaso_csv,
    plaso_jsonl,
    takeout,
)

from app.parsers.adb_usagestats import AdbUsageStatsParser
from app.parsers.app_usage import AppUsageParser
from app.parsers.base import ParseContext
from app.parsers.chrome_history import ChromiumHistoryParser, decode_transition
from app.parsers.firefox_history import FirefoxPlacesParser
from app.parsers.location import LocationParser
from app.parsers.plaso_l2tcsv import PlasoParser, classify
from app.parsers.registry import detect_parser, parse_artifact

UTC_CTX = ParseContext()
NY_CTX = ParseContext.for_timezone("America/New_York")


# --- shipped demo samples ---------------------------------------------------------------------


def test_demo_samples_parse(demo_dir: Path):
    app = AppUsageParser().parse(demo_dir / "app_usage.db", UTC_CTX)
    assert len(app.records) == 6 and app.skipped == 0
    assert any("maps" in (e.package or "") for e in app.records)
    assert app.records[0].event_type == "move_to_foreground"

    browse = ChromiumHistoryParser().parse(demo_dir / "History", UTC_CTX)
    assert len(browse.records) == 5
    assert any(e.domain and "google" in e.domain for e in browse.records)

    loc = LocationParser().parse(demo_dir / "location.csv", UTC_CTX)
    assert len(loc.records) == 3 and loc.records[0].lat is not None

    plaso = PlasoParser().parse(demo_dir / "plaso_sample.l2t.csv", UTC_CTX)
    assert len(plaso.records) == 3
    assert {r.source for r in plaso.records} == {"browsing", "app_usage", "location"}
    assert plaso.records[0].source == "browsing"


@pytest.mark.parametrize(
    ("filename", "parser_name"),
    [
        ("app_usage.db", "android_app_usage"),
        ("History", "chromium_history"),
        ("location.csv", "location"),
        ("plaso_sample.l2t.csv", "plaso_timeline"),
    ],
)
def test_demo_samples_are_detected_by_content(demo_dir: Path, filename: str, parser_name: str):
    assert detect_parser(demo_dir / filename).name == parser_name


# --- detection is content based, never name based ----------------------------------------------


def test_file_names_do_not_influence_detection(tmp_path: Path):
    loc = location_csv(tmp_path / "whatsapp_location.csv", [(iso(BASE), 12.9, 77.5)])
    assert detect_parser(loc).name == "location"
    renamed = location_csv(tmp_path / "points.dat", [(iso(BASE), 12.9, 77.5)])
    assert detect_parser(renamed).name == "location"
    usage = app_usage_csv(tmp_path / "gps_export.csv", [("com.a", iso(BASE))])
    assert detect_parser(usage).name == "android_app_usage"


def test_unrecognised_content_is_rejected(tmp_path: Path):
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"\x00\x01\x02\x03")
    assert detect_parser(junk) is None
    with pytest.raises(ValueError, match="No parser recognised"):
        parse_artifact(junk)


def test_source_hint_must_match_content(demo_dir: Path):
    with pytest.raises(ValueError, match="does not match source hint"):
        detect_parser(demo_dir / "location.csv", preferred_source="browsing")
    assert detect_parser(demo_dir / "location.csv", preferred_source="location") is not None
    with pytest.raises(ValueError, match="Unknown source hint"):
        detect_parser(demo_dir / "location.csv", preferred_source="nonsense")


def test_corrupt_sqlite_is_a_clean_error(tmp_path: Path):
    bad = tmp_path / "History"
    bad.write_bytes(b"SQLite format 3\x00" + b"\xff" * 200)
    assert detect_parser(bad) is None


# --- Chromium ----------------------------------------------------------------------------------


def test_chromium_decodes_transitions_search_terms_and_downloads(tmp_path: Path):
    db = chromium_db(
        tmp_path / "History",
        [
            ("https://www.google.com/search?q=cats", "cats - Google Search", BASE),
            ("https://example.com/", "Example", BASE + timedelta(minutes=1)),
        ],
        transition=0x02000001,  # typed + from address bar
        search_terms={1: "cats"},
        downloads=[
            ("/storage/emulated/0/Download/report.pdf", "https://example.com/report.pdf", BASE + timedelta(minutes=2))
        ],
    )
    result = ChromiumHistoryParser().parse(db, UTC_CTX)
    visits = [e for e in result.records if e.event_type == "visit"]
    downloads = [e for e in result.records if e.event_type == "download"]
    assert len(visits) == 2 and len(downloads) == 1
    assert visits[0].detail["search_term"] == "cats"
    assert visits[0].detail["transition_name"] == "typed"
    assert visits[0].detail["transition_qualifiers"] == ["from_address_bar"]
    assert visits[0].detail["visit_duration_us"] == 1_500_000
    assert downloads[0].title == "Downloaded report.pdf"
    assert downloads[0].url == "https://example.com/report.pdf"
    assert downloads[0].domain == "example.com"


def test_chromium_skips_bad_visit_times_and_counts_them(tmp_path: Path):
    db = chromium_db(
        tmp_path / "History",
        [("https://ok.test/", "ok", BASE)],
        raw_visit_times=[0, 9_000_000_000_000_000_000],  # missing, and past year 9999
    )
    result = ChromiumHistoryParser().parse(db, UTC_CTX)
    assert len(result.records) == 1
    assert result.skipped == 2
    assert "visit_time missing or out of range" in result.notes()[0]


def test_transition_decoding():
    assert decode_transition(0) == ("link", [])
    assert decode_transition(0x10000008) == ("reload", ["chain_start"])
    assert decode_transition(None) == (None, [])
    assert decode_transition(99)[0] == "unknown_99"


def test_chromium_tolerates_missing_optional_columns(tmp_path: Path):
    import sqlite3

    path = tmp_path / "History"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT);"
        "CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER);"
    )
    from builders import webkit

    conn.execute("INSERT INTO urls VALUES (1,'https://a.test/','A')")
    conn.execute("INSERT INTO visits VALUES (1,1,?)", (webkit(BASE),))
    conn.commit()
    conn.close()
    result = ChromiumHistoryParser().parse(path, UTC_CTX)
    assert len(result.records) == 1 and result.skipped == 0


# --- Firefox -----------------------------------------------------------------------------------


def test_firefox_places(tmp_path: Path):
    db = firefox_db(tmp_path / "places.sqlite", [("https://mozilla.org/", "Mozilla", BASE)])
    parser = FirefoxPlacesParser()
    assert parser.sniff(db) > 0
    assert ChromiumHistoryParser().sniff(db) == 0
    (event,) = parser.parse(db, UTC_CTX).records
    assert event.ts_utc == "2024-06-15T10:00:00.000Z"
    assert event.detail["visit_type_name"] == "typed"
    assert event.domain == "mozilla.org"


# --- App usage ---------------------------------------------------------------------------------


def test_app_usage_sqlite_decodes_event_types(tmp_path: Path):
    db = app_usage_db(
        tmp_path / "usage.db",
        [("com.a", 1, BASE), ("com.a", 2, BASE + timedelta(seconds=30)), ("android", 15, BASE), ("com.b", 999, BASE)],
    )
    records = AppUsageParser().parse(db, UTC_CTX).records
    kinds = {(e.package, e.event_type) for e in records}
    assert ("com.a", "move_to_foreground") in kinds
    assert ("com.a", "move_to_background") in kinds
    assert ("android", "screen_interactive") in kinds
    assert ("com.b", "type_999") in kinds


def test_app_usage_csv_uses_case_timezone_for_naive_times(tmp_path: Path):
    path = app_usage_csv(tmp_path / "u.csv", [("com.a", "2024-06-15 10:00:00"), ("com.b", "2024-06-15T10:00:00Z")])
    a, b = AppUsageParser().parse(path, NY_CTX).records
    assert a.ts_utc == "2024-06-15T14:00:00.000Z" and a.ts_basis == "assumed" and a.tz_assumed == "America/New_York"
    assert b.ts_utc == "2024-06-15T10:00:00.000Z" and b.ts_basis == "absolute" and b.tz_assumed == "UTC"


def test_app_usage_csv_skips_and_counts_bad_rows(tmp_path: Path):
    path = tmp_path / "u.csv"
    path.write_text("package,timestamp\ncom.a,2024-06-15T10:00:00Z\n,2024-06-15T10:00:00Z\ncom.c,garbage\ncom.d,\n")
    result = AppUsageParser().parse(path, UTC_CTX)
    assert len(result.records) == 1
    assert result.skipped == 3


def test_app_usage_offset_timestamp_no_longer_corrupts_case(tmp_path: Path):
    path = app_usage_csv(tmp_path / "u.csv", [("com.a", "2024-06-15T10:01:00-05:00")])
    (event,) = AppUsageParser().parse(path, UTC_CTX).records
    assert event.ts_utc == "2024-06-15T15:01:00.000Z" and event.ts_basis == "offset"


def test_usagestats_xml(tmp_path: Path):
    path = tmp_path / "usagestats.xml"
    path.write_text(
        '<usagestats><package package="com.x" lastTimeActive="1718445600000"/>'
        '<package package="com.y" lastTimeActive="bad"/></usagestats>'
    )
    result = AppUsageParser().parse(path, UTC_CTX)
    assert [e.package for e in result.records] == ["com.x"]
    assert result.skipped == 1


def test_xml_entity_bombs_are_refused(tmp_path: Path):
    bomb = tmp_path / "usagestats.xml"
    bomb.write_text(
        '<?xml version="1.0"?><!DOCTYPE lol [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        '<usagestats><package package="&b;" lastTimeActive="1718445600000"/></usagestats>'
    )
    with pytest.raises(ValueError, match="unsafe XML"):
        parse_artifact(bomb)


# --- Location ----------------------------------------------------------------------------------


def test_location_csv_validates_coordinates(tmp_path: Path):
    path = location_csv(
        tmp_path / "gps.csv",
        [
            (iso(BASE), 12.97, 77.59),
            (iso(BASE), 0.0, 0.0),  # placeholder no-fix
            (iso(BASE), 91.0, 10.0),  # out of range
            (iso(BASE), "abc", 10.0),  # not numeric
            ("nonsense", 12.0, 77.0),
        ],
    )
    result = LocationParser().parse(path, UTC_CTX)
    assert len(result.records) == 1
    assert result.skipped == 4


def test_location_naive_timestamp_uses_case_timezone(tmp_path: Path):
    path = location_csv(tmp_path / "gps.csv", [("2024-06-15 10:00:00", 12.9, 77.5)])
    (event,) = LocationParser().parse(path, ParseContext.for_timezone("Asia/Kolkata")).records
    assert event.ts_utc == "2024-06-15T04:30:00.000Z"
    assert event.ts_basis == "assumed" and event.tz_assumed == "Asia/Kolkata"


def test_location_supports_semicolon_delimiter_and_epoch(tmp_path: Path):
    path = tmp_path / "loc.csv"
    path.write_text("time;lat;lng\n1718445600;12.9;77.5\n")
    (event,) = LocationParser().parse(path, UTC_CTX).records
    assert event.ts_utc == "2024-06-15T10:00:00.000Z"


def test_location_sqlite_picks_biggest_geo_table(tmp_path: Path):
    import sqlite3

    path = tmp_path / "loc.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE tiny (timestamp INTEGER, lat REAL, lon REAL);"
        'CREATE TABLE "fixes ""odd""" (timestamp INTEGER, latitude REAL, longitude REAL);'
    )
    conn.execute("INSERT INTO tiny VALUES (1718445600, 1.0, 2.0)")
    conn.executemany('INSERT INTO "fixes ""odd""" VALUES (?,?,?)', [(1718445600 + i, 12.9, 77.5) for i in range(3)])
    conn.commit()
    conn.close()
    parser = LocationParser()
    assert parser.sniff(path) > 0
    result = parser.parse(path, UTC_CTX)
    assert len(result.records) == 3


def test_gpx(tmp_path: Path):
    path = gpx(tmp_path / "walk.gpx", [(12.97, 77.59, "2024-06-15T10:00:00Z"), (12.98, 77.60, None)])
    parser = LocationParser()
    assert parser.sniff(path) > 0.9
    result = parser.parse(path, UTC_CTX)
    assert len(result.records) == 1 and result.skipped == 1
    assert result.records[0].detail["ele"] == "10"


def test_google_takeout_records(tmp_path: Path):
    path = takeout(tmp_path / "Records.json", [(12.9716, 77.5946, "2024-06-15T10:00:00.123Z")])
    parser = LocationParser()
    assert parser.sniff(path) > 0.9
    (event,) = parser.parse(path, UTC_CTX).records
    assert event.lat == pytest.approx(12.9716) and event.lon == pytest.approx(77.5946)
    assert event.ts_utc == "2024-06-15T10:00:00.123Z"
    assert event.detail["source"] == "WIFI"


# --- Plaso -------------------------------------------------------------------------------------


def test_plaso_csv_honours_declared_timezone(tmp_path: Path):
    path = plaso_csv(
        tmp_path / "timeline.l2t.csv",
        [
            {
                "date": "06/15/2024",
                "time": "15:30:00",
                "timezone": "Asia/Kolkata",
                "source": "WEBHIST",
                "desc": "Chrome History",
                "message": "Visited example.com",
                "url": "https://example.com/",
            },
            {"date": "06/15/2024", "time": "10:00:00", "timezone": "UTC", "source": "LOG", "message": "syslog line"},
            {"date": "06/15/2024", "time": "10:00:00", "timezone": "", "source": "LOG", "message": "no tz"},
        ],
    )
    a, b, c = PlasoParser().parse(path, NY_CTX).records
    assert a.ts_utc == "2024-06-15T10:00:00.000Z" and a.ts_basis == "offset" and a.tz_assumed == "Asia/Kolkata"
    assert a.source == "browsing" and a.domain == "example.com"
    assert b.ts_basis == "absolute" and b.source == "plaso"
    assert c.ts_utc == "2024-06-15T14:00:00.000Z" and c.ts_basis == "assumed"


def test_plaso_lane_classification_ignores_message_text():
    assert classify("WEBHIST", "Chrome History") == "browsing"
    assert classify("chrome:history:page_visited", "sqlite/chrome_27_history") == "browsing"
    assert classify("mozilla:places", "firefox_history") == "browsing"
    assert classify("android:app_usage", "android_app_usage") == "app_usage"
    assert classify("gps", "location") == "location"
    assert classify("android_webview", "webview") == "browsing"
    assert classify("LOG", "syslog") == "plaso"
    assert classify(None, "") == "plaso"


def test_plaso_jsonlines_microsecond_timestamps(tmp_path: Path):
    path = plaso_jsonl(
        tmp_path / "events.jsonl",
        [
            {
                "__container_type__": "event",
                "data_type": "chrome:history:page_visited",
                "timestamp": 1718445600000000,
                "timestamp_desc": "Last Visited Time",
                "message": "Visited https://example.com/ (Example)",
                "url": "https://example.com/",
                "parser": "sqlite/chrome_27_history",
            },
            {"data_type": "fs:stat", "timestamp": "not a number", "message": "bad", "date": "", "time": ""},
        ],
    )
    parser = PlasoParser()
    assert parser.sniff(path) > 0.8
    result = parser.parse(path, UTC_CTX)
    (event,) = result.records
    assert event.source == "browsing" and event.ts_utc == "2024-06-15T10:00:00.000Z"
    assert event.event_type == "last_visited_time"
    assert result.skipped == 1


def test_takeout_json_is_not_mistaken_for_plaso(tmp_path: Path):
    path = takeout(tmp_path / "Records.json", [(12.9, 77.5, "2024-06-15T10:00:00Z")])
    assert detect_parser(path).name == "location"


# --- adb usagestats dump ------------------------------------------------------------------------


def test_adb_usagestats_dump_is_detected_by_content(tmp_path: Path):
    path = tmp_path / "dumpsys.txt"
    path.write_text(adb_usagestats_dump([("com.whatsapp", "MOVE_TO_FOREGROUND", BASE)]), encoding="utf-8")
    assert AdbUsageStatsParser().sniff(path) > 0.8
    assert detect_parser(path).name == "adb_usagestats_dump"


def test_adb_usagestats_modern_android_header_is_detected(tmp_path: Path):
    path = tmp_path / "dumpsys_modern.txt"
    path.write_text(
        adb_usagestats_dump([("com.whatsapp", "ACTIVITY_RESUMED", BASE)], modern=True),
        encoding="utf-8",
    )
    assert AdbUsageStatsParser().sniff(path) > 0.8
    assert detect_parser(path, preferred_source="app_usage").name == "adb_usagestats_dump"
    result = AdbUsageStatsParser().parse(path, UTC_CTX)
    assert len(result.records) == 1 and result.records[0].event_type == "activity_resumed"


def test_adb_usagestats_dump_parses_events_and_extra_fields(tmp_path: Path):
    path = tmp_path / "dumpsys.txt"
    text = adb_usagestats_dump(
        [
            ("com.whatsapp", "MOVE_TO_FOREGROUND", BASE),
            ("com.whatsapp", "MOVE_TO_BACKGROUND", BASE + timedelta(minutes=2)),
            ("com.android.chrome", "MOVE_TO_FOREGROUND", BASE + timedelta(minutes=3)),
        ]
    )
    path.write_text(text, encoding="utf-8")
    result = AdbUsageStatsParser().parse(path, UTC_CTX)
    assert len(result.records) == 3 and result.skipped == 0
    first = result.records[0]
    assert first.package == "com.whatsapp" and first.event_type == "move_to_foreground"
    assert first.ts_utc == "2024-06-15T10:00:00.000Z"
    assert first.detail["type"] == "MOVE_TO_FOREGROUND" and first.detail["instanceId"] == "7"


def test_adb_usagestats_dump_ignores_non_event_sections(tmp_path: Path):
    path = tmp_path / "dumpsys.txt"
    text = adb_usagestats_dump([("com.a", "MOVE_TO_FOREGROUND", BASE)]) + '    time="bogus" type=X package=com.b\n'
    path.write_text(text, encoding="utf-8")
    result = AdbUsageStatsParser().parse(path, UTC_CTX)
    assert len(result.records) == 1
    assert result.skipped >= 1  # bogus timestamp


def test_adb_usagestats_dump_skips_unparseable_lines(tmp_path: Path):
    path = tmp_path / "dumpsys.txt"
    text = (
        'Usage Events:\n    time="not-a-real-event" garbled\n'
        + adb_usagestats_dump([("com.a", "MOVE_TO_FOREGROUND", BASE)], preamble=False).split("Usage Events:\n", 1)[1]
    )
    path.write_text(text, encoding="utf-8")
    result = AdbUsageStatsParser().parse(path, UTC_CTX)
    assert len(result.records) == 1 and result.skipped == 1


def test_adb_usagestats_dump_uses_case_timezone_for_naive_times(tmp_path: Path):
    path = tmp_path / "dumpsys.txt"
    path.write_text(adb_usagestats_dump([("com.a", "MOVE_TO_FOREGROUND", BASE)]), encoding="utf-8")
    (event,) = AdbUsageStatsParser().parse(path, NY_CTX).records
    assert event.ts_utc == "2024-06-15T14:00:00.000Z" and event.ts_basis == "assumed"
