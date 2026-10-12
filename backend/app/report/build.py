from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from app import __version__, audit
from app.api.deps import session_from_row
from app.db import case_conn
from app.pipeline.ingest import artifact_payload, get_case, get_correlation_params
from app.report.activity import summarize_activity
from app.security import csv_safe
from app.timeutil import from_epoch_ms, nice_bucket_ms, now_iso, to_iso_utc

TEMPLATE_DIR = Path(__file__).parent / "templates"
LANE_COLORS = {
    "location": "#1f5f66",
    "browsing": "#3b4d63",
    "app_usage": "#8a4b12",
    "plaso": "#5c6670",
}
CSV_COLUMNS = [
    "id",
    "ts_utc",
    "ts_original",
    "ts_basis",
    "tz_assumed",
    "source",
    "event_type",
    "title",
    "package",
    "url",
    "domain",
    "lat",
    "lon",
    "confidence",
    "artifact",
    "artifact_sha256",
]
REPORT_EVENT_LIMIT = 300
REPORT_SESSION_LIMIT = 25


def _env() -> Environment:
    # Evidence strings (page titles, package names) are attacker-controlled, so escaping is
    # unconditional. select_autoescape() keys off the *last* extension and would silently
    # switch escaping off for "report.html.j2".
    return Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)


def _event_dict(row) -> dict[str, Any]:
    item = dict(row)
    item["detail"] = json.loads(item.pop("detail_json") or "{}")
    return item


def timeline_chart(
    series: dict[str, list[tuple[int, int]]], origin_ms: int, bucket_ms: int, bucket_count: int
) -> dict[str, Any]:
    """Pre-compute an inline-SVG density chart so the HTML report needs no scripts."""
    width, lane_h, gap, left = 900, 26, 8, 84
    plot_w = width - left - 8
    lanes = []
    peak = max((c for pts in series.values() for _, c in pts), default=1)
    for i, (source, points) in enumerate(sorted(series.items())):
        y = i * (lane_h + gap)
        bar_w = max(1.0, plot_w / max(bucket_count, 1))
        bars = [
            {
                "x": round(left + idx * plot_w / max(bucket_count, 1), 2),
                "h": round(3 + (lane_h - 3) * count / peak, 2),
                "w": round(bar_w, 2),
            }
            for idx, count in points
        ]
        lanes.append({"source": source, "y": y, "h": lane_h, "color": LANE_COLORS.get(source, "#5c6670"), "bars": bars})
    return {
        "width": width,
        "height": max(1, len(lanes)) * (lane_h + gap),
        "left": left,
        "lanes": lanes,
        "start": from_epoch_ms(origin_ms).strftime("%Y-%m-%d %H:%M UTC") if bucket_count else "",
        "end": from_epoch_ms(origin_ms + bucket_ms * bucket_count).strftime("%Y-%m-%d %H:%M UTC")
        if bucket_count
        else "",
    }


def gather_report_data(case_id: str) -> dict[str, Any]:
    case = get_case(case_id)
    with case_conn(case_id) as conn:
        artifacts = [artifact_payload(r) for r in conn.execute("SELECT * FROM artifacts ORDER BY ingested_at, rowid")]
        findings = [dict(r) for r in conn.execute("SELECT * FROM validation_findings ORDER BY rowid")]
        sessions = [
            session_from_row(r).model_dump()
            for r in conn.execute("SELECT * FROM sessions ORDER BY score DESC, start_utc, id")
        ]
        source_counts = {
            r["source"]: r["c"] for r in conn.execute("SELECT source, COUNT(*) AS c FROM events GROUP BY source")
        }
        lo, hi, event_count = conn.execute("SELECT MIN(ts_ms), MAX(ts_ms), COUNT(*) FROM events").fetchone()
        params = get_correlation_params(conn)
        series: dict[str, list[tuple[int, int]]] = {}
        origin = bucket = buckets = 0
        if event_count:
            bucket = nice_bucket_ms(max(hi - lo, 1), 120)
            origin = (lo // bucket) * bucket
            buckets = (hi - origin) // bucket + 1
            for r in conn.execute(
                "SELECT source, (ts_ms - ?) / ? AS b, COUNT(*) AS c FROM events GROUP BY source, b ORDER BY source, b",
                (origin, bucket),
            ):
                series.setdefault(r["source"], []).append((int(r["b"]), int(r["c"])))

        by_id = {}
        for s in sessions[:REPORT_SESSION_LIMIT]:
            ids = s["member_event_ids"]
            marks = ",".join("?" for _ in ids)
            by_id[s["id"]] = sorted(
                (_event_dict(r) for r in conn.execute(f"SELECT * FROM events WHERE id IN ({marks})", ids)),
                key=lambda e: (e["ts_ms"], e["id"]),
            )
        timeline_rows = [
            _event_dict(r)
            for r in conn.execute("SELECT * FROM events ORDER BY ts_ms, id LIMIT ?", (REPORT_EVENT_LIMIT,))
        ]

    return {
        "case": case,
        "artifacts": artifacts,
        "event_count": event_count,
        "source_counts": source_counts,
        "session_count": len(sessions),
        "findings": findings,
        "sessions": sessions,
        "session_events": by_id,
        "session_limit": REPORT_SESSION_LIMIT,
        "timeline_events": timeline_rows,
        "event_limit": REPORT_EVENT_LIMIT,
        "correlation": asdict(params),
        "audit": audit.list_entries(case_id),
        "first_event_utc": to_iso_utc(from_epoch_ms(lo)) if event_count else None,
        "last_event_utc": to_iso_utc(from_epoch_ms(hi)) if event_count else None,
        "chart": timeline_chart(series, origin, bucket, buckets),
        "activity": summarize_activity(case_id),
    }


def render_html_report(case_id: str) -> str:
    data = gather_report_data(case_id)
    csv_text = export_events_csv(case_id)
    template = _env().get_template("report.html.j2")
    return template.render(
        **data,
        generated_at=now_iso(),
        version=__version__,
        csv_sha256=hashlib.sha256(csv_text.encode("utf-8")).hexdigest(),
    )


def export_events_csv(case_id: str, *, raw: bool = False) -> str:
    """Events as CSV. Cells that could run as spreadsheet formulas are neutralised unless ``raw``."""
    clean = (lambda v: v) if raw else csv_safe
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    with case_conn(case_id) as conn:
        rows = conn.execute(
            """
            SELECT e.id, e.ts_utc, e.ts_original, e.ts_basis, e.tz_assumed, e.source, e.event_type,
                   e.title, e.package, e.url, e.domain, e.lat, e.lon, e.confidence,
                   a.original_name AS artifact, a.sha256 AS artifact_sha256
            FROM events e JOIN artifacts a ON a.id = e.artifact_id
            ORDER BY e.ts_ms, e.id
            """
        )
        for r in rows:
            writer.writerow([clean(r[col]) for col in CSV_COLUMNS])
    return buf.getvalue()


def export_events_json(case_id: str) -> str:
    data = gather_report_data(case_id)
    with case_conn(case_id) as conn:
        events = [_event_dict(r) for r in conn.execute("SELECT * FROM events ORDER BY ts_ms, id")]
    payload = {
        "generator": {"name": "fuseline", "version": __version__, "generated_at": now_iso()},
        "case": data["case"],
        "correlation": data["correlation"],
        "artifacts": data["artifacts"],
        "findings": data["findings"],
        "sessions": data["sessions"],
        "audit": data["audit"],
        "source_counts": data["source_counts"],
        "events": events,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)
