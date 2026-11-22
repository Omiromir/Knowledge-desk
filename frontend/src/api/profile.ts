import { ApiError, requestJson } from './client'
import { isObject } from './types'
import { API_BASE_URL } from '../config'

function profileFrom(value: unknown) {
  if (!isObject(value) || typeof value.display_name !== 'string' ||
    (value.avatar_version !== null && typeof value.avatar_version !== 'string')) {
    throw new ApiError('Сервер вернул некорректные данные профиля.')
  }
  return { display_name: value.display_name, avatar_version: value.avatar_version }
}

export async function getProfile(signal: AbortSignal) {
  return profileFrom(await requestJson('/profile', { signal }))
}

export async function saveDisplayName(displayName: string) {
  return profileFrom(await requestJson('/profile', {
    method: 'PATCH', body: JSON.stringify({ display_name: displayName }),
  }))
}

export function avatarUrl(version: string) {
  return `${API_BASE_URL}/profile/avatar?v=${encodeURIComponent(version)}`
}

export async function uploadAvatar(file: File) {
  return profileFrom(await requestJson('/profile/avatar', {
    method: 'PUT', body: file, headers: { 'Content-Type': file.type || 'application/octet-stream' },
  }))
}

export async function removeAvatar() {
  return profileFrom(await requestJson('/profile/avatar', { method: 'DELETE' }))
}

export async function changePassword(current: string, next: string) {
  await requestJson('/profile/password', {
    method: 'PUT', timeoutMs: 30000,
    body: JSON.stringify({ current_password: current, new_password: next }),
  })
}
