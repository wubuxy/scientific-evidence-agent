import { lazy, Suspense } from 'react'
import { ThemeProvider } from './research/ThemeProvider'

const ResearchWorkbench = lazy(() => import('./research/ResearchWorkbench'))
const DemoReplay = lazy(() => import('./research/DemoReplay'))
const FieldReplay = lazy(() => import('./research/FieldReplay'))

export default function App() {
  const path = window.location.pathname.replace(/\/$/, '')
  return <ThemeProvider><Suspense fallback={<p style={{ padding: 40 }}>正在打开研究空间…</p>}>{path === '/demo/fields' ? <FieldReplay /> : path === '/demo' ? <DemoReplay /> : <ResearchWorkbench />}</Suspense></ThemeProvider>
}
