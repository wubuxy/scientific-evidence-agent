export type ThemeMode = 'light' | 'dark' | 'system'
export type ResolvedThemeMode = 'light' | 'dark'
export type PaletteId = 'cool-teal' | 'cool-blue' | 'warm-amber' | 'warm-rose'
export interface ThemePreferences { mode: ThemeMode; palette: PaletteId }

export const THEME_STORAGE_KEY = 'research.appearance.v1'
export const DEFAULT_THEME: ThemePreferences = { mode: 'light', palette: 'cool-teal' }
export const PALETTES = [
  { id: 'cool-teal', tone: 'cool', name: '松石绿', description: '清透、安静的青绿色', light: '#176956', dark: '#74d5bb' },
  { id: 'cool-blue', tone: 'cool', name: '雾海蓝', description: '沉静、清晰的蓝色', light: '#2859a5', dark: '#8eb8ff' },
  { id: 'warm-amber', tone: 'warm', name: '琥珀橙', description: '温润、柔和的琥珀色', light: '#9d4c0a', dark: '#efbb76' },
  { id: 'warm-rose', tone: 'warm', name: '玫瑰粉', description: '舒缓、细腻的玫瑰色', light: '#a23e5d', dark: '#efa5bb' },
] as const

export function parseThemePreferences(raw: string | null): ThemePreferences {
  try {
    const value: unknown = raw ? JSON.parse(raw) : null
    if (value && typeof value === 'object') {
      const item = value as Record<string, unknown>
      return {
        mode: ['light', 'dark', 'system'].includes(String(item.mode)) ? item.mode as ThemeMode : DEFAULT_THEME.mode,
        palette: PALETTES.some(palette => palette.id === item.palette) ? item.palette as PaletteId : DEFAULT_THEME.palette,
      }
    }
  } catch { /* Corrupt or outdated preferences safely use the default. */ }
  return { ...DEFAULT_THEME }
}

export function readThemePreferences(): ThemePreferences {
  try { return parseThemePreferences(localStorage.getItem(THEME_STORAGE_KEY)) } catch { return { ...DEFAULT_THEME } }
}

function rgb(hex: string) { return [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16)) }
function blend(foreground: string, background: string, amount: number) {
  const front = rgb(foreground), back = rgb(background)
  return '#' + front.map((value, index) => Math.round(value * amount + back[index] * (1 - amount)).toString(16).padStart(2, '0')).join('')
}

export function themeTokens(paletteId: PaletteId, mode: ResolvedThemeMode): Record<string, string> {
  const palette = PALETTES.find(item => item.id === paletteId) ?? PALETTES[0]
  const dark = mode === 'dark', warm = palette.tone === 'warm'
  const accent = dark ? palette.dark : palette.light
  const base = dark ? warm ? {
    canvas: '#1a1412', sidebar: '#211916', surface: '#2b211d', soft: '#352923', hover: '#43332a', primary: '#f6eadf', secondary: '#dcc7b5', muted: '#c0a58d', faint: '#bfa58e',
  } : {
    canvas: '#0c1420', sidebar: '#0e1928', surface: '#142235', soft: '#192a40', hover: '#20334d', primary: '#e4edf8', secondary: '#c0cee1', muted: '#a0b2c9', faint: '#a0b2c9',
  } : warm ? {
    canvas: '#fbf8f4', sidebar: '#f4ede5', surface: '#fffdfa', soft: '#f5eee6', hover: '#eee3d6', primary: '#392b22', secondary: '#655145', muted: '#6f5f54', faint: '#6f5f54',
  } : {
    canvas: '#f6f8fc', sidebar: '#edf1f7', surface: '#ffffff', soft: '#f0f4f9', hover: '#e5edf6', primary: '#1c2b3b', secondary: '#43576b', muted: '#53677e', faint: '#53677e',
  }
  const heroStart = blend(accent, '#000000', dark ? .14 : .30)
  const heroEnd = blend(accent, '#000000', dark ? .26 : .50)
  const tokens: Record<string, string> = {
    canvas: base.canvas, sidebar: base.sidebar, surface: base.surface, 'surface-soft': base.soft, 'surface-hover': base.hover,
    'fg-strong': base.primary, 'fg-primary': base.primary, 'fg-secondary': base.secondary, 'fg-muted': base.muted, 'fg-faint': base.faint,
    accent, 'accent-hover': blend(accent, dark ? '#ffffff' : '#000000', .88),
    'accent-soft': blend(accent, base.surface, dark ? .13 : .08), 'accent-subtle': blend(accent, base.surface, dark ? .07 : .04),
    'accent-border': blend(accent, base.surface, dark ? .47 : .33),
    'on-accent': dark ? warm ? '#2b180b' : '#091d26' : '#ffffff',
    border: blend(base.primary, base.surface, dark ? .17 : .14), 'border-soft': blend(base.primary, base.surface, dark ? .11 : .09),
    'border-strong': blend(base.primary, base.surface, dark ? .30 : .25), 'focus-ring': blend(accent, base.surface, .65),
    'hero-start': heroStart, 'hero-end': heroEnd, 'hero-glow': accent, 'hero-text': '#f7fbff', 'hero-muted': '#d3e1e8',
    'hero-border': blend(accent, heroStart, .25),
    'shadow': '#000000', scrim: dark ? '#000000' : '#172333',
    'success-fg': dark ? '#9bdbc1' : '#34674d', 'success-bg': dark ? '#18332b' : '#eaf3ec', 'success-border': dark ? '#315d4b' : '#c1d8c6',
    'warning-fg': dark ? '#edce87' : '#805a13', 'warning-bg': dark ? '#352d1b' : '#f9f2df', 'warning-border': dark ? '#665630' : '#e3d2a7',
    'danger-fg': dark ? '#ffb4aa' : '#9c3b35', 'danger-bg': dark ? '#392323' : '#fbede9', 'danger-border': dark ? '#70443d' : '#e8c5bf',
    'info-fg': dark ? '#abcefa' : '#335b8c', 'info-bg': dark ? '#1a2e47' : '#eaf1fb', 'info-border': dark ? '#385479' : '#c7d8ee',
    'chart-1': accent, 'chart-2': warm ? dark ? '#9ac8ed' : '#386b9d' : dark ? '#eac38b' : '#94601c',
    'chart-3': dark ? '#a9bacd' : '#697e92',
  }
  for (const [key, value] of Object.entries({ ...tokens })) tokens[`${key}-rgb`] = rgb(value).join(' ')
  return tokens
}

export function applyTheme(preferences: ThemePreferences, mode: ResolvedThemeMode) {
  const root = document.documentElement
  root.dataset.themeMode = mode; root.dataset.themePalette = preferences.palette
  root.dataset.themeTone = PALETTES.find(item => item.id === preferences.palette)?.tone ?? 'cool'
  root.style.colorScheme = mode
  const tokens = themeTokens(preferences.palette, mode)
  for (const [name, value] of Object.entries(tokens)) root.style.setProperty(`--theme-${name}`, value)
  document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute('content', tokens.canvas)
}
