const paths = {
  filter: 'M2.5 4h11M4.5 8h7M6.5 12h3',
  more: 'M3.5 8h.1m4.35 0h.1m4.35 0h.1',
  file: 'M4.75 1.75h5.5l3 3v8.5a1 1 0 0 1-1 1h-7.5a1 1 0 0 1-1-1V2.75a1 1 0 0 1 1-1ZM10 2v3h3M6 8h5m-5 2.75h5',
  inbox:
    'M2 8.5 4.1 3h7.8L14 8.5V12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1Zm0 0h3.5l1.1 1.7h2.8l1.1-1.7H14',
  search:
    'M11 6.75a4.25 4.25 0 1 1-8.5 0 4.25 4.25 0 0 1 8.5 0Zm-1.2 3.05 3.45 3.45',
  reload: 'M13.25 6a5.5 5.5 0 1 0 .15 3.5m.1-6v3.25h-3.25',
  back: 'm9.75 3.75-4.25 4.25 4.25 4.25',
  link: 'm6.75 9.25 2.5-2.5M5 10.75l-.5.5a2.3 2.3 0 0 0 3.25 3.25l2.75-2.75a2.3 2.3 0 0 0 0-3.25m-5 0a2.3 2.3 0 0 1 0-3.25L8.25 2.5a2.3 2.3 0 0 1 3.25 3.25l-.5.5',
  compose:
    'M8 3H4a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V8M7 9l.5-2L12 2.5l1.5 1.5L9 8.5Z',
}

export function Icon({ name }: { name: keyof typeof paths }) {
  return (
    <svg
      aria-hidden="true"
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.35"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={paths[name]} />
    </svg>
  )
}
