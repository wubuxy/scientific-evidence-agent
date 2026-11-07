import { useEffect, useState } from 'react'
import BrandMark from './BrandMark'
import FieldProgress from './FieldProgress'
import { errorText, request } from './api'
import { useTheme } from './ThemeProvider'
import type { FieldState, Source } from './types'
import './field-replay.css'

interface Version { field_id: string; revision: number; answer: string; candidate_hash: string }
interface Recording {
  synthetic: true
  recording: { id: string; created_at: string; model: string; tokens_used: number; step: number; question: string;
    field_state: FieldState & { previous_versions: Version[] } }
  source: Source & { text: string }
  evidence: { id: string; source_id: string; source_hash: string; start_line: number; end_line: number; quote: string }[]
  provenance: { generation_calls: number; review_calls: number }
}
type View = 'fields' | 'evidence' | 'versions' | 'stop'
const VIEWS: [View, string][] = [['fields', '字段台账'], ['evidence', '原文证据'], ['versions', '版本记录'], ['stop', '停止与成本']]

export default function FieldReplay() {
  const [data, setData] = useState<Recording | null>(null)
  const [error, setError] = useState('')
  const [view, setView] = useState<View>('fields')
  const [evidenceId, setEvidenceId] = useState('')
  const { preferences, resolvedMode, setPreferences } = useTheme()
  useEffect(() => {
    let active = true
    request<Recording>('/demo/field-recording.json').then(value => {
      if (active) { setData(value); setEvidenceId(value.evidence[0]?.id ?? '') }
    }).catch(reason => { if (active) setError(errorText(reason)) })
    return () => { active = false }
  }, [])
  const run = data?.recording, state = run?.field_state
  const evidence = data?.evidence.find(item => item.id === evidenceId)
  return <div className="field-replay">
    <header className="replay-masthead">
      <a href="/demo" className="replay-brand"><BrandMark/><strong>研序 <span>/ EVIDENCE AGENT</span></strong></a>
      <div><a href="/demo">论文证据回放</a><button onClick={() => setPreferences({ ...preferences, mode: resolvedMode === 'light' ? 'dark' : 'light' })}>{resolvedMode === 'light' ? '切换深色' : '切换浅色'}</button></div>
    </header>
    <main className="replay-main">
      <section className="replay-intro">
        <div><span className="replay-kicker">A RESEARCH RUN, UNDER THE MICROSCOPE</span><h1>研究停下了，缺口还在。</h1><p>追到原文，查看答案版本，再看这次为什么收尾。</p></div>
        <span className="replay-label">真实模型调用 · 合成资料<br/>固定回放 / 无需 API 密钥</span>
      </section>
      {error && <div className="error-box" role="alert">{error}</div>}
      {!data || !run || !state ? <p>{error ? '记录未能加载，请刷新重试。' : '正在读取固定回放…'}</p> : <>
        <nav className="replay-views" aria-label="回放视图">{VIEWS.map(([key, label], i) => <button key={key} aria-pressed={view === key} onClick={() => setView(key)}><span>0{i + 1}</span>{label}</button>)}</nav>
        <div className="replay-stage">
          {view === 'fields' && <FieldProgress state={state} sources={[data.source]} onSelect={id => { setEvidenceId(id); setView('evidence') }}/>}
          {view === 'evidence' && <section className="replay-panel">
            <span className="replay-kicker">SOURCE → LINES → EVIDENCE ID</span><h2>答案来自哪一行？</h2>
            <p className="replay-lead">这次实际回读的完整窗口。Atlas、Train-X 和 Seed-A 均为演示用的虚构名称。</p>
            {evidence && <><div className="replay-source-heading"><strong>{data.source.title}</strong><span>行 {evidence.start_line}–{evidence.end_line}</span></div>
              <div className="replay-lines">{evidence.quote.split('\n').map((line, i) => <div key={i}><span>{evidence.start_line + i}</span><p>{line}</p></div>)}</div>
              <div className="replay-identity"><span>本次 evidence ID</span><code>{evidence.id}</code></div>
              <details className="replay-technical"><summary>查看来源版本</summary><code>{evidence.source_hash}</code></details>
              <p className="replay-note">原文没有提供额外训练数据的信息，因此该字段仍保留“本次未核实”。缺少陈述不能证明没有使用。</p></>}
            <button className="replay-back" onClick={() => setView('fields')}>← 返回字段台账</button>
          </section>}
          {view === 'versions' && <section className="replay-panel">
            <span className="replay-kicker">VERSIONED CANDIDATES</span><h2>中间候选和最终交付，分别留档。</h2>
            <p className="replay-lead">来自该运行保存的候选历史。版本变化本身不代表质量改善。</p>
            <div className="replay-versions">{state.fields.map(field => <article key={field.id}><h3>{field.name}</h3><div>{state.previous_versions.filter(v => v.field_id === field.id).map(v => <div key={v.revision}><span>v{v.revision} · 中间候选</span><p>{v.answer}</p></div>)}<div className="replay-current"><span>v{field.revision} · 最终交付</span><p>{field.answer}</p></div></div></article>)}</div>
            <p className="replay-note">核验意见绑定具体答案与证据版本；发生变化后，旧意见不能直接沿用。</p>
          </section>}
          {view === 'stop' && <section className="replay-panel">
            <span className="replay-kicker">STOPPING IS A RECORDED DECISION</span><h2>为收尾预留预算，保留未核实项。</h2>
            <p className="replay-lead">这条记录没有进入补读分支。它展示预算收尾与缺口保留，不衡量补救收益。</p>
            <div className="replay-metrics"><div><strong>{run.step}</strong><span>模型调用 · 含核验</span></div><div><strong>{run.tokens_used.toLocaleString('en-US')}</strong><span>累计 tokens</span></div><div><strong>{state.repair_rounds}</strong><span>补读轮数</span></div></div>
            <dl className="replay-decisions"><div><dt>停止原因</dt><dd>为报告预留预算 <code>{state.stop_reason}</code></dd></div><div><dt>字段覆盖</dt><dd>{state.present} / {state.total} 已填写，{state.unresolved} 项仍未核实</dd></div><div><dt>模型核验</dt><dd>{state.reviewed} 条候选意见，仍待人工确认</dd></div><div><dt>语义成功</dt><dd>尚未确定 <code>semantic_success: null</code></dd></div></dl>
            <p className="replay-note">接通检查不是准确率实验。模型的“支持”判断也需要独立校准。</p>
          </section>}
        </div>
        <footer className="replay-footer"><span>{run.model} · {new Date(run.created_at).toISOString().slice(0, 10)} · 合成接通记录</span><a href="/demo/field-recording.json" download>下载原始回放 JSON ↗</a></footer>
      </>}
    </main>
  </div>
}
