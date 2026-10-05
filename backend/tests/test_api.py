from __future__ import annotations

import csv
import io
import os
import stat
import time
from datetime import timedelta
from pathlib import Path

import pytest
from builders import (
    BASE,
    app_usage_csv,
    chromium_db,
    iso,
    location_csv,
)

from app.config import UPLOADS_DIR

EVIL_TITLE = "<script>alert(document.domain)</script>"


def stored_files(case_id: str) -> list[Path]:
    folder = UPLOADS_DIR / case_id
    return sorted(folder.glob("*")) if folder.exists() else []


def load_demo(client, case_id):
    r = client.post(f"/api/cases/{case_id}/acquire/demo")
    assert r.status_code == 200, r.text
    return r.json()


# --- basics ------------------------------------------------------------------------------------


def test_health_and_meta(client):
    assert client.get("/api/health").json()["status"] == "ok"
    meta = client.get("/api/meta").json()
    assert meta["max_upload_bytes"] > 0
    assert {f["parser"] for f in meta["formats"]} >= {"chromium_history", "location", "plaso_timeline"}


def test_case_crud_and_validation(client, make_case):
    r = client.post("/api/cases", json={"name": "  Device A  ", "examiner": " Sam ", "timezone": "Asia/Kolkata"})
    assert r.status_code == 201
    case = r.json()
    assert (case["name"], case["examiner"], case["timezone"]) == ("Device A", "Sam", "Asia/Kolkata")

    assert client.get(f"/api/cases/{case['id']}").json()["name"] == "Device A"
    assert any(c["id"] == case["id"] for c in client.get("/api/cases").json())

    patched = client.patch(f"/api/cases/{case['id']}", json={"notes": "seized 15 June", "timezone": "utc"}).json()
    assert patched["notes"] == "seized 15 June" and patched["timezone"] == "UTC" and patched["name"] == "Device A"

    assert client.post("/api/cases", json={"name": "   "}).status_code == 422
    assert client.post("/api/cases", json={"name": "x", "timezone": "Mars/Base"}).status_code == 422
    assert client.patch(f"/api/cases/{case['id']}", json={"timezone": "nope"}).status_code == 422
    assert client.get("/api/cases/not-a-uuid").status_code == 400
    assert client.get("/api/cases/479280c8-9ae9-49fe-8d25-2c7d749678bf").status_code == 404
    assert client.get("/api/cases/479280c8-9ae9-49fe-8d25-2c7d749678bf/timeline").status_code == 404


# --- demo pack ---------------------------------------------------------------------------------


def test_demo_pack_matches_documented_numbers(client, case_id, demo_dir):
    result = load_demo(client, case_id)
    assert (len(result["artifacts"]), result["events_added"], result["sessions_rebuilt"]) == (4, 17, 4)
    case = client.get(f"/api/cases/{case_id}").json()
    assert (case["event_count"], case["artifact_count"], case["session_count"]) == (17, 4, 4)

    again = load_demo(client, case_id)  # idempotent: everything is a duplicate
    assert again["events_added"] == 0 and len(again["artifacts"]) == 4
    assert client.get(f"/api/cases/{case_id}").json()["event_count"] == 17


def test_event_ids_are_deterministic_across_cases(client, make_case, demo_dir):
    first, second = make_case(), make_case()
    load_demo(client, first)
    load_demo(client, second)
    ids = lambda c: [e["id"] for e in client.get(f"/api/cases/{c}/timeline").json()["events"]]  # noqa: E731
    assert ids(first) == ids(second) and len(ids(first)) == 17


# --- regressions for defects found in the original release -------------------------------------


def test_offset_timestamp_no_longer_bricks_the_case(client, case_id, upload, tmp_path):
    good = location_csv(tmp_path / "gps.csv", [(iso(BASE), 12.9, 77.5)])
    assert upload(case_id, "gps.csv", good).status_code == 200
    offset = app_usage_csv(tmp_path / "usage.csv", [("com.a", "2024-06-15T10:01:00-05:00")])
    r = upload(case_id, "usage.csv", offset)
    assert r.status_code == 200, r.text
    later = app_usage_csv(tmp_path / "usage2.csv", [("com.b", "2024-06-15T10:02:00Z")])
    assert upload(case_id, "usage2.csv", later).status_code == 200  # the case is still usable

    events = client.get(f"/api/cases/{case_id}/timeline").json()["events"]
    assert all(e["ts_utc"].endswith(".000Z") and len(e["ts_utc"]) == 24 for e in events)
    assert "2024-06-15T15:01:00.000Z" in [e["ts_utc"] for e in events]


def test_rejected_uploads_leave_no_orphan_evidence(client, case_id, upload):
    r = upload(case_id, "mystery.bin", b"\x00\x01\x02")
    assert r.status_code == 400 and "No parser recognised" in r.json()["detail"]
    assert "mystery.bin" in r.json()["detail"] and "tmp" not in r.json()["detail"].split("contents of")[1][:12]
    assert stored_files(case_id) == []
    assert client.get(f"/api/cases/{case_id}/artifacts").json() == []


def test_recognised_file_with_no_usable_rows_is_rejected_with_reasons(client, case_id, upload, tmp_path):
    bad = location_csv(tmp_path / "gps.csv", [("garbage", 12.9, 77.5), (iso(BASE), 0.0, 0.0)])
    r = upload(case_id, "gps.csv", bad)
    assert r.status_code == 400
    assert "no usable events" in r.json()["detail"] and "skipped" in r.json()["detail"]
    assert stored_files(case_id) == [] and client.get(f"/api/cases/{case_id}/artifacts").json() == []


def test_partially_bad_file_is_ingested_and_skips_are_reported(client, case_id, upload, tmp_path):
    rows = [(iso(BASE), 12.9, 77.5), ("garbage", 12.9, 77.5), (iso(BASE + timedelta(minutes=1)), 12.91, 77.51)]
    r = upload(case_id, "gps.csv", location_csv(tmp_path / "gps.csv", rows))
    body = r.json()
    assert r.status_code == 200 and body["events_added"] == 2
    assert body["artifact"]["skipped_rows"] == 1 and "unparseable timestamp" in body["artifact"]["notes"][0]
    assert any(f["code"] == "ROWS_SKIPPED" for f in client.get(f"/api/cases/{case_id}/validation").json())


def test_case_timezone_is_applied_to_naive_timestamps(client, make_case, upload, tmp_path):
    case = make_case(timezone="America/New_York")
    path = location_csv(tmp_path / "gps.csv", [("2024-06-15 10:00:00", 12.9, 77.5)])
    assert upload(case, "gps.csv", path).status_code == 200
    (event,) = client.get(f"/api/cases/{case}/timeline").json()["events"]
    assert event["ts_utc"] == "2024-06-15T14:00:00.000Z"
    assert (event["ts_basis"], event["tz_assumed"]) == ("assumed", "America/New_York")
    findings = {f["code"]: f for f in client.get(f"/api/cases/{case}/validation").json()}
    assert "America/New_York" in findings["TZ_ASSUMED"]["message"]


def test_misleading_file_names_are_classified_by_content(client, case_id, upload, tmp_path):
    path = location_csv(tmp_path / "x.csv", [(iso(BASE), 12.9, 77.5)])
    r = upload(case_id, "whatsapp_location.csv", path)
    assert r.json()["artifact"]["source_type"] == "location"


def test_html_report_escapes_hostile_evidence(client, case_id, upload, tmp_path):
    db = chromium_db(tmp_path / "History", [("https://evil.test/", EVIL_TITLE, BASE)])
    assert upload(case_id, "History", db).status_code == 200
    r = client.get(f"/api/cases/{case_id}/report/html")
    assert r.status_code == 200
    assert EVIL_TITLE not in r.text
    assert "&lt;script&gt;alert(document.domain)&lt;/script&gt;" in r.text
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_csv_export_neutralises_formula_injection(client, case_id, upload, tmp_path):
    db = chromium_db(tmp_path / "History", [("https://x.test/", '=HYPERLINK("http://evil","click")', BASE)])
    assert upload(case_id, "History", db).status_code == 200
    safe = list(csv.DictReader(io.StringIO(client.get(f"/api/cases/{case_id}/report/csv").text)))
    assert safe[0]["title"].startswith("'=")
    raw = list(csv.DictReader(io.StringIO(client.get(f"/api/cases/{case_id}/report/csv?raw=true").text)))
    assert raw[0]["title"].startswith("=HYPERLINK")


# --- evidence handling -------------------------------------------------------------------------


def test_evidence_copy_is_read_only_and_integrity_check_detects_tampering(client, case_id, upload, tmp_path):
    path = location_csv(tmp_path / "gps.csv", [(iso(BASE), 12.9, 77.5)])
    assert upload(case_id, "gps.csv", path).status_code == 200
    (stored,) = stored_files(case_id)
    assert stat.S_IMODE(stored.stat().st_mode) & 0o222 == 0  # no write bit (os.access lies when run as root)

    ok = client.post(f"/api/cases/{case_id}/verify").json()
    assert ok["ok"] is True and ok["audit_chain_ok"] is True and ok["artifacts"][0]["status"] == "ok"

    os.chmod(stored, stat.S_IWRITE | stat.S_IREAD)
    stored.write_bytes(stored.read_bytes() + b"tampered")
    bad = client.post(f"/api/cases/{case_id}/verify").json()
    assert bad["ok"] is False and bad["artifacts"][0]["status"] == "modified"

    stored.unlink()
    assert client.post(f"/api/cases/{case_id}/verify").json()["artifacts"][0]["status"] == "missing"


def test_duplicate_upload_is_detected_by_hash(client, case_id, upload, tmp_path):
    path = location_csv(tmp_path / "gps.csv", [(iso(BASE), 12.9, 77.5)])
    first = upload(case_id, "gps.csv", path).json()
    second = upload(case_id, "copy_of_gps.csv", path).json()
    assert first["duplicate"] is False and second["duplicate"] is True and second["events_added"] == 0
    assert second["artifact"]["id"] == first["artifact"]["id"]
    assert len(stored_files(case_id)) == 1


def test_upload_size_limit_and_empty_upload(client, case_id, upload, monkeypatch):
    monkeypatch.setattr("app.api.acquire.MAX_UPLOAD_BYTES", 64)
    r = upload(case_id, "big.csv", b"x" * 200)
    assert r.status_code == 413
    assert upload(case_id, "empty.csv", b"").status_code == 400
    assert stored_files(case_id) == []


def test_deleting_a_case_removes_read_only_evidence(client, case_id, upload, tmp_path):
    path = location_csv(tmp_path / "gps.csv", [(iso(BASE), 12.9, 77.5)])
    upload(case_id, "gps.csv", path)
    assert stored_files(case_id)
    assert client.delete(f"/api/cases/{case_id}").status_code == 204
    assert not (UPLOADS_DIR / case_id).exists()
    assert client.get(f"/api/cases/{case_id}").status_code == 404
    entries = [a["action"] for a in _audit_for_deleted(case_id)]
    assert "case.delete" in entries


def _audit_for_deleted(case_id: str):
    from app import audit

    return audit.list_entries(case_id)


# --- timeline ----------------------------------------------------------------------------------


@pytest.fixture
def big_case(client, case_id, upload, tmp_path):
    rows = [
        (iso(BASE + timedelta(seconds=30 * i)), 12.0 + (i % 900) / 1000, 77.0 + (i % 700) / 1000) for i in range(2500)
    ]
    started = time.perf_counter()
    r = upload(case_id, "gps_big.csv", location_csv(tmp_path / "gps_big.csv", rows))
    assert r.status_code == 200 and r.json()["events_added"] == 2500
    assert time.perf_counter() - started < 10
    return case_id


def test_timeline_reports_truncation_and_paginates_to_the_end(client, big_case):
    first = client.get(f"/api/cases/{big_case}/timeline?limit=1000").json()
    assert (first["total"], len(first["events"]), first["has_more"]) == (2500, 1000, True)
    seen = []
    offset = 0
    while True:
        page = client.get(f"/api/cases/{big_case}/timeline?limit=1000&offset={offset}").json()
        seen += [e["id"] for e in page["events"]]
        if not page["has_more"]:
            break
        offset += 1000
    assert len(seen) == 2500 == len(set(seen))
    last = client.get(f"/api/cases/{big_case}/timeline?limit=1&order=desc").json()["events"][0]
    assert last["ts_utc"] == "2024-06-16T06:49:30.000Z"  # 10:00 + 2499 * 30 s


def test_timeline_time_range_and_filters(client, big_case, upload, tmp_path):
    start, end = "2024-06-15T10:10:00Z", "2024-06-15T10:19:30Z"
    page = client.get(f"/api/cases/{big_case}/timeline", params={"start": start, "end": end, "limit": 100}).json()
    assert page["total"] == 20 and page["events"][0]["ts_utc"] == "2024-06-15T10:10:00.000Z"
    assert page["events"][-1]["ts_utc"] == "2024-06-15T10:19:30.000Z"

    assert client.get(f"/api/cases/{big_case}/timeline?source=browsing").json()["total"] == 0
    assert client.get(f"/api/cases/{big_case}/timeline?source=location,browsing").json()["total"] == 2500
    assert client.get(f"/api/cases/{big_case}/timeline?start=yesterday").status_code == 400
    assert client.get(f"/api/cases/{big_case}/timeline?limit=0").status_code == 422


def test_timeline_search_treats_like_wildcards_literally(client, case_id, upload, tmp_path):
    db = chromium_db(
        tmp_path / "History",
        [
            ("https://a.test/100%_sure", "Sale 100% sure", BASE),
            ("https://b.test/", "Plain page", BASE + timedelta(minutes=1)),
        ],
    )
    upload(case_id, "History", db)
    hits = client.get(f"/api/cases/{case_id}/timeline", params={"q": "100%"}).json()
    assert hits["total"] == 1 and hits["events"][0]["title"] == "Sale 100% sure"
    assert client.get(f"/api/cases/{case_id}/timeline", params={"q": "%"}).json()["total"] == 1  # only the literal %
    assert client.get(f"/api/cases/{case_id}/timeline", params={"q": "b.test"}).json()["total"] == 1


def test_single_event_lookup(client, big_case):
    first = client.get(f"/api/cases/{big_case}/timeline?limit=1").json()["events"][0]
    got = client.get(f"/api/cases/{big_case}/events/{first['id']}")
    assert got.status_code == 200 and got.json() == first
    assert client.get(f"/api/cases/{big_case}/events/missing").status_code == 404


def test_overview_buckets_account_for_every_event(client, big_case):
    o = client.get(f"/api/cases/{big_case}/overview?buckets=100").json()
    assert o["total"] == 2500 and o["bucket_count"] <= 100 and o["bucket_ms"] > 0
    assert sum(c for _, c in o["series"]["location"]) == 2500
    assert all(0 <= idx < o["bucket_count"] for idx, _ in o["series"]["location"])


def test_overview_of_an_empty_case(client, case_id):
    o = client.get(f"/api/cases/{case_id}/overview").json()
    assert o["total"] == 0 and o["series"] == {}


def test_locations_are_downsampled_but_keep_first_and_last(client, big_case):
    full = client.get(f"/api/cases/{big_case}/locations?max_points=20000").json()
    assert full["returned"] == full["total"] == 2500
    small = client.get(f"/api/cases/{big_case}/locations?max_points=100").json()
    assert small["total"] == 2500 and 90 <= small["returned"] <= 101
    assert small["points"][0]["id"] == full["points"][0]["id"]
    assert small["points"][-1]["id"] == full["points"][-1]["id"]


# --- sessions & validation ---------------------------------------------------------------------


def test_sessions_rebuild_with_parameters_and_stable_ids(client, case_id, demo_dir):
    load_demo(client, case_id)
    before = client.get(f"/api/cases/{case_id}/sessions").json()
    rebuilt = client.post(f"/api/cases/{case_id}/sessions/rebuild?window_seconds=300").json()
    assert [s["id"] for s in before] == [s["id"] for s in rebuilt]  # deterministic

    strict = client.post(f"/api/cases/{case_id}/sessions/rebuild?window_seconds=30&min_sources=3").json()
    assert len(strict) < len(before)
    assert client.get(f"/api/cases/{case_id}/sessions/params").json() == {
        "window_seconds": 30, "max_span_seconds": 1800, "min_sources": 3,
    }  # fmt: skip
    assert client.post(f"/api/cases/{case_id}/sessions/rebuild?window_seconds=0").status_code == 422


def test_session_events_endpoint(client, case_id, demo_dir):
    load_demo(client, case_id)
    top = client.get(f"/api/cases/{case_id}/sessions").json()[0]
    events = client.get(f"/api/cases/{case_id}/sessions/{top['id']}/events").json()
    assert [e["id"] for e in events] == top["member_event_ids"]
    assert top["event_count"] == len(events) and top["centroid_lat"] is not None
    assert client.get(f"/api/cases/{case_id}/sessions/nope/events").status_code == 404


def test_validation_flags_disjoint_sources_and_single_source(client, case_id, upload, tmp_path):
    loc = location_csv(tmp_path / "gps.csv", [(iso(BASE), 12.9, 77.5)])
    upload(case_id, "gps.csv", loc)
    codes = {f["code"] for f in client.get(f"/api/cases/{case_id}/validation").json()}
    assert {"SINGLE_SOURCE", "HASH_INVENTORY", "TIME_SPAN"} <= codes

    later = app_usage_csv(tmp_path / "usage.csv", [("com.a", iso(BASE + timedelta(days=30)))])
    upload(case_id, "usage.csv", later)
    findings = client.get(f"/api/cases/{case_id}/validation").json()
    codes = {f["code"] for f in findings}
    assert "NO_TIME_OVERLAP" in codes and "NO_SESSIONS" in codes and "MULTI_SOURCE" in codes


# --- reports & audit ---------------------------------------------------------------------------


def test_reports_contain_provenance_and_exports_are_audited(client, case_id, demo_dir):
    load_demo(client, case_id)
    summary = client.get(f"/api/cases/{case_id}/report").json()
    assert summary["event_count"] == 17 and summary["session_count"] == 4
    assert summary["correlation"] == {"window_seconds": 300, "max_span_seconds": 1800, "min_sources": 2}
    assert summary["first_event_utc"] < summary["last_event_utc"]

    html = client.get(f"/api/cases/{case_id}/report/html").text
    for needle in ("Chain of custody", "Proximity sessions", "SHA-256", "chromium_history", "Events CSV SHA-256"):
        assert needle in html
    assert "<script" not in html

    payload = client.get(f"/api/cases/{case_id}/report/json").json()
    assert payload["generator"]["name"] == "fuseline" and len(payload["events"]) == 17
    assert {e["action"] for e in payload["audit"]} >= {"case.create", "artifact.ingest", "demo.load", "report.export"}

    csv_text = client.get(f"/api/cases/{case_id}/report/csv").text
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert len(rows) == 17 and rows[0]["artifact_sha256"] and rows[0]["ts_basis"]
    exports = [a for a in client.get(f"/api/cases/{case_id}/audit").json() if a["action"] == "report.export"]
    assert {a["detail"]["kind"] for a in exports} >= {"html", "json", "csv"}


def test_html_report_hash_matches_the_csv_export(client, case_id, demo_dir):
    import hashlib
    import re

    load_demo(client, case_id)
    html = client.get(f"/api/cases/{case_id}/report/html").text
    claimed = re.search(r"Events CSV SHA-256[^:]*: ([0-9a-f]{64})", html).group(1)
    actual = hashlib.sha256(client.get(f"/api/cases/{case_id}/report/csv").content).hexdigest()
    assert claimed == actual


def test_audit_chain_detects_tampering(client, case_id, upload, tmp_path):
    from app import audit
    from app.db import registry_conn

    upload(case_id, "gps.csv", location_csv(tmp_path / "gps.csv", [(iso(BASE), 12.9, 77.5)]))
    assert audit.verify_chain() == (True, None)
    entry = audit.list_entries(case_id)[-1]
    with registry_conn() as conn:
        original = conn.execute("SELECT detail_json FROM audit_log WHERE id=?", (entry["id"],)).fetchone()[0]
        conn.execute("UPDATE audit_log SET detail_json='{\"forged\":true}' WHERE id=?", (entry["id"],))
    try:
        ok, first_bad = audit.verify_chain()
        assert ok is False and first_bad == entry["id"]
        assert client.post(f"/api/cases/{case_id}/verify").json()["audit_chain_ok"] is False
    finally:
        with registry_conn() as conn:
            conn.execute("UPDATE audit_log SET detail_json=? WHERE id=?", (original, entry["id"]))
    assert audit.verify_chain() == (True, None)


# --- HTTP hardening on the real app -------------------------------------------------------------


def test_real_app_rejects_foreign_host_and_cross_origin_writes(client, case_id):
    assert client.get("/api/health", headers={"Host": "attacker.example"}).status_code == 400
    evil = {"Origin": "https://evil.example"}
    assert client.post("/api/cases", json={"name": "x"}, headers=evil).status_code == 403
    assert client.delete(f"/api/cases/{case_id}", headers=evil).status_code == 403
    assert client.get(f"/api/cases/{case_id}").status_code == 200


def test_cors_is_not_wildcard_and_never_allows_credentials(client):
    evil = client.get("/api/cases", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers
    dev = client.get("/api/cases", headers={"Origin": "http://127.0.0.1:5174"})
    assert dev.headers["access-control-allow-origin"] == "http://127.0.0.1:5174"
    assert "access-control-allow-credentials" not in dev.headers
    assert dev.headers["cache-control"] == "no-store"
    lookalike = client.get("/api/cases", headers={"Origin": "http://localhost.evil.com"})
    assert "access-control-allow-origin" not in lookalike.headers


def test_unhandled_errors_do_not_leak_details(client, case_id, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("secret internal path C:\\evidence")

    monkeypatch.setattr("app.api.sessions.rebuild_sessions", boom)
    quiet = type(client)(client.app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    r = quiet.post(f"/api/cases/{case_id}/sessions/rebuild")
    assert r.status_code == 500 and "secret" not in r.text
