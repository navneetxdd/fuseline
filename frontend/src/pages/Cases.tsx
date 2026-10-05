import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { type Case, type CaseInput, api } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { formatCount } from '../lib/format'
import { formatDateTime, isValidTimeZone } from '../lib/time'
import { useCase } from '../state/CaseContext'

function timeZoneNames(): string[] {
  try {
    return ['UTC', ...Intl.supportedValuesOf('timeZone')]
  } catch {
    return ['UTC']
  }
}

type FieldsProps = {
  value: CaseInput
  onChange: (v: CaseInput) => void
  zones: string[]
  idPrefix: string
}

function CaseFields({ value, onChange, zones, idPrefix }: FieldsProps) {
  const zoneOk = isValidTimeZone(value.timezone.trim() || 'UTC')
  return (
    <>
      <div className="form-grid">
        <label>
          Case name
          <input
            value={value.name}
            onChange={(e) => onChange({ ...value, name: e.target.value })}
            required
            maxLength={200}
            placeholder="e.g. Device-A-2024-06"
          />
        </label>
        <label>
          Examiner
          <input value={value.examiner} onChange={(e) => onChange({ ...value, examiner: e.target.value })} />
        </label>
        <label>
          Device timezone
          <input
            list={`${idPrefix}-zones`}
            value={value.timezone}
            onChange={(e) => onChange({ ...value, timezone: e.target.value })}
            aria-invalid={!zoneOk}
            placeholder="UTC or e.g. Asia/Kolkata"
          />
          <datalist id={`${idPrefix}-zones`}>
            {zones.map((z) => (
              <option key={z} value={z} />
            ))}
          </datalist>
          <span className={zoneOk ? 'hint' : 'hint bad'}>
            {zoneOk
              ? 'Applied to timestamps that carry no timezone (marked “assumed” on each event).'
              : 'Unknown timezone. Use an IANA name such as Asia/Kolkata.'}
          </span>
        </label>
      </div>
      <label>
        Notes
        <textarea value={value.notes} onChange={(e) => onChange({ ...value, notes: e.target.value })} rows={2} />
      </label>
    </>
  )
}

const BLANK: CaseInput = { name: '', examiner: '', timezone: 'UTC', notes: '' }

export function CasesPage() {
  const navigate = useNavigate()
  const { caseId: activeCaseId, selectCase, refreshCase } = useCase()
  const zones = useMemo(() => timeZoneNames(), [])
  const [cases, setCases] = useState<Case[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [draft, setDraft] = useState<CaseInput>(BLANK)
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Case | null>(null)
  const [editDraft, setEditDraft] = useState<CaseInput>(BLANK)
  const [deleting, setDeleting] = useState<Case | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setCases(await api.listCases())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load cases')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  async function onCreate(e: FormEvent) {
    e.preventDefault()
    if (!draft.name.trim() || !isValidTimeZone(draft.timezone.trim() || 'UTC')) return
    setCreating(true)
    setError(null)
    try {
      const created = await api.createCase({ ...draft, timezone: draft.timezone.trim() || 'UTC' })
      selectCase(created.id)
      setDraft(BLANK)
      navigate('/acquire')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Create failed')
    } finally {
      setCreating(false)
    }
  }

  function startEdit(c: Case) {
    setEditing(c)
    setEditDraft({ name: c.name, examiner: c.examiner, timezone: c.timezone, notes: c.notes })
  }

  async function saveEdit() {
    if (!editing) return
    setBusy(true)
    try {
      await api.updateCase(editing.id, { ...editDraft, timezone: editDraft.timezone.trim() || 'UTC' })
      setEditing(null)
      await refresh()
      if (editing.id === activeCaseId) await refreshCase()
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Update failed')
      setEditing(null)
    } finally {
      setBusy(false)
    }
  }

  async function confirmDelete() {
    if (!deleting) return
    setBusy(true)
    try {
      await api.deleteCase(deleting.id)
      if (activeCaseId === deleting.id) selectCase(null)
      setDeleting(null)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
      setDeleting(null)
    } finally {
      setBusy(false)
    }
  }

  function open(c: Case, to: string) {
    selectCase(c.id)
    navigate(to)
  }

  return (
    <div className="stack">
      <section className="panel">
        <h1>Cases</h1>
        <p className="muted">
          A case holds the evidence you acquire, the events parsed from it, and an audit trail of everything done to
          it. Create one, then acquire evidence.
        </p>
        <form className="stack" onSubmit={onCreate}>
          <CaseFields value={draft} onChange={setDraft} zones={zones} idPrefix="new" />
          <div className="row">
            <button
              className="btn"
              type="submit"
              disabled={creating || !draft.name.trim() || !isValidTimeZone(draft.timezone.trim() || 'UTC')}
            >
              {creating ? 'Creating…' : 'Create & acquire'}
            </button>
          </div>
        </form>
      </section>

      <section className="panel">
        <h2>Case inventory</h2>
        {error ? <div className="error-box">{error}</div> : null}
        {loading ? <div className="muted">Loading cases…</div> : null}
        {!loading && cases.length === 0 ? (
          <div className="empty">No cases yet. Create one above to start acquisition.</div>
        ) : null}
        {cases.length > 0 ? (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th scope="col">Case</th>
                  <th scope="col">Timezone</th>
                  <th scope="col" className="num">
                    Events
                  </th>
                  <th scope="col" className="num">
                    Artifacts
                  </th>
                  <th scope="col" className="num">
                    Sessions
                  </th>
                  <th scope="col">Updated (UTC)</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {cases.map((c) => (
                  <tr key={c.id} className={activeCaseId === c.id ? 'row-active' : undefined}>
                    <td>
                      <strong>{c.name}</strong>
                      {activeCaseId === c.id ? <span className="badge neutral inline">active</span> : null}
                      <div className="cell-sub">
                        {c.examiner || 'no examiner'} · <span className="mono">{c.id.slice(0, 8)}</span>
                      </div>
                    </td>
                    <td className="mono">{c.timezone}</td>
                    <td className="num">{formatCount(c.event_count)}</td>
                    <td className="num">{c.artifact_count}</td>
                    <td className="num">{c.session_count}</td>
                    <td className="mono nowrap">{formatDateTime(Date.parse(c.updated_at))}</td>
                    <td>
                      <div className="row tight nowrap">
                        <button type="button" className="btn small" onClick={() => open(c, '/acquire')}>
                          Open
                        </button>
                        <button type="button" className="btn secondary small" onClick={() => open(c, '/timeline')}>
                          Timeline
                        </button>
                        <button type="button" className="btn ghost small" onClick={() => startEdit(c)}>
                          Edit
                        </button>
                        <button type="button" className="btn ghost small danger-text" onClick={() => setDeleting(c)}>
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <ConfirmDialog
        open={editing !== null}
        title="Edit case"
        confirmLabel="Save changes"
        busy={busy}
        onConfirm={() => void saveEdit()}
        onCancel={() => setEditing(null)}
      >
        <div className="stack">
          <CaseFields value={editDraft} onChange={setEditDraft} zones={zones} idPrefix="edit" />
          <p className="muted small">
            Changing the timezone affects how <em>future</em> acquisitions interpret timestamps without an offset;
            events already ingested keep the interpretation recorded on them.
          </p>
        </div>
      </ConfirmDialog>

      <ConfirmDialog
        open={deleting !== null}
        title="Delete this case?"
        confirmLabel="Delete case"
        danger
        busy={busy}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setDeleting(null)}
      >
        <p>
          <strong>{deleting?.name}</strong> and its {deleting?.artifact_count ?? 0} stored evidence cop
          {deleting?.artifact_count === 1 ? 'y' : 'ies'} will be permanently removed from this workstation. Your
          original files are not touched. The deletion itself stays recorded in the audit log.
        </p>
      </ConfirmDialog>
    </div>
  )
}
