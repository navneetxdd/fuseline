from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import device

SAMPLE_DEVICES = (
    "List of devices attached\n"
    "emulator-5554\tdevice product:sdk_gphone model:Pixel_6 device:emu64a\n"
    "ZY3222ABCD\tunauthorized\n"
    "0123456789ABCDEF\toffline\n"
    "\n"
)


def test_adb_path_raises_when_not_on_path(monkeypatch, tmp_path):
    monkeypatch.delenv("FUSELINE_ADB", raising=False)
    monkeypatch.setattr(device.shutil, "which", lambda _: None)
    monkeypatch.setattr(device, "BUNDLED_DIR", tmp_path / "missing-platform-tools")
    monkeypatch.setattr(device, "_candidate_paths", lambda: [])
    with pytest.raises(device.AdbNotAvailable, match="ensure_platform_tools"):
        device.adb_path()


def test_adb_path_prefers_env_then_bundle(monkeypatch, tmp_path):
    monkeypatch.delenv("FUSELINE_ADB", raising=False)
    bundled = tmp_path / "platform-tools"
    bundled.mkdir()
    fake = bundled / ("adb.exe" if device.sys.platform == "win32" else "adb")
    fake.write_text("x", encoding="utf-8")
    monkeypatch.setattr(device, "BUNDLED_DIR", bundled)
    monkeypatch.setattr(device.shutil, "which", lambda _: None)
    assert device.adb_path() == str(fake.resolve())

    env_bin = tmp_path / "custom-adb"
    env_bin.write_text("y", encoding="utf-8")
    monkeypatch.setenv("FUSELINE_ADB", str(env_bin))
    assert device.adb_path() == str(env_bin.resolve())


def test_list_devices_parses_states_and_model(monkeypatch):
    monkeypatch.setattr(device, "adb_path", lambda: "/usr/bin/adb")
    monkeypatch.setattr(
        device.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=SAMPLE_DEVICES, stderr=""),
    )
    found = device.list_devices()
    assert [(d.serial, d.state, d.ready) for d in found] == [
        ("emulator-5554", "device", True),
        ("ZY3222ABCD", "unauthorized", False),
        ("0123456789ABCDEF", "offline", False),
    ]
    assert found[0].model == "Pixel_6"
    assert found[1].model is None


def test_list_devices_empty(monkeypatch):
    monkeypatch.setattr(device, "adb_path", lambda: "/usr/bin/adb")
    monkeypatch.setattr(
        device.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="List of devices attached\n\n", stderr=""),
    )
    assert device.list_devices() == []


def test_run_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(device, "adb_path", lambda: "/usr/bin/adb")
    monkeypatch.setattr(
        device.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="no permissions"),
    )
    with pytest.raises(device.AdbDeviceError, match="no permissions"):
        device.list_devices()


def test_run_raises_on_timeout(monkeypatch):
    monkeypatch.setattr(device, "adb_path", lambda: "/usr/bin/adb")

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="adb", timeout=10)

    monkeypatch.setattr(device.subprocess, "run", raise_timeout)
    with pytest.raises(device.AdbDeviceError, match="timed out"):
        device.list_devices()


def test_pull_rejects_a_serial_that_looks_like_a_shell_argument(monkeypatch):
    monkeypatch.setattr(device, "adb_path", lambda: "/usr/bin/adb")
    monkeypatch.setattr(
        device.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=SAMPLE_DEVICES, stderr=""),
    )
    with pytest.raises(device.AdbDeviceError, match="Invalid device serial"):
        device.pull_usagestats_text("emulator-5554; rm -rf /")


def test_pull_rejects_a_serial_not_currently_connected(monkeypatch):
    monkeypatch.setattr(device, "adb_path", lambda: "/usr/bin/adb")
    monkeypatch.setattr(
        device.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=SAMPLE_DEVICES, stderr=""),
    )
    with pytest.raises(device.AdbDeviceError, match="no longer connected"):
        device.pull_usagestats_text("does-not-exist")


def test_pull_rejects_an_unauthorized_device(monkeypatch):
    monkeypatch.setattr(device, "adb_path", lambda: "/usr/bin/adb")
    monkeypatch.setattr(
        device.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=SAMPLE_DEVICES, stderr=""),
    )
    with pytest.raises(device.AdbDeviceError, match="unauthorized"):
        device.pull_usagestats_text("ZY3222ABCD")


def test_pull_calls_adb_with_the_verified_serial_and_no_shell(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:] == ["devices", "-l"]:
            return subprocess.CompletedProcess(args, 0, stdout=SAMPLE_DEVICES, stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="Usage Events:\n", stderr="")

    monkeypatch.setattr(device, "adb_path", lambda: "/usr/bin/adb")
    monkeypatch.setattr(device.subprocess, "run", fake_run)
    out = device.pull_usagestats_text("emulator-5554")
    assert out == "Usage Events:\n"
    assert calls[-1] == ["/usr/bin/adb", "-s", "emulator-5554", "shell", "dumpsys", "usagestats"]
    for call in calls:
        assert isinstance(call, list)  # never a shell string


def test_is_pullable_export_name_rejects_traversal():
    assert device._is_pullable_export_name("Records.json")
    assert device._is_pullable_export_name("History")
    assert device._is_pullable_export_name("track.gpx")
    assert not device._is_pullable_export_name("../etc/passwd")
    assert not device._is_pullable_export_name("evil.exe")
    assert not device._is_pullable_export_name("a/b.csv")


def test_list_shared_exports_filters_supported_names(monkeypatch):
    def fake_run(args, **kwargs):
        if args[1:] == ["devices", "-l"]:
            return subprocess.CompletedProcess(args, 0, stdout=SAMPLE_DEVICES, stderr="")
        if "find" in args:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "ls" in args:
            if args[-1] != "/sdcard/Download":
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="Records.json\nphoto.jpg\nlocation.csv\n../nope\nHistory\n",
                stderr="",
            )
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="fail")

    monkeypatch.setattr(device, "adb_path", lambda: "/usr/bin/adb")
    monkeypatch.setattr(device.subprocess, "run", fake_run)
    found = device.list_shared_exports("emulator-5554")
    names = [e.name for e in found]
    assert names == ["Records.json", "History", "location.csv"]
    assert all(e.remote_path.startswith("/sdcard/Download/") for e in found)
    assert all(e.display_path.startswith("Download/") for e in found)


def test_pull_shared_export_writes_temp_file(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:] == ["devices", "-l"]:
            return subprocess.CompletedProcess(args, 0, stdout=SAMPLE_DEVICES, stderr="")
        if "find" in args:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "ls" in args:
            if args[-1] != "/sdcard/Download":
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(args, 0, stdout="location.csv\n", stderr="")
        if "pull" in args:
            dest = Path(args[-1])
            dest.write_text("ts,lat,lon\n2024-06-15T10:00:00Z,1.0,2.0\n", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="unexpected")

    monkeypatch.setattr(device, "adb_path", lambda: "/usr/bin/adb")
    monkeypatch.setattr(device.subprocess, "run", fake_run)
    local = device.pull_shared_export(
        "emulator-5554",
        remote_path="/sdcard/Download/location.csv",
    )
    try:
        assert local.is_file() and local.read_text(encoding="utf-8").startswith("ts,")
        assert any("pull" in c for c in calls)
    finally:
        parent = local.parent
        local.unlink(missing_ok=True)
        parent.rmdir()
