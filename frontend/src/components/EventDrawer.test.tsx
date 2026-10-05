import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { makeArtifact, makeEvent } from '../test/fixtures'
import { EventDrawer } from './EventDrawer'

const noop = () => undefined

describe('EventDrawer', () => {
  it('prompts when nothing is selected', () => {
    render(<EventDrawer event={null} artifacts={[]} timeZone="UTC" onClose={noop} />)
    expect(screen.getByText(/Select an event/)).toBeInTheDocument()
  })

  it('never turns an evidence URL into a link, and defangs it', () => {
    const event = makeEvent({ url: 'javascript:alert(document.cookie)', title: 'phish' })
    const { container } = render(<EventDrawer event={event} artifacts={[]} timeZone="UTC" onClose={noop} />)
    expect(container.querySelectorAll('a')).toHaveLength(0)
    expect(screen.getByText(/javascript:alert\(document\[\.\]cookie\)/)).toBeInTheDocument()
  })

  it('defangs ordinary web URLs too, so they cannot be clicked by accident', () => {
    const event = makeEvent({ url: 'https://evil.example.com/login' })
    const { container } = render(<EventDrawer event={event} artifacts={[]} timeZone="UTC" onClose={noop} />)
    expect(container.querySelectorAll('a')).toHaveLength(0)
    expect(screen.getByText('https[://]evil[.]example[.]com/login')).toBeInTheDocument()
  })

  it('renders hostile titles as text, not markup', () => {
    const event = makeEvent({ title: '<img src=x onerror=alert(1)>' })
    const { container } = render(<EventDrawer event={event} artifacts={[]} timeZone="UTC" onClose={noop} />)
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByRole('heading', { level: 3 })).toHaveTextContent('<img src=x onerror=alert(1)>')
  })

  it('flags assumed local time and names the assumed zone', () => {
    const event = makeEvent({ ts_basis: 'assumed', tz_assumed: 'Asia/Kolkata' })
    render(<EventDrawer event={event} artifacts={[]} timeZone="UTC" onClose={noop} />)
    const basis = screen.getByText(/Assumed: the source had no timezone/)
    expect(basis).toHaveTextContent('Asia/Kolkata')
    expect(basis).toHaveClass('warn-text')
  })

  it('shows the artifact it came from', () => {
    const event = makeEvent()
    render(
      <EventDrawer
        event={event}
        artifacts={[makeArtifact({ original_name: 'History', parser: 'chromium_history' })]}
        timeZone="UTC"
        onClose={noop}
      />,
    )
    expect(screen.getByText('History')).toBeInTheDocument()
    expect(screen.getByText(/chromium_history/)).toBeInTheDocument()
  })

  it('adds a second clock when the display zone is not UTC', () => {
    const event = makeEvent({ ts_ms: Date.UTC(2024, 5, 15, 10, 0, 0) })
    render(<EventDrawer event={event} artifacts={[]} timeZone="Asia/Kolkata" onClose={noop} />)
    expect(screen.getByText('2024-06-15 10:00:00.000')).toBeInTheDocument() // UTC
    expect(screen.getByText('2024-06-15 15:30:00.000')).toBeInTheDocument() // IST
  })

  it('offers copy for coordinates and closes on request', async () => {
    const onClose = vi.fn()
    const event = makeEvent({ source: 'location', lat: 12.9716, lon: 77.5946 })
    render(<EventDrawer event={event} artifacts={[]} timeZone="UTC" onClose={onClose} />)
    expect(screen.getByText('12.971600, 77.594600')).toBeInTheDocument()
    screen.getByRole('button', { name: 'Close' }).click()
    expect(onClose).toHaveBeenCalledOnce()
  })
})
