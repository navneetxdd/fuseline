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
from dataclasses import dataclass
from pathlib import Path

from app.config import PROJECT_ROOT

SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
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
