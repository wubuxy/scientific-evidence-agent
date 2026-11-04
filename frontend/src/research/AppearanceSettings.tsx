import { useEffect, useId, useRef } from 'react'
import Icon from './Icons'
import { useTheme } from './ThemeProvider'
import { DEFAULT_THEME, PALETTES, type PaletteId, type ThemeMode } from './theme'

export function AppearanceEntry({ onOpen }: { onOpen: () => void }) {
  const { preferences, resolvedMode } = useTheme()
  const palette = PALETTES.find(item => item.id === preferences.palette) ?? PALETTES[0]
  return <button className="settings-entry" aria-label="打开设置" onClick={onOpen}><Icon name="settings" size={17}/><span><strong>设置</strong><small>{resolvedMode === 'dark' ? '暗黑' : '亮白'} · {palette.name}</small></span><i/></button>
}

export default function AppearanceSettings({ onClose, motion, onMotionChange }: { onClose: () => void; motion: boolean; onMotionChange: (value: boolean) => void }) {
  const { preferences, resolvedMode, setPreferences } = useTheme()
  const palette = PALETTES.find(item => item.id === preferences.palette) ?? PALETTES[0]
  const panel = useRef<HTMLElement>(null)
  const close = useRef<HTMLButtonElement>(null)
  const id = useId()
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    close.current?.focus()
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return }
      if (event.key !== 'Tab') return
      const controls = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex="0"]') ?? []).filter(item => item.getClientRects().length > 0)
      const first = controls[0], last = controls[controls.length - 1]
      if (event.shiftKey && (document.activeElement === first || !panel.current?.contains(document.activeElement))) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && (document.activeElement === last || !panel.current?.contains(document.activeElement))) { event.preventDefault(); first?.focus() }
    }
    document.addEventListener('keydown', keydown)
    return () => { document.removeEventListener('keydown', keydown); previous?.focus() }
  }, [onClose])
  function choosePalette(value: PaletteId) { setPreferences({ ...preferences, palette: value }) }
  return <div className="modal-backdrop appearance-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <section ref={panel} role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} className="modal appearance-settings">
      <header className="modal-header"><div><span className="eyebrow">MAKE THIS SPACE YOURS</span><h2 id={`${id}-title`}>设置</h2><p>调整外观，让工作台更适合你。</p></div><button ref={close} className="icon-button" onClick={onClose} aria-label="关闭设置"><Icon name="close"/></button></header>
      <fieldset className="appearance-section"><legend>显示模式</legend><div className="appearance-modes">{([
        ['light', 'sun', '亮白', '清晰、明亮'], ['dark', 'moon', '暗黑', '深色、柔和'], ['system', 'monitor', '跟随系统', '自动切换明暗'],
      ] as const).map(([mode, icon, title, description]) => <label key={mode} className={preferences.mode === mode ? 'selected' : ''}><input type="radio" name={`${id}-mode`} value={mode} checked={preferences.mode === mode} onChange={() => setPreferences({ ...preferences, mode: mode as ThemeMode })}/><Icon name={icon} size={22}/><strong>{title}</strong><small>{description}</small>{preferences.mode === mode && <span className="appearance-check"><Icon name="check" size={11}/></span>}</label>)}</div></fieldset>
      <fieldset className="appearance-section"><legend>色彩基调</legend><div className="appearance-tones">{([['cool', '冷色调', '清透、沉静'], ['warm', '暖色调', '温润、舒缓']] as const).map(([tone, title, description]) => <label key={tone} className={palette.tone === tone ? 'selected' : ''}><input type="radio" name={`${id}-tone`} value={tone} checked={palette.tone === tone} onChange={() => { const first = PALETTES.find(item => item.tone === tone); if (first) choosePalette(first.id) }}/><span className={`tone-sample ${tone}`}/><span><strong>{title}</strong><small>{description}</small></span><Icon name={palette.tone === tone ? 'check' : 'chevron'} size={15}/></label>)}</div></fieldset>
      <fieldset className="appearance-section"><legend>主题配色</legend><div className="appearance-palettes">{PALETTES.filter(item => item.tone === palette.tone).map(item => <label key={item.id} className={preferences.palette === item.id ? 'selected' : ''}><input type="radio" name={`${id}-palette`} value={item.id} checked={preferences.palette === item.id} onChange={() => choosePalette(item.id)}/><span className="palette-swatch" style={{ background: `linear-gradient(135deg, ${item.light} 0% 50%, ${item.dark} 50% 100%)` }}/><span><strong>{item.name}</strong><small>{item.description}</small></span>{preferences.palette === item.id && <Icon name="check" size={15}/>}</label>)}</div></fieldset>
      <div className="appearance-preview" aria-label="当前主题预览"><div className="preview-sidebar"><i/><i/><i/></div><div className="preview-content"><div><span>你的研究工作台</span><span className="preview-status">{resolvedMode === 'dark' ? '暗黑' : '亮白'} · {palette.name}</span></div><strong>让每一段阅读，都更舒适。</strong><div className="preview-cards"><i/><i/><span>继续研究 <Icon name="arrow" size={12}/></span></div></div></div>
      <div className="appearance-motion"><div><strong>页面动效</strong><p>页面过渡与背景动画；遵循系统的减少动态效果偏好。</p></div><button className={`settings-switch ${motion ? 'on' : ''}`} role="switch" aria-label="页面动效" aria-checked={motion} onClick={() => onMotionChange(!motion)}><i/></button></div>
      <footer className="modal-footer"><button className="text-button" onClick={() => setPreferences({ ...DEFAULT_THEME })}>恢复默认外观</button><span>已自动保存到当前浏览器</span><button className="button primary" onClick={onClose}>完成<Icon name="check" size={15}/></button></footer>
    </section>
  </div>
}
