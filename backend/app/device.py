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
# Public shared storage roots (no root). Subfolders included via `find` (Takeout, etc.).
SCAN_ROOTS = (
    "/sdcard/Download",
    "/storage/emulated/0/Download",
    "/sdcard/Documents",
    "/storage/emulated/0/Documents",
)
SHARED_DOWNLOAD_DIRS = SCAN_ROOTS[:2]
MAX_EXPORT_BYTES = 200 * 1024 * 1024
MAX_EXPORT_LIST = 48
MAX_FIND_DEPTH = 6
LIST_TIMEOUT_SECONDS = 10
FIND_TIMEOUT_SECONDS = 25
PULL_TIMEOUT_SECONDS = 60
LOCATION_DUMP_TIMEOUT_SECONDS = 30
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
    """A supported evidence file on shared storage that Fuseline can pull without root."""

    name: str
    remote_path: str
    display_path: str
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


def pull_location_text(serial: str) -> str:
    """Raw `dumpsys location` text (last fixes / history where the OS exposes them)."""
    device = _require_ready_device(serial)
    return _run(["-s", device.serial, "shell", "dumpsys", "location"], LOCATION_DUMP_TIMEOUT_SECONDS)


def _is_pullable_export_name(name: str) -> bool:
    if not name or not EXPORT_NAME_RE.match(name) or "/" in name or "\\" in name or ".." in name:
        return False
    lower = name.lower()
    if lower in EXPORT_EXACT_NAMES:
        return True
    return any(lower.endswith(suf) for suf in EXPORT_SUFFIXES)


def _display_path(remote_path: str) -> str:
    for prefix in ("/storage/emulated/0/", "/sdcard/"):
        if remote_path.startswith(prefix):
            return remote_path[len(prefix) :]
    return remote_path


def _is_safe_export_path(remote_path: str) -> bool:
    if not remote_path or ".." in remote_path or "\0" in remote_path or remote_path.startswith("-"):
        return False
    if not any(remote_path == root or remote_path.startswith(f"{root}/") for root in SCAN_ROOTS):
        return False
    base = remote_path.rsplit("/", 1)[-1]
    return _is_pullable_export_name(base)


def _export_from_remote(remote_path: str) -> SharedExport:
    name = remote_path.rsplit("/", 1)[-1]
    return SharedExport(name=name, remote_path=remote_path, display_path=_display_path(remote_path))


def _list_exports_shallow(serial: str, device: AdbDevice) -> dict[str, SharedExport]:
    found: dict[str, SharedExport] = {}
    for folder in SCAN_ROOTS:
        try:
            listing = _run(["-s", device.serial, "shell", "ls", "-1", folder], LIST_TIMEOUT_SECONDS)
        except AdbDeviceError:
            continue
        for line in listing.splitlines():
            name = line.strip()
            if name.startswith("./"):
                name = name[2:]
            if not name or name.lower().startswith("no such file") or name.endswith(":"):
                continue
            if not _is_pullable_export_name(name):
                continue
            remote = f"{folder.rstrip('/')}/{name}"
            export = _export_from_remote(remote)
            found.setdefault(export.remote_path, export)
    return found


def _list_exports_deep(serial: str, device: AdbDevice) -> dict[str, SharedExport]:
    roots = " ".join(SCAN_ROOTS)
    script = f"find {roots} -maxdepth {MAX_FIND_DEPTH} -type f 2>/dev/null"
    try:
        listing = _run(["-s", device.serial, "shell", script], FIND_TIMEOUT_SECONDS)
    except AdbDeviceError:
        return {}
    found: dict[str, SharedExport] = {}
    for line in listing.splitlines():
        remote = line.strip()
        if not remote or not _is_safe_export_path(remote):
            continue
        export = _export_from_remote(remote)
        found.setdefault(export.remote_path, export)
        if len(found) >= MAX_EXPORT_LIST:
            break
    return found


def list_shared_exports(serial: str) -> list[SharedExport]:
    """List ingestible files on shared storage (Download/Documents, nested Takeout, etc.)."""
    device = _require_ready_device(serial)
    found = _list_exports_deep(serial, device)
    shallow = _list_exports_shallow(serial, device)
    found = shallow if not found else {**shallow, **found}
    ranked = sorted(
        found.values(),
        key=lambda e: (
            0 if e.name.lower() == "records.json" else 1,
            0 if "takeout" in e.display_path.lower() else 1,
            e.display_path.lower(),
        ),
    )
    return ranked[:MAX_EXPORT_LIST]


def pull_shared_export(
    serial: str,
    name: str | None = None,
    *,
    remote_path: str | None = None,
) -> Path:
    """Pull one shared-storage file to a temp path. Caller must delete the returned path."""
    device = _require_ready_device(serial)
    export: SharedExport | None = None
    if remote_path:
        if not _is_safe_export_path(remote_path):
            raise AdbDeviceError("That remote path is not allowed for device import.")
        export = _export_from_remote(remote_path)
    elif name:
        if not _is_pullable_export_name(name):
            raise AdbDeviceError("That file name is not allowed for device import.")
        matches = [e for e in list_shared_exports(device.serial) if e.name == name]
        if not matches:
            raise AdbDeviceError("File not found on the phone (or not a supported type).")
        if len(matches) > 1:
            raise AdbDeviceError(
                f"Multiple files named {name!r} on the device. Pull by full path from the export list."
            )
        export = matches[0]
    else:
        raise AdbDeviceError("Specify a file name or remote_path.")

    tmp_dir = Path(tempfile.mkdtemp(prefix="fuseline-adb-"))
    local = tmp_dir / export.name
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
