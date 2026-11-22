import { useContext } from 'react'
import { avatarUrl } from '../../api/profile'
import type { MessageAuthor } from '../../api/types'
import { API_BASE_URL } from '../../config'
import { CurrentProfile } from '../profile/currentProfileContext'

export function useMessageAuthor(author: MessageAuthor | undefined, fallback: string, requestId: string) {
  const own = useContext(CurrentProfile)?.data
  const profile = author?.is_self && own ? own : author
  const version = profile?.avatar_version
  return {
    name: profile?.display_name || fallback,
    src: version ? (author?.is_self
      ? avatarUrl(version)
      : `${API_BASE_URL}/requests/${encodeURIComponent(requestId)}/avatars/${encodeURIComponent(version)}`) : undefined,
  }
}
