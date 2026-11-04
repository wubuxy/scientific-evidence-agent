import ResearchWorkbench from './research/ResearchWorkbench'
import { ThemeProvider } from './research/ThemeProvider'

export default function App() {
  return <ThemeProvider><ResearchWorkbench /></ThemeProvider>
}
