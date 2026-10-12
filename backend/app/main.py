from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app import __version__
from app.api import acquire, cases, device, report, sessions, timeline
from app.config import ALLOW_LOOPBACK_ORIGINS, ALLOWED_HOSTS, ALLOWED_ORIGINS, FRONTEND_DIST, MAX_UPLOAD_BYTES
from app.db import init_registry
from app.http_security import LOOPBACK_ORIGIN_REGEX, LocalOnlyMiddleware
from app.models import MetaOut
from app.parsers.registry import SUPPORTED_FORMATS

log = logging.getLogger("fuseline")

SPA_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_registry()
    yield


app = FastAPI(
    title="Fuseline",
    description="Mobile forensic timeline — correlate location, browsing, and app usage",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_origin_regex=LOOPBACK_ORIGIN_REGEX if ALLOW_LOOPBACK_ORIGINS else None,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)
app.add_middleware(
    LocalOnlyMiddleware,
    allowed_hosts=ALLOWED_HOSTS,
    allowed_origins=ALLOWED_ORIGINS,
    allow_loopback_origins=ALLOW_LOOPBACK_ORIGINS,
)

app.include_router(cases.router)
app.include_router(device.router)
app.include_router(acquire.router)
app.include_router(timeline.router)
app.include_router(sessions.router)
app.include_router(report.router)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


PROJECT_REPORT = Path(__file__).resolve().parents[2] / "docs" / "Fuseline-Capstone-Report.pdf"


@app.get("/api/project-report")
def project_report() -> FileResponse:
    if not PROJECT_REPORT.is_file():
        raise HTTPException(status_code=404, detail="Project report has not been generated")
    return FileResponse(
        PROJECT_REPORT,
        media_type="application/pdf",
        filename="Fuseline-Capstone-Report.pdf",
    )


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "fuseline", "version": __version__}


@app.get("/api/meta", response_model=MetaOut)
def meta() -> MetaOut:
    return MetaOut(version=__version__, max_upload_bytes=MAX_UPLOAD_BYTES, formats=SUPPORTED_FORMATS)  # type: ignore[arg-type]


if FRONTEND_DIST.exists():
    _dist = FRONTEND_DIST.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        # Never let the SPA swallow API misses (path traversal / unknown routes)
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (_dist / full_path).resolve()
        try:
            candidate.relative_to(_dist)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Not found") from exc
        if full_path and candidate.is_file():
            headers = (
                {"Cache-Control": "public, max-age=31536000, immutable"} if full_path.startswith("assets/") else {}
            )
            return FileResponse(candidate, headers=headers)
        return FileResponse(
            Path(_dist / "index.html"),
            headers={"Content-Security-Policy": SPA_CSP, "Cache-Control": "no-cache"},
        )
