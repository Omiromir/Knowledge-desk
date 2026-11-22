import { ApiError, requestJson } from './client'
import { isObject } from './types'

const statuses = ['pending', 'delivered', 'failed', 'unknown'] as const

export type Delivery = {
  event_id: string
  request_id: string
  status: (typeof statuses)[number]
  reason: string | null
  receipt: { status: 'accepted'; event_id: string; request_id: string; execution_id: string } | null
  updated_at: string
  can_retry: boolean
}

export function isDelivery(value: unknown): value is Delivery {
  if (!isObject(value) || typeof value.event_id !== 'string' || typeof value.request_id !== 'string' ||
    !statuses.some((status) => status === value.status) || (value.reason !== null && typeof value.reason !== 'string') ||
    typeof value.updated_at !== 'string' || typeof value.can_retry !== 'boolean') return false
  if (value.status !== 'delivered') return value.receipt === null
  return isObject(value.receipt) && value.receipt.status === 'accepted' && value.receipt.event_id === value.event_id &&
    value.receipt.request_id === value.request_id && typeof value.receipt.execution_id === 'string'
}

export async function getDelivery(requestId: string, signal: AbortSignal): Promise<Delivery | null> {
  const value = await requestJson(`/requests/${encodeURIComponent(requestId)}/delivery`, { signal })
  if (value === null) return null
  if (!isDelivery(value) || value.request_id !== requestId) throw new ApiError('Сервер вернул неполный статус доставки.')
  return value
}

export async function retryDelivery(requestId: string, eventId: string): Promise<Delivery> {
  const value = await requestJson(`/requests/${encodeURIComponent(requestId)}/delivery/retry`, { method: 'POST' })
  if (!isDelivery(value) || value.request_id !== requestId || value.event_id !== eventId)
    throw new ApiError('Не удалось сверить ID события. Обновите статус доставки.')
  return value
}
