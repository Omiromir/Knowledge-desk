import { ApiError, requestJson } from './client'
import { isDocument, type DocumentRecord } from './types'

const uploadErrors: Record<string, string> = {
  upload_format: 'Выберите PDF, DOCX, TXT или Markdown. Старый файл .doc можно пересохранить как .docx.',
  upload_encoding: 'Сохраните файл в UTF-8 и попробуйте снова.',
  upload_empty: 'В файле нет текста.',
  upload_too_large: 'TXT и Markdown можно загрузить до 64 КиБ.',
  upload_file_too_large: 'PDF и DOCX можно загрузить до 10 МиБ.',
  upload_text_too_large: 'Текста слишком много. Разделите документ на части.',
  upload_pdf_locked: 'PDF защищён паролем. Загрузите копию без пароля.',
  upload_pdf_no_text: 'Текст в PDF прочитать не удалось. Попробуйте загрузить более чёткий скан.',
  upload_ocr_pages: 'В файле больше 20 отсканированных страниц. Разделите PDF на части.',
  upload_ocr_timeout: 'Распознавание заняло слишком много времени. Разделите PDF на части.',
  upload_ocr_busy: 'Сейчас распознаётся другой скан. Дождитесь завершения и загрузите файл снова.',
  upload_ocr_unavailable: 'Не удалось запустить распознавание. Повторите загрузку. Для первой загрузки модели нужен интернет.',
  upload_pdf_pages: 'В PDF больше 150 страниц. Разделите его на части.',
  upload_word_package: 'Файл Word слишком большой после распаковки. Разделите его или уменьшите размер изображений.',
  upload_unreadable: 'Не удалось прочитать файл. Откройте его и сохраните новую копию.',
  upload_index_unavailable:
    'Не удалось обновить поиск по документу. Попробуйте снова.',
  upload_storage_unavailable: 'Не удалось сохранить оригинал PDF. Попробуйте снова.',
}

export async function documentSharing(id: string, options: RequestInit = {}) {
  try {
    const value = await requestJson(`/documents/${encodeURIComponent(id)}/access`, options)
    if (typeof value !== 'object' || value === null || !('shared' in value) || typeof value.shared !== 'boolean')
      throw new ApiError('Не удалось проверить доступ.')
    return value.shared
  } catch (failure) {
    if (!options.method && failure instanceof ApiError && failure.status === 404) return null
    throw failure
  }
}

function documentError(error: unknown): never {
  if (
    error instanceof ApiError &&
    error.code &&
    Object.hasOwn(uploadErrors, error.code)
  ) {
    throw new ApiError(uploadErrors[error.code], error.status, {}, error.code)
  }
  throw error
}

export async function uploadDocument(file: File) {
  const plain = /\.(txt|md)$/i.test(file.name)
  if (file.size > (plain ? 64 * 1024 : 10 * 1024 * 1024)) {
    throw new ApiError(uploadErrors[plain ? 'upload_too_large' : 'upload_file_too_large'])
  }
  const result = await requestJson(
    `/documents/upload?${new URLSearchParams({ filename: file.name })}`,
    {
      method: 'POST',
      body: file,
      headers: { 'Content-Type': 'application/octet-stream' },
      timeoutMs: 180000,
    },
  ).catch(documentError)
  if (!isDocument(result))
    throw new ApiError('Сервер не подтвердил загрузку документа.')
  return result
}

export async function listDocuments(signal?: AbortSignal) {
  const result = await requestJson('/documents', { signal })
  if (!Array.isArray(result) || !result.every(isDocument)) {
    throw new ApiError(
      'Не удалось прочитать список документов. Повторите загрузку.',
    )
  }
  return result
}

export async function getDocument(id: string, signal?: AbortSignal) {
  const result = await requestJson(`/documents/${encodeURIComponent(id)}`, {
    signal,
  })
  if (!isDocument(result))
    throw new ApiError('Сервер вернул неполный документ.')
  return result
}

export type DocumentFields = Pick<DocumentRecord, 'title' | 'source' | 'text'>

export async function updateDocument(id: string, fields: DocumentFields) {
  const result = await requestJson(`/documents/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(fields),
    timeoutMs: 180000,
  }).catch(documentError)
  if (!isDocument(result)) throw new ApiError('Сервер не подтвердил сохранение.')
  return result
}

export async function deleteDocument(id: string) {
  await requestJson(`/documents/${encodeURIComponent(id)}`, { method: 'DELETE' })
}
