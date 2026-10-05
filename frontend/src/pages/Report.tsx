import { useCallback, useEffect, useState } from 'react'
import {
  type AuditEntry,
  type IntegrityResult,
  type ReportSummary,
  type Session,
  type TimelineEvent,
  api,
} from '../api/client'
import { Callout } from '../components/Callout'
import { FindingsTable } from '../components/FindingsTable'
import { NoCase } from '../components/NoCase'
import { SourceChip } from '../components/SourceChip'
import { formatBytes, formatCount, shortHash } from '../lib/format'
import { formatDateTime, formatDuration } from '../lib/time'
import { useCase } from '../state/CaseContext'

async function downloadExport(url: string, filename: string): Promise<void> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Export failed (${res.status})`)
  const blob = await res.blob()
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filename
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(objectUrl)
}

export function ReportPage() {
  const { caseId } = useCase()
  if (!caseId) return <NoCase title="Report" />
  return <Report key={caseId} caseId={caseId} />
}

function Report({ caseId }: { caseId: string }) {
  const [report, setReport] = useState<ReportSummary | null>(null)
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [exporting, setExporting] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [integrity, setIntegrity] = useState<IntegrityResult | null>(null)
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [sessionEvents, setSessionEvents] = useState<Record<string, TimelineEvent[]>>({})

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      // The audit trail is fetched from its own endpoint (not the copy embedded in the report
      // summary, which is capped at 500) so long-lived cases still show their full history.
      const [rep, audit] = await Promise.all([api.report(caseId), api.audit(caseId)])
      setReport(rep)
      setAuditEntries(audit)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load report')
    } finally {
      setLoading(false)
    }
  }, [caseId])

  useEffect(() => {
    void load()
  }, [load])

  async function onExport(kind: 'html' | 'csv' | 'json', raw = false) {
    const label = raw ? 'csv-raw' : kind
    setExporting(label)
    setError(null)
    try {
      if (kind === 'html') window.open(`/api/cases/${caseId}/report/html`, '_blank', 'noopener,noreferrer')
      else {
        const url = `/api/cases/${caseId}/report/${kind}${raw ? '?raw=true' : ''}`
        await downloadExport(url, `fuseline_${caseId.slice(0, 8)}${raw ? '_raw' : ''}.${kind}`)
      }
      // Exports are audited server-side; reload so the trail below shows this one.
      setTimeout(() => void load(), 400)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export failed')
    } finally {
      setExporting(null)
    }
  }

  async function verify() {
    setError(null)
    try {
      setIntegrity(await api.verify(caseId))
      void load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Verification failed')
    }
  }

  async function toggle(session: Session) {
    const next = new Set(open)
    if (next.has(session.id)) next.delete(session.id)
    else {
      next.add(session.id)
      if (!sessionEvents[session.id]) {
        try {
          const events = await api.sessionEvents(caseId, session.id)
          setSessionEvents((prev) => ({ ...prev, [session.id]: events }))
        } catch {
          setSessionEvents((prev) => ({ ...prev, [session.id]: [] }))
        }
      }
    }
    setOpen(next)
  }

  const tz = report?.case.timezone ?? 'UTC'

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-head">
          <div>
            <h1>Report</h1>
            <p className="muted">Validation findings, provenance and examiner exports for this case.</p>
          </div>
          <div className="row">
            <button type="button" className="btn secondary small" disabled={loading} onClick={() => void load()}>
              {loading ? 'Loading…' : 'Refresh'}
            </button>
            <button
              type="button"
              className="btn secondary small"
              disabled={!!exporting}
              onClick={() => void onExport('html')}
            >
              HTML report
            </button>
            <button
              type="button"
              className="btn secondary small"
              disabled={!!exporting}
              onClick={() => void onExport('csv')}
              title="Cells that could run as spreadsheet formulas are prefixed with an apostrophe"
            >
              {exporting === 'csv' ? 'CSV…' : 'CSV'}
            </button>
            <button type="button" className="btn small" disabled={!!exporting} onClick={() => void onExport('json')}>
              {exporting === 'json' ? 'JSON…' : 'JSON (lossless)'}
            </button>
          </div>
        </div>
        <div className="row" style={{ marginTop: '-0.25rem' }}>
          <button
            type="button"
            className="btn ghost small"
            disabled={!!exporting}
            onClick={() => void onExport('csv', true)}
            title="Same data, without the spreadsheet-formula safeguard. Only use this if you trust every cell and need pristine values"
          >
            {exporting === 'csv-raw' ? 'Exporting raw CSV…' : 'Export raw CSV (unescaped)'}
          </button>
        </div>

        {error ? <div className="error-box">{error}</div> : null}

        {report ? (
          <>
            <div className="stat-row">
              <div className="stat">
                <div className="n">{formatCount(report.event_count)}</div>
                <div className="l">Events</div>
              </div>
              <div className="stat">
                <div className="n">{report.artifacts.length}</div>
                <div className="l">Artifacts</div>
              </div>
              <div className="stat">
                <div className="n">{report.session_count}</div>
                <div className="l">Sessions</div>
              </div>
              {report.first_event_utc && report.last_event_utc ? (
                <div className="stat wide">
                  <div className="n small-n">
                    {formatDuration(Date.parse(report.last_event_utc) - Date.parse(report.first_event_utc))}
                  </div>
                  <div className="l">
                    {formatDateTime(Date.parse(report.first_event_utc))} → {formatDateTime(Date.parse(report.last_event_utc))}{' '}
                    UTC
                  </div>
                </div>
              ) : null}
            </div>
            <div className="row">
              {Object.entries(report.source_counts).map(([src, n]) => (
                <span key={src} className="row tight">
                  <SourceChip source={src} />
                  <span className="mono muted">{formatCount(n)}</span>
                </span>
              ))}
            </div>
          </>
        ) : null}
      </section>

      <section className="panel">
        <h2>Validation</h2>
        <FindingsTable findings={report?.findings ?? []} empty="No findings yet. Acquire evidence first." />
      </section>

      <section className="panel">
        <h2>Proximity sessions</h2>
        {report ? (
          <p className="muted small">
            Chained while events are within <strong>{report.correlation.window_seconds}s</strong> of each other, at least{' '}
            <strong>{report.correlation.min_sources}</strong> sources, capped at{' '}
            <strong>{formatDuration(report.correlation.max_span_seconds * 1000)}</strong>. Sessions show temporal
            coincidence, not causation.
          </p>
        ) : null}
        {!report || report.sessions.length === 0 ? (
          <div className="empty">No correlated sessions.</div>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col" className="num">
                    Score
                  </th>
                  <th scope="col">Window ({tz === 'UTC' ? 'UTC' : tz})</th>
                  <th scope="col">Sources</th>
                  <th scope="col">Summary</th>
                  <th scope="col">
                    <span className="sr-only">Details</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {report.sessions.slice(0, 25).flatMap((s) => {
                  const expanded = open.has(s.id)
                  const rows = [
                    <tr key={s.id}>
                      <td className="num mono">{s.score.toFixed(2)}</td>
                      <td className="mono nowrap">
                        {formatDateTime(Date.parse(s.start_utc), tz)}
                        <div className="cell-sub">
                          {formatDuration(Date.parse(s.end_utc) - Date.parse(s.start_utc))} · {s.event_count} events
                        </div>
                      </td>
                      <td>
                        <div className="row tight">
                          {s.sources.map((src) => (
                            <SourceChip key={src} source={src} />
                          ))}
                        </div>
                      </td>
                      <td>{s.summary}</td>
                      <td>
                        <button
                          type="button"
                          className="btn ghost small"
                          aria-expanded={expanded}
                          onClick={() => void toggle(s)}
                        >
                          {expanded ? 'Hide' : 'Events'}
                        </button>
                      </td>
                    </tr>,
                  ]
                  if (expanded) {
                    rows.push(
                      <tr key={`${s.id}-events`} className="subrow">
                        <td colSpan={5}>
                          <div className="subrow-scroll">
                            {(sessionEvents[s.id] ?? []).map((e) => (
                              <div key={e.id} className="subrow-line">
                                <span className="mono muted">{formatDateTime(e.ts_ms, tz, true)}</span>
                                <SourceChip source={e.source} />
                                <span>{e.title}</span>
                              </div>
                            ))}
                            {sessionEvents[s.id] === undefined ? <span className="muted">Loading…</span> : null}
                          </div>
                        </td>
                      </tr>,
                    )
                  }
                  return rows
                })}
              </tbody>
            </table>
            {report.sessions.length > 25 ? (
              <p className="muted small">Showing the top 25 of {report.sessions.length} by score. The exports contain all of them.</p>
            ) : null}
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>Chain of custody</h2>
            <p className="muted small">
              An append-only log of everything done to this case. Each entry commits to the one before it, so edits
              or deletions are detectable.
            </p>
          </div>
          <button type="button" className="btn secondary small" onClick={() => void verify()}>
            Verify integrity
          </button>
        </div>
        {integrity ? (
          integrity.ok ? (
            <Callout tone="pass">
              Evidence hashes match and the audit chain is intact (checked {formatDateTime(Date.parse(integrity.checked_at))} UTC).
            </Callout>
          ) : (
            <Callout tone="fail" title="Integrity check failed">
              {integrity.artifacts.filter((a) => a.status !== 'ok').map((a) => `${a.original_name}: ${a.status}. `)}
              {!integrity.audit_chain_ok ? `Audit chain broken at entry #${integrity.audit_first_bad_entry}.` : ''}
            </Callout>
          )
        ) : null}
        {report && report.artifacts.length > 0 ? (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Artifact</th>
                  <th scope="col">Parser</th>
                  <th scope="col">SHA-256</th>
                  <th scope="col" className="num">
                    Size
                  </th>
                </tr>
              </thead>
              <tbody>
                {report.artifacts.map((a) => (
                  <tr key={a.id}>
                    <td>{a.original_name}</td>
                    <td className="mono">{a.parser}</td>
                    <td className="mono hash" title={a.sha256}>
                      {shortHash(a.sha256, 16, 8)}
                    </td>
                    <td className="num">{formatBytes(a.size_bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {auditEntries.length > 0 ? (
          <div className="table-wrap audit">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col" className="num">
                    #
                  </th>
                  <th scope="col">UTC</th>
                  <th scope="col">Actor</th>
                  <th scope="col">Action</th>
                  <th scope="col">Detail</th>
                </tr>
              </thead>
              <tbody>
                {auditEntries.map((a) => (
                  <tr key={a.id}>
                    <td className="num mono">{a.id}</td>
                    <td className="mono nowrap">{formatDateTime(Date.parse(a.ts))}</td>
                    <td>{a.actor || '—'}</td>
                    <td className="mono">{a.action}</td>
                    <td className="mono small-cell">
                      {Object.entries(a.detail)
                        .map(([k, v]) => `${k}=${typeof v === 'string' ? shortHash(v, 24, 0) : JSON.stringify(v)}`)
                        .join(' · ')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  )
}
