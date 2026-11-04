import { createContext, useContext, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from 'react'
import { applyTheme, DEFAULT_THEME, parseThemePreferences, readThemePreferences, THEME_STORAGE_KEY, type ResolvedThemeMode, type ThemePreferences } from './theme'

const ThemeContext = createContext<{ preferences: ThemePreferences; resolvedMode: ResolvedThemeMode; setPreferences: (value: ThemePreferences) => void }>({ preferences: DEFAULT_THEME, resolvedMode: 'light', setPreferences() {} })

export function useTheme() { return useContext(ThemeContext) }

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preferences, updatePreferences] = useState(readThemePreferences)
  const [systemDark, setSystemDark] = useState(() => typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  const resolvedMode = preferences.mode === 'system' ? systemDark ? 'dark' : 'light' : preferences.mode
  useEffect(() => {
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    const change = () => setSystemDark(query.matches)
    query.addEventListener('change', change); change()
    const storage = (event: StorageEvent) => { if (event.key === THEME_STORAGE_KEY || event.key === null) updatePreferences(parseThemePreferences(event.newValue)) }
    window.addEventListener('storage', storage)
    return () => { query.removeEventListener('change', change); window.removeEventListener('storage', storage) }
  }, [])
  useLayoutEffect(() => { applyTheme(preferences, resolvedMode) }, [preferences, resolvedMode])
  const context = useMemo(() => ({ preferences, resolvedMode, setPreferences(value: ThemePreferences) {
    const valid = parseThemePreferences(JSON.stringify(value))
    updatePreferences(valid)
    try { localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(valid)) } catch { /* The current session still works when storage is unavailable. */ }
  } }), [preferences, resolvedMode])
  return <ThemeContext.Provider value={context}>{children}</ThemeContext.Provider>
}
