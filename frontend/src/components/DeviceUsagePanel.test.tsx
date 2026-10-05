import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '../api/client'
import { DeviceUsagePanel } from './DeviceUsagePanel'

const CASE_ID = 'case-1'

function renderPanel(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

function open() {
  fireEvent.click(screen.getByRole('button', { name: 'Detect device…' }))
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.spyOn(api, 'deviceExports').mockResolvedValue([])
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('DeviceUsagePanel', () => {
  it('is closed until asked to detect a device, and stops polling once closed', async () => {
    const devices = vi.spyOn(api, 'devices').mockResolvedValue([])
    renderPanel(<DeviceUsagePanel caseId={CASE_ID} onImported={vi.fn()} />)
    expect(devices).not.toHaveBeenCalled()

    open()
    await vi.waitFor(() => expect(devices).toHaveBeenCalledTimes(1))
    await vi.advanceTimersByTimeAsync(2000)
    expect(devices).toHaveBeenCalledTimes(2)

    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    await vi.advanceTimersByTimeAsync(5000)
    expect(devices).toHaveBeenCalledTimes(2) // no more polls after closing
  })

  it('shows adb-missing guidance instead of a device list', async () => {
    vi.spyOn(api, 'devices').mockRejectedValue(new ApiError('adb was not found on this machine.', 503))
    renderPanel(<DeviceUsagePanel caseId={CASE_ID} onImported={vi.fn()} />)
    open()
    expect(await screen.findByText('adb was not found on this machine.')).toBeInTheDocument()
  })

  it('shows an unauthorized device without a pull button', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([{ serial: 'ZY1', state: 'unauthorized', model: null, ready: false }])
    renderPanel(<DeviceUsagePanel caseId={CASE_ID} onImported={vi.fn()} />)
    open()
    expect(await screen.findByText('unauthorized')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'App usage' })).toBeNull()
    expect(screen.getByText(/Accept the USB debugging prompt/)).toBeInTheDocument()
  })

  it('pulls app usage from a ready device and reports the result', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([
      { serial: 'emulator-5554', state: 'device', model: 'Pixel_6', ready: true },
    ])
    const pull = vi.spyOn(api, 'pullDeviceAppUsage').mockResolvedValue({
      artifact: {
        id: 'a1',
        source_type: 'app_usage',
        original_name: 'adb_usagestats_emulator-5554.txt',
        sha256: 'x'.repeat(64),
        ingested_at: '2024-06-15T10:00:00.000Z',
        row_count: 42,
        size_bytes: 100,
        skipped_rows: 0,
        parser: 'adb_usagestats_dump',
        notes: [],
      },
      events_added: 42,
      sessions_rebuilt: 1,
      findings: [],
      duplicate: false,
    })
    const onImported = vi.fn()
    renderPanel(<DeviceUsagePanel caseId={CASE_ID} onImported={onImported} />)
    open()
    const button = await screen.findByRole('button', { name: 'App usage' })
    fireEvent.click(button)
    expect(pull).toHaveBeenCalledWith(CASE_ID, 'emulator-5554')
    await waitFor(() => expect(onImported).toHaveBeenCalledOnce())
    expect(await screen.findByText(/Imported 42 events/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Timeline' })).toHaveAttribute('href', '/timeline')
  })

  it('surfaces a pull error without crashing', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([{ serial: 'S1', state: 'device', model: null, ready: true }])
    vi.spyOn(api, 'pullDeviceAppUsage').mockRejectedValue(new Error('adb shell dumpsys usagestats timed out'))
    renderPanel(<DeviceUsagePanel caseId={CASE_ID} onImported={vi.fn()} />)
    open()
    fireEvent.click(await screen.findByRole('button', { name: 'App usage' }))
    expect(await screen.findByText('adb shell dumpsys usagestats timed out')).toBeInTheDocument()
  })

  it('lists Download exports and pulls one through ingest', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([
      { serial: 'emulator-5554', state: 'device', model: 'Pixel_6', ready: true },
    ])
    vi.spyOn(api, 'deviceExports').mockResolvedValue([
      {
        name: 'Records.json',
        remote_path: '/sdcard/Download/Takeout/Records.json',
        display_path: 'Download/Takeout/Records.json',
        size_bytes: null,
      },
    ])
    const pullExport = vi.spyOn(api, 'pullDeviceExport').mockResolvedValue({
      artifact: {
        id: 'a2',
        source_type: 'location',
        original_name: 'Records.json',
        sha256: 'y'.repeat(64),
        ingested_at: '2024-06-15T10:00:00.000Z',
        row_count: 3,
        size_bytes: 50,
        skipped_rows: 0,
        parser: 'location',
        notes: [],
      },
      events_added: 3,
      sessions_rebuilt: 0,
      findings: [],
      duplicate: false,
    })
    renderPanel(<DeviceUsagePanel caseId={CASE_ID} onImported={vi.fn()} />)
    open()
    fireEvent.click(await screen.findByRole('button', { name: 'Pull & ingest' }))
    expect(pullExport).toHaveBeenCalledWith(CASE_ID, 'emulator-5554', '/sdcard/Download/Takeout/Records.json')
    expect(await screen.findByText(/Imported 3 events/)).toBeInTheDocument()
  })
})
