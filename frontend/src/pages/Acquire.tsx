import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { type Artifact, type IngestResult, type IntegrityResult, type Meta, api } from '../api/client'
import { Callout } from '../components/Callout'
import { DeviceUsagePanel } from '../components/DeviceUsagePanel'
import { NoCase } from '../components/NoCase'
import { SourceCard } from '../components/SourceCard'
import { SourceChip } from '../components/SourceChip'
import { CopyButton } from '../components/CopyButton'
import { formatBytes, formatCount, shortHash } from '../lib/format'
import { formatDateTime } from '../lib/time'
import { SOURCES } from '../lib/sources'
import { useCase } from '../state/CaseContext'

const HINTS = [
  { value: 'auto', label: 'Auto-detect (recommended)' },
  { value: 'browsing', label: 'Browsing history' },
  { value: 'app_usage', label: 'App usage' },
  { value: 'location', label: 'Location' },
  { value: 'plaso', label: 'Plaso timeline' },
]

const SOURCE_META: Record<(typeof SOURCES)[number], { label: string; help: string }> = {
  location: { label: 'Location', help: 'GPS track, GPX, or Takeout Records.json' },
  browsing: { label: 'Browsing', help: 'Chrome, Edge, or Firefox history' },
  app_usage: { label: 'App usage', help: 'UsageStats file, or a phone over USB' },
  plaso: { label: 'Plaso', help: 'psort L2TCSV or JSON lines' },
}

type QueueItem = {
  id: string
  name: string
  size: number
  status: 'queued' | 'uploading' | 'done' | 'error'
  progress: number
  result?: IngestResult
  error?: string
}

export function AcquirePage() {
  const { caseId, refreshCase } = useCase()
  if (!caseId) return <NoCase title="Acquire" />
  return <Acquire key={caseId} caseId={caseId} onChanged={refreshCase} />
}

function Acquire({ caseId, onChanged }: { caseId: string; onChanged: () => Promise<void> }) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [meta, setMeta] = useState<Meta | null>(null)
  const [bulkHint, setBulkHint] = useState('auto')
  const [dragOverCard, setDragOverCard] = useState<string | null>(null)
  const [bulkDragOver, setBulkDragOver] = useState(false)
  const [queue, setQueue] = useState<QueueItem[]>([])
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [integrity, setIntegrity] = useState<IntegrityResult | null>(null)
  const [verifying, setVerifying] = useState(false)

  const pending = useRef<{ id: string; file: File; hint: string }[]>([])
  const draining = useRef(false)

  const refresh = useCallback(async () => {
    try {
      setArtifacts(await api.listArtifacts(caseId))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load artifacts')
    }
  }, [caseId])

  useEffect(() => {
    void refresh()
    api
      .meta()
      .then(setMeta)
      .catch(() => undefined)
  }, [refresh])

  const patch = (id: string, change: Partial<QueueItem>) =>
    setQueue((items) => items.map((it) => (it.id === id ? { ...it, ...change } : it)))

  const drain = useCallback(async () => {
    if (draining.current) return
    draining.current = true
    try {
      while (pending.current.length > 0) {
        const next = pending.current.shift()
        if (!next) break
        patch(next.id, { status: 'uploading', progress: 0 })
        try {
          const result = await api.upload(caseId, next.file, next.hint, (p) => patch(next.id, { progress: p }))
          patch(next.id, { status: 'done', progress: 1, result })
        } catch (err) {
          patch(next.id, { status: 'error', error: err instanceof Error ? err.message : 'Upload failed' })
        }
      }
    } finally {
      draining.current = false
      setIntegrity(null)
      await refresh()
      await onChanged()
    }
  }, [caseId, refresh, onChanged])

  function enqueue(files: File[], hint: string) {
    if (files.length === 0) return
    setMessage(null)
    setError(null)
    const limit = meta?.max_upload_bytes ?? Number.POSITIVE_INFINITY
    const items: QueueItem[] = files.map((file) => ({
      id: crypto.randomUUID(),
      name: file.name,
      size: file.size,
      status: file.size > limit ? 'error' : 'queued',
      progress: 0,
      error: file.size > limit ? `Larger than the ${formatBytes(limit)} upload limit.` : undefined,
    }))
    setQueue((prev) => [...items, ...prev])
    files.forEach((file, i) => {
      if (items[i].status === 'queued') pending.current.push({ id: items[i].id, file, hint })
    })
    void drain()
  }


  async function verify() {
    setVerifying(true)
    setError(null)
    try {
      setIntegrity(await api.verify(caseId))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Verification failed')
    } finally {
      setVerifying(false)
    }
  }

  const byCategory = useMemo(() => {
    const map = new Map<string, Artifact[]>()
    for (const s of SOURCES) map.set(s, [])
    for (const a of artifacts) (map.get(a.source_type) ?? map.set(a.source_type, []).get(a.source_type)!).push(a)
    return map
  }, [artifacts])

  const integrityBySource = useMemo(
    () => new Map(integrity?.artifacts.map((a) => [a.artifact_id, a.status]) ?? []),
    [integrity],
  )

  const busy = queue.some((q) => q.status === 'uploading' || q.status === 'queued')

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-head">
          <div>
            <h1>Acquire</h1>
            <p className="muted small">
              Each file is hashed with SHA-256, stored read-only, and recognised from its content.
            </p>
          </div>
        </div>

        {message ? <Callout tone="pass">{message}</Callout> : null}
        {error ? <div className="error-box">{error}</div> : null}

        <div className="source-grid">
          {SOURCES.map((source) => {
            const meta_ = SOURCE_META[source]
            const list = byCategory.get(source) ?? []
            return (
              <SourceCard
                key={source}
                source={source}
                label={meta_.label}
                help={meta_.help}
                artifacts={list}
                totalEvents={list.reduce((n, a) => n + a.row_count, 0)}
                integrityBySource={integrityBySource}
                dragOver={dragOverCard === source}
                onDragOver={() => setDragOverCard(source)}
                onDragLeave={() => setDragOverCard(null)}
                onDrop={(files) => {
                  setDragOverCard(null)
                  enqueue(files.slice(0, 1), source)
                }}
                onChoose={(files) => enqueue(files.slice(0, 1), source)}
                disabled={busy}
              />
            )
          })}
        </div>

        {artifacts.length > 0 ? (
          <div className="acquired-files">
            <h2>Acquired files</h2>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th scope="col">Source</th>
                    <th scope="col">File</th>
                    <th scope="col" className="num">
                      Events
                    </th>
                    <th scope="col">Ingested</th>
                    <th scope="col">SHA-256</th>
                  </tr>
                </thead>
                <tbody>
                  {artifacts.map((artifact) => {
                    const status = integrityBySource.get(artifact.id)
                    return (
                      <tr key={artifact.id}>
                        <td>
                          <SourceChip source={artifact.source_type} />
                        </td>
                        <td>
                          {artifact.original_name}
                          <div className="cell-sub">
                            {formatBytes(artifact.size_bytes)}
                            {artifact.skipped_rows > 0 ? ` · ${formatCount(artifact.skipped_rows)} skipped` : ''}
                          </div>
                        </td>
                        <td className="num mono">{formatCount(artifact.row_count)}</td>
                        <td className="mono nowrap">{formatDateTime(Date.parse(artifact.ingested_at))}</td>
                        <td>
                          <div className="hash-row">
                            <code className="hash-text mono" title={artifact.sha256}>
                              {shortHash(artifact.sha256, 10, 8)}
                            </code>
                            <CopyButton value={artifact.sha256} label="Copy hash" className="copy-hash" />
                            {status ? (
                              <span className={`sev sev-${status === 'ok' ? 'pass' : 'fail'}`}>{status}</span>
                            ) : null}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        <div className="phone-acquire">
          <DeviceUsagePanel
            caseId={caseId}
            onImported={() => {
              setIntegrity(null)
              void refresh()
              void onChanged()
            }}
          />
        </div>

        {queue.length > 0 ? (
          <ul className="queue" aria-label="Upload results">
            {queue.map((item) => (
              <li key={item.id} className={`queue-item ${item.status}`}>
                <div className="queue-main">
                  <span className="queue-name">{item.name}</span>
                  <span className="mono muted">{formatBytes(item.size)}</span>
                </div>
                <div className="queue-status">
                  {item.status === 'queued' ? <span className="muted">Waiting…</span> : null}
                  {item.status === 'uploading' ? (
                    <progress value={item.progress} max={1} aria-label={`Uploading ${item.name}`} />
                  ) : null}
                  {item.status === 'error' ? <span className="bad-text">{item.error}</span> : null}
                  {item.status === 'done' && item.result ? <QueueResult result={item.result} /> : null}
                </div>
              </li>
            ))}
          </ul>
        ) : null}

        <details className="formats">
          <summary>Advanced: bulk upload / unusual files</summary>
          <div className="row wrap-gap" style={{ marginTop: '0.6rem' }}>
            <label className="inline-field">
              Source hint
              <select value={bulkHint} onChange={(e) => setBulkHint(e.target.value)} disabled={busy}>
                {HINTS.map((h) => (
                  <option key={h.value} value={h.value}>
                    {h.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div
            className={`dropzone${bulkDragOver ? ' active' : ''}`}
            style={{ marginTop: '0.6rem' }}
            onDragOver={(e) => {
              e.preventDefault()
              setBulkDragOver(true)
            }}
            onDragLeave={() => setBulkDragOver(false)}
            onDrop={(e) => {
              e.preventDefault()
              setBulkDragOver(false)
              enqueue(Array.from(e.dataTransfer.files), bulkHint)
            }}
          >
            <p>
              <strong>Drop any number of files here</strong>, or
            </p>
            <label className="btn secondary file-btn">
              Choose files…
              <input
                type="file"
                multiple
                onChange={(e) => {
                  enqueue(Array.from(e.target.files ?? []), bulkHint)
                  e.target.value = ''
                }}
              />
            </label>
            {meta ? <p className="muted small">Up to {formatBytes(meta.max_upload_bytes)} per file.</p> : null}
          </div>
        </details>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Integrity</h2>
          <button
            type="button"
            className="btn secondary small"
            disabled={verifying || artifacts.length === 0}
            onClick={() => void verify()}
          >
            {verifying ? 'Verifying…' : 'Verify integrity'}
          </button>
        </div>
        {integrity ? (
          integrity.ok ? (
            <Callout tone="pass" title="Integrity verified">
              All {integrity.artifacts.length} stored artifacts match the hashes recorded at acquisition, and the
              audit chain is intact.
            </Callout>
          ) : (
            <Callout tone="fail" title="Integrity check failed">
              {integrity.artifacts.filter((a) => a.status !== 'ok').length > 0
                ? 'One or more stored evidence copies no longer match their recorded hash. '
                : ''}
              {!integrity.audit_chain_ok
                ? `The audit log was altered (first bad entry #${integrity.audit_first_bad_entry}).`
                : ''}
            </Callout>
          )
        ) : (
          <p className="muted small">Re-hashes every stored evidence copy and checks the audit chain.</p>
        )}
      </section>

    </div>
  )
}

function QueueResult({ result }: { result: IngestResult }) {
  if (result.duplicate) {
    return (
      <span className="muted">
        Already in this case (identical SHA-256 to <strong>{result.artifact.original_name}</strong>). Nothing
        added.
      </span>
    )
  }
  const a = result.artifact
  return (
    <div>
      <span className="ok-text">
        Ingested as <SourceChip source={a.source_type} /> · {formatCount(result.events_added)} events
        {a.skipped_rows > 0 ? ` · ${formatCount(a.skipped_rows)} rows skipped` : ''}
      </span>
      {a.notes.map((n) => (
        <div key={n} className="cell-sub warn-text">
          {n}
        </div>
      ))}
    </div>
  )
}
