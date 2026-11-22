import { ApiError, requestJson } from './client'
import { isObject } from './types'
import { API_BASE_URL } from '../config'

export const MAX_ASK_LENGTH = 1200

interface Citation {
  chunk_id: string
  doc_id: string
  title: string
  text: string
  source?: string
  version_date?: string
  page_number?: number
}

interface SourceExcerpt extends Citation {
  before: string
  after: string
}

export interface AskResult {
  answer: string
  answerable: boolean
  citations: Citation[]
}

function isCitation(value: unknown): value is Citation {
  return (
    isObject(value) &&
    ['chunk_id', 'doc_id', 'title', 'text'].every(
      (key) => typeof value[key] === 'string' && value[key].length > 0,
    ) &&
    (value.source === undefined || typeof value.source === 'string') &&
    (value.version_date === undefined || typeof value.version_date === 'string') &&
    (value.page_number === undefined ||
      (typeof value.page_number === 'number' && Number.isInteger(value.page_number) && value.page_number > 0))
  )
}

function pdfUrl(docId: string) {
  return `${API_BASE_URL}/documents/${encodeURIComponent(docId)}/original.pdf`
}

export function pdfPageUrl(docId: string, page: number) {
  // Fit the selected page vertically so a short last page is the active page.
  return `${pdfUrl(docId)}#page=${page}&view=FitV`
}

export function pdfPreviewUrl(docId: string, page: number) {
  return `${pdfUrl(docId)}?page=${page}#view=FitH&toolbar=0&navpanes=0`
}

const errors: Record<string, string> = {
  index_unavailable:
    'Поиск недоступен. Попробуйте снова.',
  not_configured: 'Модель ответов не настроена на сервере.',
  provider_timeout: 'Модель не ответила вовремя. Попробуйте снова.',
  provider_rate_limit:
    'Модель временно ограничила запросы. Повторите чуть позже.',
  invalid_citation: 'Не удалось проверить источники ответа. Попробуйте снова.',
  local_limit: 'Лимит ответов на этой сессии сервера исчерпан.',
}

export async function askQuestion(question: string): Promise<AskResult> {
  let result: unknown
  try {
    result = await requestJson('/ask', {
      method: 'POST',
      body: JSON.stringify({ question }),
      timeoutMs: 30000,
    })
  } catch (error) {
    if (
      error instanceof ApiError &&
      error.code &&
      Object.hasOwn(errors, error.code)
    )
      throw new ApiError(errors[error.code], error.status, {}, error.code)
    throw error
  }
  if (
    !isObject(result) ||
    typeof result.answer !== 'string' ||
    !result.answer.trim() ||
    typeof result.answerable !== 'boolean' ||
    !Array.isArray(result.citations) ||
    !result.citations.every(isCitation) ||
    result.answerable !== result.citations.length > 0
  )
    throw new ApiError('Сервер вернул некорректный ответ.')
  return {
    answer: result.answer,
    answerable: result.answerable,
    citations: result.citations,
  }
}

export async function getAskSource(
  chunkId: string,
  signal: AbortSignal,
): Promise<SourceExcerpt> {
  const result = await requestJson(
    `/ask/sources/${encodeURIComponent(chunkId)}`,
    { signal },
  )
  if (
    !isObject(result) ||
    typeof result.before !== 'string' ||
    typeof result.after !== 'string' ||
    !isCitation(result) ||
    result.chunk_id !== chunkId
  ) {
    throw new ApiError('Сервер вернул некорректный источник.')
  }
  return { ...result, before: result.before, after: result.after }
}
