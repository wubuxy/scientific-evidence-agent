import { useId, useState, type Dispatch, type SetStateAction } from 'react'
import Icon from './Icons'

export interface TranslationForm {
  text: string
  target: 'auto' | 'zh-CN' | 'en'
  style: 'direct' | 'academic'
  glossary: string
  budget: number
}

export function resolveTranslationTarget(text: string, target: TranslationForm['target']) {
  if (target !== 'auto') return target
  const chinese = (text.match(/[\u3400-\u9fff]/g) ?? []).length
  const latin = (text.match(/[a-z]/gi) ?? []).length
  return chinese > 0 && chinese >= latin * .25 ? 'en' : 'zh-CN'
}

export default function TranslationInput({ form, setForm, busy, onTranslate }: { form: TranslationForm; setForm: Dispatch<SetStateAction<TranslationForm>>; busy: boolean; onTranslate: () => void }) {
  const [settings, setSettings] = useState(false)
  const id = useId()
  const resolved = resolveTranslationTarget(form.text, form.target)
  function update<K extends keyof TranslationForm>(key: K, value: TranslationForm[K]) {
    setForm(previous => ({ ...previous, [key]: value }))
  }
  return <>
    <div className="list-heading"><h3>粘贴需要翻译的内容</h3><Icon name="translate" size={18}/></div>
    <label htmlFor={`${id}-text`}>原文
      <textarea id={`${id}-text`} className="translation-input" aria-label="待翻译文本" value={form.text} onChange={e => update('text', e.target.value)} placeholder="在这里粘贴中文或英文内容，例如论文摘要、研究段落或报告草稿…" maxLength={80000}/>
      <small>{form.text.length.toLocaleString()} / 80,000 字符</small>
    </label>
    <label htmlFor={`${id}-language`}>语言方向
      <select id={`${id}-language`} value={form.target} onChange={e => update('target', e.target.value as TranslationForm['target'])}>
        <option value="auto">自动识别 · 中英互译</option>
        <option value="zh-CN">英文 → 简体中文</option>
        <option value="en">中文 → English</option>
      </select>
    </label>
    <p className="translation-direction-hint"><Icon name="compare" size={13}/>{form.text.trim() ? `将译为${resolved === 'en' ? '英文' : '简体中文'}` : '默认中文译为英文，英文译为中文'}</p>
    <fieldset className="translation-style-picker"><legend>翻译方式</legend>
      <div role="radiogroup" aria-label="翻译方式">
        <button type="button" role="radio" aria-checked={form.style === 'direct'} className={form.style === 'direct' ? 'active' : ''} onClick={() => update('style', 'direct')}><Icon name="translate" size={17}/><strong>直接翻译</strong><small>自然清晰，贴近原文</small></button>
        <button type="button" role="radio" aria-checked={form.style === 'academic'} className={form.style === 'academic' ? 'active' : ''} onClick={() => update('style', 'academic')}><Icon name="book" size={17}/><strong>学术翻译</strong><small>正式严谨，采用学术表达</small></button>
      </div>
    </fieldset>
    <div className="glossary-heading"><label htmlFor={`${id}-glossary`}>术语表 <span>选填</span></label><button className="text-button" onClick={() => update('glossary', resolved === 'en' ? '检索增强生成=retrieval-augmented generation\n基准评测=benchmark\n消融实验=ablation study' : 'retrieval-augmented generation=检索增强生成\nbenchmark=基准评测\nablation study=消融实验')}>填入示例</button></div>
    <textarea id={`${id}-glossary`} className="glossary-input" value={form.glossary} onChange={e => update('glossary', e.target.value)} placeholder={resolved === 'en' ? '检索=retrieval\n置信区间=confidence interval' : 'retrieval=检索\nconfidence interval=置信区间'} maxLength={3500}/>
    <p className="glossary-hint">每行一个术语，使用 = 连接原词与指定译法。</p>
    <button className="text-button translation-budget" aria-expanded={settings} onClick={() => setSettings(!settings)}><Icon name="settings" size={13}/>翻译预算<Icon name="down" size={12}/></button>
    {settings && <label htmlFor={`${id}-budget`}>Token 总预算<input id={`${id}-budget`} type="number" min={4000} max={200000} step={1000} value={form.budget} onChange={e => update('budget', Number(e.target.value))}/></label>}
    <button className="button primary translate-submit" disabled={busy || !form.text.trim()} onClick={onTranslate}><Icon name="translate" size={16}/>{busy ? '正在提交…' : form.style === 'academic' ? '开始学术翻译' : '开始直接翻译'}<Icon name="arrow" size={15}/></button>
    <p className="translation-provider-note">使用当前模型分段翻译粘贴内容。较长文本可分批粘贴；相同原文、语言、翻译方式与术语表会复用已有结果。</p>
  </>
}
