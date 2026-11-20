import { API_BASE_URL } from '../config'
import { isObject } from './types'

export class ApiError extends Error {
  status: number
  fields: Record<string, string>
  code: string | undefined

  constructor(
    message: string,
    status = 0,
    fields: Record<string, string> = {},
    code?: string,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.fields = fields
    this.code = code
  }
}

function responseError(status: number, payload: unknown): ApiError {
  const fields: Record<string, string> = {}
  const detail = isObject(payload) ? payload.detail : undefined
  if (Array.isArray(detail)) {
    for (const issue of detail) {
      if (!isObject(issue) || !Array.isArray(issue.loc)) continue
      const field = issue.loc.at(-1)
      if (field === 'name' || field === 'question') {
        fields[field] =
          field === 'name' ? 'Укажите ваше имя.' : 'Напишите ваш вопрос.'
      }
    }
  }
  const message =
    status === 422
      ? 'Проверьте поля формы и отправьте заявку ещё раз.'
      : status === 404
        ? 'Запись не найдена. Возможно, её удалили или выбрана другая база.'
        : status >= 500
          ? 'На сервере произошла ошибка. Попробуйте ещё раз позже.'
          : typeof detail === 'string'
            ? detail
            : `Сервер отклонил запрос (${status}).`
  const code =
    isObject(detail) && typeof detail.code === 'string'
      ? detail.code
      : undefined
  return new ApiError(message, status, fields, code)
}

export async function requestJson(
  path: string,
  { timeoutMs = 12000, ...options }: RequestInit & { timeoutMs?: number } = {},
): Promise<unknown> {
  const deadline = AbortSignal.timeout(timeoutMs)
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      credentials: 'include',
      ...options,
      headers: {
        Accept: 'application/json',
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers,
      },
      signal: options.signal
        ? AbortSignal.any([options.signal, deadline])
        : deadline,
    })
  } catch (error) {
    if (options.signal?.aborted) throw error
    if (deadline.aborted)
      throw new ApiError(
        'Сервер не ответил вовремя. Попробуйте ещё раз.',
        0,
        {},
        'client_timeout',
      )
    throw new ApiError(
      'Нет связи с сервером. Проверьте, запущен ли API, и повторите попытку.',
    )
  }
  if (response.status === 204) return null
  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    if (deadline.aborted)
      throw new ApiError(
        'Сервер не ответил вовремя. Попробуйте ещё раз.',
        0,
        {},
        'client_timeout',
      )
    throw new ApiError(
      'Сервер вернул некорректный ответ. Повторите попытку.',
      response.status,
    )
  }
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event('knowledge-desk-session-expired'))
    throw responseError(response.status, payload)
  }
  return payload
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : 'Не удалось выполнить действие. Повторите попытку.'
}
