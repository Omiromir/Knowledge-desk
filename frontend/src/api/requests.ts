import { ApiError, requestJson } from './client'
import { isRequest, type RequestInput, type RequestStatus } from './types'

export const MAX_REQUEST_REPLY_LENGTH = 10000

export async function listRequests(signal: AbortSignal) {
  const result = await requestJson('/requests', { signal })
  if (!Array.isArray(result) || !result.every(isRequest)) throw new ApiError('Сервер вернул неполный список заявок.')
  return result
}

export async function createRequest(input: RequestInput) {
  const result = await requestJson('/requests', {
    method: 'POST',
    body: JSON.stringify(input),
  })
  if (!isRequest(result))
    throw new ApiError('Отправка не подтверждена: в ответе нет данных заявки.')
  return result
}

export async function getRequest(id: string, signal?: AbortSignal) {
  const result = await requestJson(`/requests/${encodeURIComponent(id)}`, {
    signal,
  })
  if (!isRequest(result)) throw new ApiError('Сервер вернул неполную заявку.')
  return result
}

export async function updateRequestStatus(id: string, expected: RequestStatus, status: RequestStatus) {
  const result = await requestJson(`/requests/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ expected_status: expected, status }),
  })
  if (!isRequest(result) || result.id !== id || result.status !== status) {
    throw new ApiError('Сервер не подтвердил изменение статуса.')
  }
  return result
}

export async function replyToRequest(id: string, replyId: string, text: string) {
  const result = await requestJson(`/requests/${encodeURIComponent(id)}/replies`, {
    method: 'POST',
    body: JSON.stringify({ id: replyId, text }),
  })
  if (!isRequest(result) || result.id !== id || !result.replies?.some((reply) => reply.id === replyId)) {
    throw new ApiError('Сервер не подтвердил сохранение ответа. Попробуйте ещё раз.')
  }
  return result
}

export async function changeRequestReply(id: string, replyId: string, text: string | null) {
  const result = await requestJson(
    `/requests/${encodeURIComponent(id)}/replies/${encodeURIComponent(replyId)}`,
    text === null
      ? { method: 'DELETE' }
      : { method: 'PATCH', body: JSON.stringify({ text }) },
  )
  if (!isRequest(result) || result.id !== id) {
    throw new ApiError('Сервер не подтвердил изменение ответа.')
  }
  const reply = result.replies?.find((entry) => entry.id === replyId)
  if (text === null ? reply !== undefined : reply?.text !== text) {
    throw new ApiError('Сервер не подтвердил изменение ответа.')
  }
  return result
}
