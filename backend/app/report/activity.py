"""Roll up stored events into counts an examiner can read.

Foreground time is the gap from a move-to-foreground event to the next
background or stop for the same package. An open foreground with no later
stop is not given a duration.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from app.db import case_conn
from app.timeutil import from_epoch_ms, to_iso_utc

FOREGROUND = frozenset({"move_to_foreground", "activity_resumed"})
BACKGROUND = frozenset({"move_to_background", "activity_paused", "activity_stopped"})
MAX_FOREGROUND_MS = 6 * 60 * 60 * 1000
TOP = 12
HOUR_MS = 3_600_000
MAX_FILLED_HOURS = 24 * 14
HOUR_APP_LIMIT = 6
HOUR_KIND_LIMIT = 5

SOURCE_PLAIN = {
    "app_usage": "app usage",
    "browsing": "browsing history",
    "location": "location",
    "plaso": "a Plaso timeline",
}

TYPE_LABELS = {
    "move_to_foreground": "Moved to foreground",
    "move_to_background": "Moved to background",
    "activity_resumed": "Activity resumed",
    "activity_paused": "Activity paused",
    "activity_stopped": "Activity stopped",
    "screen_interactive": "Screen on",
    "screen_non_interactive": "Screen off",
    "keyguard_shown": "Keyguard shown",
    "keyguard_hidden": "Keyguard hidden",
    "notification_seen": "Notification seen",
    "notification_interruption": "Notification interruption",
    "user_interaction": "User interaction",
    "foreground_service_start": "Foreground service started",
    "foreground_service_stop": "Foreground service stopped",
    "device_startup": "Device startup",
    "device_shutdown": "Device shutdown",
}


def _label(key: str) -> str:
    if key in TYPE_LABELS:
        return TYPE_LABELS[key]
    return key.replace("_", " ")


_GENERIC_TAIL = frozenset({"android", "app", "application", "apps", "mobile"})
_PRETTY = {
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "youtube": "YouTube",
    "chrome": "Chrome",
    "spotify": "Spotify",
    "chatgpt": "ChatGPT",
    "maps": "Maps",
    "launcher3": "Launcher",
}


def _fmt_duration(ms: int) -> str:
    seconds = max(0, int(ms) // 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours >= 48:
        days, hour = divmod(hours, 24)
        return f"{days}d {hour}h"
    if hours:
        return f"{hours}h {minute:02d}m"
    if minutes:
        return f"{minutes}m {sec:02d}s"
    return f"{sec}s"


def _clock(iso: str) -> str:
    return iso.replace("T", " ").replace(".000", "").replace("Z", " UTC")


def _hour_window(start_iso: str) -> str:
    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    end = start + timedelta(hours=1)
    if start.date() == end.date():
        return f"{start:%Y-%m-%d %H:%M} to {end:%H:%M} UTC"
    return f"{start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC"


def _when(ms: int) -> str:
    return _clock(to_iso_utc(from_epoch_ms(ms)))


def _sentence_name(package: str) -> str:
    return _pkg_label(package) or package


def _pkg_label(package: str) -> str:
    parts = [part for part in package.split(".") if part]
    if not parts:
        return package
    raw = parts[-2] if parts[-1] in _GENERIC_TAIL and len(parts) >= 2 else parts[-1]
    return _PRETTY.get(raw, raw)


def summarize_activity(case_id: str) -> dict:
    with case_conn(case_id) as conn:
        source_counts = {
            row["source"]: row["c"] for row in conn.execute("SELECT source, COUNT(*) AS c FROM events GROUP BY source")
        }
        lo, hi, event_count = conn.execute("SELECT MIN(ts_ms), MAX(ts_ms), COUNT(*) FROM events").fetchone()
        usage_rows = conn.execute(
            """
            SELECT package, event_type, ts_ms FROM events
            WHERE source = 'app_usage'
            ORDER BY ts_ms, id
            """
        ).fetchall()
        domains = [
            {"key": row["domain"], "label": row["domain"], "count": row["c"]}
            for row in conn.execute(
                """
                SELECT domain, COUNT(*) AS c FROM events
                WHERE source = 'browsing' AND domain IS NOT NULL AND domain != ''
                GROUP BY domain ORDER BY c DESC, domain LIMIT ?
                """,
                (TOP,),
            )
        ]
        location_fixes = conn.execute(
            "SELECT COUNT(*) FROM events WHERE source = 'location' AND lat IS NOT NULL AND lon IS NOT NULL"
        ).fetchone()[0]
        hour_rows = conn.execute(
            """
            SELECT CAST(ts_ms / ? AS INTEGER) AS hour_index, COUNT(*) AS c
            FROM events
            GROUP BY hour_index
            ORDER BY hour_index
            """,
            (HOUR_MS,),
        ).fetchall()
        other_rows = conn.execute(
            """
            SELECT ts_ms, source, domain FROM events
            WHERE source != 'app_usage'
            """
        ).fetchall()

    event_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    foreground_ms: Counter[str] = Counter()
    hour_apps: dict[int, Counter[str]] = defaultdict(Counter)
    hour_kinds: dict[int, Counter[str]] = defaultdict(Counter)
    open_at: dict[str, int] = {}
    for row in usage_rows:
        package = (row["package"] or "").strip()
        kind = row["event_type"] or "usage_event"
        type_counts[kind] += 1
        ts_ms = int(row["ts_ms"])
        hour_index = ts_ms // HOUR_MS
        hour_kinds[hour_index][kind] += 1
        if package:
            hour_apps[hour_index][package] += 1
        if not package:
            continue
        event_counts[package] += 1
        if kind in FOREGROUND:
            open_at[package] = ts_ms
        elif kind in BACKGROUND and package in open_at:
            delta = ts_ms - open_at.pop(package)
            if 0 < delta <= MAX_FOREGROUND_MS:
                foreground_ms[package] += delta
    for row in other_rows:
        hour_index = int(row["ts_ms"]) // HOUR_MS
        source = row["source"] or "event"
        hour_kinds[hour_index][source] += 1
        domain = (row["domain"] or "").strip()
        if source == "browsing" and domain:
            hour_apps[hour_index][f"site:{domain}"] += 1

    ranked = sorted(event_counts, key=lambda pkg: (-foreground_ms[pkg], -event_counts[pkg], pkg))
    packages = [
        {
            "package": pkg,
            "label": _pkg_label(pkg),
            "events": event_counts[pkg],
            "foreground_ms": foreground_ms[pkg],
        }
        for pkg in ranked[:TOP]
    ]
    types_ranked = type_counts.most_common(8)
    shown = sum(count for _, count in types_ranked)
    event_types = [{"key": key, "label": _label(key), "count": count} for key, count in types_ranked]
    rest = sum(type_counts.values()) - shown
    if rest > 0:
        event_types.append({"key": "other", "label": "Other recorded types", "count": rest})

    hours = _hours(hour_rows, lo, hi, event_count, hour_apps, hour_kinds)
    return {
        "event_count": event_count,
        "source_counts": source_counts,
        "first_event_utc": to_iso_utc(from_epoch_ms(lo)) if event_count else None,
        "last_event_utc": to_iso_utc(from_epoch_ms(hi)) if event_count else None,
        "packages": packages,
        "event_types": event_types,
        "domains": domains,
        "location_fixes": location_fixes,
        "hours": hours,
        "notes": _notes(
            event_count=event_count,
            lo=lo,
            hi=hi,
            source_counts=source_counts,
            event_counts=event_counts,
            foreground_ms=foreground_ms,
            ranked=ranked,
            type_counts=type_counts,
            hours=hours,
        ),
    }


def _hour_app_label(key: str) -> str:
    if key.startswith("site:"):
        return key[5:]
    return _pkg_label(key)


def _kind_label(key: str) -> str:
    if key in SOURCE_PLAIN:
        return SOURCE_PLAIN[key]
    return _label(key)


def _slices(counter: Counter[str], limit: int, labeler) -> list[dict]:
    merged: Counter[str] = Counter()
    for key, count in counter.items():
        merged[labeler(key)] += count
    ranked = merged.most_common()
    head = ranked[:limit]
    rest = sum(count for _, count in ranked[limit:])
    rows = [{"label": label, "count": count} for label, count in head]
    if rest:
        rows.append({"label": "Other", "count": rest})
    return rows


def _hours(
    hour_rows,
    lo,
    hi,
    event_count: int,
    hour_apps: dict[int, Counter[str]],
    hour_kinds: dict[int, Counter[str]],
) -> list[dict]:
    if not event_count or lo is None or hi is None:
        return []
    counts = {int(row["hour_index"]): int(row["c"]) for row in hour_rows}
    start = int(lo) // HOUR_MS
    end = int(hi) // HOUR_MS
    indexes = sorted(counts) if end - start > MAX_FILLED_HOURS else range(start, end + 1)
    rows = []
    for index in indexes:
        count = counts.get(index, 0)
        rows.append(
            {
                "start_utc": to_iso_utc(from_epoch_ms(index * HOUR_MS)),
                "count": count,
                "apps": _slices(hour_apps.get(index, Counter()), HOUR_APP_LIMIT, _hour_app_label) if count else [],
                "kinds": _slices(hour_kinds.get(index, Counter()), HOUR_KIND_LIMIT, _kind_label) if count else [],
            }
        )
    return rows


def _notes(
    *,
    event_count: int,
    lo,
    hi,
    source_counts: dict,
    event_counts: Counter[str],
    foreground_ms: Counter[str],
    ranked: list[str],
    type_counts: Counter[str],
    hours: list[dict],
) -> list[str]:
    if event_count == 0:
        return ["No events have been parsed from the acquired files."]
    notes: list[str] = []
    if lo is not None and hi is not None:
        notes.append(
            f"{event_count:,} events from {_when(int(lo))} to {_when(int(hi))} ({_fmt_duration(int(hi) - int(lo))})."
        )
    if ranked and foreground_ms[ranked[0]] > 0:
        top = ranked[0]
        notes.append(
            f"{_sentence_name(top)} was in the foreground the longest: {_fmt_duration(foreground_ms[top])} "
            f"across {event_counts[top]:,} events."
        )
        most = max(event_counts, key=lambda pkg: (event_counts[pkg], pkg))
        if most != top:
            measured = foreground_ms[most]
            spent = _fmt_duration(measured) if measured else "no measured foreground time"
            notes.append(
                f"{_sentence_name(most)} has the most events ({event_counts[most]:,}) and {spent}. "
                "Event count includes pauses, stops, and notifications, so it is not time on screen."
            )
        total_fg = sum(foreground_ms.values())
        if total_fg:
            notes.append(f"Measured foreground time across all packages is {_fmt_duration(total_fg)}.")
    elif event_counts:
        most = max(event_counts, key=lambda pkg: (event_counts[pkg], pkg))
        notes.append(
            f"{_sentence_name(most)} has the most events ({event_counts[most]:,}). "
            "No foreground interval could be measured."
        )
    screen_on = type_counts.get("screen_interactive", 0)
    screen_off = type_counts.get("screen_non_interactive", 0)
    if screen_on or screen_off:
        notes.append(f"The log records the screen turning on {screen_on:,} times and off {screen_off:,} times.")
    if hours:
        peak = max(hours, key=lambda item: item["count"])
        if peak["count"]:
            notes.append(f"The busiest hour is {_hour_window(peak['start_utc'])}, with {peak['count']:,} events.")
    if len(source_counts) < 2:
        names = [SOURCE_PLAIN.get(name, name.replace("_", " ")) for name in sorted(source_counts)]
        only = names[0] if names else "nothing"
        notes.append(
            "Sessions are only built when two sources fall within a few minutes of each other. "
            f"This case only has {only}."
        )
    return notes
