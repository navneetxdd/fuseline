from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None:
        raw = default
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except ValueError:
        return default


DATA_DIR = Path(os.environ.get("FUSELINE_DATA_DIR") or PROJECT_ROOT / "data").resolve()
CASES_DIR = DATA_DIR / "cases"
UPLOADS_DIR = DATA_DIR / "uploads"
REGISTRY_DB = DATA_DIR / "registry.sqlite"

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

DEFAULT_CORRELATION_WINDOW_SECONDS = 300
DEFAULT_MAX_SESSION_SPAN_SECONDS = 1800
DEFAULT_MIN_SOURCES = 2
DEFAULT_TIMEZONE = "UTC"

MAX_UPLOAD_BYTES = _int_env("FUSELINE_MAX_UPLOAD_MB", 64) * 1024 * 1024

# Fuseline is a local-workstation tool with no authentication, so the API only answers
# requests addressed to a loopback host name and only accepts state-changing browser
# requests from its own origin or another loopback origin (e.g. the Vite dev server on
# whichever port it picked). Widen these deliberately, never by default: "*" disables the
# host check, and FUSELINE_ALLOW_LOOPBACK_ORIGINS=0 restricts writes to same-origin plus
# the explicit FUSELINE_ALLOWED_ORIGINS list.
ALLOWED_HOSTS = _csv_env("FUSELINE_ALLOWED_HOSTS", "127.0.0.1,localhost,[::1]")
ALLOWED_ORIGINS = _csv_env("FUSELINE_ALLOWED_ORIGINS", "")
ALLOW_LOOPBACK_ORIGINS = os.environ.get("FUSELINE_ALLOW_LOOPBACK_ORIGINS", "1") != "0"


def ensure_dirs() -> None:
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
