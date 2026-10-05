import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { type AdbDevice, type DeviceExport, type IngestResult, ApiError, api } from '../api/client'
import { formatCount } from '../lib/format'

type Props = {
  caseId: string
  onImported: (result: IngestResult) => void
}

const POLL_MS = 2000

/**
 * Live device import: polls ADB for a connected phone, pulls dumpsys usagestats, and lists
 * ingestible exports from the public Download folder (Takeout JSON, GPX, History DB, etc.).
 * Private app data (Chrome profile DB under /data/data) still needs a manual file export or root.
 */
export function DeviceUsagePanel({ caseId, onImported }: Props) {
  const [open, setOpen] = useState(false)
  const [devices, setDevices] = useState<AdbDevice[] | null>(null)
  const [exports, setExports] = useState<DeviceExport[] | null>(null)
  const [adbMissing, setAdbMissing] = useState<string | null>(null)
  const [pulling, setPulling] = useState(false)
  const [result, setResult] = useState<IngestResult | null>(null)
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

  async function pullExport(serial: string, filename: string) {
    setPulling(true)
    setError(null)
    setResult(null)
    try {
      const r = await api.pullDeviceExport(caseId, serial, filename)
      setResult(r)
      onImported(r)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Pull failed')
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
                <button type="button" className="btn accent small" onClick={() => void pullUsage(d.serial)}>
                  Pull app usage
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {ready && exports && exports.length > 0 && !pulling ? (
        <div className="device-exports">
          <p className="muted small">Download folder exports (location / browsing files you copied onto the phone):</p>
          <ul className="device-list">
            {exports.map((file) => (
              <li key={file.name} className="device-row">
                <span className="mono">{file.name}</span>
                <button
                  type="button"
                  className="btn secondary small"
                  onClick={() => void pullExport(ready.serial, file.name)}
                >
                  Pull &amp; ingest
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {error ? <div className="error-box">{error}</div> : null}
      {result ? (
        <p className="ok-text small">
          {result.duplicate ? (
            'Already pulled from this device (same data). Nothing added.'
          ) : (
            <>
              Imported {formatCount(result.events_added)} events ({result.artifact.source_type}). Open{' '}
              <Link to="/timeline">Timeline</Link> to inspect them.
            </>
          )}
        </p>
      ) : null}

      {!ready ? (
        <details className="formats">
          <summary>Phone not showing up?</summary>
          <ol>
            <li>
              Fuseline bundles <span className="mono">adb</span> under{" "}
              <span className="mono">tools/platform-tools/</span> (run{" "}
              <span className="mono">python scripts/ensure_platform_tools.py</span> once if missing)
            </li>
            <li>Settings → About phone → tap &quot;Build number&quot; 7 times to unlock Developer options</li>
            <li>Settings → Developer options → turn on USB debugging</li>
            <li>Connect via USB and accept the authorisation prompt on the phone screen</li>
            <li>
              Live pulls: app usage via dumpsys, plus supported files from Download (Takeout JSON, GPX, CSV,
              History). Private browser DBs under /data/data still need a manual export.
            </li>
          </ol>
        </details>
      ) : null}
    </div>
  )
}
