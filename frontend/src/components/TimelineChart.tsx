import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent, MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent } from 'react'
import type { Overview, Session, TimelineEvent } from '../api/client'
import { useElementWidth } from '../hooks/useElementSize'
import { SOURCE_LABEL, laneColor } from '../lib/sources'
import {
  type Range,
  clamp,
  formatDateTime,
  formatTimeOfDay,
  nearestIndex,
  niceTicks,
  panRange,
  zoomRange,
} from '../lib/time'

export const LABEL_W = 92
const PAD_R = 14
const AXIS_H = 30
const LANE_H = 62
const LANE_GAP = 8
const HIT_PX = 8
const DRAG_THRESHOLD_PX = 3

type Hover =
  | { kind: 'event'; x: number; y: number; event: TimelineEvent }
  | { kind: 'session'; x: number; y: number; session: Session }

type Props = {
  view: Range
  bounds: Range
  onViewChange: (range: Range) => void
  lanes: string[]
  /** Bucketed counts for the current view; drawn when individual events are not available. */
  density: Overview | null
  /** Every event in the view, ascending; `null` when there are too many to draw individually. */
  marks: TimelineEvent[] | null
  sessions: Session[]
  activeSessionId: string | null
  highlightIds: Set<string> | null
  selectedId: string | null
  onSelectEvent: (event: TimelineEvent) => void
  onSelectSession: (session: Session) => void
  timeZone: string
}

type LaneMarks = { events: TimelineEvent[]; times: number[] }

/** Index of the first element of the ascending `sorted` that is >= `value`. */
function lowerBound(sorted: number[], value: number): number {
  let lo = 0
  let hi = sorted.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (sorted[mid] < value) lo = mid + 1
    else hi = mid
  }
  return lo
}

export function TimelineChart({
  view,
  bounds,
  onViewChange,
  lanes,
  density,
  marks,
  sessions,
  activeSessionId,
  highlightIds,
  selectedId,
  onSelectEvent,
  onSelectSession,
  timeZone,
}: Props) {
  const [wrapRef, width] = useElementWidth<HTMLDivElement>(900)
  const svgRef = useRef<SVGSVGElement>(null)
  const dragRef = useRef<{ startX: number; startView: Range; moved: boolean } | null>(null)
  const [hover, setHover] = useState<Hover | null>(null)
  const [cursorX, setCursorX] = useState<number | null>(null)

  const plotW = Math.max(width - LABEL_W - PAD_R, 50)
  const span = Math.max(view.end - view.start, 1)
  const height = AXIS_H + lanes.length * (LANE_H + LANE_GAP)
  const xOf = useCallback((t: number) => LABEL_W + ((t - view.start) / span) * plotW, [view.start, span, plotW])
  const timeAt = useCallback((x: number) => view.start + ((x - LABEL_W) / plotW) * span, [view.start, span, plotW])

  const laneMarks = useMemo(() => {
    const map = new Map<string, LaneMarks>()
    for (const lane of lanes) map.set(lane, { events: [], times: [] })
    for (const ev of marks ?? []) {
      const lane = map.get(ev.source) ?? map.get('plaso')
      if (lane) {
        lane.events.push(ev)
        lane.times.push(ev.ts_ms)
      }
    }
    return map
  }, [marks, lanes])

  const sessionSpans = useMemo(
    () =>
      sessions
        .map((s) => ({ session: s, a: Date.parse(s.start_utc), b: Date.parse(s.end_utc) }))
        .filter((s) => Number.isFinite(s.a) && s.b >= view.start && s.a <= view.end),
    [sessions, view.start, view.end],
  )

  const ticks = useMemo(
    () => niceTicks(view, Math.max(3, Math.floor(plotW / 120)), timeZone),
    [view, plotW, timeZone],
  )

  useEffect(() => {
    const el = svgRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = el.getBoundingClientRect()
      const x = clamp(e.clientX - rect.left, LABEL_W, LABEL_W + plotW)
      if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        onViewChange(panRange(view, ((e.deltaX || e.deltaY) / plotW) * span, bounds))
      } else {
        const anchor = view.start + ((x - LABEL_W) / plotW) * span
        onViewChange(zoomRange(view, Math.exp(e.deltaY * 0.0015), anchor, bounds))
      }
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [view, bounds, plotW, span, onViewChange])

  const localPoint = (e: { clientX: number; clientY: number }) => {
    const rect = svgRef.current?.getBoundingClientRect()
    return { x: e.clientX - (rect?.left ?? 0), y: e.clientY - (rect?.top ?? 0) }
  }

  const hitTest = useCallback(
    (x: number, y: number): Hover | null => {
      if (x < LABEL_W || y < AXIS_H) return null
      const laneIndex = Math.floor((y - AXIS_H) / (LANE_H + LANE_GAP))
      const inLane = laneIndex >= 0 && laneIndex < lanes.length && (y - AXIS_H) % (LANE_H + LANE_GAP) <= LANE_H
      if (inLane) {
        const lm = laneMarks.get(lanes[laneIndex])
        if (lm && lm.times.length > 0) {
          const idx = nearestIndex(lm.times, timeAt(x))
          if (idx >= 0 && Math.abs(xOf(lm.times[idx]) - x) <= HIT_PX) return { kind: 'event', x, y, event: lm.events[idx] }
        }
      }
      const band = sessionSpans.find(({ a, b }) => x >= xOf(a) - 3 && x <= Math.max(xOf(b), xOf(a) + 6) + 3)
      return band ? { kind: 'session', x, y, session: band.session } : null
    },
    [lanes, laneMarks, sessionSpans, timeAt, xOf],
  )

  function onPointerDown(e: ReactPointerEvent<SVGSVGElement>) {
    if (e.button !== 0) return
    const { x } = localPoint(e)
    if (x < LABEL_W) return
    svgRef.current?.setPointerCapture?.(e.pointerId)
    dragRef.current = { startX: e.clientX, startView: view, moved: false }
  }

  function onPointerMove(e: ReactPointerEvent<SVGSVGElement>) {
    const { x, y } = localPoint(e)
    const drag = dragRef.current
    if (drag) {
      const dx = e.clientX - drag.startX
      if (!drag.moved && Math.abs(dx) > DRAG_THRESHOLD_PX) drag.moved = true
      if (drag.moved) {
        setHover(null)
        setCursorX(null)
        const startSpan = drag.startView.end - drag.startView.start
        onViewChange(panRange(drag.startView, -(dx / plotW) * startSpan, bounds))
      }
      return
    }
    setCursorX(x >= LABEL_W && y >= AXIS_H ? clamp(x, LABEL_W, LABEL_W + plotW) : null)
    setHover(hitTest(x, y))
  }

  function onPointerUp(e: ReactPointerEvent<SVGSVGElement>) {
    const drag = dragRef.current
    dragRef.current = null
    if (svgRef.current?.hasPointerCapture?.(e.pointerId)) svgRef.current.releasePointerCapture(e.pointerId)
    if (!drag || drag.moved) return
    const { x, y } = localPoint(e)
    const hit = hitTest(x, y)
    if (hit?.kind === 'event') onSelectEvent(hit.event)
    else if (hit?.kind === 'session') onSelectSession(hit.session)
  }

  function onPointerLeave() {
    if (dragRef.current) return
    setHover(null)
    setCursorX(null)
  }

  function onDoubleClick(e: ReactMouseEvent<SVGSVGElement>) {
    const { x } = localPoint(e)
    if (x < LABEL_W) return
    onViewChange(zoomRange(view, 0.5, timeAt(x), bounds))
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const center = view.start + span / 2
    switch (e.key) {
      case 'ArrowLeft':
        onViewChange(panRange(view, -span * 0.2, bounds))
        break
      case 'ArrowRight':
        onViewChange(panRange(view, span * 0.2, bounds))
        break
      case '+':
      case '=':
        onViewChange(zoomRange(view, 0.6, center, bounds))
        break
      case '-':
      case '_':
        onViewChange(zoomRange(view, 1 / 0.6, center, bounds))
        break
      case '0':
      case 'Home':
        onViewChange(bounds)
        break
      default:
        return
    }
    e.preventDefault()
  }

  if (lanes.length === 0) {
    return <div className="empty">No events in view. Acquire evidence, or adjust the source filters.</div>
  }

  const cursorTime = cursorX === null ? null : timeAt(cursorX)
  const tooltipLeft = hover ? clamp(hover.x + 14, 0, Math.max(width - 260, 0)) : 0

  return (
    <div
      ref={wrapRef}
      className="chart"
      tabIndex={0}
      role="group"
      aria-label="Timeline chart"
      aria-describedby="chart-help"
      onKeyDown={onKeyDown}
    >
      <p id="chart-help" className="sr-only">
        Drag to pan, scroll to zoom, double-click to zoom in. Arrow keys pan, plus and minus zoom, zero resets. Use the
        event table below to browse events with the keyboard.
      </p>
      <svg
        ref={svgRef}
        width={width}
        height={height}
        className="chart-svg"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={onPointerLeave}
        onDoubleClick={onDoubleClick}
      >
        <defs>
          <clipPath id="plot-clip">
            <rect x={LABEL_W} y={0} width={plotW} height={height} />
          </clipPath>
        </defs>

        {lanes.map((source, i) => (
          <rect
            key={`bg-${source}`}
            x={LABEL_W}
            y={AXIS_H + i * (LANE_H + LANE_GAP)}
            width={plotW}
            height={LANE_H}
            className="lane-bg"
          />
        ))}

        {ticks.map((t) => {
          const x = xOf(t.t)
          return (
            <g key={t.t}>
              <line x1={x} x2={x} y1={AXIS_H - 6} y2={height} className={t.major ? 'grid major' : 'grid'} />
              <text x={x} y={AXIS_H - 12} textAnchor="middle" className={t.major ? 'tick-label major' : 'tick-label'}>
                {t.label}
              </text>
            </g>
          )
        })}

        <g clipPath="url(#plot-clip)">
          {sessionSpans.map(({ session, a, b }) => {
            const x = xOf(a)
            const w = Math.max(xOf(b) - x, 6)
            const active = session.id === activeSessionId
            return (
              <rect
                key={session.id}
                x={x - (w === 6 ? 3 : 0)}
                y={AXIS_H - 4}
                width={w}
                height={height - AXIS_H + 4}
                className={`session-band${active ? ' active' : ''}`}
              />
            )
          })}
        </g>

        {lanes.map((source, i) => {
          const y0 = AXIS_H + i * (LANE_H + LANE_GAP)
          const color = laneColor(source)
          const lm = laneMarks.get(source)
          const series = density?.series[source] ?? []
          const peak = series.reduce((m, [, c]) => Math.max(m, c), 1)
          const inView = lm ? lowerBound(lm.times, view.end + 1) - lowerBound(lm.times, view.start) : 0
          return (
            <g key={source}>
              <text x={LABEL_W - 10} y={y0 + LANE_H / 2 - 2} textAnchor="end" className="lane-label">
                {SOURCE_LABEL[source] ?? source}
              </text>
              <text x={LABEL_W - 10} y={y0 + LANE_H / 2 + 12} textAnchor="end" className="lane-count">
                {marks ? `${inView} in view` : 'density'}
              </text>
              <g clipPath="url(#plot-clip)">
                {marks === null
                  ? series.map(([idx, count]) => {
                      const a = density ? density.origin_ms + idx * density.bucket_ms : 0
                      const x = xOf(a)
                      const w = Math.max(xOf(a + (density?.bucket_ms ?? 0)) - x - 0.5, 1.5)
                      const h = 4 + ((LANE_H - 12) * count) / peak
                      return (
                        <rect
                          key={idx}
                          x={x}
                          y={y0 + LANE_H - 4 - h}
                          width={w}
                          height={h}
                          fill={color}
                          opacity={0.85}
                        />
                      )
                    })
                  : lm && (
                      <LaneTicks
                        lane={lm}
                        y0={y0}
                        xOf={xOf}
                        color={color}
                        dim={highlightIds !== null}
                        highlightIds={highlightIds}
                        selectedId={selectedId}
                      />
                    )}
              </g>
            </g>
          )
        })}

        {cursorX !== null && cursorTime !== null ? (
          <g pointerEvents="none">
            <line x1={cursorX} x2={cursorX} y1={AXIS_H - 4} y2={height} className="cursor-line" />
            <g transform={`translate(${clamp(cursorX, LABEL_W + 52, LABEL_W + plotW - 52)},2)`}>
              <rect x={-52} y={0} width={104} height={16} rx={2} className="cursor-chip" />
              <text x={0} y={12} textAnchor="middle" className="cursor-text">
                {formatTimeOfDay(cursorTime, timeZone, true)}
              </text>
            </g>
          </g>
        ) : null}
      </svg>

      {hover ? (
        <div className="chart-tip" style={{ left: tooltipLeft, top: hover.y + 16 }} role="tooltip">
          {hover.kind === 'event' ? (
            <>
              <div className="tip-head">
                <span className={`dot ${hover.event.source}`} />
                {SOURCE_LABEL[hover.event.source] ?? hover.event.source}
              </div>
              <div className="tip-title">{hover.event.title}</div>
              <div className="mono muted">{formatDateTime(hover.event.ts_ms, timeZone, true)}</div>
            </>
          ) : (
            <>
              <div className="tip-head">Proximity session · score {hover.session.score.toFixed(2)}</div>
              <div className="tip-title">{hover.session.summary}</div>
              <div className="mono muted">{hover.session.event_count} events. Click to focus</div>
            </>
          )}
        </div>
      ) : null}
    </div>
  )
}

function LaneTicks({
  lane,
  y0,
  xOf,
  color,
  dim,
  highlightIds,
  selectedId,
}: {
  lane: LaneMarks
  y0: number
  xOf: (t: number) => number
  color: string
  dim: boolean
  highlightIds: Set<string> | null
  selectedId: string | null
}) {
  const { base, lit, selected } = useMemo(() => {
    const seen = new Set<number>()
    let baseD = ''
    let litD = ''
    let selD = ''
    for (const ev of lane.events) {
      const x = Math.round(xOf(ev.ts_ms) * 2) / 2
      const seg = `M${x} ${y0 + 12}v${LANE_H - 24}`
      if (ev.id === selectedId) selD += seg
      if (highlightIds?.has(ev.id)) litD += seg
      else if (!seen.has(x)) {
        seen.add(x)
        baseD += seg
      }
    }
    return { base: baseD, lit: litD, selected: selD }
  }, [lane, xOf, y0, highlightIds, selectedId])

  return (
    <>
      <path d={base} stroke={color} strokeWidth={2} opacity={dim ? 0.25 : 0.95} fill="none" />
      {lit ? <path d={lit} stroke={color} strokeWidth={3} fill="none" /> : null}
      {selected ? (
        <>
          <path d={selected} className="mark-halo" strokeWidth={7} fill="none" />
          <path d={selected} stroke={color} strokeWidth={3} fill="none" />
        </>
      ) : null}
    </>
  )
}
