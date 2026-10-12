import { useState } from 'react'
import type { ActivitySummary, HourCount } from '../api/client'
import { formatCount } from '../lib/format'
import { formatDateTime, formatDuration, formatTimeOfDay } from '../lib/time'
import { SOURCE_LABEL } from '../lib/sources'

const CHECKS_WORTH_SHOWING = new Set([
  'NO_EVENTS',
  'ROWS_SKIPPED',
  'TZ_ASSUMED',
  'LARGE_GAP',
  'NO_TIME_OVERLAP',
  'FUTURE_TS',
  'EARLY_TS',
  'DUPLICATE_HASH',
  'NO_SOURCES',
])

export function checksWorthShowing<T extends { code: string }>(findings: T[]): T[] {
  return findings.filter((f) => CHECKS_WORTH_SHOWING.has(f.code))
}

export function correlationSentence(activity: ActivitySummary, windowSeconds: number, sessionCount: number): string {
  const names = Object.keys(activity.source_counts)
  if (names.length === 0) return 'Nothing has been ingested yet.'
  if (names.length < 2) {
    const only = SOURCE_LABEL[names[0]] ?? names[0]
    return `A session needs two sources inside ${windowSeconds} seconds. This case only has ${only.toLowerCase()}.`
  }
  if (sessionCount === 0) {
    return `No events from different sources fall inside ${windowSeconds} seconds of each other.`
  }
  return `${formatCount(sessionCount)} session${sessionCount === 1 ? '' : 's'}, where at least two sources fall inside ${windowSeconds} seconds.`
}

function HourList({ title, rows }: { title: string; rows: { label: string; count: number }[] }) {
  if (rows.length === 0) return null
  return (
    <div>
      <h3 className="hour-list-title">{title}</h3>
      <ul className="hour-list">
        {rows.map((row) => (
          <li key={`${title}-${row.label}`}>
            <span>{row.label}</span>
            <span className="mono">{formatCount(row.count)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

const HOUR_MS = 60 * 60 * 1000

function dayLabel(ms: number): string {
  return formatDateTime(ms).slice(0, 10)
}

function clockLabel(ms: number): string {
  return formatTimeOfDay(ms, 'UTC', false)
}

function rangeLabel(startMs: number, endMs: number): string {
  if (dayLabel(startMs) === dayLabel(endMs)) return `${dayLabel(startMs)} ${clockLabel(startMs)}–${clockLabel(endMs)}`
  return `${dayLabel(startMs)} ${clockLabel(startMs)}–${dayLabel(endMs)} ${clockLabel(endMs)}`
}

function hourWindow(startIso: string): string {
  const start = Date.parse(startIso)
  return rangeLabel(start, start + HOUR_MS)
}

function quietNote(hours: HourCount[]): string | null {
  const runs: string[] = []
  let runStart: string | null = null
  let runEnd: string | null = null
  const close = () => {
    if (runStart && runEnd) {
      runs.push(rangeLabel(Date.parse(runStart), Date.parse(runEnd) + HOUR_MS))
    }
    runStart = null
    runEnd = null
  }
  for (const hour of hours) {
    if (hour.count === 0) {
      if (!runStart) runStart = hour.start_utc
      runEnd = hour.start_utc
    } else {
      close()
    }
  }
  close()
  if (runs.length === 0) return null
  return `The blank stretch is hours with no events: ${runs.join('; ')}.`
}

export function ActivityReadout({
  activity,
  hideBriefing = false,
}: {
  activity: ActivitySummary
  hideBriefing?: boolean
}) {
  const span =
    activity.first_event_utc && activity.last_event_utc
      ? `${formatDateTime(Date.parse(activity.first_event_utc))} to ${formatDateTime(Date.parse(activity.last_event_utc))}`
      : null

  const hours = activity.hours ?? []
  const peak = hours.reduce((max, hour) => Math.max(max, hour.count), 0)
  const busiest = hours.reduce<HourCount | null>((best, hour) => (hour.count > (best?.count ?? -1) ? hour : best), null)
  const [picked, setPicked] = useState<string | null>(null)
  const selected = hours.find((hour) => hour.start_utc === picked) ?? busiest
  const gap = quietNote(hours)

  return (
    <div className="activity-readout">
      {!hideBriefing && activity.notes?.length ? (
        <ul className="briefing">
          {activity.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : hideBriefing ? null : (
        <p className="activity-lead">
          {formatCount(activity.event_count)} events
          {span ? ` from ${span}` : ''}.
          {Object.entries(activity.source_counts).map(([src, n]) => (
            <span key={src}>
              {' '}
              {SOURCE_LABEL[src] ?? src} {formatCount(n)}.
            </span>
          ))}
        </p>
      )}
      {hours.length > 1 && peak > 0 && selected ? (
        <figure className="hour-figure">
          <figcaption>Events per hour (UTC). Select a bar to see which apps and record types it contains.</figcaption>
          <div className="hour-strip" role="group" aria-label={`Events per hour, peak ${formatCount(peak)}`}>
            {hours.map((hour) => (
              <button
                key={hour.start_utc}
                type="button"
                aria-pressed={hour.start_utc === selected.start_utc}
                aria-label={`${hourWindow(hour.start_utc)}, ${formatCount(hour.count)} events`}
                onClick={() => setPicked(hour.start_utc)}
              >
                <i style={{ height: `${Math.max(hour.count > 0 ? 8 : 0, (hour.count / peak) * 100)}%` }} />
              </button>
            ))}
          </div>
          <div className="hour-axis">
            <span>{`${dayLabel(Date.parse(hours[0].start_utc))} ${clockLabel(Date.parse(hours[0].start_utc))}`}</span>
            <span>
              {`${dayLabel(Date.parse(hours[hours.length - 1].start_utc) + HOUR_MS)} ${clockLabel(Date.parse(hours[hours.length - 1].start_utc) + HOUR_MS)}`}
            </span>
          </div>
          {gap ? <p className="muted small hour-gap">{gap}</p> : null}
          <div className="hour-detail">
            <p className="hour-detail-title">
              {hourWindow(selected.start_utc)} · {formatCount(selected.count)} events
            </p>
            {selected.count === 0 ? (
              <p className="muted small">Nothing was recorded in this hour.</p>
            ) : (
              <div className="hour-detail-grid">
                <HourList title="Apps" rows={selected.apps ?? []} />
                <HourList title="Record types" rows={selected.kinds ?? []} />
              </div>
            )}
          </div>
        </figure>
      ) : null}
      {activity.packages.length > 0 ? (
        <div className="table-wrap">
          <table className="data">
            <caption>Apps in the usage log, busiest first</caption>
            <thead>
              <tr>
                <th scope="col">Package</th>
                <th scope="col" className="num">
                  Events
                </th>
                <th scope="col" className="num">
                  Foreground
                </th>
              </tr>
            </thead>
            <tbody>
              {activity.packages.map((pkg) => (
                <tr key={pkg.package}>
                  <td>
                    <div>{pkg.label}</div>
                    <div className="cell-sub mono">{pkg.package}</div>
                  </td>
                  <td className="num mono">{formatCount(pkg.events)}</td>
                  <td className="num mono">{pkg.foreground_ms > 0 ? formatDuration(pkg.foreground_ms) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {activity.event_types.length > 0 ? (
        <ul className="type-pills">
          {activity.event_types.map((item) => (
            <li key={item.key}>
              <span>{item.label}</span>
              <span className="mono">{formatCount(item.count)}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {activity.domains.length > 0 ? (
        <div className="table-wrap">
          <table className="data">
            <caption>Domains in browsing history</caption>
            <thead>
              <tr>
                <th scope="col">Domain</th>
                <th scope="col" className="num">
                  Events
                </th>
              </tr>
            </thead>
            <tbody>
              {activity.domains.map((item) => (
                <tr key={item.key}>
                  <td className="mono">{item.label}</td>
                  <td className="num mono">{formatCount(item.count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {activity.location_fixes > 0 ? (
        <p className="muted small">{formatCount(activity.location_fixes)} location fixes include coordinates.</p>
      ) : null}
    </div>
  )
}
