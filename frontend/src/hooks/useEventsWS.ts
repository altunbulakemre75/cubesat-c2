import { useAppStore } from '../store'
import type { AppEvent } from '../types'
import { useTicketedSocket } from './useTicketedSocket'

export function useEventsWS(): void {
  const pushEvent = useAppStore((s) => s.pushEvent)
  useTicketedSocket<AppEvent>('/ws/events', pushEvent)
}
