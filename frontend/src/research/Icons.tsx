import type { CSSProperties, ReactNode } from 'react'

const paths: Record<string, ReactNode> = {
  sun: <><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/></>,
  moon: <path d="M20.5 13A8.5 8.5 0 0 1 11 3.5 8.5 8.5 0 1 0 20.5 13Z"/>,
  monitor: <><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M12 17v4m-4 0h8"/></>,
  chart: <><path d="M4 3v17h17M8 16v-5m5 5V7m5 9v-7"/></>,
  wave: <><path d="M2 13h4l3-8 5 15 3-10 2 3h3"/></>,
  compare: <><path d="M3 7h16m-4-4 4 4-4 4M21 17H5m4-4-4 4 4 4"/></>,
  translate: <><path d="M2 5h12M8 2v3m-3 0c0 6 3 9 8 11M12 5c0 5-3 9-9 12m11 4 4-12 4 12m-6-4h4"/></>,
  research: <><path d="m12 3 2.3 6.7L21 12l-6.7 2.3L12 21l-2.3-6.7L3 12l6.7-2.3Z"/><path d="m20 2 .5 1.5L22 4l-1.5.5L20 6l-.5-1.5L18 4l1.5-.5Z"/></>,
  book: <><path d="M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Z"/><path d="M12 5v15M6 8h3m6 0h3M6 11h3m6 0h3"/></>,
  file: <><path d="M14 3H5v18h14V8Z"/><path d="M14 3v5h5M8 12h8m-8 4h6"/></>,
  evidence: <><rect x="4" y="4" width="16" height="16" rx="3"/><path d="m8 12 3 3 5-6M8 2v4m8-4v4M8 18v4m8-4v4"/></>,
  history: <><path d="M3 11a9 9 0 1 1 2.7 7M3 4v7h7m2-4v6l4 2"/></>,
  database: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/></>,
  plus: <path d="M12 5v14M5 12h14"/>, arrow: <path d="M5 12h14m-6-6 6 6-6 6"/>, chevron: <path d="m8 5 7 7-7 7"/>, down: <path d="m6 9 6 6 6-6"/>,
  search: <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></>,
  upload: <><path d="M4 15v5h16v-5M12 16V3m-5 5 5-5 5 5"/></>, download: <><path d="M4 15v5h16v-5M12 3v13m-5-5 5 5 5-5"/></>,
  close: <path d="m6 6 12 12M6 18 18 6"/>, settings: <><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/></>,
  globe: <><circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18M5 7h14M5 17h14"/></>,
  check: <path d="m5 12 4 4L19 6"/>, stop: <rect x="6" y="6" width="12" height="12" rx="2"/>,
  link: <><path d="m9 15 6-6M8 13l-2 2a3 3 0 0 0 4 4l3-3m-2-8 3-3a3 3 0 0 1 4 4l-2 2"/></>, clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  panel: <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16m3-10-2 2 2 2"/></>, warning: <><path d="m12 3 10 18H2Z"/><path d="M12 9v5m0 3h.01"/></>,
  refresh: <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M5 8a8 8 0 0 1 13-3l2 3M4 16l2 3a8 8 0 0 0 13-3"/></>, menu: <path d="M4 6h16M4 12h16M4 18h16"/>,
}
export default function Icon({ name, size = 20, style }: { name: string; size?: number; style?: CSSProperties }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={style}>{paths[name] ?? paths.file}</svg>
}
