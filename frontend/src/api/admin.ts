import { ApiError, requestJson } from './client'
import { isObject } from './types'

export const accessLabels = {
  reader: 'Читатель',
  editor: 'Редактор',
  operator: 'Оператор',
  editor_operator: 'Редактор и оператор',
} as const

export type AccessPreset = keyof typeof accessLabels
export type MemberAccess = { preset: AccessPreset }
export type DirectoryMember = {
  user_id: string
  email: string
  preset: AccessPreset | null
  certificate_issuer: boolean
}

function parseMember(value: unknown): DirectoryMember {
  if (!isObject(value) || typeof value.user_id !== 'string' || typeof value.email !== 'string' ||
    typeof value.certificate_issuer !== 'boolean' ||
    (value.preset !== null && (typeof value.preset !== 'string' || !Object.hasOwn(accessLabels, value.preset)))) {
    throw new ApiError('Сервер вернул неполные данные пользователя.')
  }
  return value as DirectoryMember
}

export async function listMembers(signal: AbortSignal) {
  const value = await requestJson('/admin/members', { signal })
  if (!isObject(value) || !Array.isArray(value.members) || typeof value.account_creation_enabled !== 'boolean') {
    throw new ApiError('Сервер вернул неполный список пользователей.')
  }
  return { members: value.members.map(parseMember), account_creation_enabled: value.account_creation_enabled }
}

export async function createMember(input: MemberAccess & { email: string; password: string }) {
  return parseMember(await requestJson('/admin/members', {
    method: 'POST', body: JSON.stringify(input),
  }))
}

export async function setMemberAccess(userId: string, input: MemberAccess) {
  return parseMember(await requestJson(`/admin/members/${encodeURIComponent(userId)}/access`, {
    method: 'PUT', body: JSON.stringify(input),
  }))
}

export async function setCertificateIssuer(userId: string, allowed: boolean) {
  return parseMember(await requestJson(`/admin/members/${encodeURIComponent(userId)}/certificate-issuer`, {
    method: 'PUT', body: JSON.stringify({ allowed }),
  }))
}
