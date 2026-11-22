import { createContext } from 'react'
import type { getProfile } from '../../api/profile'
import type { useResource } from '../../hooks/useResource'

export const CurrentProfile = createContext<ReturnType<typeof useResource<Awaited<ReturnType<typeof getProfile>>>> | null>(null)
