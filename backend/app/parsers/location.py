from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from app.geo import valid_fix
from app.parsers.base import (
    EventRecord,
    ParseContext,
    ParseResult,
    compact,
    csv_header,
    csv_rows,
    open_sqlite_readonly,
    read_text_head,
    sqlite_schema,
)
from app.security import quote_ident
from app.timeutil import TimestampError, to_iso_utc

TIME_KEYS = ("timestamp", "time", "ts", "datetime", "date_time", "fix", "utc")
LAT_KEYS = ("lat", "latitude")
LON_KEYS = ("lon", "lng", "long", "longitude")


def _pick(mapping: Mapping[str, object], keys: tuple[str, ...]) -> object | None:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _column(cols: set[str], keys: tuple[str, ...]) -> str | None:
    return next((k for k in keys if k in cols), None)


def _has_geo_cols(cols: set[str]) -> bool:
    return bool(_column(cols, LAT_KEYS) and _column(cols, LON_KEYS) and _column(cols, TIME_KEYS))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class LocationParser:
    """GPS fixes from CSV, SQLite, GPX or a Google Takeout ``Records.json`` export."""

    source = "location"
    name = "location"

    def sniff(self, path: Path) -> float:
        schema = sqlite_schema(path)
        if schema is not None:
            return 0.7 if any(_has_geo_cols(cols) for cols in schema.values()) else 0.0

        head = read_text_head(path)
        if head is None:
            return 0.0
        stripped = head.lstrip()
        lowered = head.lower()
        if stripped.startswith("<"):
            return 0.95 if "<gpx" in lowered else 0.0
        if stripped.startswith("{"):
            has_locations = '"locations"' in head
            has_coords = "latitudee7" in lowered or '"latitude"' in lowered
            return 0.92 if has_locations and has_coords else 0.0

        header = csv_header(path)
        if header and _has_geo_cols(set(header)):
            # psort L2TCSV can carry lat/lon columns; leave those files to PlasoParser.
            if (
                "date" in header
                and "time" in header
                and {"desc", "message", "source", "short", "sourcetype"} & set(header)
            ):
                return 0.0
            return 0.9
        return 0.0

    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        if sqlite_schema(path) is not None:
            return self._parse_sqlite(path, ctx)
        head = (read_text_head(path) or "").lstrip()
        if head.startswith("<"):
            return self._parse_gpx(path, ctx)
        if head.startswith("{"):
            return self._parse_takeout(path, ctx)
        return self._parse_csv(path, ctx)

    def _fix(
        self,
        result: ParseResult,
        ctx: ParseContext,
        *,
        raw_ts: object,
        lat: object,
        lon: object,
        detail: dict[str, object],
        unit: str = "auto",
    ) -> None:
        try:
            lat_f, lon_f = float(lat), float(lon)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            result.skip("non-numeric coordinates")
            return
        if not valid_fix(lat_f, lon_f):
            result.skip("invalid or placeholder coordinates")
            return
        try:
            ts = ctx.timestamp(raw_ts, unit=unit)
        except (TimestampError, OverflowError):
            result.skip("unparseable timestamp")
            return
        result.records.append(
            EventRecord(
                ts_utc=to_iso_utc(ts.utc),
                ts_original=str(raw_ts),
                source=self.source,
                event_type="fix",
                title=f"{lat_f:.5f}, {lon_f:.5f}",
                tz_assumed=ctx.tz_label(ts),
                ts_basis=ts.basis,
                lat=lat_f,
                lon=lon_f,
                detail=compact({"lat": lat_f, "lon": lon_f, **detail}),
                confidence=0.95,
            )
        )

    def _parse_csv(self, path: Path, ctx: ParseContext) -> ParseResult:
        result = ParseResult()
        for row in csv_rows(path):
            raw_ts = _pick(row, TIME_KEYS)
            lat = _pick(row, LAT_KEYS)
            lon = _pick(row, LON_KEYS)
            if raw_ts is None or lat is None or lon is None:
                result.skip("missing time or coordinates")
                continue
            self._fix(result, ctx, raw_ts=raw_ts, lat=lat, lon=lon, detail=dict(row))
        return result

    def _parse_sqlite(self, path: Path, ctx: ParseContext) -> ParseResult:
        result = ParseResult()
        schema = sqlite_schema(path) or {}
        with open_sqlite_readonly(path) as conn:
            best: tuple[int, str, str, str, str] | None = None  # rows, table, time col, lat col, lon col
            for table, cols in schema.items():
                time_col, lat_col = _column(cols, TIME_KEYS), _column(cols, LAT_KEYS)
                lon_col = _column(cols, LON_KEYS)
                if not (time_col and lat_col and lon_col):
                    continue
                count = conn.execute(f"SELECT COUNT(*) FROM {quote_ident(table)}").fetchone()[0]
                if best is None or count > best[0]:
                    best = (count, table, time_col, lat_col, lon_col)
            if best is None:
                return result
            _, table, time_col, lat_col, lon_col = best
            rows = conn.execute(
                f"SELECT {quote_ident(time_col)} AS t, {quote_ident(lat_col)} AS lat, "
                f"{quote_ident(lon_col)} AS lon FROM {quote_ident(table)} ORDER BY {quote_ident(time_col)}"
            )
            for row in rows:
                if row["t"] is None or row["lat"] is None or row["lon"] is None:
                    result.skip("missing time or coordinates")
                    continue
                self._fix(result, ctx, raw_ts=row["t"], lat=row["lat"], lon=row["lon"], detail={"table": table})
        return result

    def _parse_gpx(self, path: Path, ctx: ParseContext) -> ParseResult:
        result = ParseResult()
        try:
            for _, el in SafeET.iterparse(str(path), events=("end",)):
                if _local(el.tag) not in {"trkpt", "wpt", "rtept"}:
                    continue
                children = {_local(c.tag): (c.text or "").strip() for c in el}
                if not children.get("time"):
                    result.skip("gpx point without <time>")
                else:
                    self._fix(
                        result,
                        ctx,
                        raw_ts=children["time"],
                        lat=el.get("lat"),
                        lon=el.get("lon"),
                        detail={"ele": children.get("ele"), "name": children.get("name")},
                    )
                el.clear()
        except (SafeET.ParseError, DefusedXmlException) as exc:
            raise ValueError(f"invalid or unsafe GPX/XML: {exc}") from exc
        return result

    def _parse_takeout(self, path: Path, ctx: ParseContext) -> ParseResult:
        result = ParseResult()
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc
        locations = data.get("locations") if isinstance(data, dict) else None
        if not isinstance(locations, list):
            raise ValueError("JSON has no 'locations' array")
        for item in locations:
            if not isinstance(item, dict):
                result.skip("location entry is not an object")
                continue
            if "latitudeE7" in item and "longitudeE7" in item:
                try:
                    lat, lon = item["latitudeE7"] / 1e7, item["longitudeE7"] / 1e7
                except TypeError:
                    result.skip("non-numeric coordinates")
                    continue
            else:
                lat, lon = item.get("latitude"), item.get("longitude")
            raw_ts = item.get("timestamp") or item.get("timestampMs")
            unit = "ms" if "timestampMs" in item and not item.get("timestamp") else "auto"
            if raw_ts is None:
                result.skip("missing time or coordinates")
                continue
            self._fix(
                result,
                ctx,
                raw_ts=raw_ts,
                lat=lat,
                lon=lon,
                detail={k: item.get(k) for k in ("accuracy", "source", "deviceTag", "altitude", "velocity")},
                unit=unit,
            )
        return result
