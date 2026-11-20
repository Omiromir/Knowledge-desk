export interface DocumentRecord {
  id: string
  title: string
  text: string
  source: string
}

export interface RequestInput {
  name: string
  question: string
  document_id?: string
}

export type RequestStatus = 'new' | 'in_progress' | 'resolved'

export interface RequestReply {
  id: string
  text: string
  created_at: string
  edited_at?: string
}

export interface RequestRecord extends RequestInput {
  id: string
  status: RequestStatus
  replies?: RequestReply[]
  created_at?: string
}

export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isDocument(value: unknown): value is DocumentRecord {
  return (
    isObject(value) &&
    ['id', 'title', 'text', 'source'].every(
      (key) => typeof value[key] === 'string' && value[key].length > 0,
    )
  )
}

export function isRequest(value: unknown): value is RequestRecord {
  return (
    isObject(value) &&
    (value.status === 'new' || value.status === 'in_progress' || value.status === 'resolved') &&
    (value.document_id === undefined ||
      typeof value.document_id === 'string') &&
    (value.created_at === undefined ||
      (typeof value.created_at === 'string' && Number.isFinite(Date.parse(value.created_at)))) &&
    (value.replies === undefined ||
      (Array.isArray(value.replies) && value.replies.every((reply) =>
        isObject(reply) &&
        typeof reply.id === 'string' &&
        typeof reply.text === 'string' &&
        typeof reply.created_at === 'string' &&
        Number.isFinite(Date.parse(reply.created_at)) &&
        (reply.edited_at === undefined || typeof reply.edited_at === 'string'),
      ))) &&
    ['id', 'name', 'question'].every(
      (key) => typeof value[key] === 'string' && value[key].length > 0,
    )
  )
}
