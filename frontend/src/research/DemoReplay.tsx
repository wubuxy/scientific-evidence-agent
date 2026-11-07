import { useEffect, useState } from 'react'
import BrandMark from './BrandMark'
import Icon from './Icons'
import { errorText, post, request, workspacePath } from './api'
import type { Report, Workspace } from './types'
import './demo.css'

interface DemoSource { id: string; title: string; short_title: string; authors: string; uri: string; license: string; license_url: string; source_hash: string; content_sha256: string; line_count: number; file: string; blocks_file: string }
interface DemoEvidence { id: string; source_id: string; source_hash: string; start_line: number; end_line: number; quote: string; preview_start_line: number; preview_end_line: number }
interface Focus { source_id: string; start_line: number; end_line: number; quote: string; evidence_id?: string; original_start?: number; original_end?: number }
interface Recording {
  schema_version: number
  recording: { run_id: string; recorded_at: string; model: string; tokens_used: number; question: string; report: Report; tool_calls: { id: string; name: string; arguments: Record<string, unknown>; status: string; elapsed_ms: number; evidence_ids: string[] }[] }
  sources: DemoSource[]
  evidence: DemoEvidence[]
  failure: { original_text: string; diagnosis: string; correction: Focus & { scope: string; label: string } }
  experiment: { label: string; model: string; tasks: number; note: string; summaries: Record<string, { answer_token_f1: number; tokens_used: number }>; paired: { mean_difference: number; bootstrap_95: number[]; improved: number; tied: number } }
}
type View = 'report' | 'trace' | 'failure' | 'experiment'
const VIEWS: [View, string, string][] = [['report', '01', '结论与引用'], ['trace', '02', '工具轨迹'], ['failure', '03', '失败定位'], ['experiment', '04', '核验补读 · 实验']]
const TOOLS: Record<string, string> = { sources_list: '列出两篇论文', sources_read: '按行回读原文', sources_grep: '检索数据规模关键词' }

export default function DemoReplay() {
  const [data, setData] = useState<Recording | null>(null)
  const [error, setError] = useState('')
  const [view, setView] = useState<View>('report')
  const [focus, setFocus] = useState<Focus | null>(null)
  const [fullWindow, setFullWindow] = useState(false)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let alive = true
    request<Recording>('/demo/recording.json').then(value => { if (alive) setData(value) }).catch(reason => { if (alive) setError(errorText(reason)) })
    return () => { alive = false }
  }, [])

  function selectEvidence(evidence: DemoEvidence, full = false) {
    const start = full ? evidence.start_line : evidence.preview_start_line
    const end = full ? evidence.end_line : evidence.preview_end_line
    setFocus({ source_id: evidence.source_id, start_line: start, end_line: end,
      quote: evidence.quote.split('\n').slice(start - evidence.start_line, end - evidence.start_line + 1).join('\n'),
      evidence_id: evidence.id, original_start: evidence.start_line, original_end: evidence.end_line })
    setFullWindow(full)
  }

  useEffect(() => { if (data) selectEvidence(data.evidence[0]) }, [data])

  function changeView(next: View) {
    setView(next)
    if (next === 'failure' && data) { setFocus(data.failure.correction); setFullWindow(false) }
    if ((next === 'report' || next === 'trace') && data) selectEvidence(data.evidence[0])
  }

  async function openExample() {
    if (!data || busy) return
    setBusy(true); setError('')
    try {
      const blocks = await Promise.all(data.sources.map(source => request<unknown[]>(source.blocks_file)))
      const workspace = await post<Workspace>('/api/v1/workspaces', { title: '示例 · QASPER 与 ArxivDIGESTables', research_question: data.recording.question })
      for (const [index, source] of data.sources.entries()) {
        await post(workspacePath(workspace.id, '/sources'), { title: source.title, kind: 'paper', uri: source.uri, blocks: blocks[index] })
      }
      localStorage.setItem('research.workspace', workspace.id)
      window.location.assign('/')
    } catch (reason) { setError(errorText(reason)); setBusy(false) }
  }

  const source = data?.sources.find(item => item.id === focus?.source_id)
  return <div className="demo-shell">
    <header className="demo-topbar"><a className="demo-brand" href="/"><BrandMark/><strong>研序<span> / EVIDENCE AGENT</span></strong></a><span className="demo-offline"><span/>真实运行回放 · 无需 API 密钥</span><a className="button compact" href="/demo/fields">字段状态回放<Icon name="arrow" size={15}/></a><a className="button compact" href="/">进入工作台<Icon name="arrow" size={15}/></a></header>
    <main className="demo-main">
      <section className="demo-intro"><div><span className="eyebrow">FOLLOW THE EVIDENCE</span><h1>每条结论，都能回到原文。</h1><p>用一份真实报告，看模型读了什么、引用了哪里，以及一条看似合理的断言为什么会出错。</p></div><div className="demo-start"><button className="button primary" disabled={!data || busy} onClick={() => void openExample()}>{busy ? '正在导入两篇示例论文…' : '用示例论文开始研究'}<Icon name="arrow" size={16}/></button><small>导入与检索无需密钥；新生成需配置模型。</small></div></section>
      {error && <div className="error-box" role="alert">{error}</div>}
      {!data ? <p className="demo-loading">{error ? '请检查服务后刷新页面。' : '正在读取历史运行记录…'}</p> : <>
        <div className="demo-context"><div className="demo-paper-list">{data.sources.map(item => <a key={item.id} href={item.uri} target="_blank" rel="noreferrer"><Icon name="file" size={14}/>{item.short_title}<Icon name="globe" size={12}/></a>)}</div><span>历史模型 {data.recording.model} · {new Date(data.recording.recorded_at).toLocaleDateString('zh-CN')} · {data.recording.tokens_used.toLocaleString()} tokens</span></div>
        <nav className="demo-tabs" aria-label="演示步骤">{VIEWS.map(([key, number, label]) => <button key={key} aria-pressed={view === key} className={view === key ? 'active' : ''} onClick={() => changeView(key)}><span>{number}</span>{label}</button>)}</nav>
        <div className="demo-grid">
          <section className="demo-record">
            {view === 'report' && <>
              <div className="demo-section-heading"><span className="eyebrow">RECORDED ANSWER</span><span className="demo-pill">原始结论摘录</span></div>
              <h2>两种研究任务，分别有哪些依据？</h2><details className="demo-question"><summary>查看当时的问题</summary><p>{data.recording.question}</p></details>
              <div className="demo-claims">{data.recording.report.claims.map((claim, index) => <article key={index} className="demo-claim"><span className="demo-claim-index">0{index + 1}</span><div><p>{claim.text}</p>{claim.evidence_ids.map(id => <button className="demo-citation" key={id} onClick={() => selectEvidence(data.evidence.find(item => item.id === id)!)}><Icon name="link" size={14}/>{data.sources.find(item => item.id === data.evidence.find(e => e.id === id)?.source_id)?.short_title} · 查看原文<Icon name="chevron" size={12}/></button>)}</div></article>)}</div>
              <div className="demo-insight"><Icon name="evidence" size={20}/><p>这两条任务定义可以追溯到对应摘要。原始报告仍含一条无依据的限制声明，可在“失败定位”中检查。</p></div>
              <details className="demo-raw"><summary>查看完整原始报告（含未通过核对的声明）</summary><pre>{JSON.stringify(data.recording.report, null, 2)}</pre></details>
            </>}
            {view === 'trace' && <>
              <span className="eyebrow">OBSERVED TOOL CALLS</span><h2>先看真实调用，再判断过程。</h2><p className="demo-description">以下按原运行顺序排列。本例先读摘要，再检索规模关键词；回放不会重新调用模型。</p>
              <div className="demo-trace">{data.recording.tool_calls.map((call, index) => <article key={call.id}><span className="demo-trace-index">{index + 1}</span><div><div className="demo-trace-heading"><strong>{TOOLS[call.name] ?? call.name}</strong><small>{call.elapsed_ms} ms</small></div><code>{call.name}</code><details><summary>参数与证据地址</summary><pre>{JSON.stringify(call.arguments, null, 2)}</pre><small>调用 ID · {call.id}</small></details>{call.evidence_ids.filter(id => data.evidence.some(item => item.id === id)).map(id => <button className="demo-citation" key={id} onClick={() => selectEvidence(data.evidence.find(item => item.id === id)!)}><Icon name="link" size={13}/>打开此次回读的证据</button>)}{call.name === 'sources_grep' && <small>检索命中提供定位；本页两条结论引用的是上方实际回读记录。</small>}</div></article>)}</div>
            </>}
            {view === 'failure' && <>
              <span className="eyebrow">A FAILURE YOU CAN INSPECT</span><h2>“本次没读到”，不能推出“论文没报告”。</h2><div className="demo-failed-claim"><span>原始报告中的限制声明</span><blockquote>{data.failure.original_text}</blockquote><strong><Icon name="warning" size={15}/>未通过内容核对</strong></div><p className="demo-description">{data.failure.diagnosis}</p>
              <div className="demo-diagnosis"><h3>从生成结果追到缺口</h3><ol><li>原运行只回读了两篇论文的第 1–80 行。</li><li>报告把片段中的缺失扩大成了论文层面的否定。</li><li>事后核对 QASPER 第 453–470 行，可以看到 LED、BART 和实验配置。</li></ol></div>
              <button className="demo-citation" onClick={() => { setFocus(data.failure.correction); setFullWindow(false) }}><Icon name="search" size={14}/>查看事后核对的原文</button><button className="demo-citation" onClick={() => selectEvidence(data.evidence[0])}>对照原运行读过的窗口</button>
              <div className="demo-insight demo-amber"><Icon name="warning" size={20}/><p>这是人工事后定位，不是原运行自动修复。证据 ID、来源归属和回读校验不能替代语义支持判断。</p></div>
            </>}
            {view === 'experiment' && <>
              <span className="eyebrow">HISTORICAL PILOT · SEPARATE EXPERIMENT</span><h2>历史先导：一次补读改善了答案。</h2><p className="demo-description">这组 6 篇论文的历史短答案实验使用 {data.experiment.model}，与上面的跨论文报告、当前字段补救属于不同任务。后续 20 题五组对照尚未确认定向补读优于普通补搜。<a href="https://github.com/wubuxy/scientific-evidence-agent/blob/main/docs/results/REPAIR_FIVE_ARM_DEV20_V1.md" target="_blank" rel="noreferrer"> 查看后续结果 ↗</a></p>
              <div className="demo-experiment-results">{Object.entries(data.experiment.summaries).map(([method, value]) => <div key={method}><div><strong>{{ baseline: '原始 Grep', 'review-rewrite': '核验后仅重答', 'review-reread': '核验后补读重答' }[method]}</strong><b>{(value.answer_token_f1 * 100).toFixed(2)}</b></div><div className="demo-bar"><span style={{ width: `${value.answer_token_f1 * 100}%` }}/></div><small>平均总 tokens · {Math.round(value.tokens_used).toLocaleString()}</small></div>)}</div><p className="demo-description">数值为 Answer token F1 × 100，并非答案正确率。</p><div className="demo-insight demo-amber"><Icon name="warning" size={20}/><p>{data.experiment.note}</p></div>
            </>}
          </section>
          {view === 'experiment' ? <aside className="demo-source"><div className="demo-source-header"><Icon name="research" size={19}/><strong>实验边界</strong></div><div className="demo-protocol"><div><span>独立任务</span><strong>6 题 / 6 篇论文</strong></div><div><span>任务级变化</span><strong>1 题改善，5 题不变</strong></div><div><span>F1 平均差</span><strong>+8.33 个百分点</strong></div><div><span>Bootstrap 95% 区间</span><strong>[0, 0.25]</strong></div><p>每题只运行一次。分支共享前序轨迹，20k 为相同总上限，实际用量并不相等。单个成功案例不足以证明核验优于更强的普通搜索对照。</p><span className="demo-pill">离线实验摘要 · 非逐步调用回放</span></div></aside> : <aside className="demo-source" aria-label="原文证据面板">
            <div className="demo-source-header"><Icon name="evidence" size={19}/><strong>{focus?.evidence_id ? '本次运行已回读的原文' : '事后核对定位'}</strong><span>{source?.short_title}</span></div>
            {focus && source && <><div className="demo-source-meta"><h3>{source.title}</h3><div><span className="demo-pill">{focus.evidence_id ? `原回读 ${focus.original_start}–${focus.original_end} 行` : '原运行未回读此窗口'}</span><span>当前显示 {focus.start_line}–{focus.end_line} 行</span></div></div><div className="demo-source-lines">{focus.quote.split('\n').map((line, index) => <div className="demo-source-line" key={index}><span>{focus.start_line + index}</span><p>{line || ' '}</p></div>)}</div><div className="demo-source-footer">{focus.evidence_id && <><label>原始 evidence ID</label><code>{focus.evidence_id}</code><button className="text-button" onClick={() => selectEvidence(data.evidence.find(item => item.id === focus.evidence_id)!, !fullWindow)}>{fullWindow ? '仅看关键片段' : '展开完整回读窗口'}</button></>}<details><summary>来源版本与完整文本</summary><label>原始来源版本（包含页码等块元数据）</label><code>{source.source_hash}</code><a href={source.file} download>下载带固定换行的论文文本</a><a href={source.uri} target="_blank" rel="noreferrer">查看论文原始页面</a><p>{source.authors} · <a href={source.license_url} target="_blank" rel="noreferrer">{source.license}</a>。文本由 PDF 提取，保留原运行换行与页码信息，非原版版面。</p></details></div></>}
          </aside>}
        </div>
        <footer className="demo-footer"><span><Icon name="history" size={14}/>历史记录可重复查看；当前页面不生成新答案。</span><a href="/demo/recording.json" download>下载回放数据<Icon name="download" size={14}/></a></footer>
      </>}
    </main>
  </div>
}
