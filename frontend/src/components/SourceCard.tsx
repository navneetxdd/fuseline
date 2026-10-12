import type { ReactNode } from 'react'
import type { Artifact } from '../api/client'
import { formatCount } from '../lib/format'

type Props = {
  source: string
  label: string
  help: string
  artifacts: Artifact[]
  totalEvents: number
  integrityBySource?: Map<string, 'ok' | 'modified' | 'missing'>
  dragOver: boolean
  onDragOver: () => void
  onDragLeave: () => void
  onDrop: (files: File[]) => void
  onChoose: (files: File[]) => void
  disabled: boolean
  children?: ReactNode
}

/** One evidence category: status and a drop target. Files are listed once, under the grid. */
export function SourceCard({
  source,
  label,
  help,
  artifacts,
  totalEvents,
  integrityBySource,
  dragOver,
  onDragOver,
  onDragLeave,
  onDrop,
  onChoose,
  disabled,
  children,
}: Props) {
  const problem = artifacts.find((artifact) => {
    const status = integrityBySource?.get(artifact.id)
    return status === 'modified' || status === 'missing'
  })
  const problemStatus = problem ? integrityBySource?.get(problem.id) : undefined

  return (
    <div className="source-card">
      <div className="source-card-head">
        <span className={`dot ${source}`} aria-hidden="true" />
        <div className="source-card-title">
          <h3>{label}</h3>
          <p className="muted small">{help}</p>
        </div>
        <div className="source-card-status">
          {artifacts.length > 0 ? (
            <span className="mono small">
              {artifacts.length} file{artifacts.length === 1 ? '' : 's'} · {formatCount(totalEvents)} events
              {problemStatus ? ` · ${problemStatus}` : ''}
            </span>
          ) : (
            <span className="muted small">Not yet acquired</span>
          )}
        </div>
      </div>

      <div className="source-card-actions">
        <div
          className={`dropzone compact${dragOver ? ' active' : ''}`}
          onDragOver={(e) => {
            e.preventDefault()
            onDragOver()
          }}
          onDragLeave={onDragLeave}
          onDrop={(e) => {
            e.preventDefault()
            onDrop(Array.from(e.dataTransfer.files))
          }}
        >
          <span className="muted small">Drop file, or</span>
          <label className="btn secondary small file-btn">
            Choose…
            <input
              type="file"
              disabled={disabled}
              onChange={(e) => {
                onChoose(Array.from(e.target.files ?? []))
                e.target.value = ''
              }}
            />
          </label>
        </div>
        {children}
      </div>
    </div>
  )
}
