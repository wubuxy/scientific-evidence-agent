// Apply saved mode before React mounts to avoid a white flash on dark pages.
(() => {
  let mode = 'light', palette = 'cool-teal'
  try {
    const saved = JSON.parse(localStorage.getItem('research.appearance.v1') || 'null')
    if (saved && typeof saved === 'object') {
      if (['light', 'dark', 'system'].includes(saved.mode)) mode = saved.mode
      if (['cool-teal', 'cool-blue', 'warm-amber', 'warm-rose'].includes(saved.palette)) palette = saved.palette
    }
  } catch { /* Storage may be unavailable. */ }
  const resolved = mode === 'system' ? matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light' : mode
  const root = document.documentElement
  root.dataset.themeMode = resolved; root.dataset.themePalette = palette; root.dataset.themeTone = palette.startsWith('warm-') ? 'warm' : 'cool'
  root.style.colorScheme = resolved
})()
