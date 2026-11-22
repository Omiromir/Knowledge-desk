import { useContext } from 'react'
import { avatarUrl } from '../../api/profile'
import { profileHref } from '../../navigation/routes'
import { CurrentProfile } from './currentProfileContext'
import { ProfileAvatar } from './ProfileAvatar'

export function AccountProfileLink({ email, role, active }: { email: string; role?: string; active: boolean }) {
  const profile = useContext(CurrentProfile)?.data
  const name = profile?.display_name
  return (
    <a className="account-profile-link" href={profileHref} aria-current={active ? 'page' : undefined}
      aria-label={name ? `Мой профиль — ${name}` : 'Мой профиль'} title={name || 'Мой профиль'}>
      <ProfileAvatar className="account-avatar" name={name || email} size={28}
        src={profile?.avatar_version ? avatarUrl(profile.avatar_version) : undefined} />
      <span className="account-identity"><b>{name || 'Мой профиль'}</b><span title={role}>{role}</span></span>
    </a>
  )
}
