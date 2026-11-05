import { lazy, Suspense } from 'react'
import { ThemeProvider } from './research/ThemeProvider'

const ResearchWorkbench = lazy(() => import('./research/ResearchWorkbench'))
const DemoReplay = lazy(() => import('./research/DemoReplay'))

export default function App() {
  const replay = window.location.pathname.replace(/\/$/, '') === '/demo'
  return <ThemeProvider><Suspense fallback={<p style={{ padding: 40 }}>正在打开研究空间…</p>}>{replay ? <DemoReplay /> : <ResearchWorkbench />}</Suspense></ThemeProvider>
}
