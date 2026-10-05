from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app import audit
from app.api.deps import require_case_id
from app.config import MAX_UPLOAD_BYTES
from app.db import case_conn
from app.models import (
    ArtifactOut,
    AuditEntryOut,
    IngestResult,
    IntegrityResult,
    ValidationFindingOut,
)
from app.pipeline.ingest import (
    artifact_payload,
    ingest_file,
    verify_case_integrity,
)
from app.security import READ_CHUNK, sanitize_filename

router = APIRouter(prefix="/api/cases/{case_id}", tags=["acquire"])


async def _read_upload_capped(upload: UploadFile, suffix: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)  # noqa: SIM115 - handed to the ingest worker
    tmp_path = Path(tmp.name)
    total = 0
    try:
        while chunk := await upload.read(READ_CHUNK):
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit",
                )
            tmp.write(chunk)
        tmp.close()
        if total == 0:
            raise HTTPException(status_code=400, detail="Empty upload")
        return tmp_path
    except BaseException:
        tmp.close()
        tmp_path.unlink(missing_ok=True)
        raise


@router.post("/acquire", response_model=IngestResult)
async def acquire_artifact(
    case_id: str,
    file: UploadFile = File(...),
    source_hint: str | None = Form(default=None),
) -> IngestResult:
    case_id = require_case_id(case_id)
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename required")
    try:
        safe_name = sanitize_filename(file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    hint = source_hint.strip() if source_hint else None
    if hint == "auto":
        hint = None

    tmp_path = await _read_upload_capped(file, Path(safe_name).suffix)
    try:
        result = await run_in_threadpool(ingest_file, case_id, tmp_path, safe_name, preferred_source=hint)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    return IngestResult(
        artifact=ArtifactOut(**result["artifact"]),
        events_added=result["events_added"],
        sessions_rebuilt=result["sessions_rebuilt"],
        findings=[ValidationFindingOut(**f) for f in result["findings"]],
        duplicate=result["duplicate"],
    )


@router.get("/artifacts", response_model=list[ArtifactOut])
def list_artifacts(case_id: str) -> list[ArtifactOut]:
    case_id = require_case_id(case_id)
    with case_conn(case_id) as conn:
        rows = conn.execute("SELECT * FROM artifacts ORDER BY ingested_at, rowid").fetchall()
    return [ArtifactOut(**artifact_payload(r)) for r in rows]


@router.post("/verify", response_model=IntegrityResult)
def verify_integrity(case_id: str) -> IntegrityResult:
    case_id = require_case_id(case_id)
    return IntegrityResult(**verify_case_integrity(case_id))


@router.get("/audit", response_model=list[AuditEntryOut])
def audit_log(case_id: str, limit: int = 500) -> list[dict]:
    case_id = require_case_id(case_id)
    return audit.list_entries(case_id, limit=max(1, min(limit, 2000)))
