from __future__ import annotations

from datetime import timedelta

from builders import BASE, adb_location_dump, adb_usagestats_dump

from app.api import device as device_api
from app.device import AdbDevice, AdbDeviceError, AdbNotAvailable


def test_devices_endpoint_reports_adb_missing_as_503(client, monkeypatch):
    def raise_missing():
        raise AdbNotAvailable("adb was not found on this machine.")

    monkeypatch.setattr(device_api, "list_devices", raise_missing)
    r = client.get("/api/devices")
    assert r.status_code == 503 and "adb was not found" in r.json()["detail"]


def test_devices_endpoint_lists_connected_devices(client, monkeypatch):
    monkeypatch.setattr(
        device_api,
        "list_devices",
        lambda: [
            AdbDevice(serial="emulator-5554", state="device", model="Pixel_6"),
            AdbDevice(serial="ZY3222ABCD", state="unauthorized", model=None),
        ],
    )
    r = client.get("/api/devices")
    assert r.status_code == 200
    body = r.json()
    assert body[0] == {"serial": "emulator-5554", "state": "device", "model": "Pixel_6", "ready": True}
    assert body[1]["ready"] is False


def test_pull_app_usage_rejects_when_adb_missing(client, case_id, monkeypatch):
    def raise_missing(serial):
        raise AdbNotAvailable("no adb")

    monkeypatch.setattr(device_api, "pull_usagestats_text", raise_missing)
    r = client.post(f"/api/cases/{case_id}/acquire/device/emulator-5554/app-usage")
    assert r.status_code == 503


def test_pull_app_usage_rejects_unready_device(client, case_id, monkeypatch):
    def raise_not_ready(serial):
        raise AdbDeviceError("Device is 'unauthorized', not ready.")

    monkeypatch.setattr(device_api, "pull_usagestats_text", raise_not_ready)
    r = client.post(f"/api/cases/{case_id}/acquire/device/ZY3222ABCD/app-usage")
    assert r.status_code == 400 and "not ready" in r.json()["detail"]


def test_pull_app_usage_ingests_through_the_normal_pipeline(client, case_id, monkeypatch):
    text = adb_usagestats_dump(
        [
            ("com.whatsapp", "MOVE_TO_FOREGROUND", BASE),
            ("com.whatsapp", "MOVE_TO_BACKGROUND", BASE + timedelta(minutes=1)),
        ]
    )
    monkeypatch.setattr(device_api, "pull_usagestats_text", lambda serial: text)

    r = client.post(f"/api/cases/{case_id}/acquire/device/emulator-5554/app-usage")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["events_added"] == 2 and body["duplicate"] is False
    assert body["artifact"]["parser"] == "adb_usagestats_dump"
    assert body["artifact"]["source_type"] == "app_usage"

    # it went through the real ingest pipeline: hashed, stored read-only, shows up in the case
    timeline = client.get(f"/api/cases/{case_id}/timeline").json()
    assert timeline["total"] == 2

    audit_entries = client.get(f"/api/cases/{case_id}/audit").json()
    pulls = [a for a in audit_entries if a["action"] == "device.pull"]
    assert len(pulls) == 1
    assert pulls[0]["detail"] == {"serial": "emulator-5554", "kind": "app_usage", "events": 2}


def test_pull_app_usage_is_deduplicated_by_hash_like_any_other_upload(client, case_id, monkeypatch):
    text = adb_usagestats_dump([("com.a", "MOVE_TO_FOREGROUND", BASE)])
    monkeypatch.setattr(device_api, "pull_usagestats_text", lambda serial: text)

    first = client.post(f"/api/cases/{case_id}/acquire/device/emulator-5554/app-usage").json()
    second = client.post(f"/api/cases/{case_id}/acquire/device/emulator-5554/app-usage").json()
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert client.get(f"/api/cases/{case_id}/timeline").json()["total"] == 1


def test_pull_app_usage_requires_a_valid_case(client, monkeypatch):
    monkeypatch.setattr(device_api, "pull_usagestats_text", lambda serial: "Usage Events:\n")
    r = client.post("/api/cases/not-a-uuid/acquire/device/emulator-5554/app-usage")
    assert r.status_code == 400
    r2 = client.post("/api/cases/479280c8-9ae9-49fe-8d25-2c7d749678bf/acquire/device/emulator-5554/app-usage")
    assert r2.status_code == 404


def test_list_exports_endpoint(client, monkeypatch):
    from app.device import SharedExport

    monkeypatch.setattr(
        device_api,
        "list_shared_exports",
        lambda serial: [
            SharedExport(
                name="Records.json",
                remote_path="/sdcard/Download/Takeout/Records.json",
                display_path="Download/Takeout/Records.json",
            )
        ],
    )
    r = client.get("/api/devices/emulator-5554/exports")
    assert r.status_code == 200
    assert r.json() == [
        {
            "name": "Records.json",
            "remote_path": "/sdcard/Download/Takeout/Records.json",
            "display_path": "Download/Takeout/Records.json",
            "size_bytes": None,
        }
    ]


def test_pull_export_ingests_location_csv(client, case_id, monkeypatch, tmp_path):
    export = tmp_path / "location.csv"
    export.write_text("timestamp,latitude,longitude\n2024-06-15T10:00:00Z,12.97,77.59\n", encoding="utf-8")
    monkeypatch.setattr(device_api, "pull_shared_export", lambda serial, name: export)

    r = client.post(f"/api/cases/{case_id}/acquire/device/emulator-5554/export/location.csv")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["events_added"] >= 1
    assert body["artifact"]["source_type"] == "location"
    assert not export.exists()  # cleaned up by the route


def test_pull_location_ingests_dumpsys(client, case_id, monkeypatch):
    ms = int(BASE.timestamp() * 1000)
    text = adb_location_dump(12.97, 77.59, ms)
    monkeypatch.setattr(device_api, "pull_location_text", lambda serial: text)
    r = client.post(f"/api/cases/{case_id}/acquire/device/emulator-5554/location")
    assert r.status_code == 200, r.text
    assert r.json()["events_added"] == 1
    assert r.json()["artifact"]["parser"] == "adb_location_dump"


def test_device_bundle_pulls_usage_and_exports(client, case_id, monkeypatch, tmp_path):
    usage = adb_usagestats_dump([("com.a", "MOVE_TO_FOREGROUND", BASE)])
    export = tmp_path / "location.csv"
    export.write_text("timestamp,latitude,longitude\n2024-06-15T10:00:00Z,12.97,77.59\n", encoding="utf-8")

    from app.device import SharedExport

    monkeypatch.setattr(device_api, "pull_usagestats_text", lambda serial: usage)
    monkeypatch.setattr(device_api, "pull_location_text", lambda serial: "Location Manager State:\n")
    monkeypatch.setattr(
        device_api,
        "list_shared_exports",
        lambda serial: [
            SharedExport(
                name="location.csv",
                remote_path="/sdcard/Download/location.csv",
                display_path="Download/location.csv",
            )
        ],
    )

    def fake_pull(serial, name=None, remote_path=None):
        return export

    monkeypatch.setattr(device_api, "pull_shared_export", fake_pull)

    r = client.post(f"/api/cases/{case_id}/acquire/device/emulator-5554/bundle")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_events_added"] >= 2
    kinds = {item["kind"] for item in body["items"] if item["ok"]}
    assert "app_usage" in kinds and "export" in kinds
