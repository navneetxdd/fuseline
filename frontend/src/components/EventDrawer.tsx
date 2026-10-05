import type { ReactNode } from 'react'
import type { Artifact, TimeBasis, TimelineEvent } from '../api/client'
import { clipJson, defangUrl, shortHash } from '../lib/format'
import { formatDateTime } from '../lib/time'
import { CopyButton } from './CopyButton'
import { SourceChip } from './SourceChip'

type Props = {
  event: TimelineEvent | null
  artifacts: Artifact[]
  timeZone: string
  onClose: () => void
}

const BASIS_TEXT: Record<TimeBasis, string> = {
  absolute: 'Exact: the source stored an absolute time (epoch or UTC).',
  offset: 'Converted from a timestamp that carried an explicit UTC offset.',
  assumed: 'Assumed: the source had no timezone, so it was interpreted as local time in',
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="kv">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}

export function EventDrawer({ event, artifacts, timeZone, onClose }: Props) {
  if (!event) {
    return (
      <div className="drawer empty-drawer">
        Select an event (chart mark, table row, or map point) to inspect its provenance.
      </div>
    )
  }

  const artifact = artifacts.find((a) => a.id === event.artifact_id)
  const hasCoords = event.lat !== null && event.lon !== null
  const coords = hasCoords ? `${event.lat?.toFixed(6)}, ${event.lon?.toFixed(6)}` : ''

  return (
    <div className="drawer" aria-live="polite">
      <div className="drawer-head">
        <div className="row">
          <SourceChip source={event.source} />
          <span className="badge neutral">{event.event_type}</span>
        </div>
        <button type="button" className="btn secondary small" onClick={onClose}>
          Close
        </button>
      </div>
      <h3 className="drawer-title">{event.title}</h3>

      <dl className="kv-list">
        <Field label="UTC">
          <span className="mono">{formatDateTime(event.ts_ms, 'UTC', true)}</span>
        </Field>
        {timeZone !== 'UTC' ? (
          <Field label={timeZone}>
            <span className="mono">{formatDateTime(event.ts_ms, timeZone, true)}</span>
          </Field>
        ) : null}
        <Field label="Source value">
          <span className="mono">{event.ts_original}</span>
        </Field>
        <Field label="Time basis">
          <span className={event.ts_basis === 'assumed' ? 'warn-text' : undefined}>
            {BASIS_TEXT[event.ts_basis]}
            {event.ts_basis === 'assumed' ? ` ${event.tz_assumed}.` : ''}
          </span>
        </Field>
        {artifact ? (
          <Field label="Artifact">
            <span>{artifact.original_name}</span>
            <span className="mono muted"> · {artifact.parser} · sha256 {shortHash(artifact.sha256, 10, 6)}</span>
          </Field>
        ) : null}
        {event.package ? (
          <Field label="Package">
            <span className="mono">{event.package}</span>
          </Field>
        ) : null}
        {event.url ? (
          <Field label="URL">
            <span className="mono url-text" title="Shown inert on purpose: evidence URLs are never made clickable">
              {defangUrl(event.url)}
            </span>
            <CopyButton value={event.url} label="Copy original" />
          </Field>
        ) : null}
        {hasCoords ? (
          <Field label="Position">
            <span className="mono">{coords}</span>
            <CopyButton value={coords} />
          </Field>
        ) : null}
        <Field label="Parser confidence">
          <span className="mono">{event.confidence.toFixed(2)}</span>
        </Field>
        <Field label="Event ID">
          <span className="mono muted">{event.id}</span>
        </Field>
      </dl>

      <details className="raw">
        <summary>Raw parsed record</summary>
        <pre className="code-block">{clipJson(event.detail)}</pre>
      </details>
    </div>
  )
}
