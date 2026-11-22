import { API_BASE_URL } from '../config'
import { ApiError, requestJson } from './client'
import { isObject, isRequest } from './types'

export const MAX_CERTIFICATE_PDF_BYTES = 5 * 1024 * 1024
export const MAX_CLARIFICATION_LENGTH = 4000

function watchCertificateEvents(
  onUpdate: (data: string) => void, onClosed?: () => void, scope?: 'mine' | { requestId: string },
) {
  const query = scope === 'mine' ? '?mine=true' : scope ? `?request_id=${encodeURIComponent(scope.requestId)}` : ''
  const eventName = scope === 'mine' ? 'certificate-list' : scope ? 'certificate-card' : 'certificate-queue'
  const events = new EventSource(`${API_BASE_URL}/requests/certificate-clarification-events${query}`, { withCredentials: true })
  events.addEventListener(eventName, (event) => {
    const { data } = event as MessageEvent<unknown>
    if (typeof data === 'string') onUpdate(data)
  })
  events.addEventListener('error', () => {
    if (events.readyState === EventSource.CLOSED) onClosed?.()
  })
  return () => events.close()
}

export function watchCertificateUpdates(onUpdate: (revision: string) => void, onClosed: () => void, scope?: { requestId: string }) {
  return watchCertificateEvents(onUpdate, onClosed, scope)
}

export interface OwnCertificateUpdate {
  revision: string
  waiting: number
}

export function watchOwnCertificateUpdates(onUpdate: (update: OwnCertificateUpdate) => void) {
  return watchCertificateEvents((message) => {
    let data: unknown
    try {
      data = JSON.parse(message)
    } catch {
      return
    }
    if (isObject(data) && typeof data.revision === 'string' && typeof data.waiting === 'number'
        && Number.isSafeInteger(data.waiting) && data.waiting >= 0) {
      onUpdate({ revision: data.revision, waiting: data.waiting })
    }
  }, undefined, 'mine')
}

const certificatePath = (id: string, variant: 'draft' | 'issued') =>
  `/requests/${encodeURIComponent(id)}/certificate/${variant}`

export function certificateUrl(id: string, variant: 'draft' | 'issued') {
  return API_BASE_URL + certificatePath(id, variant)
}

export async function requestCertificate(purpose: string) {
  const result = await requestJson('/requests/employment-certificate', {
    method: 'POST', body: JSON.stringify({ purpose }),
  })
  if (!isRequest(result) || result.kind !== 'employment_certificate') {
    throw new ApiError('Сервер не подтвердил заявку на справку.')
  }
  return result
}

export async function issueCertificate(id: string, file: File) {
  const result = await requestJson(certificatePath(id, 'issued'), {
    method: 'PUT', headers: { 'Content-Type': 'application/pdf' }, body: file,
  })
  if (!isRequest(result) || result.id !== id || result.kind !== 'employment_certificate' || result.status !== 'resolved') {
    throw new ApiError('Сервер не подтвердил выдачу справки. Обновите заявку.')
  }
  return result
}

export async function sendCertificateClarification(requestId: string, clarificationId: string, text: string, answer: boolean) {
  const base = `/requests/${encodeURIComponent(requestId)}/certificate/clarifications`
  const result = await requestJson(answer ? `${base}/${encodeURIComponent(clarificationId)}/answer` : base, {
    method: answer ? 'PUT' : 'POST',
    body: JSON.stringify(answer ? { text } : { id: clarificationId, text }),
  })
  if (!isRequest(result) || result.id !== requestId || !result.certificate_clarifications?.some(entry =>
    entry.id === clarificationId && (answer ? entry.answer : entry.question) === text,
  )) {
    throw new ApiError('Сервер не подтвердил сохранение. Попробуйте ещё раз.')
  }
  return result
}
