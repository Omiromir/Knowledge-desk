import type { RequestStatus } from '../../api/types'

export const workflow: Record<RequestStatus, { label: string; action: string; next: RequestStatus }> = {
  new: { label: 'Новая', action: 'Взять в работу', next: 'in_progress' },
  in_progress: { label: 'В работе', action: 'Закрыть заявку', next: 'resolved' },
  resolved: { label: 'Закрыта', action: 'Возобновить', next: 'in_progress' },
}
export const requestDateTime = new Intl.DateTimeFormat('ru-RU', {
  day: 'numeric', month: 'long', year: 'numeric',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
})
