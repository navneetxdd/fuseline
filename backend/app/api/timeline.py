from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import event_from_row, parse_bound, require_case_id
from app.db import case_conn
from app.models import ActivityOut, EventOut, LocationPoint, LocationsResponse, OverviewResponse, TimelineResponse
from app.report.activity import summarize_activity
from app.timeutil import nice_bucket_ms

router = APIRouter(prefix="/api/cases/{case_id}", tags=["timeline"])


@router.get("/activity", response_model=ActivityOut)
def case_activity(case_id: str) -> ActivityOut:
    case_id = require_case_id(case_id)
    return ActivityOut(**summarize_activity(case_id))


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filters(
    source: str | None,
    q: str | None,
    start_ms: int | None,
    end_ms: int | None,
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    sources = [s.strip() for s in (source or "").split(",") if s.strip()]
    if sources:
        clauses.append(f"source IN ({','.join('?' for _ in sources)})")
        params.extend(sources)
    if q and q.strip():
        like = f"%{_like_escape(q.strip())}%"
        clauses.append(
            "(title LIKE ? ESCAPE '\\' OR package LIKE ? ESCAPE '\\' "
            "OR url LIKE ? ESCAPE '\\' OR domain LIKE ? ESCAPE '\\')"
        )
        params.extend([like] * 4)
    if start_ms is not None:
        clauses.append("ts_ms >= ?")
        params.append(start_ms)
    if end_ms is not None:
        clauses.append("ts_ms <= ?")
        params.append(end_ms)
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


@router.get("/timeline", response_model=TimelineResponse)
def get_timeline(
    case_id: str,
    source: str | None = Query(default=None, description="Comma-separated source names"),
    q: str | None = Query(default=None, description="Substring match on title/package/url/domain"),
    start: str | None = Query(default=None, description="Inclusive lower bound, ISO-8601"),
    end: str | None = Query(default=None, description="Inclusive upper bound, ISO-8601"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=20000),
    order: Literal["asc", "desc"] = "asc",
) -> TimelineResponse:
    case_id = require_case_id(case_id)
    where, params = _filters(source, q, parse_bound(start, "start"), parse_bound(end, "end"))
    direction = "DESC" if order == "desc" else "ASC"

    with case_conn(case_id) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM events {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM events {where} ORDER BY ts_ms {direction}, id {direction} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        source_rows = conn.execute("SELECT source, COUNT(*) AS c FROM events GROUP BY source").fetchall()

    events: list[EventOut] = [event_from_row(r) for r in rows]
    return TimelineResponse(
        case_id=case_id,
        events=events,
        total=total,
        offset=offset,
        limit=limit,
        has_more=offset + len(events) < total,
        sources={r["source"]: r["c"] for r in source_rows},
    )


@router.get("/events/{event_id}", response_model=EventOut)
def get_event(case_id: str, event_id: str) -> EventOut:
    case_id = require_case_id(case_id)
    with case_conn(case_id) as conn:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event_from_row(row)


@router.get("/overview", response_model=OverviewResponse)
def get_overview(
    case_id: str,
    source: str | None = None,
    q: str | None = None,
    start: str | None = None,
    end: str | None = None,
    buckets: int = Query(default=240, ge=10, le=2000),
) -> OverviewResponse:
    """Per-source event counts in evenly sized time buckets, for the zoomable overview lanes."""
    case_id = require_case_id(case_id)
    where, params = _filters(source, q, parse_bound(start, "start"), parse_bound(end, "end"))
    with case_conn(case_id) as conn:
        lo, hi, total = conn.execute(f"SELECT MIN(ts_ms), MAX(ts_ms), COUNT(*) FROM events {where}", params).fetchone()
        if not total:
            return OverviewResponse(origin_ms=0, bucket_ms=60_000, bucket_count=0, total=0, series={})
        bucket_ms = nice_bucket_ms(max(hi - lo, 1), buckets)
        origin = (lo // bucket_ms) * bucket_ms
        rows = conn.execute(
            f"""
            SELECT source, (ts_ms - ?) / ? AS b, COUNT(*) AS c
            FROM events {where}
            GROUP BY source, b
            ORDER BY source, b
            """,
            [origin, bucket_ms, *params],
        ).fetchall()
    series: dict[str, list[tuple[int, int]]] = {}
    for r in rows:
        series.setdefault(r["source"], []).append((int(r["b"]), int(r["c"])))
    return OverviewResponse(
        origin_ms=origin,
        bucket_ms=bucket_ms,
        bucket_count=(hi - origin) // bucket_ms + 1,
        total=total,
        series=series,
    )


@router.get("/locations", response_model=LocationsResponse)
def get_locations(
    case_id: str,
    start: str | None = None,
    end: str | None = None,
    max_points: int = Query(default=2000, ge=10, le=20000),
) -> LocationsResponse:
    """Location fixes in time order, evenly down-sampled (first and last always kept)."""
    case_id = require_case_id(case_id)
    where, params = _filters(None, None, parse_bound(start, "start"), parse_bound(end, "end"))
    geo = "lat IS NOT NULL AND lon IS NOT NULL"
    where = f"{where} AND {geo}" if where else f"WHERE {geo}"
    with case_conn(case_id) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM events {where}", params).fetchone()[0]
        stride = max(1, -(-total // max_points))
        rows = conn.execute(
            f"""
            SELECT id, ts_ms, lat, lon FROM (
              SELECT id, ts_ms, lat, lon, ROW_NUMBER() OVER (ORDER BY ts_ms, id) AS rn
              FROM events {where}
            )
            WHERE (rn - 1) % ? = 0 OR rn = ?
            ORDER BY rn
            """,
            [*params, stride, total],
        ).fetchall()
    points = [LocationPoint(id=r["id"], ts_ms=r["ts_ms"], lat=r["lat"], lon=r["lon"]) for r in rows]
    return LocationsResponse(points=points, total=total, returned=len(points))
