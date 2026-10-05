"""Resolve and (via scripts) install a local copy of Android Platform Tools for device import.

Lookup order for ``adb``:
1. ``FUSELINE_ADB`` — explicit path to the adb binary
2. Bundled ``tools/platform-tools/adb`` (or ``adb.exe``) next to the project
3. ``adb`` on PATH
4. Common Windows install locations (Android Studio / SDK)

Binaries are not committed; run ``python scripts/ensure_platform_tools.py`` (or
``scripts/run_dev.*``, which calls it) to download Google's platform-tools zip.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.config import PROJECT_ROOT

SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
# Shared-storage basenames only (no path separators). Covers Takeout / GPX / history DBs examiners drop in Download.
EXPORT_NAME_RE = re.compile(r"^[A-Za-z0-9._ ()\[\]-]{1,180}$")
EXPORT_SUFFIXES = (
    ".csv",
    ".json",
    ".gpx",
    ".xml",
    ".txt",
    ".db",
    ".sqlite",
    ".sqlite3",
)
EXPORT_EXACT_NAMES = {"history", "places.sqlite", "records.json"}
SHARED_DOWNLOAD_DIRS = (
    "/sdcard/Download",
    "/storage/emulated/0/Download",
)
MAX_EXPORT_BYTES = 200 * 1024 * 1024
LIST_TIMEOUT_SECONDS = 10
PULL_TIMEOUT_SECONDS = 45
PLATFORM_TOOLS_URL = "https://developer.android.com/tools/releases/platform-tools"
BUNDLED_DIR = PROJECT_ROOT / "tools" / "platform-tools"


class AdbNotAvailable(RuntimeError):
    """adb is not installed / not on PATH / not bundled."""


class AdbDeviceError(RuntimeError):
    """The requested device is not connected, not authorised, or the pull failed."""


@dataclass
class AdbDevice:
    serial: str
    state: str  # "device" (ready), "unauthorized", "offline", ...
    model: str | None = None

    @property
    def ready(self) -> bool:
        return self.state == "device"


@dataclass(frozen=True)
class SharedExport:
    """A file under the phone's public Download folder that Fuseline can ingest."""

    name: str
    remote_path: str
    size_bytes: int | None = None


def _adb_name() -> str:
    return "adb.exe" if sys.platform == "win32" else "adb"


def bundled_adb_path() -> Path:
    return BUNDLED_DIR / _adb_name()


def _candidate_paths() -> list[Path]:
    """Ordered places to look for an adb binary (existence checked by the caller)."""
    out: list[Path] = []

    env = os.environ.get("FUSELINE_ADB", "").strip()
    if env:
        out.append(Path(env))

    out.append(bundled_adb_path())

    which = shutil.which("adb")
    if which:
        out.append(Path(which))

    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        user = os.environ.get("USERPROFILE", "")
        extras = [
            Path(local) / "Android" / "Sdk" / "platform-tools" / "adb.exe" if local else None,
            Path(user) / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe" if user else None,
            Path(r"C:\Android\platform-tools\adb.exe"),
            Path(r"C:\Program Files\Android\android-sdk\platform-tools\adb.exe"),
        ]
        out.extend(p for p in extras if p is not None)
    else:
        out.extend(
            [
                Path("/usr/lib/android-sdk/platform-tools/adb"),
                Path("/opt/android-sdk/platform-tools/adb"),
                Path.home() / "Library" / "Android" / "sdk" / "platform-tools" / "adb",
                Path.home() / "Android" / "Sdk" / "platform-tools" / "adb",
            ]
        )

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[Path] = []
    for path in out:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def adb_path() -> str:
    for candidate in _candidate_paths():
        if candidate.is_file():
            return str(candidate.resolve())
    raise AdbNotAvailable(
        "adb was not found. Run `python scripts/ensure_platform_tools.py` from the Fuseline "
        f"project root (downloads Google Platform Tools into tools/platform-tools/), or install "
        f"them yourself ({PLATFORM_TOOLS_URL}) and put adb on PATH, then refresh."
    )


def _run(args: list[str], timeout: int) -> str:
    try:
        proc = subprocess.run(  # list args, no shell=True
            [adb_path(), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdbDeviceError(f"adb {' '.join(args)} timed out after {timeout}s.") from exc
    except OSError as exc:
        raise AdbNotAvailable(f"Could not run adb: {exc}") from exc
    if proc.returncode != 0:
        raise AdbDeviceError(proc.stderr.strip() or f"adb {' '.join(args)} failed (exit {proc.returncode}).")
    return proc.stdout


def list_devices() -> list[AdbDevice]:
    """Devices `adb devices -l` currently reports. Read-only; touches nothing on the phone."""
    out = _run(["devices", "-l"], LIST_TIMEOUT_SECONDS)
    devices: list[AdbDevice] = []
    for line in out.splitlines()[1:]:  # first line is the "List of devices attached" header
        parts = line.split()
        if not parts:
            continue
        serial, state = parts[0], parts[1] if len(parts) > 1 else "unknown"
        model = next((p.split(":", 1)[1] for p in parts[2:] if p.startswith("model:")), None)
        devices.append(AdbDevice(serial=serial, state=state, model=model))
    return devices


def _require_ready_device(serial: str) -> AdbDevice:
    if not SERIAL_RE.match(serial):
        raise AdbDeviceError("Invalid device serial.")
    match = next((d for d in list_devices() if d.serial == serial), None)
    if match is None:
        raise AdbDeviceError("That device is no longer connected. Refresh the device list and try again.")
    if not match.ready:
        raise AdbDeviceError(
            f"Device is '{match.state}', not ready. Check the phone screen and accept the "
            "USB debugging authorisation prompt, then refresh."
        )
    return match


def pull_usagestats_text(serial: str) -> str:
    """Raw `dumpsys usagestats` text from the given, currently-connected, authorised device."""
    device = _require_ready_device(serial)
    return _run(["-s", device.serial, "shell", "dumpsys", "usagestats"], PULL_TIMEOUT_SECONDS)


def _is_pullable_export_name(name: str) -> bool:
    if not name or not EXPORT_NAME_RE.match(name) or "/" in name or "\\" in name or ".." in name:
        return False
    lower = name.lower()
    if lower in EXPORT_EXACT_NAMES:
        return True
    return any(lower.endswith(suf) for suf in EXPORT_SUFFIXES)


def list_shared_exports(serial: str) -> list[SharedExport]:
    """List ingestible files in the phone's public Download folder (no root required)."""
    device = _require_ready_device(serial)
    found: dict[str, SharedExport] = {}
    for folder in SHARED_DOWNLOAD_DIRS:
        try:
            listing = _run(["-s", device.serial, "shell", "ls", "-1", folder], LIST_TIMEOUT_SECONDS)
        except AdbDeviceError:
            continue
        for line in listing.splitlines():
            name = line.strip()
            # `ls` sometimes prefixes with './' or returns "No such file or directory"
            if name.startswith("./"):
                name = name[2:]
            if not name or name.lower().startswith("no such file") or name.endswith(":"):
                continue
            if not _is_pullable_export_name(name):
                continue
            remote = f"{folder.rstrip('/')}/{name}"
            found.setdefault(name, SharedExport(name=name, remote_path=remote))
    return sorted(found.values(), key=lambda e: e.name.lower())


def pull_shared_export(serial: str, name: str) -> Path:
    """Pull one Download-folder file to a temp path. Caller must delete the returned path."""
    if not _is_pullable_export_name(name):
        raise AdbDeviceError("That file name is not allowed for device import.")
    device = _require_ready_device(serial)
    exports = {e.name: e for e in list_shared_exports(device.serial)}
    export = exports.get(name)
    if export is None:
        raise AdbDeviceError("File not found in the phone Download folder (or not a supported type).")

    tmp_dir = Path(tempfile.mkdtemp(prefix="fuseline-adb-"))
    local = tmp_dir / name
    try:
        _run(["-s", device.serial, "pull", export.remote_path, str(local)], PULL_TIMEOUT_SECONDS)
    except AdbDeviceError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    if not local.is_file():
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise AdbDeviceError("adb pull did not produce a local file.")
    size = local.stat().st_size
    if size <= 0:
        local.unlink(missing_ok=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise AdbDeviceError("Pulled file is empty.")
    if size > MAX_EXPORT_BYTES:
        local.unlink(missing_ok=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise AdbDeviceError(f"Pulled file exceeds the {MAX_EXPORT_BYTES // (1024 * 1024)} MiB import limit.")
    return local
