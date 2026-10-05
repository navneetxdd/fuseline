import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  type AdbDevice,
  type DeviceBundleResult,
  type DeviceExport,
  type IngestResult,
  ApiError,
  api,
} from '../api/client'
import { formatCount } from '../lib/format'

type Props = {
  caseId: string
  onImported: (result: IngestResult) => void
}

const POLL_MS = 2000

/**
 * Live Android import over USB: dumpsys usagestats + location, deep scan of Download/Documents
 * (nested Takeout, GPX, History copies), or one-click bundle. Private /data/data still needs export.
 */
export function DeviceUsagePanel({ caseId, onImported }: Props) {
  const [open, setOpen] = useState(false)
  const [devices, setDevices] = useState<AdbDevice[] | null>(null)
  const [exports, setExports] = useState<DeviceExport[] | null>(null)
  const [adbMissing, setAdbMissing] = useState<string | null>(null)
  const [pulling, setPulling] = useState(false)
  const [result, setResult] = useState<IngestResult | null>(null)
  const [bundle, setBundle] = useState<DeviceBundleResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    const poll = async () => {
      try {
        const found = await api.devices()
        if (cancelled) return
        setDevices(found)
        setAdbMissing(null)
        const ready = found.find((d) => d.ready)
        if (ready) {
          try {
            const listed = await api.deviceExports(ready.serial)
            if (!cancelled) setExports(listed)
          } catch {
            if (!cancelled) setExports([])
          }
        } else if (!cancelled) {
          setExports(null)
        }
      } catch (err) {
        if (cancelled) return
        if (err instanceof ApiError && err.status === 503) setAdbMissing(err.message)
        else setDevices([])
      }
      if (!cancelled) timer.current = setTimeout(poll, POLL_MS)
    }
    void poll()
    return () => {
      cancelled = true
      clearTimeout(timer.current)
    }
  }, [open])

  async function pullUsage(serial: string) {
    setPulling(true)
    setError(null)
    setResult(null)
    setBundle(null)
    try {
      const r = await api.pullDeviceAppUsage(caseId, serial)
      setResult(r)
      onImported(r)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Pull failed')
    } finally {
      setPulling(false)
    }
  }

  async function pullLocation(serial: string) {
    setPulling(true)
    setError(null)
    setResult(null)
    setBundle(null)
    try {
      const r = await api.pullDeviceLocation(caseId, serial)
      setResult(r)
      onImported(r)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Location pull failed')
    } finally {
      setPulling(false)
    }
  }

  async function pullExport(serial: string, file: DeviceExport) {
    setPulling(true)
    setError(null)
    setResult(null)
    setBundle(null)
    try {
      const r = await api.pullDeviceExport(caseId, serial, file.remote_path)
      setResult(r)
      onImported(r)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Pull failed')
    } finally {
      setPulling(false)
    }
  }

  async function pullBundle(serial: string) {
    setPulling(true)
    setError(null)
    setResult(null)
    setBundle(null)
    try {
      const r = await api.pullDeviceBundle(caseId, serial)
      setBundle(r)
      if (r.total_events_added > 0) {
        onImported({
          artifact: {
            id: '',
            original_name: 'device bundle',
            source_type: 'mixed',
            parser: '',
            sha256: '',
            size_bytes: 0,
            row_count: r.total_events_added,
            skipped_rows: 0,
            ingested_at: new Date().toISOString(),
            notes: [],
          },
          events_added: r.total_events_added,
          sessions_rebuilt: r.sessions_rebuilt,
          findings: [],
          duplicate: false,
        })
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Bundle pull failed')
    } finally {
      setPulling(false)
    }
  }

  if (!open) {
    return (
      <button type="button" className="btn secondary small" onClick={() => setOpen(true)}>
        Detect device…
      </button>
    )
  }

  const ready = devices?.find((d) => d.ready)

  return (
    <div className="device-panel">
      <div className="device-panel-head">
        <span className="scan-dot" aria-hidden="true" />
        <span className="mono small">{adbMissing ? 'adb unavailable' : 'Watching for a connected phone…'}</span>
        <button type="button" className="btn ghost small" onClick={() => setOpen(false)}>
          Close
        </button>
      </div>

      {adbMissing ? (
        <p className="muted small">{adbMissing}</p>
      ) : devices === null ? (
        <p className="muted small">Checking…</p>
      ) : devices.length === 0 ? (
        <p className="muted small">
          No device detected. Plug your phone in via USB with debugging enabled. Setup steps are below.
        </p>
      ) : (
        <ul className="device-list">
          {devices.map((d) => (
            <li key={d.serial} className="device-row">
              <span className={`sev ${d.ready ? 'sev-pass' : 'sev-warn'}`}>{d.state}</span>
              <span className="mono">{d.model ?? d.serial}</span>
              {!d.ready ? (
                <span className="muted small">Accept the USB debugging prompt on the phone screen.</span>
              ) : pulling ? (
                <span className="scan-bar" aria-label="Reading from device" />
              ) : (
                <span className="device-actions">
                  <button type="button" className="btn accent small" onClick={() => void pullBundle(d.serial)}>
                    Acquire all available
                  </button>
                  <button type="button" className="btn secondary small" onClick={() => void pullUsage(d.serial)}>
                    App usage
                  </button>
                  <button type="button" className="btn secondary small" onClick={() => void pullLocation(d.serial)}>
                    Location dump
                  </button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {ready && exports && exports.length > 0 && !pulling ? (
        <div className="device-exports">
          <p className="muted small">
            Shared storage (Download / Documents, including nested Takeout folders):
          </p>
          <ul className="device-list">
            {exports.map((file) => (
              <li key={file.remote_path} className="device-row">
                <span className="mono">{file.display_path}</span>
                <button
                  type="button"
                  className="btn secondary small"
                  onClick={() => void pullExport(ready.serial, file)}
                >
                  Pull &amp; ingest
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : ready && exports && exports.length === 0 && !pulling ? (
        <p className="muted small">
          No supported exports on shared storage yet. Copy Takeout JSON, GPX, or a History DB into Download, or upload
          from your PC.
        </p>
      ) : null}

      {error ? <div className="error-box">{error}</div> : null}
      {result ? (
        <p className="ok-text small">
          {result.duplicate ? (
            'Already pulled from this device (same data). Nothing added.'
          ) : result.events_added === 0 ? (
            'Pull completed but no timeline events were parsed (common for location on locked-down builds).'
          ) : (
            <>
              Imported {formatCount(result.events_added)} events ({result.artifact.source_type}). Open{' '}
              <Link to="/timeline">Timeline</Link> to inspect them.
            </>
          )}
        </p>
      ) : null}
      {bundle ? (
        <div className="device-bundle-summary">
          <p className="ok-text small">
            Bundle finished: {formatCount(bundle.total_events_added)} new events across{' '}
            {bundle.items.filter((i) => i.ok).length} successful pull(s).
          </p>
          <ul className="muted small">
            {bundle.items.map((item) => (
              <li key={`${item.kind}-${item.label}`}>
                {item.ok ? (
                  <>
                    {item.label}: {item.duplicate ? 'unchanged (duplicate)' : `+${formatCount(item.events_added)}`}
                    {item.source_type ? ` (${item.source_type})` : ''}
                  </>
                ) : (
                  <>
                    {item.label}: failed — {item.error}
                  </>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {!ready ? (
        <details className="formats">
          <summary>Phone not showing up?</summary>
          <ol>
            <li>
              Fuseline bundles <span className="mono">adb</span> under{' '}
              <span className="mono">tools/platform-tools/</span> (run{' '}
              <span className="mono">python scripts/ensure_platform_tools.py</span> once if missing)
            </li>
            <li>Settings → About phone → tap &quot;Build number&quot; 7 times to unlock Developer options</li>
            <li>Settings → Developer options → turn on USB debugging</li>
            <li>Connect via USB and accept the authorisation prompt on the phone screen</li>
            <li>
              <strong>Acquire all available</strong> pulls app usage, a location dumpsys snapshot, and every supported
              file under Download/Documents (including nested Google Takeout). Chrome&apos;s private profile DB still
              requires exporting History to Download or uploading from a PC.
            </li>
          </ol>
        </details>
      ) : null}
    </div>
  )
}
