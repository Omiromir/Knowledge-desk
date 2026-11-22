export function ProfileAvatar({ name, src, size, className }: {
  name: string
  src?: string
  size: number
  className: string
}) {
  return (
    <span className={`${className} profile-avatar-image`} aria-hidden="true">
      {Array.from(name.trim())[0]?.toLocaleUpperCase()}
      {src && <img key={src} src={src} alt="" width={size} height={size}
        onError={(event) => { event.currentTarget.hidden = true }} />}
    </span>
  )
}
