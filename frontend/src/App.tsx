import { useEffect, useState } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { api } from './api/client'
import { ErrorBoundary } from './components/ErrorBoundary'
import { useTheme } from './hooks/useTheme'
import { AcquirePage } from './pages/Acquire'
import { CasesPage } from './pages/Cases'
import { ReportPage } from './pages/Report'
import { TimelinePage } from './pages/Timeline'
import { useCase } from './state/CaseContext'

const THEME_LABEL = { system: 'Auto', light: 'Light', dark: 'Dark' } as const

export default function App() {
  const { caseId, activeCase } = useCase()
  const { preference, cycle } = useTheme()
  const [apiOk, setApiOk] = useState<boolean | null>(null)
  const [version, setVersion] = useState<string | null>(null)

  // Probe the backend now, and keep probing every few seconds while it is unreachable.
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const probe = async () => {
      try {
        const h = await api.health()
        if (cancelled) return
        setApiOk(true)
        setVersion(h.version)
      } catch {
        if (cancelled) return
        setApiOk(false)
        timer = setTimeout(probe, 4000)
      }
    }
    void probe()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [])

  const link = ({ isActive }: { isActive: boolean }) => (isActive ? 'active' : undefined)

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <div className="brand">
          <div className="brand-name">Fuseline</div>
          <div className="brand-sub">Mobile forensic timeline{version ? ` · v${version}` : ''}</div>
        </div>
        <nav className="nav" aria-label="Workflow">
          <NavLink to="/" end className={link}>
            Cases
          </NavLink>
          <NavLink to="/acquire" className={link}>
            Acquire
          </NavLink>
          <NavLink to="/timeline" className={link}>
            Timeline
          </NavLink>
          <NavLink to="/report" className={link}>
            Report
          </NavLink>
        </nav>
        <div className="top-actions">
          <div className="case-chip" title={caseId ?? undefined}>
            {apiOk === false ? 'API offline' : activeCase ? activeCase.name : 'no case selected'}
            {activeCase && apiOk !== false ? <span className="muted"> · {activeCase.timezone}</span> : null}
          </div>
          <button
            type="button"
            className="btn ghost small"
            onClick={cycle}
            title="Switch between automatic, light and dark themes"
          >
            Theme: {THEME_LABEL[preference]}
          </button>
        </div>
      </header>
      <main id="main" className="main">
        {apiOk === false ? (
          <div className="error-box banner">
            Backend unreachable. Retrying. Start it with <span className="mono">scripts/run_dev.ps1</span> or{' '}
            <span className="mono">uvicorn app.main:app --app-dir backend</span>.
          </div>
        ) : null}
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<CasesPage />} />
            <Route path="/acquire" element={<AcquirePage />} />
            <Route path="/timeline" element={<TimelinePage />} />
            <Route path="/report" element={<ReportPage />} />
            <Route
              path="*"
              element={
                <section className="panel">
                  <h1>Page not found</h1>
                  <p className="muted">That address does not exist.</p>
                </section>
              }
            />
          </Routes>
        </ErrorBoundary>
      </main>
    </div>
  )
}
