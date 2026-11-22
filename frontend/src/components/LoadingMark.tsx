export function LoadingMark({ compact = false }: { compact?: boolean }) {
  return (
    <span className={compact ? 'desk-loader is-compact' : 'desk-loader'} aria-hidden="true">
      <span className="desk-loader-page" />
      <span className="desk-loader-page" />
      <span className="desk-loader-page"><span className="desk-loader-lines" /></span>
    </span>
  )
}
