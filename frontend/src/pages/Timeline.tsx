import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { type Session, type TimelineEvent, api } from '../api/client'
import { Callout } from '../components/Callout'
import { CorrelationSettings } from '../components/CorrelationSettings'
import { EventDrawer } from '../components/EventDrawer'
import { EventTable } from '../components/EventTable'
import { MapPanel } from '../components/MapPanel'
import { NoCase } from '../components/NoCase'
import { OverviewBrush } from '../components/OverviewBrush'
import { SessionCard } from '../components/SessionCard'
import { SourceChip } from '../components/SourceChip'
import { TimelineChart } from '../components/TimelineChart'
import { useDebounced } from '../hooks/useDebounced'
import { formatCount } from '../lib/format'
import { SOURCES } from '../lib/sources'
import { formatDateTime, formatDuration, padRange, zoneLabel } from '../lib/time'
import { useCase } from '../state/CaseContext'
import { useTimelineData } from './timeline/useTimelineData'

const TZ_KEY = 'fuseline.tzMode'

function readTzMode(): 'utc' | 'case' {
  try {
    return localStorage.getItem(TZ_KEY) === 'case' ? 'case' : 'utc'
  } catch {
    return 'utc'
  }
}

export function TimelinePage() {
  const { caseId, activeCase } = useCase()
  if (!caseId) return <NoCase title="Timeline" />
  return <Workspace key={caseId} caseId={caseId} caseTimezone={activeCase?.timezone ?? 'UTC'} />
}

function Workspace({ caseId, caseTimezone }: { caseId: string; caseTimezone: string }) {
  const [enabled, setEnabled] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(SOURCES.map((s) => [s, true])),
  )
  const [query, setQuery] = useState('')
  const debouncedQuery = useDebounced(query.trim(), 300)
  const [tzMode, setTzMode] = useState<'utc' | 'case'>(readTzMode)
  const [selected, setSelected] = useState<TimelineEvent | null>(null)
  const [activeSession, setActiveSession] = useState<Session | null>(null)
  const [sessionEvents, setSessionEvents] = useState<TimelineEvent[] | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [sideTab, setSideTab] = useState<'sessions' | 'map'>('sessions')

  const timeZone = tzMode === 'case' ? caseTimezone : 'UTC'
  const canSwitchZone = caseTimezone !== 'UTC'

  // Filters: `null` means "everything", so toggling nothing never triggers a refetch.
  const [presentKey, setPresentKey] = useState('')
  const present = useMemo(() => SOURCES.filter((s) => presentKey.split(',').includes(s)), [presentKey])
  const activeSources = present.filter((s) => enabled[s])
  const sourceFilter = present.length === 0 || activeSources.length === present.length ? null : activeSources

  const data = useTimelineData(caseId, { sources: sourceFilter, q: debouncedQuery })

  useEffect(() => {
    const key = SOURCES.filter((s) => (data.sourceCounts[s] ?? 0) > 0).join(',')
    setPresentKey((prev) => (prev === key ? prev : key))
  }, [data.sourceCounts])

  const highlightIds = useMemo(
    () => (activeSession ? new Set(activeSession.member_event_ids) : null),
    [activeSession],
  )

  const focusSession = useCallback(
    (session: Session | null) => {
      if (!session || session.id === activeSession?.id) {
        setActiveSession(null)
        setSessionEvents(null)
        return
      }
      setActiveSession(session)
      setSessionEvents(null)
      setSideTab('sessions')
      const a = Date.parse(session.start_utc)
      const b = Date.parse(session.end_utc)
      data.setView(padRange(a, b, 0.6, 60_000))
      api
        .sessionEvents(caseId, session.id)
        .then(setSessionEvents)
        .catch(() => setSessionEvents([]))
    },
    [activeSession?.id, caseId, data],
  )

  const selectById = useCallback(
    async (id: string) => {
      const known = data.events.find((e) => e.id === id) ?? sessionEvents?.find((e) => e.id === id)
      setSelected(known ?? (await api.event(caseId, id).catch(() => null)))
    },
    [caseId, data.events, sessionEvents],
  )

  function toggleZone(mode: 'utc' | 'case') {
    setTzMode(mode)
    try {
      localStorage.setItem(TZ_KEY, mode)
    } catch {
      /* storage unavailable */
    }
  }

  const rebuilt = useCallback(() => {
    setActiveSession(null)
    setSessionEvents(null)
    data.refresh()
  }, [data])

  const totalInCase = Object.values(data.sourceCounts).reduce((a, b) => a + b, 0)
  const truncated = data.marks === null && data.total > 0
  const warnings = data.findings.filter((f) => f.severity === 'warn' || f.severity === 'fail')

  const tableEvents = activeSession ? (sessionEvents ?? []) : data.events
  const tableTotal = activeSession ? (sessionEvents?.length ?? 0) : data.total
  const points = enabled.location === false ? [] : (data.locations?.points ?? [])

  // "Empty" comes from the overview's own total (not the per-source counts, which arrive a beat later),
  // and only when no filter is hiding events.
  const caseEmpty =
    data.overview?.total === 0 && debouncedQuery === '' && sourceFilter === null && data.sessions.length === 0
  const initialising = !data.ready || (totalInCase === 0 && !caseEmpty && !data.error)

  if (data.ready && caseEmpty && !data.error) {
    return (
      <section className="panel">
        <h1>Timeline</h1>
        <div className="empty">
          This case has no events yet. <Link to="/acquire">Acquire evidence</Link> from files or a connected device.
        </div>
      </section>
    )
  }

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-head">
          <div>
            <h1>Timeline</h1>
            <p className="muted">
              {formatCount(totalInCase)} events · {data.sessions.length} proximity sessions · times in{' '}
              <strong>{zoneLabel(timeZone)}</strong>
            </p>
          </div>
          <div className="row">
            {canSwitchZone ? (
              <div className="segmented" role="group" aria-label="Display timezone">
                <button
                  type="button"
                  className={tzMode === 'utc' ? 'on' : ''}
                  aria-pressed={tzMode === 'utc'}
                  onClick={() => toggleZone('utc')}
                >
                  UTC
                </button>
                <button
                  type="button"
                  className={tzMode === 'case' ? 'on' : ''}
                  aria-pressed={tzMode === 'case'}
                  onClick={() => toggleZone('case')}
                  title={caseTimezone}
                >
                  Case zone
                </button>
              </div>
            ) : null}
            <button
              type="button"
              className="btn secondary small"
              aria-expanded={settingsOpen}
              onClick={() => setSettingsOpen((v) => !v)}
            >
              Correlation…
            </button>
            <button type="button" className="btn secondary small" disabled={data.loading} onClick={data.refresh}>
              {data.loading ? 'Loading…' : 'Refresh'}
            </button>
          </div>
        </div>

        {settingsOpen ? (
          <div className="popover">
            <CorrelationSettings caseId={caseId} onRebuilt={rebuilt} />
          </div>
        ) : null}

        <div className="toolbar">
          {present.map((s) => (
            <label key={s} className="check-label">
              <input
                type="checkbox"
                checked={enabled[s] ?? true}
                onChange={(e) => setEnabled((prev) => ({ ...prev, [s]: e.target.checked }))}
              />
              <SourceChip source={s} />
              <span className="mono muted">{formatCount(data.sourceCounts[s] ?? 0)}</span>
            </label>
          ))}
          <input
            type="search"
            className="grow"
            placeholder="Filter by title, package, URL or domain"
            aria-label="Filter events"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>

        {data.error ? <div className="error-box">{data.error}</div> : null}
        {warnings.length > 0 ? (
          <Callout tone="warn" title={`${warnings.length} validation warning${warnings.length === 1 ? '' : 's'}`}>
            {warnings.slice(0, 2).map((f) => (
              <div key={f.id}>{f.message}</div>
            ))}
            <Link to="/report">See all findings in the report</Link>
          </Callout>
        ) : null}

        {initialising ? <div className="empty compact">Loading timeline…</div> : null}
        <div className="chart-block" hidden={initialising}>
          <OverviewBrush
            overview={data.overview}
            lanes={activeSources}
            bounds={data.bounds}
            view={data.view}
            onViewChange={data.setView}
            timeZone={timeZone}
          />
          <TimelineChart
            view={data.view}
            bounds={data.bounds}
            onViewChange={data.setView}
            lanes={activeSources}
            density={data.density}
            marks={data.marks}
            sessions={data.sessions}
            activeSessionId={activeSession?.id ?? null}
            highlightIds={highlightIds}
            selectedId={selected?.id ?? null}
            onSelectEvent={setSelected}
            onSelectSession={focusSession}
            timeZone={timeZone}
          />
        </div>

        <div className="chart-foot">
          <span className="muted small">
            Scroll to zoom · drag to pan · double-click to zoom in · shaded bands are proximity sessions
          </span>
          <span className="mono muted small">
            {formatDateTime(data.view.start, timeZone)} → {formatDateTime(data.view.end, timeZone)} (
            {formatDuration(data.view.end - data.view.start)})
          </span>
          {data.isZoomed ? (
            <button type="button" className="btn secondary small" onClick={() => data.setView(null)}>
              Show everything
            </button>
          ) : null}
        </div>
        {truncated ? (
          <Callout tone="info">
            {formatCount(data.total)} events in this window. Too many to draw one by one. The chart shows density;
            zoom in (or use the brush above) to see individual events.
          </Callout>
        ) : null}
      </section>

      <div className="workspace-grid">
        <div className="stack">
          <section className="panel">
            <EventDrawer
              event={selected}
              artifacts={data.artifacts}
              timeZone={timeZone}
              onClose={() => setSelected(null)}
            />
          </section>
          <section className="panel">
            <h2>{activeSession ? 'Session events' : 'Events in view'}</h2>
            <EventTable
              events={tableEvents}
              total={tableTotal}
              hasMore={!activeSession && data.hasMore}
              loading={data.loadingMore || (activeSession !== null && sessionEvents === null)}
              selectedId={selected?.id ?? null}
              highlightIds={highlightIds}
              timeZone={timeZone}
              onSelect={setSelected}
              onLoadMore={data.loadMore}
            />
          </section>
        </div>

        <aside className="panel">
          <div className="panel-head">
            <h2 className="sr-only">Sessions and map</h2>
            <div className="segmented" role="tablist" aria-label="Sessions or map">
              <button
                type="button"
                role="tab"
                aria-selected={sideTab === 'sessions'}
                className={sideTab === 'sessions' ? 'on' : ''}
                onClick={() => setSideTab('sessions')}
              >
                Sessions{data.sessions.length > 0 ? ` (${data.sessions.length})` : ''}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={sideTab === 'map'}
                className={sideTab === 'map' ? 'on' : ''}
                onClick={() => setSideTab('map')}
              >
                Map
              </button>
            </div>
          </div>

          {sideTab === 'sessions' ? (
            data.sessions.length === 0 ? (
              <div className="empty compact">
                No multi-source clusters. Ingest at least two source types, or widen the window under “Correlation…”.
              </div>
            ) : (
              <div className="session-list">
                {data.sessions.map((s) => (
                  <SessionCard
                    key={s.id}
                    session={s}
                    active={activeSession?.id === s.id}
                    timeZone={timeZone}
                    onSelect={focusSession}
                  />
                ))}
              </div>
            )
          ) : (
            <MapPanel
              points={points}
              total={data.locations?.total ?? 0}
              selectedId={selected?.id ?? null}
              highlightIds={highlightIds}
              activeSession={activeSession}
              onSelectPoint={(id) => void selectById(id)}
              timeZone={timeZone}
            />
          )}
        </aside>
      </div>
    </div>
  )
}
