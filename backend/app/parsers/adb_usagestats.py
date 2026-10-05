from __future__ import annotations

import re
from pathlib import Path

from app.parsers.base import EventRecord, ParseContext, ParseResult, compact, read_text_head
from app.timeutil import TimestampError, to_iso_utc

# `adb shell dumpsys usagestats` event lines look like:
#   time="2024-06-15 10:00:00" type=MOVE_TO_FOREGROUND package=com.whatsapp class=... instanceId=12
# Older Android dumps group them under "Usage Events:"; Android 12+ often uses
# "Last 24 hour events (...)" (and similar windows) with no "Usage Events:" header.
_EVENT_LINE = re.compile(r'time="(?P<time>[^"]+)"\s+type=(?P<type>\w+)\s+package=(?P<package>\S+)(?P<rest>.*)')
_KV = re.compile(r"(\w+)=(\S+)")
_DUMP_MARKERS = (
    "Usage Events:",
    "Last 24 hour events",
    "hour events",
    "componentUsageStatsService",
    "android.app.usage",
)


class AdbUsageStatsParser:
    """Live pull via ADB: `adb shell dumpsys usagestats` text output, no root required."""

    source = "app_usage"
    name = "adb_usagestats_dump"

    def sniff(self, path: Path) -> float:
        # Event blocks can sit well past the first 64 KiB on a device with months of history.
        head = read_text_head(path, 4_000_000)
        if head is None or not _EVENT_LINE.search(head):
            return 0.0
        if any(marker in head for marker in _DUMP_MARKERS):
            return 0.95
        # Bare event lines still look like this dump (prefer over guessing another parser).
        return 0.85

    def parse(self, path: Path, ctx: ParseContext) -> ParseResult:
        result = ParseResult()
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if 'time="' not in line:
                    continue
                match = _EVENT_LINE.match(line)
                if not match:
                    result.skip("unrecognised usage-event line")
                    continue
                try:
                    ts = ctx.timestamp(match.group("time"))
                except (TimestampError, OverflowError):
                    result.skip("unparseable timestamp")
                    continue
                package = match.group("package")
                extra = dict(_KV.findall(match.group("rest")))
                result.records.append(
                    EventRecord(
                        ts_utc=to_iso_utc(ts.utc),
                        ts_original=match.group("time"),
                        source=self.source,
                        event_type=match.group("type").lower(),
                        title=package,
                        package=package,
                        tz_assumed=ctx.tz_label(ts),
                        ts_basis=ts.basis,
                        detail=compact({"type": match.group("type"), **extra}),
                        confidence=0.85,
                    )
                )
        return result
