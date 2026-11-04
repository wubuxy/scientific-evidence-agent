import { useEffect, useRef, useState } from 'react'
import { download, errorText, post, request, workspacePath } from './api'
import Icon from './Icons'
import TranslationSegments from './TranslationSegments'
import TranslationInput, { type TranslationForm } from './TranslationInput'
import { activeJob, JOB_STATUS, type TranslationJob } from './labTypes'

export default function TranslationDesk({ workspaceId, onEvidence }: { workspaceId: string; onEvidence: (id: string) => void }) {
  const [form, setForm] = useState<TranslationForm>({ text: '', target: 'auto', style: 'direct', glossary: '', budget: 60000 })
  const { text, target, style, glossary, budget } = form
  const [jobs, setJobs] = useState<TranslationJob[]>([])
  const [job, setJob] = useState<TranslationJob | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [showOriginal, setShowOriginal] = useState(true)
  const alive = useRef(true)

  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => {
    request<{ items: TranslationJob[] }>(workspacePath(workspaceId, '/translations')).then(data => {
      if (!alive.current) return
      setJobs(data.items); setJob(data.items.find(activeJob) ?? null)
    }).catch(err => { if (alive.current) setError(errorText(err)) })
  }, [workspaceId])
  useEffect(() => {
    if (!job || !activeJob(job)) return
    let stopped = false, pending = false
    const poll = async () => {
      if (stopped || pending) return
      pending = true
      try {
        const value = await request<TranslationJob>(workspacePath(workspaceId, `/translations/${job.id}`))
        if (!stopped) { setJob(value); setJobs(previous => [value, ...previous.filter(item => item.id !== value.id)]); if (!activeJob(value)) clearInterval(timer) }
      } catch (err) { if (!stopped) setError(errorText(err)) } finally { pending = false }
    }
    const timer = setInterval(() => void poll(), 1200)
    void poll()
    return () => { stopped = true; clearInterval(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, workspaceId])

  async function translate() {
    setBusy(true); setError('')
    try {
      const terms: Record<string, string> = Object.create(null) as Record<string, string>
      for (const line of glossary.split('\n').filter(line => line.trim())) {
        const separator = line.indexOf('=')
        if (separator < 1 || !line.slice(separator + 1).trim()) throw new Error('术语表请每行填写“原词=指定译法”。')
        const key = line.slice(0, separator).trim()
        if (Object.prototype.hasOwnProperty.call(terms, key)) throw new Error(`术语「${key}」重复，请保留一种译法。`)
        terms[key] = line.slice(separator + 1).trim()
      }
      const result = await post<TranslationJob>(workspacePath(workspaceId, '/translations'), {
        text: text.trim(), target_language: target, style, glossary: terms, max_tokens: budget,
      })
      if (!alive.current) return
      setJob(result); setJobs(previous => [result, ...previous.filter(item => item.id !== result.id)])
    } catch (err) { if (alive.current) setError(errorText(err)) } finally { if (alive.current) setBusy(false) }
  }
  async function cancel() {
    if (!job) return
    try { setJob(await post<TranslationJob>(workspacePath(workspaceId, `/translations/${job.id}/cancel`), {})) } catch (err) { setError(errorText(err)) }
  }
  async function exportTranslation() {
    if (!job) return
    try {
      const response = await fetch(workspacePath(workspaceId, `/translations/${job.id}/export`))
      if (!response.ok) throw new Error('尚无可导出的译文，或导出失败')
      download(await response.text(), `translation-${job.id}.md`)
    } catch (err) { setError(errorText(err)) }
  }
  const completed = job?.segments.filter(segment => segment.translated).length ?? 0
  return <div className="page-body translation-page"><section className="feature-hero translation-hero"><div className="translation-art" aria-hidden="true"><span>A</span><i/><b>文</b><small>↔</small></div><div className="hero-copy"><span className="eyebrow">READ ACROSS LANGUAGES</span><h1>粘贴原文，切换表达。</h1><p>中英互译，直接传达原意，或采用正式严谨的学术表达。</p><div className="hero-pills"><span><Icon name="compare" size={13}/>中英互译</span><span><Icon name="book" size={13}/>学术表达</span><span><Icon name="translate" size={13}/>术语统一</span></div></div></section>
    {error && <div className="error-box lab-message" role="alert">{error}</div>}
    <div className="translation-layout paste-translation-layout"><aside className="translation-settings paste-translation-settings"><TranslationInput form={form} setForm={setForm} busy={busy} onTranslate={() => void translate()}/>
      {jobs.length > 0 && <div className="translation-history"><h3>翻译记录 <span>{jobs.length}</span></h3>{jobs.slice(0, 20).map(item => <button className={job?.id === item.id ? 'active' : ''} key={item.id} onClick={() => setJob(item)}><Icon name="file" size={15}/><span><strong>{item.title}</strong><small>{(item.style ?? 'academic') === 'direct' ? '直接翻译' : '学术翻译'} · {JOB_STATUS[item.status]} · {item.progress.completed}/{item.progress.total} 段</small></span></button>)}</div>}
    </aside><div className="translation-content">{job ? <>
      <header className="translation-result-heading">
        <div><span className="eyebrow">ALIGNED READING</span><h2>{job.title}</h2><div>
          <span className={`pill ${job.status === 'completed' ? 'green' : 'neutral'}`}>{activeJob(job) && <span className="spinner"/>}{job.status === 'completed' ? '译文已生成 · 待校对' : JOB_STATUS[job.status]}</span>
          <span className="translation-mode-pill">{(job.style ?? 'academic') === 'direct' ? '直接翻译' : '学术翻译'} · {job.target_language === 'en' ? '中文 → 英文' : '英文 → 中文'}</span>
          <span>{completed}/{job.progress.total} 段 · {job.tokens_estimated ? '约 ' : ''}{job.tokens_used.toLocaleString()} tokens</span>
        </div></div>
        <div className="translation-result-actions">
          {activeJob(job) && <button className="button compact" onClick={() => void cancel()}><Icon name="stop" size={13}/>停止</button>}
          <button className="icon-button" aria-label={showOriginal ? '仅看译文' : '显示原文对照'} title={showOriginal ? '仅看译文' : '显示原文对照'} onClick={() => setShowOriginal(!showOriginal)}><Icon name="compare" size={18}/></button>
          <button className="button compact" disabled={completed === 0} onClick={() => void exportTranslation()}><Icon name="download" size={14}/>导出</button>
        </div>
      </header>
      {activeJob(job) && <progress className="translation-progress" value={completed} max={job.progress.total}/>}
      {job.error && <div className="warning-box">{job.error}</div>}
      <div className="translation-review-note"><Icon name="evidence" size={15}/>译文由模型生成。请对照核验数字、公式与实验条件；学术表达不会替代原文事实。</div>
      <TranslationSegments job={job} showOriginal={showOriginal} onEvidence={onEvidence}/>
      <footer className="translation-version"><Icon name="link" size={13}/>{job.model} · 原文 SHA-256 <code>{job.source_hash.slice(0, 16)}…</code></footer>
    </> : <div className="translation-empty">
      <div className="language-emblem"><span>A</span><Icon name="arrow" size={20}/><strong>文</strong></div>
      <span className="eyebrow">READ IN THE LANGUAGE YOU THINK IN</span><h2>从一段原文，开始翻译。</h2>
      <p>将中文或英文粘贴到输入框，选择直接翻译或学术翻译。默认自动识别语言，译文将在这里逐段呈现。</p>
      <div className="translation-empty-points"><span><Icon name="check" size={14}/>中英互译</span><span><Icon name="check" size={14}/>两种表达</span><span><Icon name="check" size={14}/>双语导出</span></div>
    </div>}</div></div>
  </div>
}
