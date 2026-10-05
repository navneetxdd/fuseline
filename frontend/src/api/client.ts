export type Case = {
  id: string
  name: string
  examiner: string
  timezone: string
  notes: string
  created_at: string
  updated_at: string
  event_count: number
  artifact_count: number
  session_count: number
}

export type CaseInput = { name: string; examiner: string; timezone: string; notes: string }

export type Artifact = {
  id: string
  source_type: string
  original_name: string
  sha256: string
  ingested_at: string
  row_count: number
  size_bytes: number
  skipped_rows: number
  parser: string
  notes: string[]
}

export type TimeBasis = 'absolute' | 'offset' | 'assumed'

export type TimelineEvent = {
  id: string
  artifact_id: string
  ts_utc: string
  ts_ms: number
  ts_original: string
  tz_assumed: string
  ts_basis: TimeBasis
  source: string
  event_type: string
  title: string
  detail: Record<string, unknown>
  lat: number | null
  lon: number | null
  package: string | null
  url: string | null
  domain: string | null
  confidence: number
}

export type Session = {
  id: string
  start_utc: string
  end_utc: string
  score: number
  summary: string
  member_event_ids: string[]
  sources: string[]
  event_count: number
  centroid_lat: number | null
  centroid_lon: number | null
  radius_m: number | null
}

export type CorrelationParams = {
  window_seconds: number
  max_span_seconds: number
  min_sources: number
}

export type Finding = {
  id: string
  severity: 'pass' | 'warn' | 'fail' | 'info' | string
  code: string
  message: string
  created_at: string
}

export type TimelineResponse = {
  case_id: string
  events: TimelineEvent[]
  total: number
  offset: number
  limit: number
  has_more: boolean
  sources: Record<string, number>
}

export type Overview = {
  origin_ms: number
  bucket_ms: number
  bucket_count: number
  total: number
  series: Record<string, [number, number][]>
}

export type LocationPoint = { id: string; ts_ms: number; lat: number; lon: number }
export type LocationsResponse = { points: LocationPoint[]; total: number; returned: number }

export type IngestResult = {
  artifact: Artifact
  events_added: number
  sessions_rebuilt: number
  findings: Finding[]
  duplicate: boolean
}


export type AuditEntry = {
  id: number
  ts: string
  case_id: string | null
  actor: string
  action: string
  detail: Record<string, unknown>
  prev_hash: string
  entry_hash: string
}

export type ArtifactIntegrity = {
  artifact_id: string
  original_name: string
  expected_sha256: string
  actual_sha256: string | null
  status: 'ok' | 'modified' | 'missing'
}

export type IntegrityResult = {
  ok: boolean
  checked_at: string
  artifacts: ArtifactIntegrity[]
  audit_chain_ok: boolean
  audit_first_bad_entry: number | null
}

export type Meta = {
  version: string
  max_upload_bytes: number
  formats: { source: string; parser: string; label: string }[]
}

export type AdbDevice = {
  serial: string
  state: string
  model: string | null
  ready: boolean
}

export type DeviceExport = {
  name: string
  remote_path: string
  display_path: string
  size_bytes: number | null
}

export type DeviceBundleItem = {
  kind: 'app_usage' | 'location' | 'export'
  label: string
  ok: boolean
  events_added: number
  duplicate: boolean
  error: string | null
  source_type: string | null
}

export type DeviceBundleResult = {
  items: DeviceBundleItem[]
  total_events_added: number
  sessions_rebuilt: number
}

export type ReportSummary = {
  case: Case
  artifacts: Artifact[]
  event_count: number
  source_counts: Record<string, number>
  session_count: number
  findings: Finding[]
  sessions: Session[]
  correlation: CorrelationParams
  audit: AuditEntry[]
  first_event_utc: string | null
  last_event_utc: string | null
}

export type TimelineQuery = {
  source?: string
  q?: string
  start?: number
  end?: number
  offset?: number
  limit?: number
  order?: 'asc' | 'desc'
}

export class ApiError extends Error {
  status: number

  constructor(message: string, status = 0) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** Turn a FastAPI error body (string detail, or a list of validation errors) into one readable line. */
export function extractDetail(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        const item = d as { msg?: string; loc?: unknown[] }
        const field = Array.isArray(item.loc) ? item.loc.filter((p) => p !== 'body').join('.') : ''
        const msg = (item.msg ?? '').replace(/^Value error, /, '')
        return field ? `${field}: ${msg}` : msg
      })
      .filter(Boolean)
    if (parts.length) return parts.join('; ')
  }
  return fallback
}

const UNREACHABLE = 'Cannot reach the Fuseline API. Is the backend running on :8000?'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, init)
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError(UNREACHABLE)
  }
  if (!res.ok) {
    let body: unknown = null
    try {
      body = await res.json()
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(extractDetail(body, res.statusText || `Request failed (${res.status})`), res.status)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') q.set(k, String(v))
  }
  const text = q.toString()
  return text ? `?${text}` : ''
}

const iso = (ms: number | undefined) => (ms === undefined ? undefined : new Date(ms).toISOString())
const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  health: () => request<{ status: string; version: string }>('/api/health'),
  meta: () => request<Meta>('/api/meta'),
  devices: () => request<AdbDevice[]>('/api/devices'),
  deviceExports: (serial: string) =>
    request<DeviceExport[]>(`/api/devices/${encodeURIComponent(serial)}/exports`),
  pullDeviceAppUsage: (caseId: string, serial: string) =>
    request<IngestResult>(`/api/cases/${caseId}/acquire/device/${encodeURIComponent(serial)}/app-usage`, {
      method: 'POST',
    }),
  pullDeviceLocation: (caseId: string, serial: string) =>
    request<IngestResult>(`/api/cases/${caseId}/acquire/device/${encodeURIComponent(serial)}/location`, {
      method: 'POST',
    }),
  pullDeviceExport: (caseId: string, serial: string, remotePath: string) =>
    request<IngestResult>(
      `/api/cases/${caseId}/acquire/device/${encodeURIComponent(serial)}/export`,
      json('POST', { remote_path: remotePath }),
    ),
  pullDeviceBundle: (caseId: string, serial: string) =>
    request<DeviceBundleResult>(`/api/cases/${caseId}/acquire/device/${encodeURIComponent(serial)}/bundle`, {
      method: 'POST',
    }),

  listCases: () => request<Case[]>('/api/cases'),
  createCase: (body: CaseInput) => request<Case>('/api/cases', json('POST', body)),
  getCase: (id: string) => request<Case>(`/api/cases/${id}`),
  updateCase: (id: string, body: Partial<CaseInput>) => request<Case>(`/api/cases/${id}`, json('PATCH', body)),
  deleteCase: (id: string) => request<void>(`/api/cases/${id}`, { method: 'DELETE' }),

  listArtifacts: (id: string) => request<Artifact[]>(`/api/cases/${id}/artifacts`),
  verify: (id: string) => request<IntegrityResult>(`/api/cases/${id}/verify`, { method: 'POST' }),
  audit: (id: string) => request<AuditEntry[]>(`/api/cases/${id}/audit`),

  upload: (id: string, file: File, sourceHint: string, onProgress?: (fraction: number) => void) =>
    new Promise<IngestResult>((resolve, reject) => {
      const form = new FormData()
      form.append('file', file)
      form.append('source_hint', sourceHint)
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `/api/cases/${id}/acquire`)
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress?.(e.loaded / e.total)
      }
      xhr.onerror = () => reject(new ApiError(UNREACHABLE))
      xhr.onload = () => {
        let body: unknown = null
        try {
          body = JSON.parse(xhr.responseText)
        } catch {
          /* non-JSON body */
        }
        if (xhr.status >= 200 && xhr.status < 300) resolve(body as IngestResult)
        else reject(new ApiError(extractDetail(body, xhr.statusText || `Upload failed (${xhr.status})`), xhr.status))
      }
      xhr.send(form)
    }),

  timeline: (id: string, q: TimelineQuery = {}, signal?: AbortSignal) =>
    request<TimelineResponse>(
      `/api/cases/${id}/timeline${qs({
        source: q.source,
        q: q.q,
        start: iso(q.start),
        end: iso(q.end),
        offset: q.offset,
        limit: q.limit,
        order: q.order,
      })}`,
      { signal },
    ),
  event: (id: string, eventId: string) => request<TimelineEvent>(`/api/cases/${id}/events/${eventId}`),
  overview: (
    id: string,
    q: { source?: string; q?: string; start?: number; end?: number; buckets?: number } = {},
    signal?: AbortSignal,
  ) =>
    request<Overview>(
      `/api/cases/${id}/overview${qs({
        source: q.source,
        q: q.q,
        start: iso(q.start),
        end: iso(q.end),
        buckets: q.buckets,
      })}`,
      { signal },
    ),
  locations: (id: string, q: { start?: number; end?: number; maxPoints?: number } = {}, signal?: AbortSignal) =>
    request<LocationsResponse>(
      `/api/cases/${id}/locations${qs({ start: iso(q.start), end: iso(q.end), max_points: q.maxPoints })}`,
      { signal },
    ),

  sessions: (id: string) => request<Session[]>(`/api/cases/${id}/sessions`),
  sessionEvents: (id: string, sessionId: string) =>
    request<TimelineEvent[]>(`/api/cases/${id}/sessions/${sessionId}/events`),
  correlationParams: (id: string) => request<CorrelationParams>(`/api/cases/${id}/sessions/params`),
  rebuildSessions: (id: string, p: CorrelationParams) =>
    request<Session[]>(`/api/cases/${id}/sessions/rebuild${qs({ ...p })}`, { method: 'POST' }),

  validation: (id: string, refresh = false) =>
    request<Finding[]>(`/api/cases/${id}/validation${refresh ? '?refresh=true' : ''}`),
  report: (id: string) => request<ReportSummary>(`/api/cases/${id}/report`),
}
