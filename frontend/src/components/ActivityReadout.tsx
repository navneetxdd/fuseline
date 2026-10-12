import type { ActivitySummary } from '../api/client'
import { formatCount } from '../lib/format'
import { formatDateTime, formatDuration } from '../lib/time'
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

export function ActivityReadout({ activity }: { activity: ActivitySummary }) {
  const span =
    activity.first_event_utc && activity.last_event_utc
      ? `${formatDateTime(Date.parse(activity.first_event_utc))} to ${formatDateTime(Date.parse(activity.last_event_utc))}`
      : null

  const hours = activity.hours ?? []
  const peak = hours.reduce((max, hour) => Math.max(max, hour.count), 0)

  return (
    <div className="activity-readout">
      {activity.notes?.length ? (
        <ul className="briefing">
          {activity.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : (
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
      {hours.length > 1 && peak > 0 ? (
        <figure className="hour-figure">
          <figcaption>Events per hour (UTC)</figcaption>
          <div className="hour-strip" role="img" aria-label={`Events per hour, peak ${formatCount(peak)}`}>
            {hours.map((hour) => (
              <span
                key={hour.start_utc}
                style={{ height: `${Math.max(hour.count > 0 ? 8 : 0, (hour.count / peak) * 100)}%` }}
                title={`${formatDateTime(Date.parse(hour.start_utc))} · ${formatCount(hour.count)} events`}
              />
            ))}
          </div>
          <div className="hour-axis">
            <span>{formatDateTime(Date.parse(hours[0].start_utc))}</span>
            <span>{formatDateTime(Date.parse(hours[hours.length - 1].start_utc))}</span>
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
