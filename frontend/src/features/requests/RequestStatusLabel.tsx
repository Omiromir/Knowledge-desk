import type { RequestRecord } from '../../api/types'
import { workflow } from './requestPresentation'

export function RequestStatusLabel({ record, personal = false }: { record: RequestRecord; personal?: boolean }) {
  const waiting = record.certificate_awaiting_reply
  const label = waiting ? (personal ? 'Нужен ваш ответ' : 'Ждёт ответа') : workflow[record.status].label
  return (
    <span className="request-status" data-status={waiting ? 'awaiting_employee' : record.status}
      aria-live={personal && record.kind === 'employment_certificate' ? 'polite' : undefined}>{label}</span>
  )
}
