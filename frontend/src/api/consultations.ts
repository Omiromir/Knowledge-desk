import { ApiError, requestJson } from './client'
import { isDelivery, type Delivery } from './delivery'
import { isObject } from './types'

export type ConsultationInput = { name: string; question: string }
type Source = { chunk_id: string; title: string; text: string }
const statuses = ['draft', 'awaiting_confirmation', 'submitted', 'failed', 'cancelled'] as const

export type Consultation = ConsultationInput & {
  draft_id: string
  version: number
  status: (typeof statuses)[number]
  stop_code: string | null
  request_id: string | null
  sources: Source[]
  delivery: Delivery | null
}

function isSource(value: unknown): value is Source {
  return isObject(value) && [value.chunk_id, value.title, value.text].every((text) => typeof text === 'string')
}

function isConsultation(value: unknown): value is Consultation {
  return isObject(value) && typeof value.draft_id === 'string' && typeof value.version === 'number' &&
    Number.isInteger(value.version) && value.version > 0 && typeof value.name === 'string' &&
    typeof value.question === 'string' && statuses.some((status) => status === value.status) &&
    (value.stop_code === null || typeof value.stop_code === 'string') &&
    (value.request_id === null || typeof value.request_id === 'string') &&
    (value.status === 'submitted' ? Boolean(value.request_id) : value.request_id === null) &&
    (value.delivery === null || (isDelivery(value.delivery) && value.delivery.request_id === value.request_id)) &&
    Array.isArray(value.sources) && value.sources.every(isSource)
}

const conflicts: Record<string, string> = {
  stale_version: 'Версия черновика изменилась. Откройте актуальные данные и проверьте их перед подтверждением.',
  payload_conflict: 'Данные отличаются от сохранённой версии. Обновите черновик и проверьте поля.',
  not_awaiting_confirmation: 'Эта версия больше не ожидает подтверждения. Обновите черновик.',
  draft_closed: 'Черновик уже закрыт. Обновите его состояние.',
  forbidden_consultation: 'Сценарий консультации недоступен для текущего пользователя.',
}

export async function consultation(
  path = '', method = 'GET', body?: ConsultationInput | { version: number }, signal?: AbortSignal,
): Promise<Consultation> {
  let result: unknown
  try {
    result = await requestJson(`/consultations${path}`, {
      method, body: body ? JSON.stringify(body) : undefined, signal, timeoutMs: 22000,
    })
  } catch (error) {
    if (error instanceof ApiError && error.code && Object.hasOwn(conflicts, error.code)) {
      throw new ApiError(conflicts[error.code], error.status, {}, error.code)
    }
    throw error
  }
  if (!isConsultation(result)) throw new ApiError('Сервер вернул некорректный черновик.')
  return result
}
