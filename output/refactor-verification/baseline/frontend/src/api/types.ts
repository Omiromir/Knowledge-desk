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

export interface MessageAuthor {
  display_name?: string
  avatar_version?: string
  is_self?: boolean
}

export interface RequestReply {
  id: string
  text: string
  created_at: string
  edited_at?: string
  author?: MessageAuthor
}

const certificateEmailStatuses = ['pending', 'accepted', 'delayed', 'delivered', 'not_delivered', 'unknown'] as const
export type CertificateEmailStatus = (typeof certificateEmailStatuses)[number]

export interface CertificateClarification {
  id: string
  question: string
  asked_at: string
  answer?: string
  answered_at?: string
  question_author?: MessageAuthor
  answer_author?: MessageAuthor
}

export interface RequestRecord extends RequestInput {
  id: string
  status: RequestStatus
  author?: MessageAuthor
  replies?: RequestReply[]
  created_at?: string
  kind?: 'employment_certificate'
  certificate_downloadable?: boolean
  certificate_owned?: boolean
  certificate_email_status?: CertificateEmailStatus
  certificate_awaiting_reply?: boolean
  certificate_clarifications?: CertificateClarification[]
  certificate_can_answer?: boolean
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

function isMessageAuthor(value: unknown): value is MessageAuthor | undefined {
  return value === undefined || (isObject(value) &&
    (value.display_name === undefined || typeof value.display_name === 'string') &&
    (value.avatar_version === undefined || typeof value.avatar_version === 'string') &&
    (value.is_self === undefined || typeof value.is_self === 'boolean'))
}

export function isRequest(value: unknown): value is RequestRecord {
  return (
    isObject(value) &&
    isMessageAuthor(value.author) &&
    (value.status === 'new' || value.status === 'in_progress' || value.status === 'resolved') &&
    (value.kind === undefined || value.kind === 'employment_certificate') &&
    (value.certificate_downloadable === undefined || typeof value.certificate_downloadable === 'boolean') &&
    (value.certificate_owned === undefined || typeof value.certificate_owned === 'boolean') &&
      (value.certificate_email_status === undefined ||
        certificateEmailStatuses.some(status => status === value.certificate_email_status)) &&
      (value.certificate_awaiting_reply === undefined || typeof value.certificate_awaiting_reply === 'boolean') &&
      (value.certificate_can_answer === undefined || typeof value.certificate_can_answer === 'boolean') &&
      (value.certificate_clarifications === undefined ||
        (Array.isArray(value.certificate_clarifications) && value.certificate_clarifications.every(entry =>
          isObject(entry) && typeof entry.id === 'string' && typeof entry.question === 'string' &&
          isMessageAuthor(entry.question_author) && isMessageAuthor(entry.answer_author) &&
          typeof entry.asked_at === 'string' && Number.isFinite(Date.parse(entry.asked_at)) &&
          (entry.answer === undefined ? entry.answered_at === undefined :
            typeof entry.answer === 'string' && typeof entry.answered_at === 'string' &&
            Number.isFinite(Date.parse(entry.answered_at))),
        ))) &&
    (value.document_id === undefined ||
      typeof value.document_id === 'string') &&
    (value.created_at === undefined ||
      (typeof value.created_at === 'string' && Number.isFinite(Date.parse(value.created_at)))) &&
    (value.replies === undefined ||
      (Array.isArray(value.replies) && value.replies.every((reply) =>
        isObject(reply) &&
        isMessageAuthor(reply.author) &&
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
