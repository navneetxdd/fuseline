from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app import audit
from app.api.deps import require_case_id
from app.device import (
    AdbDeviceError,
    AdbNotAvailable,
    list_devices,
    list_shared_exports,
    pull_shared_export,
    pull_usagestats_text,
)
from app.models import ArtifactOut, DeviceExportInfo, DeviceInfo, IngestResult, ValidationFindingOut
from app.pipeline.ingest import get_case, ingest_file

router = APIRouter(prefix="/api", tags=["device"])


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
    """Ingestible files in the phone Download folder (location/browsing exports, no root)."""
    try:
        found = list_shared_exports(serial)
    except AdbNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AdbDeviceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [DeviceExportInfo(name=e.name, remote_path=e.remote_path, size_bytes=e.size_bytes) for e in found]


@router.post("/cases/{case_id}/acquire/device/{serial}/app-usage", response_model=IngestResult)
def acquire_app_usage_from_device(case_id: str, serial: str) -> IngestResult:
    """Pull `adb shell dumpsys usagestats` from a connected, authorised phone and ingest it.

    Runs as a normal (sync) route so FastAPI executes it on the thread pool, since the ADB
    call blocks. Everything after the pull reuses the same ingest pipeline as a manual upload:
    hash, read-only storage, parse, normalise, correlate, audit.
    """
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
        result = ingest_file(case_id, tmp_path, f"adb_usagestats_{serial}.txt", preferred_source="app_usage")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    if not result["duplicate"]:
        audit.record(
            "device.pull",
            case_id=case_id,
            actor=get_case(case_id)["examiner"],
            detail={"serial": serial, "kind": "app_usage", "events": result["events_added"]},
        )
    return IngestResult(
        artifact=ArtifactOut(**result["artifact"]),
        events_added=result["events_added"],
        sessions_rebuilt=result["sessions_rebuilt"],
        findings=[ValidationFindingOut(**f) for f in result["findings"]],
        duplicate=result["duplicate"],
    )


@router.post("/cases/{case_id}/acquire/device/{serial}/export/{filename}", response_model=IngestResult)
def acquire_export_from_device(case_id: str, serial: str, filename: str) -> IngestResult:
    """Pull a Download-folder export (Takeout JSON, GPX, History DB, etc.) and ingest it."""
    case_id = require_case_id(case_id)
    try:
        local = pull_shared_export(serial, filename)
    except AdbNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AdbDeviceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        result = ingest_file(case_id, local, filename, preferred_source=None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        parent = local.parent
        local.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            parent.rmdir()

    if not result["duplicate"]:
        audit.record(
            "device.pull",
            case_id=case_id,
            actor=get_case(case_id)["examiner"],
            detail={
                "serial": serial,
                "kind": "shared_export",
                "filename": filename,
                "events": result["events_added"],
                "source_type": result["artifact"]["source_type"],
            },
        )
    return IngestResult(
        artifact=ArtifactOut(**result["artifact"]),
        events_added=result["events_added"],
        sessions_rebuilt=result["sessions_rebuilt"],
        findings=[ValidationFindingOut(**f) for f in result["findings"]],
        duplicate=result["duplicate"],
    )
