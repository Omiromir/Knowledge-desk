import type { ReactNode } from 'react'
import { getProfile } from '../../api/profile'
import { useResource } from '../../hooks/useResource'

import { CurrentProfile } from './currentProfileContext'

export function CurrentProfileProvider({ children }: { children: ReactNode }) {
  const profile = useResource(getProfile)
  return <CurrentProfile.Provider value={profile}>{children}</CurrentProfile.Provider>
}
