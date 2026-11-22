import { MAX_ASK_LENGTH } from './ask'
import { ApiError, requestJson } from './client'
import { isObject } from './types'

export const MAX_TOOL_MESSAGE_LENGTH = MAX_ASK_LENGTH

type Fragment = { chunk_id: string; title: string; text: string }
type Draft = { name: string; question: string }
type ToolName = 'search_documents' | 'prepare_request'
type ToolError = { status: 'error'; code: string; reason: string; retryable: boolean; attempts: number }
type Execution =
  | { tool: 'search_documents'; result: { status: 'success'; data: { fragments: Fragment[] }; attempts: number } }
  | { tool: 'prepare_request'; result: { status: 'success'; data: Draft; attempts: number } }
  | { tool: ToolName | null; result: ToolError }

export type ToolTurn = { execution: Execution | null; message: string | null }

function isFragment(value: unknown): value is Fragment {
  return isObject(value) && [value.chunk_id, value.title, value.text].every((part) => typeof part === 'string' && part.trim())
}

function isExecution(value: unknown): value is Execution {
  if (!isObject(value) || !isObject(value.result)) return false
  const { tool, result } = value
  const validTool = tool === 'search_documents' || tool === 'prepare_request'
  if (typeof result.attempts !== 'number' || !Number.isInteger(result.attempts) || result.attempts < 0 || result.attempts > 2) return false
  if (result.status === 'error') {
    return (validTool || tool === null) && typeof result.reason === 'string' &&
      typeof result.code === 'string' && typeof result.retryable === 'boolean'
  }
  if (result.status !== 'success' || !isObject(result.data) || result.attempts === 0) return false
  if (tool === 'search_documents') {
    const { fragments } = result.data
    return Array.isArray(fragments) && fragments.length <= 5 && fragments.every(isFragment)
  }
  return tool === 'prepare_request' && typeof result.data.name === 'string' && Boolean(result.data.name.trim()) &&
    typeof result.data.question === 'string' && Boolean(result.data.question.trim())
}

const serverMessages: Record<string, string> = {
  not_configured: 'Модель помощника не настроена на сервере.',
  provider_timeout: 'Модель не ответила вовремя. Можно повторить запрос.',
  provider_rate_limit: 'Модель ограничила запросы. Повторите чуть позже.',
  local_limit: 'Лимит запросов к помощнику на этом запуске сервера исчерпан.',
  forbidden_tool: 'Инструменты недоступны для текущего пользователя.',
  invalid_tool_proposal: 'Модель вернула некорректное предложение. Можно повторить запрос.',
}

export async function runAssistant(message: string): Promise<ToolTurn> {
  let value: unknown
  try {
    value = await requestJson('/ai/tools', { method: 'POST', body: JSON.stringify({ message }), timeoutMs: 40000 })
  } catch (error) {
    if (error instanceof ApiError && error.code && Object.hasOwn(serverMessages, error.code)) {
      throw new ApiError(serverMessages[error.code], error.status, {}, error.code)
    }
    throw error
  }
  if (!isObject(value) || (value.message !== null && typeof value.message !== 'string') ||
    (value.execution !== null && !isExecution(value.execution)) || (!value.execution && !value.message)) {
    throw new ApiError('Сервер вернул некорректный результат помощника.')
  }
  return { execution: value.execution, message: value.message }
}
