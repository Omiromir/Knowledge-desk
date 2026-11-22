import { ApiError, requestJson } from './client'
import { isObject } from './types'

export const requestTypeLabels = {
  course_question: 'Вопрос о курсе',
  information_request: 'Запрос информации',
  consultation: 'Консультация',
  out_of_scope: 'Вне темы',
}

export const MAX_SUMMARY_INPUT = 4000

const summaryErrors: Record<string, string> = {
  local_limit: 'Исчерпан лимит подготовки резюме для этой сессии сервера.',
  provider_rate_limit:
    'Модель временно ограничила запросы. Повторите чуть позже.',
  provider_timeout: 'Модель не ответила за 15 секунд. Попробуйте ещё раз.',
  provider_unavailable: 'Модель временно недоступна. Попробуйте ещё раз.',
  provider_refused:
    'Модель отклонила запрос. Уточните вопрос и попробуйте ещё раз.',
  invalid_summary: 'Модель вернула некорректное резюме. Попробуйте ещё раз.',
  not_configured:
    'Модель не настроена на сервере. Заявку можно отправить как обычно.',
}

export interface RequestSummary {
  summary: string
  request_type: keyof typeof requestTypeLabels
  needs_human: boolean
}

export async function summarizeRequest(text: string): Promise<RequestSummary> {
  let result: unknown
  try {
    result = await requestJson('/ai/summary', {
      method: 'POST',
      body: JSON.stringify({ text }),
      timeoutMs: 20000,
    })
  } catch (error) {
    if (error instanceof ApiError) {
      const message =
        error.status === 422
          ? `Для резюме нужен вопрос длиной до ${MAX_SUMMARY_INPUT} символов.`
          : error.code && Object.hasOwn(summaryErrors, error.code)
            ? summaryErrors[error.code]
            : null
      if (message) throw new ApiError(message, error.status, {}, error.code)
    }
    throw error
  }
  if (
    !isObject(result) ||
    typeof result.summary !== 'string' ||
    !result.summary.trim() ||
    typeof result.needs_human !== 'boolean' ||
    (result.request_type !== 'course_question' &&
      result.request_type !== 'information_request' &&
      result.request_type !== 'consultation' &&
      result.request_type !== 'out_of_scope')
  ) {
    throw new ApiError('Сервер вернул некорректное резюме.')
  }
  return {
    summary: result.summary,
    request_type: result.request_type,
    needs_human: result.needs_human,
  }
}
