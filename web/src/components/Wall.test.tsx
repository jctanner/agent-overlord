import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import { Wall } from './Wall'
import type { WallEvent } from '../types'

const event: WallEvent = {
  event_id: 'event-1',
  created_at: '2026-07-16T17:00:00Z',
  actor: 'observer',
  kind: 'state_changed',
  message: 'Worker changed',
  worker_id: null,
  host: 'laptop',
  intent: null,
  severity: 'info',
  data: {},
}

test('follows by default and only pauses through the explicit control', () => {
  render(<Wall events={[event]} />)
  expect(screen.getByRole('button', { name: 'Following' })).toHaveClass('active')

  fireEvent.scroll(screen.getByLabelText('Council wall activity'))
  expect(screen.getByRole('button', { name: 'Following' })).toHaveClass('active')

  fireEvent.click(screen.getByRole('button', { name: 'Following' }))
  expect(screen.getByRole('button', { name: 'Paused' })).not.toHaveClass('active')
})
