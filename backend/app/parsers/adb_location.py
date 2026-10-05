from __future__ import annotations

import re
from pathlib import Path

from app.parsers.base import EventRecord, ParseContext, ParseResult, compact, read_text_head
from app.timeutil import BASIS_ABSOLUTE, Timestamp, TimestampError, from_epoch_ms, to_iso_utc

# `adb shell dumpsys location` — last fixes and history lines vary by OEM/Android version.
_LOCATION_BRACKET = re.compile(
    r"Location\[[^\]]*?(?P<lat>-?\d+(?:\.\d+)?)\s*,\s*(?P<lon>-?\d+(?:\.\d+)?)",
)
_TIME_MS = re.compile(r"(?:time=|t=)(?P<ms>\d{10,13})")
_LATLON_KV = re.compile(
    r"lat(?:itude)?[=:]\s*(?P<lat>-?\d+(?:\.\d+)?)[^\n]{0,120}?lon(?:itude)?[=:]\s*(?P<lon>-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_MARKERS = (
    "Location Manager",
    "LocationManagerService",
    "last location",
    "Last Location",
    "Location[gps",
    "Location[network",
    "Location[fused",
)


def _valid_coord(lat: float, lon: float) -> bool:
    if lat == 0.0 and lon == 0.0:
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


class AdbLocationParser:
    """Live pull via ADB: `adb shell dumpsys location` text (no root)."""

    source = "location"
    name = "adb_location_dump"

    def sniff(self, path: Path) -> float:
        head = read_text_head(path, 512_000)
        if head is None:
            return 0.0
        if not any(m in head for m in _MARKERS):
            return 0.0
        if _LOCATION_BRACKET.search(head) or _LATLON_KV.search(head):
            return 0.92
        return 0.0

    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        result = ParseResult()
        seen: set[tuple[str, str, str]] = set()
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                for match in _LOCATION_BRACKET.finditer(line):
                    try:
                        lat = float(match.group("lat"))
                        lon = float(match.group("lon"))
                    except ValueError:
                        result.skip("invalid coordinates")
                        continue
                    if not _valid_coord(lat, lon):
                        result.skip("placeholder coordinates")
                        continue
                    tmatch = _TIME_MS.search(line)
                    if not tmatch:
                        result.skip("location line without timestamp")
                        continue
                    ms = int(tmatch.group("ms"))
                    if ms < 1_000_000_000_000:
                        ms *= 1000
                    try:
                        ts = Timestamp(utc=from_epoch_ms(ms), basis=BASIS_ABSOLUTE)
                    except (TimestampError, OverflowError, ValueError):
                        result.skip("unparseable timestamp")
                        continue
                    key = (to_iso_utc(ts.utc), f"{lat:.6f}", f"{lon:.6f}")
                    if key in seen:
                        continue
                    seen.add(key)
                    result.records.append(
                        EventRecord(
                            ts_utc=key[0],
                            ts_original=str(ms),
                            source=self.source,
                            event_type="gps_fix",
                            title="Device location (dumpsys)",
                            lat=lat,
                            lon=lon,
                            tz_assumed=ctx.tz_label(ts),
                            ts_basis=ts.basis,
                            detail=compact({"provider": "dumpsys", "line": line[:240]}),
                            confidence=0.75,
                        )
                    )
                for match in _LATLON_KV.finditer(line):
                    try:
                        lat = float(match.group("lat"))
                        lon = float(match.group("lon"))
                    except ValueError:
                        continue
                    if not _valid_coord(lat, lon):
                        continue
                    tmatch = _TIME_MS.search(line)
                    if not tmatch:
                        continue
                    ms = int(tmatch.group("ms"))
                    if ms < 1_000_000_000_000:
                        ms *= 1000
                    try:
                        ts = Timestamp(utc=from_epoch_ms(ms), basis=BASIS_ABSOLUTE)
                    except (TimestampError, OverflowError, ValueError):
                        continue
                    key = (to_iso_utc(ts.utc), f"{lat:.6f}", f"{lon:.6f}")
                    if key in seen:
                        continue
                    seen.add(key)
                    result.records.append(
                        EventRecord(
                            ts_utc=key[0],
                            ts_original=str(ms),
                            source=self.source,
                            event_type="gps_fix",
                            title="Device location (dumpsys)",
                            lat=lat,
                            lon=lon,
                            tz_assumed=ctx.tz_label(ts),
                            ts_basis=ts.basis,
                            detail=compact({"provider": "dumpsys-kv"}),
                            confidence=0.7,
                        )
                    )
        return result
