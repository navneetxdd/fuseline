from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app import audit
from app.config import (
    DEFAULT_CORRELATION_WINDOW_SECONDS,
    DEFAULT_MAX_SESSION_SPAN_SECONDS,
    DEFAULT_MIN_SOURCES,
    UPLOADS_DIR,
)
from app.db import case_conn, registry_conn
from app.parsers.base import ParseContext
from app.parsers.registry import parse_artifact
from app.pipeline.correlate import RawEvent, correlate_events
from app.pipeline.normalize import normalize_records
from app.pipeline.validate import findings_with_ids, gather_stats, validate_case
from app.security import READ_CHUNK, assert_safe_case_id, assert_under, sanitize_filename
from app.timeutil import now_iso

EVENT_NAMESPACE = uuid.UUID("c1a2f5d0-3b7e-4e0a-9c55-2f0f6a3d8e10")


@dataclass
class CorrelationParams:
    window_seconds: int = DEFAULT_CORRELATION_WINDOW_SECONDS
    max_span_seconds: int = DEFAULT_MAX_SESSION_SPAN_SECONDS
    min_sources: int = DEFAULT_MIN_SOURCES


def sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while chunk := fh.read(READ_CHUNK):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def get_case(case_id: str) -> dict[str, Any]:
    with registry_conn() as reg:
        row = reg.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if row is None:
        raise FileNotFoundError(f"Case not found: {case_id}")
    return dict(row)


def get_correlation_params(conn: sqlite3.Connection) -> CorrelationParams:
    row = conn.execute("SELECT value FROM meta WHERE key = 'correlation'").fetchone()
    if row is None:
        return CorrelationParams()
    try:
        return CorrelationParams(**json.loads(row["value"]))
    except (TypeError, ValueError):
        return CorrelationParams()


def _save_correlation_params(conn: sqlite3.Connection, params: CorrelationParams) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('correlation', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (json.dumps(asdict(params)),),
    )


def refresh_registry_counts(case_id: str) -> None:
    with case_conn(case_id) as conn:
        counts = (
            conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
        )
    with registry_conn() as reg:
        reg.execute(
            "UPDATE cases SET event_count=?, artifact_count=?, session_count=?, updated_at=? WHERE id=?",
            (*counts, now_iso(), case_id),
        )


def rebuild_sessions_conn(conn: sqlite3.Connection, params: CorrelationParams | None = None) -> int:
    params = params or get_correlation_params(conn)
    rows = conn.execute("SELECT id, ts_utc, ts_ms, source, title, package, domain, lat, lon FROM events").fetchall()
    events = [
        RawEvent(
            id=r["id"],
            ts_utc=r["ts_utc"],
            source=r["source"],
            title=r["title"],
            package=r["package"],
            domain=r["domain"],
            lat=r["lat"],
            lon=r["lon"],
            ts_ms=r["ts_ms"],
        )
        for r in rows
    ]
    sessions = correlate_events(
        events,
        window_seconds=params.window_seconds,
        max_span_seconds=params.max_span_seconds,
        min_sources=params.min_sources,
    )
    conn.execute("DELETE FROM sessions")
    conn.executemany(
        """
        INSERT INTO sessions (id, start_utc, end_utc, score, summary, member_event_ids, sources,
                              event_count, centroid_lat, centroid_lon, radius_m)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                s.id,
                s.start_utc,
                s.end_utc,
                s.score,
                s.summary,
                json.dumps(s.member_event_ids),
                json.dumps(s.sources),
                s.event_count,
                s.centroid_lat,
                s.centroid_lon,
                s.radius_m,
            )
            for s in sessions
        ],
    )
    _save_correlation_params(conn, params)
    return len(sessions)


def run_validation_conn(conn: sqlite3.Connection) -> list[dict]:
    findings = validate_case(gather_stats(conn))
    rows = findings_with_ids(findings, now_iso())
    conn.execute("DELETE FROM validation_findings")
    conn.executemany(
        "INSERT INTO validation_findings (id, severity, code, message, created_at) VALUES (?, ?, ?, ?, ?)",
        [(r["id"], r["severity"], r["code"], r["message"], r["created_at"]) for r in rows],
    )
    return rows


def rebuild_sessions(case_id: str, params: CorrelationParams | None = None, *, actor: str = "") -> int:
    with case_conn(case_id) as conn:
        count = rebuild_sessions_conn(conn, params)
        applied = get_correlation_params(conn)
        run_validation_conn(conn)
    refresh_registry_counts(case_id)
    audit.record(
        "sessions.rebuild",
        case_id=case_id,
        actor=actor,
        detail={"sessions": count, **asdict(applied)},
    )
    return count


def run_validation(case_id: str) -> list[dict]:
    with case_conn(case_id) as conn:
        return run_validation_conn(conn)


def finalize_case(case_id: str) -> tuple[int, list[dict]]:
    with case_conn(case_id) as conn:
        sessions_n = rebuild_sessions_conn(conn)
        findings = run_validation_conn(conn)
    refresh_registry_counts(case_id)
    return sessions_n, findings


def artifact_payload(row: sqlite3.Row) -> dict[str, Any]:
    try:
        notes = json.loads(row["notes"] or "[]")
    except ValueError:
        notes = []
    return {
        "id": row["id"],
        "source_type": row["source_type"],
        "original_name": row["original_name"],
        "sha256": row["sha256"],
        "ingested_at": row["ingested_at"],
        "row_count": row["row_count"],
        "size_bytes": row["size_bytes"],
        "skipped_rows": row["skipped_rows"],
        "parser": row["parser"],
        "notes": notes,
    }


def _store_evidence(case_id: str, artifact_id: str, safe_name: str, src: Path, digest: str) -> Path:
    case_upload = assert_under(UPLOADS_DIR / case_id, UPLOADS_DIR)
    case_upload.mkdir(parents=True, exist_ok=True)
    dest = assert_under(case_upload / f"{artifact_id}_{safe_name}", case_upload)
    shutil.copyfile(src, dest)
    try:
        if sha256_file(dest)[0] != digest:
            raise OSError("evidence copy does not match the source hash")
        os.chmod(dest, stat.S_IREAD)
    except Exception:
        _discard_evidence(dest)
        raise
    return dest


def _discard_evidence(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        path.unlink(missing_ok=True)
    except OSError:
        pass


def ingest_file(
    case_id: str,
    src_path: Path,
    original_name: str,
    preferred_source: str | None = None,
    *,
    finalize: bool = True,
) -> dict[str, Any]:
    """Hash, parse, normalise and store one artifact.

    Parsing happens on the source file *before* anything is written, so a rejected upload
    leaves no orphan copy behind. Storage and the database write then succeed or fail
    together.
    """
    case_id = assert_safe_case_id(case_id)
    safe_name = sanitize_filename(original_name)
    src_path = Path(src_path)
    case = get_case(case_id)
    actor = case["examiner"]
    digest, size = sha256_file(src_path)

    with case_conn(case_id) as conn:
        existing = conn.execute("SELECT * FROM artifacts WHERE sha256 = ?", (digest,)).fetchone()
        if existing is not None:
            stored = [dict(r) for r in conn.execute("SELECT * FROM validation_findings ORDER BY rowid").fetchall()]
            payload = {
                "artifact": artifact_payload(existing),
                "events_added": 0,
                "sessions_rebuilt": conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                "findings": stored,
                "duplicate": True,
            }
    if existing is not None:
        audit.record(
            "artifact.duplicate",
            case_id=case_id,
            actor=actor,
            detail={"name": safe_name, "sha256": digest, "matches": payload["artifact"]["original_name"]},
        )
        return payload

    ctx = ParseContext.for_timezone(case["timezone"])
    parser, parsed = parse_artifact(src_path, preferred_source, ctx, label=safe_name)
    records = normalize_records(parsed.records)
    skipped = parsed.skipped + (len(parsed.records) - len(records))
    notes = parsed.notes()
    if not records:
        reasons = f" ({'; '.join(notes)})" if notes else ""
        raise ValueError(f"{safe_name} was recognised as {parser.name} but contained no usable events{reasons}.")

    artifact_id = str(uuid.uuid4())
    dest = _store_evidence(case_id, artifact_id, safe_name, src_path, digest)
    ingested_at = now_iso()
    sessions_n = 0
    findings: list[dict] = []
    try:
        with case_conn(case_id) as conn:
            conn.execute(
                """
                INSERT INTO artifacts (id, source_type, original_name, stored_path, sha256, ingested_at,
                                       row_count, size_bytes, skipped_rows, parser, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    parser.source,
                    safe_name,
                    str(dest),
                    digest,
                    ingested_at,
                    len(records),
                    size,
                    skipped,
                    parser.name,
                    json.dumps(notes),
                ),
            )
            conn.executemany(
                """
                INSERT INTO events (
                  id, artifact_id, ts_utc, ts_ms, ts_original, tz_assumed, ts_basis, source, event_type,
                  title, detail_json, lat, lon, package, url, domain, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(uuid.uuid5(EVENT_NAMESPACE, f"{digest}:{i}")),
                        artifact_id,
                        rec.ts_utc,
                        rec.ts_ms,
                        rec.ts_original,
                        rec.tz_assumed,
                        rec.ts_basis,
                        rec.source,
                        rec.event_type,
                        rec.title,
                        rec.detail_json(),
                        rec.lat,
                        rec.lon,
                        rec.package,
                        rec.url,
                        rec.domain,
                        rec.confidence,
                    )
                    for i, rec in enumerate(records)
                ],
            )
            if finalize:
                sessions_n = rebuild_sessions_conn(conn)
                findings = run_validation_conn(conn)
    except Exception:
        _discard_evidence(dest)
        raise

    if finalize:
        refresh_registry_counts(case_id)
    audit.record(
        "artifact.ingest",
        case_id=case_id,
        actor=actor,
        detail={
            "name": safe_name,
            "sha256": digest,
            "size_bytes": size,
            "parser": parser.name,
            "source": parser.source,
            "events": len(records),
            "skipped_rows": skipped,
        },
    )
    return {
        "artifact": {
            "id": artifact_id,
            "source_type": parser.source,
            "original_name": safe_name,
            "sha256": digest,
            "ingested_at": ingested_at,
            "row_count": len(records),
            "size_bytes": size,
            "skipped_rows": skipped,
            "parser": parser.name,
            "notes": notes,
        },
        "events_added": len(records),
        "sessions_rebuilt": sessions_n,
        "findings": findings,
        "duplicate": False,
    }


def verify_case_integrity(case_id: str) -> dict[str, Any]:
    """Re-hash every stored evidence copy and compare with the hash recorded at acquisition."""
    case_id = assert_safe_case_id(case_id)
    with case_conn(case_id) as conn:
        rows = conn.execute("SELECT * FROM artifacts ORDER BY ingested_at, rowid").fetchall()
    results = []
    for row in rows:
        path = Path(row["stored_path"])
        if not path.exists():
            status, actual = "missing", None
        else:
            actual = sha256_file(path)[0]
            status = "ok" if actual == row["sha256"] else "modified"
        results.append(
            {
                "artifact_id": row["id"],
                "original_name": row["original_name"],
                "expected_sha256": row["sha256"],
                "actual_sha256": actual,
                "status": status,
            }
        )
    chain_ok, first_bad = audit.verify_chain()
    ok = all(r["status"] == "ok" for r in results) and chain_ok
    result = {
        "ok": ok,
        "checked_at": now_iso(),
        "artifacts": results,
        "audit_chain_ok": chain_ok,
        "audit_first_bad_entry": first_bad,
    }
    audit.record(
        "integrity.verify",
        case_id=case_id,
        actor=get_case(case_id)["examiner"],
        detail={"ok": ok, "artifacts": len(results), "audit_chain_ok": chain_ok},
    )
    return result
