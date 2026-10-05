from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import audit
from app.api.deps import require_case_id
from app.device import (
    AdbDeviceError,
    AdbNotAvailable,
    list_devices,
    list_shared_exports,
    pull_location_text,
    pull_shared_export,
    pull_usagestats_text,
)
from app.models import (
    ArtifactOut,
    DeviceBundleItem,
    DeviceBundleResult,
    DeviceExportInfo,
    DeviceInfo,
    IngestResult,
    ValidationFindingOut,
)
from app.pipeline.ingest import get_case, ingest_file

router = APIRouter(prefix="/api", tags=["device"])


class DeviceExportPullIn(BaseModel):
    remote_path: str = Field(min_length=1, max_length=512)


def _ingest_result(case_id: str, result: dict[str, Any]) -> IngestResult:
    return IngestResult(
        artifact=ArtifactOut(**result["artifact"]),
        events_added=result["events_added"],
        sessions_rebuilt=result["sessions_rebuilt"],
        findings=[ValidationFindingOut(**f) for f in result["findings"]],
        duplicate=result["duplicate"],
    )


def _ingest_temp_file(
    case_id: str,
    tmp_path: Path,
    filename: str,
    preferred_source: str | None,
) -> dict[str, Any]:
    try:
        return ingest_file(case_id, tmp_path, filename, preferred_source=preferred_source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _record_pull(case_id: str, detail: dict[str, Any]) -> None:
    audit.record(
        "device.pull",
        case_id=case_id,
        actor=get_case(case_id)["examiner"],
        detail=detail,
    )


@router.get("/devices", response_model=list[DeviceInfo])
def devices() -> list[DeviceInfo]:
    """Devices currently visible to `adb devices`. Read-only; nothing is pulled here."""
    try:
        found = list_devices()
    except AdbNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return [DeviceInfo(serial=d.serial, state=d.state, model=d.model, ready=d.ready) for d in found]


@router.get("/devices/{serial}/exports", response_model=list[DeviceExportInfo])
def device_exports(serial: str) -> list[DeviceExportInfo]:
    """Ingestible files on shared storage (Download/Documents, nested exports; no root)."""
    try:
        found = list_shared_exports(serial)
    except AdbNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AdbDeviceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [
        DeviceExportInfo(
            name=e.name,
            remote_path=e.remote_path,
            display_path=e.display_path,
            size_bytes=e.size_bytes,
        )
        for e in found
    ]


@router.post("/cases/{case_id}/acquire/device/{serial}/app-usage", response_model=IngestResult)
def acquire_app_usage_from_device(case_id: str, serial: str) -> IngestResult:
    case_id = require_case_id(case_id)
    try:
        text = pull_usagestats_text(serial)
    except AdbNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AdbDeviceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    try:
        result = _ingest_temp_file(
            case_id,
            tmp_path,
            f"adb_usagestats_{serial}.txt",
            preferred_source="app_usage",
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    if not result["duplicate"]:
        _record_pull(case_id, {"serial": serial, "kind": "app_usage", "events": result["events_added"]})
    return _ingest_result(case_id, result)


@router.post("/cases/{case_id}/acquire/device/{serial}/location", response_model=IngestResult)
def acquire_location_from_device(case_id: str, serial: str) -> IngestResult:
    case_id = require_case_id(case_id)
    try:
        text = pull_location_text(serial)
    except AdbNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AdbDeviceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    try:
        result = _ingest_temp_file(
            case_id,
            tmp_path,
            f"adb_location_{serial}.txt",
            preferred_source="location",
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    if not result["duplicate"]:
        _record_pull(case_id, {"serial": serial, "kind": "location", "events": result["events_added"]})
    return _ingest_result(case_id, result)


def _ingest_pulled_export(case_id: str, serial: str, filename: str | None, remote_path: str | None) -> IngestResult:
    try:
        if remote_path:
            local = pull_shared_export(serial, remote_path=remote_path)
            label = Path(remote_path).name
        else:
            local = pull_shared_export(serial, name=filename or "")
            label = filename or local.name
    except AdbNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AdbDeviceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        result = _ingest_temp_file(case_id, local, label, preferred_source=None)
    finally:
        parent = local.parent
        local.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            parent.rmdir()

    if not result["duplicate"]:
        _record_pull(
            case_id,
            {
                "serial": serial,
                "kind": "shared_export",
                "filename": label,
                "events": result["events_added"],
                "source_type": result["artifact"]["source_type"],
            },
        )
    return _ingest_result(case_id, result)


@router.post("/cases/{case_id}/acquire/device/{serial}/export", response_model=IngestResult)
def acquire_export_from_device_body(case_id: str, serial: str, body: DeviceExportPullIn) -> IngestResult:
    case_id = require_case_id(case_id)
    return _ingest_pulled_export(case_id, serial, None, body.remote_path)


@router.post("/cases/{case_id}/acquire/device/{serial}/export/{filename}", response_model=IngestResult)
def acquire_export_from_device(case_id: str, serial: str, filename: str) -> IngestResult:
    case_id = require_case_id(case_id)
    return _ingest_pulled_export(case_id, serial, filename, None)


@router.post("/cases/{case_id}/acquire/device/{serial}/bundle", response_model=DeviceBundleResult)
def acquire_device_bundle(case_id: str, serial: str) -> DeviceBundleResult:
    """Pull everything Fuseline can reach without root: usagestats, location dump, and all listed exports."""
    case_id = require_case_id(case_id)
    items: list[DeviceBundleItem] = []
    total_events = 0
    sessions_rebuilt = 0

    def add_item(kind: str, label: str, result: dict[str, Any] | None, error: str | None) -> None:
        nonlocal total_events, sessions_rebuilt
        if error:
            items.append(DeviceBundleItem(kind=kind, label=label, ok=False, error=error))
            return
        assert result is not None
        added = int(result["events_added"])
        total_events += added
        sessions_rebuilt = int(result["sessions_rebuilt"])
        items.append(
            DeviceBundleItem(
                kind=kind,
                label=label,
                ok=True,
                events_added=added,
                duplicate=bool(result["duplicate"]),
                source_type=result["artifact"]["source_type"],
            )
        )

    # App usage
    try:
        text = pull_usagestats_text(serial)
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
            tmp.write(text)
            tmp_path = Path(tmp.name)
        try:
            result = ingest_file(case_id, tmp_path, f"adb_usagestats_{serial}.txt", preferred_source="app_usage")
            if not result["duplicate"]:
                _record_pull(case_id, {"serial": serial, "kind": "app_usage", "events": result["events_added"]})
            add_item("app_usage", "App usage (dumpsys usagestats)", result, None)
        except ValueError as exc:
            add_item("app_usage", "App usage (dumpsys usagestats)", None, str(exc))
        finally:
            tmp_path.unlink(missing_ok=True)
    except (AdbNotAvailable, AdbDeviceError) as exc:
        add_item("app_usage", "App usage (dumpsys usagestats)", None, str(exc))

    # Location dump (may yield zero events on some devices — still attempted)
    try:
        loc_text = pull_location_text(serial)
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
            tmp.write(loc_text)
            tmp_path = Path(tmp.name)
        try:
            result = ingest_file(case_id, tmp_path, f"adb_location_{serial}.txt", preferred_source="location")
            if not result["duplicate"]:
                _record_pull(case_id, {"serial": serial, "kind": "location", "events": result["events_added"]})
            add_item("location", "Location (dumpsys location)", result, None)
        except ValueError as exc:
            add_item("location", "Location (dumpsys location)", None, str(exc))
        finally:
            tmp_path.unlink(missing_ok=True)
    except (AdbNotAvailable, AdbDeviceError) as exc:
        add_item("location", "Location (dumpsys location)", None, str(exc))

    # Shared exports
    try:
        exports = list_shared_exports(serial)
    except (AdbNotAvailable, AdbDeviceError) as exc:
        add_item("export", "Shared storage scan", None, str(exc))
        exports = []

    for export in exports:
        label = export.display_path
        try:
            local = pull_shared_export(serial, remote_path=export.remote_path)
        except AdbDeviceError as exc:
            add_item("export", label, None, str(exc))
            continue
        try:
            result = ingest_file(case_id, local, export.name, preferred_source=None)
            if not result["duplicate"]:
                _record_pull(
                    case_id,
                    {
                        "serial": serial,
                        "kind": "shared_export",
                        "filename": export.name,
                        "events": result["events_added"],
                        "source_type": result["artifact"]["source_type"],
                    },
                )
            add_item("export", label, result, None)
        except ValueError as exc:
            add_item("export", label, None, str(exc))
        finally:
            parent = local.parent
            local.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                parent.rmdir()

    return DeviceBundleResult(items=items, total_events_added=total_events, sessions_rebuilt=sessions_rebuilt)
