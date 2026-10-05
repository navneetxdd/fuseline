from __future__ import annotations

import json
import re
from pathlib import Path

from app.parsers.base import (
    EventRecord,
    ParseContext,
    ParseResult,
    clip,
    compact,
    csv_header,
    csv_rows,
    read_text_head,
)
from app.parsers.chrome_history import extract_domain
from app.timeutil import (
    BASIS_ABSOLUTE,
    BASIS_ASSUMED,
    BASIS_OFFSET,
    Timestamp,
    TimestampError,
    coerce_timestamp,
    resolve_timezone,
    to_iso_utc,
)

_UTC_NAMES = {"UTC", "Z", "GMT"}
# Label fields only (see classify docstring). Patterns cover common Plaso parser / source_short names.
_BROWSING = re.compile(
    r"webhist|chrome|chromium|firefox|mozilla|safari|edge|opera|browser|page_visited|places|webview",
    re.IGNORECASE,
)
_APP_USAGE = re.compile(r"app[_ :-]?usage|usagestats|android_app_usage|android:app_usage", re.IGNORECASE)
_LOCATION = re.compile(
    r"\bgps\b|location|geo(?:location|json)?\b|latitude|longitude|\bgpx\b|fused.?location|maps\.google",
    re.IGNORECASE,
)
_SNAKE = re.compile(r"[^a-z0-9:]+")

CSV_MARKERS = {"desc", "message", "source", "short", "sourcetype"}


def classify(*labels: object) -> str:
    """Map Plaso's source labels onto Fuseline lanes.

    Only the short label fields are inspected (never the free-text message), so an event
    that merely *mentions* a URL or a package name is not pulled into the wrong lane.
    """
    blob = " ".join(str(x) for x in labels if x)
    if _BROWSING.search(blob):
        return "browsing"
    if _APP_USAGE.search(blob):
        return "app_usage"
    if _LOCATION.search(blob):
        return "location"
    return "plaso"


def _event_type(*candidates: object) -> str:
    for cand in candidates:
        if cand:
            return _SNAKE.sub("_", str(cand).strip().lower()).strip("_")[:60] or "plaso_event"
    return "plaso_event"


class PlasoParser:
    """Plaso ``psort`` output: L2TCSV (``-o l2tcsv``) or JSON lines (``-o json_line``)."""

    source = "plaso"
    name = "plaso_timeline"

    def sniff(self, path: Path) -> float:
        head = read_text_head(path)
        if head is None:
            return 0.0
        if head.lstrip().startswith("{"):
            obj = self._first_json_object(path)
            if obj is None:
                return 0.0
            markers = {"data_type", "timestamp_desc", "parser", "message", "display_name"}
            has_time = any(k in obj for k in ("timestamp", "datetime", "date"))
            return 0.9 if has_time and markers & obj.keys() else 0.0
        header = csv_header(path)
        if header and "date" in header and "time" in header and CSV_MARKERS & set(header):
            return 0.95
        return 0.0

    @staticmethod
    def _first_json_object(path: Path) -> dict | None:
        with path.open("r", encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    return None
                return obj if isinstance(obj, dict) else None
        return None

    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        head = (read_text_head(path) or "").lstrip()
        if head.startswith("{"):
            return self._parse_jsonl(path, ctx)
        return self._parse_csv(path, ctx)

    def _parse_csv(self, path: Path, ctx: ParseContext) -> ParseResult:
        result = ParseResult()
        for row in csv_rows(path):
            date_s, time_s = row.get("date", "").strip(), row.get("time", "").strip()
            if not date_s:
                result.skip("no date")
                continue
            declared = row.get("timezone", "").strip()
            try:
                ts, tz_label = self._stamp(f"{date_s} {time_s or '00:00:00'}", declared, ctx)
            except (TimestampError, OverflowError):
                result.skip("unparseable date/time")
                continue
            short = row.get("short") or ""
            desc = row.get("desc") or row.get("description") or row.get("filename") or ""
            message = row.get("message") or short or desc or "plaso event"
            source_label, sourcetype, kind = row.get("source"), row.get("sourcetype"), row.get("type")
            lane = classify(source_label, sourcetype, kind, desc if len(desc) < 80 else "", row.get("format"))
            url = row.get("url") or None
            result.records.append(
                EventRecord(
                    ts_utc=to_iso_utc(ts.utc),
                    ts_original=f"{date_s} {time_s}".strip(),
                    source=lane,
                    event_type=_event_type(kind, f"plaso_{lane}"),
                    title=message[:200],
                    tz_assumed=tz_label,
                    ts_basis=ts.basis,
                    url=url,
                    domain=extract_domain(url),
                    package=row.get("package") or None,
                    lat=_maybe_float(row.get("latitude") or row.get("lat")),
                    lon=_maybe_float(row.get("longitude") or row.get("lon")),
                    detail=compact(row),
                    confidence=0.8,
                )
            )
        return result

    def _parse_jsonl(self, path: Path, ctx: ParseContext) -> ParseResult:
        result = ParseResult()
        with path.open("r", encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    result.skip("invalid JSON line")
                    continue
                if not isinstance(obj, dict):
                    result.skip("JSON line is not an object")
                    continue
                self._from_json(obj, ctx, result)
        return result

    def _from_json(self, obj: dict, ctx: ParseContext, result: ParseResult) -> None:
        try:
            if isinstance(obj.get("timestamp"), (int, float)) and not isinstance(obj["timestamp"], bool):
                ts, tz_label = ctx.timestamp(obj["timestamp"], unit="us"), "UTC"
                original = str(obj["timestamp"])
            else:
                raw = obj.get("datetime") or f"{obj.get('date', '')} {obj.get('time', '')}".strip()
                ts, tz_label = self._stamp(raw, str(obj.get("timezone") or ""), ctx)
                original = str(raw)
        except (TimestampError, OverflowError):
            result.skip("unparseable timestamp")
            return
        message = str(obj.get("message") or obj.get("display_name") or obj.get("title") or "plaso event")
        lane = classify(
            obj.get("data_type"), obj.get("parser"), obj.get("source_short"), obj.get("source"), obj.get("sourcetype")
        )
        url = obj.get("url") if isinstance(obj.get("url"), str) else None
        lat, lon = _maybe_float(obj.get("latitude")), _maybe_float(obj.get("longitude"))
        result.records.append(
            EventRecord(
                ts_utc=to_iso_utc(ts.utc),
                ts_original=original,
                source=lane,
                event_type=_event_type(obj.get("timestamp_desc"), obj.get("data_type"), f"plaso_{lane}"),
                title=message[:200],
                tz_assumed=tz_label,
                ts_basis=ts.basis,
                url=url,
                domain=extract_domain(url),
                package=obj.get("package_name") or obj.get("package") or None,
                lat=lat,
                lon=lon,
                detail={k: clip(v) for k, v in obj.items() if not k.startswith("__") and v not in (None, "")},
                confidence=0.8,
            )
        )

    @staticmethod
    def _stamp(text: str, declared_tz: str, ctx: ParseContext) -> tuple[Timestamp, str]:
        """Honour the timezone the Plaso row declares; fall back to the case timezone."""
        declared = declared_tz.strip()
        if declared:
            try:
                zone = resolve_timezone(declared)
            except ValueError:
                zone = None
            if zone is not None:
                is_utc = declared.upper() in _UTC_NAMES
                ts = coerce_timestamp(text, tz=zone)
                if ts.basis == BASIS_ASSUMED:
                    ts = Timestamp(ts.utc, BASIS_ABSOLUTE if is_utc else BASIS_OFFSET)
                return ts, "UTC" if is_utc else declared
        ts = ctx.timestamp(text)
        return ts, ctx.tz_label(ts)


def _maybe_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
