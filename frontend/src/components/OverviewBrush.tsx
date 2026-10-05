import { useMemo, useRef } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import type { Overview } from '../api/client'
import { useElementWidth } from '../hooks/useElementSize'
import { laneColor } from '../lib/sources'
import { type Range, clamp, formatDateTime, shiftInto } from '../lib/time'

const HEIGHT = 58
const PAD = 8
const HANDLE = 7
const MIN_VIEW_MS = 1000

type Mode = 'move' | 'left' | 'right'

type Props = {
  overview: Overview | null
  lanes: string[]
  bounds: Range
  view: Range
  onViewChange: (range: Range) => void
  timeZone: string
}

/** Whole-case density strip with a draggable window: the "map" for the zoomable chart below it. */
export function OverviewBrush({ overview, lanes, bounds, view, onViewChange, timeZone }: Props) {
  const [wrapRef, width] = useElementWidth<HTMLDivElement>(900)
  const svgRef = useRef<SVGSVGElement>(null)
  const dragRef = useRef<{ mode: Mode; grabOffset: number } | null>(null)

  const plotW = Math.max(width - PAD * 2, 50)
  const total = Math.max(bounds.end - bounds.start, 1)
  const xOf = (t: number) => PAD + ((t - bounds.start) / total) * plotW
  const timeAt = (x: number) => bounds.start + ((x - PAD) / plotW) * total

  const stacks = useMemo(() => {
    if (!overview) return { bars: [] as { idx: number; parts: { source: string; count: number }[]; sum: number }[], peak: 1 }
    const byIdx = new Map<number, { source: string; count: number }[]>()
    for (const source of lanes) {
      for (const [idx, count] of overview.series[source] ?? []) {
        const parts = byIdx.get(idx) ?? []
        parts.push({ source, count })
        byIdx.set(idx, parts)
      }
    }
    const bars = [...byIdx.entries()].map(([idx, parts]) => ({
      idx,
      parts,
      sum: parts.reduce((s, p) => s + p.count, 0),
    }))
    return { bars, peak: bars.reduce((m, b) => Math.max(m, b.sum), 1) }
  }, [overview, lanes])

  const selX0 = xOf(view.start)
  const selX1 = xOf(view.end)
  const isFull = view.start <= bounds.start + 1 && view.end >= bounds.end - 1

  const localX = (e: { clientX: number }) => e.clientX - (svgRef.current?.getBoundingClientRect().left ?? 0)

  function apply(next: Range) {
    onViewChange(shiftInto(next, bounds))
  }

  function onPointerDown(e: ReactPointerEvent<SVGSVGElement>) {
    if (e.button !== 0) return
    const x = localX(e)
    svgRef.current?.setPointerCapture?.(e.pointerId)
    if (Math.abs(x - selX0) <= HANDLE) dragRef.current = { mode: 'left', grabOffset: 0 }
    else if (Math.abs(x - selX1) <= HANDLE) dragRef.current = { mode: 'right', grabOffset: 0 }
    else if (x > selX0 && x < selX1) dragRef.current = { mode: 'move', grabOffset: timeAt(x) - view.start }
    else {
      // Click outside the window: recentre on the click and keep dragging from its middle.
      const span = view.end - view.start
      const start = timeAt(x) - span / 2
      apply({ start, end: start + span })
      dragRef.current = { mode: 'move', grabOffset: span / 2 }
    }
  }

  function onPointerMove(e: ReactPointerEvent<SVGSVGElement>) {
    const drag = dragRef.current
    if (!drag) return
    const t = clamp(timeAt(localX(e)), bounds.start, bounds.end)
    if (drag.mode === 'move') {
      const span = view.end - view.start
      apply({ start: t - drag.grabOffset, end: t - drag.grabOffset + span })
    } else if (drag.mode === 'left') {
      onViewChange({ start: Math.min(t, view.end - MIN_VIEW_MS), end: view.end })
    } else {
      onViewChange({ start: view.start, end: Math.max(t, view.start + MIN_VIEW_MS) })
    }
  }

  function endDrag(e: ReactPointerEvent<SVGSVGElement>) {
    dragRef.current = null
    if (svgRef.current?.hasPointerCapture?.(e.pointerId)) svgRef.current.releasePointerCapture(e.pointerId)
  }

  const label = `${formatDateTime(view.start, timeZone)} → ${formatDateTime(view.end, timeZone)}`

  return (
    <div ref={wrapRef} className="brush">
      <svg
        ref={svgRef}
        width={width}
        height={HEIGHT}
        role="img"
        aria-label={`Overview of the whole case. Visible window: ${label}`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onDoubleClick={() => onViewChange(bounds)}
      >
        <rect x={PAD} y={4} width={plotW} height={HEIGHT - 8} className="brush-bg" />
        {overview
          ? stacks.bars.map((bar) => {
              const a = overview.origin_ms + bar.idx * overview.bucket_ms
              const x = xOf(a)
              const w = Math.max(xOf(a + overview.bucket_ms) - x - 0.5, 1.5)
              let y = HEIGHT - 6
              return (
                <g key={bar.idx}>
                  {bar.parts.map((p) => {
                    const h = Math.max(2, ((HEIGHT - 14) * p.count) / stacks.peak)
                    y -= h
                    return <rect key={p.source} x={x} y={y} width={w} height={h} fill={laneColor(p.source)} opacity={0.85} />
                  })}
                </g>
              )
            })
          : null}
        {!isFull ? (
          <>
            <rect x={PAD} y={4} width={Math.max(selX0 - PAD, 0)} height={HEIGHT - 8} className="brush-shade" />
            <rect x={selX1} y={4} width={Math.max(PAD + plotW - selX1, 0)} height={HEIGHT - 8} className="brush-shade" />
          </>
        ) : null}
        <rect
          x={selX0}
          y={4}
          width={Math.max(selX1 - selX0, 2)}
          height={HEIGHT - 8}
          className={`brush-window${isFull ? ' full' : ''}`}
        />
        <rect x={selX0 - 2} y={14} width={4} height={HEIGHT - 28} rx={2} className="brush-handle" />
        <rect x={selX1 - 2} y={14} width={4} height={HEIGHT - 28} rx={2} className="brush-handle" />
      </svg>
      <div className="brush-caption mono muted">
        <span>{formatDateTime(bounds.start, timeZone)}</span>
        <span>{isFull ? 'showing everything. Drag the window or scroll the chart to zoom' : label}</span>
        <span>{formatDateTime(bounds.end, timeZone)}</span>
      </div>
    </div>
  )
}
