import { useCallback, useEffect, useRef, useState } from 'react'
import { download, errorText, post, request, safeUrl, workspacePath } from './api'
import DatabaseView from './DatabaseView'
import Icon from './Icons'
import ImportDialog from './ImportDialog'
import ReportView from './ReportView'
import ToolTrace from './ToolTrace'
import DataLab from './DataLab'
import TranslationDesk from './TranslationDesk'
import AppearanceSettings, { AppearanceEntry } from './AppearanceSettings'
import BrandMark from './BrandMark'
import type { Evidence, Message, Run, RunEvent, SearchResult, Session, Source, Strategy, ToolResult, Workspace } from './types'

type Page = 'research' | 'library' | 'evidence' | 'history' | 'database' | 'analysis' | 'translation'
const NAV = [['research', 'research', '研究工作台'], ['library', 'book', '文献资料'], ['analysis', 'chart', '数据分析'], ['translation', 'translate', '文献翻译'], ['evidence', 'evidence', '证据笔记'], ['history', 'history', '研究记录'], ['database', 'database', '数据查询']] as const
const STRATEGIES: Record<Strategy, string> = { grep: '关键词与原文 · Grep', rag: '语义检索 · RAG', combined: '联合检索 · Grep + RAG' }
const STATUS: Record<Run['status'], string> = { queued: '排队中', running: '研究中', completed: '报告已生成', failed: '执行失败', cancelled: '已取消', interrupted: '已中断' }
const KINDS: Record<string, string> = { paper: '论文全文', document: '文档', slides: '演示文稿', transcript: '转写文本', web: '网络线索', abstract: '论文摘要' }
const TOOL_NAMES: Record<string, string> = { sources_search: '定位原文证据', plan_research: '规划必读论文与字段', papers_collect: '收集候选论文全文', sources_grep: '检索关键词与原文', sources_read: '回读原文', sources_rag: '检索语义证据', papers_search: '检索 arXiv 文献', web_search: '搜索网络资料', database_query: '查询资料数据', sources_list: '浏览课题资料' }
const isActive = (run: Run | null) => run?.status === 'running' || run?.status === 'queued'
const sorted = <T extends { created_at: string }>(items: T[]) => [...items].sort((a, b) => b.created_at.localeCompare(a.created_at))
function dateLabel(value: string) { return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }
function locationLabel(evidence: Evidence) {
  const pages = [...new Set(evidence.locations.map(item => item.page).filter(Boolean))]
  const time = evidence.locations.find(item => item.start_seconds != null)
  return `${pages.length ? `第 ${pages.join('、')} 页 · ` : time ? `${time.start_seconds}–${time.end_seconds} 秒 · ` : ''}行 ${evidence.start_line}–${evidence.end_line}`
}
function mergeEvidence(previous: Evidence[], next: Evidence[]) { return [...new Map([...previous, ...next].map(item => [item.id, item])).values()] }

export default function ResearchWorkbench() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [wid, setWid] = useState('')
  const [page, setPage] = useState<Page>('research')
  const [sources, setSources] = useState<Source[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [sessions, setSessions] = useState<Session[]>([])
  const [evidence, setEvidence] = useState<Evidence[]>([])
  const [messages, setMessages] = useState<Message[]>([])
  const [selectedRun, setSelectedRun] = useState<Run | null>(null)
  const [sessionId, setSessionId] = useState('')
  const [events, setEvents] = useState<RunEvent[]>([])
  const [tab, setTab] = useState<'report' | 'process' | 'conversation'>('report')
  const [question, setQuestion] = useState('')
  const [strategy, setStrategy] = useState<Strategy>('grep')
  const [network, setNetwork] = useState(false)
  const [budget, setBudget] = useState(20000)
  const [settings, setSettings] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [newWorkspace, setNewWorkspace] = useState(false)
  const [topicTitle, setTopicTitle] = useState('')
  const [topicQuestion, setTopicQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [healthy, setHealthy] = useState<boolean | null>(null)
  const [inspector, setInspector] = useState(false)
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(null)
  const [reading, setReading] = useState(false)
  const [indexing, setIndexing] = useState<string[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null)
  const [searching, setSearching] = useState(false)
  const [libraryFilter, setLibraryFilter] = useState('')
  const [sidebar, setSidebar] = useState(false)
  const [motion, setMotion] = useState(() => typeof window === 'undefined' || localStorage.getItem('research.motion') !== 'off')
  const [appearanceOpen, setAppearanceOpen] = useState(false)
  const closeAppearance = useCallback(() => setAppearanceOpen(false), [])
  const activeWorkspace = workspaces.find(item => item.id === wid)
  const currentWid = useRef(wid); currentWid.current = wid
  const questionInput = useRef<HTMLTextAreaElement>(null)
  const indexed = sources.filter(item => item.rag_indexed).length

  useEffect(() => {
    let alive = true
    request<{ items: Workspace[] }>('/api/v1/workspaces').then(({ items }) => {
      if (!alive) return
      setWorkspaces(items)
      const saved = localStorage.getItem('research.workspace')
      setWid(items.find(item => item.id === saved)?.id ?? items[0]?.id ?? '')
    }).catch(err => { if (alive) setError(errorText(err)) }).finally(() => { if (alive) setLoading(false) })
    const check = () => request<{ status: string }>('/research-health').then(data => { if (alive) setHealthy(data.status === 'ok') }).catch(() => { if (alive) setHealthy(false) })
    void check()
    const timer = setInterval(check, 30000)
    return () => { alive = false; clearInterval(timer) }
  }, [])

  const refreshSources = useCallback(async () => {
    const data = await request<{ items: Source[] }>(workspacePath(wid, '/sources?limit=500'))
    if (currentWid.current === wid) setSources(sorted(data.items))
  }, [wid])
  const refreshEvidence = useCallback(async () => {
    const data = await request<{ items: Evidence[] }>(workspacePath(wid, '/evidence?limit=500'))
    if (currentWid.current === wid) setEvidence(previous => mergeEvidence(previous, data.items))
  }, [wid])

  useEffect(() => {
    setSources([]); setRuns([]); setSessions([]); setEvidence([]); setSelectedRun(null); setSelectedEvidence(null)
    setSessionId(''); setMessages([]); setEvents([]); setSearchResult(null); setInspector(false); setError(''); setNotice(''); setQuestion('')
    if (!wid) return
    localStorage.setItem('research.workspace', wid)
    const controller = new AbortController()
    setLoading(true)
    Promise.all([
      request<{ items: Source[] }>(workspacePath(wid, '/sources?limit=500'), { signal: controller.signal }),
      request<{ items: Run[] }>(workspacePath(wid, '/runs'), { signal: controller.signal }),
      request<{ items: Session[] }>(workspacePath(wid, '/sessions'), { signal: controller.signal }),
      request<{ items: Evidence[] }>(workspacePath(wid, '/evidence?limit=500'), { signal: controller.signal }),
    ]).then(([sourceData, runData, sessionData, evidenceData]) => {
      if (controller.signal.aborted) return
      setSources(sorted(sourceData.items)); setRuns(sorted(runData.items)); setSessions(sorted(sessionData.items)); setEvidence(evidenceData.items)
      const latest = sorted(runData.items)[0]
      if (latest) { setSelectedRun(latest); setSessionId(latest.session_id); setTab(isActive(latest) ? 'process' : 'report') }
    }).catch(err => { if (!controller.signal.aborted) setError(errorText(err)) }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [wid])

  useEffect(() => {
    setMessages([])
    if (!wid || !sessionId) return
    const controller = new AbortController()
    request<{ items: Message[] }>(workspacePath(wid, `/sessions/${sessionId}/messages?limit=100`), { signal: controller.signal }).then(data => { if (!controller.signal.aborted) setMessages(data.items) }).catch(err => { if (!controller.signal.aborted) setError(errorText(err)) })
    return () => controller.abort()
  }, [wid, sessionId, selectedRun?.status])

  // Durable event replay + live SSE. Polling also recovers status when the stream disconnects.
  useEffect(() => {
    setEvents([])
    if (!wid || !selectedRun) return
    const runId = selectedRun.id
    let alive = true
    let cursor = 0
    let pending = false
    let stream: EventSource | undefined
    const add = (event: RunEvent) => {
      if (!alive) return
      cursor = Math.max(cursor, event.id)
      setEvents(previous => [...new Map([...previous, event].map(item => [item.id, item])).values()].sort((a, b) => a.id - b.id))
    }
    async function poll() {
      if (pending || !alive) return
      pending = true
      try {
        const [run, data] = await Promise.all([
          request<Run>(workspacePath(wid, `/runs/${runId}`)),
          request<{ items: RunEvent[] }>(workspacePath(wid, `/runs/${runId}/events?after=${cursor}`)),
        ])
        if (!alive) return
        data.items.forEach(add)
        let batch = data.items
        while (alive && batch.length === 100) {
          const next = await request<{ items: RunEvent[] }>(workspacePath(wid, `/runs/${runId}/events?after=${batch[batch.length - 1].id}`))
          if (!alive) return
          batch = next.items
          batch.forEach(add)
        }
        setSelectedRun(run); setRuns(previous => sorted([run, ...previous.filter(item => item.id !== run.id)]))
        if (!isActive(run)) { stream?.close(); clearInterval(timer); void refreshEvidence().catch(() => undefined) }
      } catch (err) { if (alive) setError(`任务状态更新失败：${errorText(err)}`) } finally { pending = false }
    }
    const timer = setInterval(() => void poll(), 2500)
    void poll()
    if (isActive(selectedRun)) {
      stream = new EventSource(workspacePath(wid, `/runs/${runId}/events/stream`))
      for (const type of ['run_started', 'tool_completed', 'tool_error', 'report_ready', 'run_failed', 'run_cancelled']) {
        stream.addEventListener(type, event => { try { add(JSON.parse((event as MessageEvent).data) as RunEvent) } catch { /* Polling recovers malformed or dropped events. */ } })
      }
      stream.addEventListener('done', () => { stream?.close(); void poll() })
    }
    return () => { alive = false; stream?.close(); clearInterval(timer) }
    // Keep the subscription stable as the selected run moves through states.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wid, selectedRun?.id, refreshEvidence])

  useEffect(() => { if (!notice) return; const timer = setTimeout(() => setNotice(''), 6000); return () => clearTimeout(timer) }, [notice])

  async function createWorkspace() {
    if (!topicTitle.trim()) return
    setBusy(true); setError('')
    try {
      const item = await post<Workspace>('/api/v1/workspaces', { title: topicTitle.trim(), research_question: topicQuestion.trim(), field: 'AI/NLP' })
      setWorkspaces(previous => [item, ...previous]); setWid(item.id); setPage('research'); setNewWorkspace(false); setTopicTitle(''); setTopicQuestion('')
    } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  async function startResearch() {
    if (!wid || !question.trim() || busy || runs.some(isActive)) return
    if (!sources.length && !network) { setError('请先添加资料，或在研究设置中允许联网发现文献。'); return }
    if (strategy === 'rag' && !indexed) { setError('语义检索需要先在文献资料页为至少一份资料建立索引。'); return }
    const taskWid = wid
    setBusy(true); setError('')
    try {
      let sid = sessionId
      if (!sid) {
        const session = await post<Session>(workspacePath(taskWid, '/sessions'), { title: question.trim().slice(0, 80) })
        sid = session.id
        if (currentWid.current === taskWid) { setSessions(previous => [session, ...previous]); setSessionId(sid) }
      }
      const run = await post<Run>(workspacePath(taskWid, '/runs'), { session_id: sid, question: question.trim(), retrieval_strategy: strategy, allow_network: network, max_steps: 10, max_tokens: budget, max_seconds: 300 })
      if (currentWid.current !== taskWid) return
      setSelectedRun(run); setRuns(previous => [run, ...previous]); setQuestion(''); setTab('process'); setPage('research')
      const data = await request<{ items: Message[] }>(workspacePath(taskWid, `/sessions/${sid}/messages?limit=100`))
      if (currentWid.current === taskWid) setMessages(data.items)
    } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  async function cancelRun() {
    if (!selectedRun) return
    setBusy(true)
    try { const run = await post<Run>(workspacePath(wid, `/runs/${selectedRun.id}/cancel`), {}); setSelectedRun(run); setRuns(previous => previous.map(item => item.id === run.id ? run : item)) } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  function openRun(run: Run) { setSelectedRun(run); setSessionId(run.session_id); setPage('research'); setTab(isActive(run) ? 'process' : 'report') }
  function freshResearch() { setSelectedRun(null); setSessionId(''); setMessages([]); setEvents([]); setQuestion(''); setPage('research'); setTab('report'); setSidebar(false); questionInput.current?.focus() }
  async function showEvidence(id: string) {
    const taskWid = wid
    setInspector(true); setReading(true); setSelectedEvidence(null)
    try {
      const item = evidence.find(item => item.id === id) ?? await request<Evidence>(workspacePath(taskWid, `/evidence/${encodeURIComponent(id)}`))
      if (currentWid.current === taskWid) { setSelectedEvidence(item); setEvidence(previous => mergeEvidence(previous, [item])) }
    } catch (err) { setError(errorText(err)) } finally { setReading(false) }
  }
  async function readSource(sourceId: string, start = 1) {
    const taskWid = wid
    setInspector(true); setReading(true)
    try {
      const call = await post<ToolResult<Evidence>>(workspacePath(taskWid, '/sources/read'), { source_id: sourceId, start_line: start, line_count: 40 })
      if (call.status === 'failed') throw new Error(call.error ?? '原文读取失败')
      if (currentWid.current === taskWid) { setSelectedEvidence(call.data); setEvidence(previous => mergeEvidence(previous, [call.data])) }
    } catch (err) { setError(errorText(err)) } finally { setReading(false) }
  }
  async function indexSource(source: Source) {
    const taskWid = wid
    setIndexing(previous => [...previous, source.id]); setError('')
    try {
      await post(workspacePath(taskWid, `/sources/${source.id}/indexes/rag`), {})
      if (currentWid.current === taskWid) { await refreshSources(); setNotice(`已为「${source.title}」建立语义索引`) }
    } catch (err) { setError(errorText(err)) } finally { setIndexing(previous => previous.filter(id => id !== source.id)) }
  }
  async function searchEvidence() {
    if (!searchQuery.trim()) return
    const taskWid = wid
    setSearching(true); setError(''); setSearchResult(null)
    try {
      const result = await post<SearchResult>(workspacePath(taskWid, '/retrieval/search'), { query: searchQuery.trim(), strategy, limit: 20, regex: false, ignore_case: true })
      if (currentWid.current === taskWid) { setSearchResult(result); setEvidence(previous => mergeEvidence(previous, result.hits)) }
    } catch (err) { setError(errorText(err)) } finally { setSearching(false) }
  }
  async function originalSource() {
    if (!selectedEvidence) return
    setReading(true)
    try {
      const response = await fetch(workspacePath(wid, `/sources/${selectedEvidence.source_id}/original`))
      if (!response.ok) throw new Error(response.status === 404 ? '这份资料通过摘要或文本导入，没有原始附件。可打开来源网页。' : '附件下载失败')
      const disposition = response.headers.get('content-disposition') ?? ''
      const ext = disposition.match(/\.(pdf|pptx|md|txt)/i)?.[1] ?? 'bin'
      download(await response.blob(), `${selectedEvidence.title.slice(0, 90).replace(/[<>:"/\\|?*]/g, '_')}.${ext}`, 'application/octet-stream')
    } catch (err) { setError(errorText(err)) } finally { setReading(false) }
  }
  async function exportReport() {
    if (!selectedRun) return
    try {
      const result = await request<{ markdown: string }>(workspacePath(wid, `/runs/${selectedRun.id}/report`))
      download(result.markdown, `${(selectedRun.report?.title ?? '研究报告').slice(0, 80).replace(/[<>:"/\\|?*]/g, '_')}.md`)
    } catch (err) { setError(errorText(err)) }
  }

  const renderComposer = () => <div className="composer-wrap"><div className="composer">
    <textarea ref={questionInput} aria-label="研究问题" placeholder={selectedRun ? '继续追问，或对这份报告提出核验问题…' : '描述你想研究的问题，例如：这些方法的实验设置是否可比？'} value={question} onChange={e => setQuestion(e.target.value)} maxLength={10000} disabled={!wid || busy} onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.nativeEvent.isComposing) { e.preventDefault(); void startResearch() } }}/>
    <div className="composer-toolbar"><div className="composer-options"><button className={`soft-button ${settings ? 'selected' : ''}`} onClick={() => setSettings(!settings)} aria-expanded={settings}><Icon name="settings" size={15}/>{strategy === 'grep' ? '原文检索' : strategy === 'rag' ? '语义检索' : '联合检索'}<Icon name="down" size={12}/></button><span className={`network-indicator ${network ? 'on' : ''}`}><Icon name="globe" size={14}/>{network ? '允许联网' : '仅课题资料'}</span></div>{runs.some(isActive) ? <button className="button" disabled={busy} onClick={() => { const run = runs.find(isActive); if (run) openRun(run) }}>查看进行中的研究 <Icon name="arrow" size={15}/></button> : <button className="button primary" disabled={!wid || !question.trim() || busy} onClick={() => void startResearch()}>{busy ? '提交中…' : '开始研究'}<Icon name="arrow" size={17}/></button>}</div>
  </div>{settings && <div className="research-settings"><label>检索方式<select value={strategy} onChange={e => setStrategy(e.target.value as Strategy)}>{Object.entries(STRATEGIES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label className="checkbox-label"><input type="checkbox" checked={network} onChange={e => setNetwork(e.target.checked)}/>允许搜索 arXiv 与网络资料</label><label>Token 总预算<input type="number" min={2000} max={100000} step={1000} value={budget} onChange={e => setBudget(Math.min(100000, Math.max(2000, Number(e.target.value) || 20000)))}/></label><p>Grep 在全文中匹配并回读；RAG 检索已建索引的资料。联合模式执行关键词与语义双路检索，再融合证据；单路不可用时会标记降级。当前 {indexed}/{sources.length} 份资料已建索引。</p></div>}<p className="composer-hint">范围：当前课题全部资料{strategy !== 'grep' ? '（语义部分仅覆盖已建索引资料）' : ''}<span>Ctrl / ⌘ + Enter 发送</span></p></div>

  return <div className={`workbench ${inspector ? 'has-inspector' : ''}`} data-motion={motion ? 'on' : 'off'}>
    {sidebar && <button className="sidebar-scrim" aria-label="收起导航" onClick={() => setSidebar(false)}/>}
    <aside className={`sidebar ${sidebar ? 'open' : ''}`}><a className="brand" href="/" onClick={e => { e.preventDefault(); setPage('research'); setSidebar(false) }}><BrandMark/><div><strong>研序<span> BETA</span></strong><small>RESEARCH DESK</small></div></a>
      <div className="workspace-switch"><label htmlFor="workspace-select">我的研究空间</label><div><select id="workspace-select" value={wid} onChange={e => { setWid(e.target.value); setSidebar(false) }} disabled={busy || loading}><option value="" disabled>选择课题</option>{workspaces.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select><button className="icon-button" title="创建课题" aria-label="创建课题" onClick={() => setNewWorkspace(true)}><Icon name="plus" size={17}/></button></div></div>
      <button className="new-research" disabled={!wid} onClick={freshResearch}><Icon name="plus" size={18}/>发起新研究<span>↗</span></button>
      <nav aria-label="主导航">{NAV.map(([key, icon, label]) => <button key={key} className={page === key ? 'active' : ''} onClick={() => { setPage(key); setSidebar(false); setError('') }}><Icon name={icon} size={19}/><span>{label}</span>{key === 'library' && <b>{sources.length}</b>}</button>)}</nav>
      <div className="recent-heading">最近研究 <button className="icon-button" aria-label="查看全部研究" onClick={() => setPage('history')}><Icon name="history" size={14}/></button></div>
      <div className="recent-runs">{runs.slice(0, 4).map(run => <button key={run.id} className={run.id === selectedRun?.id ? 'selected' : ''} onClick={() => { openRun(run); setSidebar(false) }}><span className={`tiny-dot ${run.status === 'completed' ? 'done' : isActive(run) ? 'live' : ''}`}/><span>{run.question}</span></button>)}{!runs.length && <p>从一个好问题开始。</p>}</div>
      <div className="sidebar-bottom"><AppearanceEntry onOpen={() => setAppearanceOpen(true)}/><div className="local-profile"><span>研</span><div><strong>本地研究空间</strong><small>AI / NLP · 个人工作台</small></div></div><div className="service-state"><i className={healthy ? 'online' : healthy === false ? 'offline' : ''}/>{healthy ? '研究服务已连接' : healthy === false ? '研究服务未连接' : '正在连接服务'}<span>LOCAL</span></div></div>
    </aside>
    <div className="main-shell"><header className="topbar"><div className="breadcrumbs"><button className="icon-button mobile-menu" aria-label="展开导航" onClick={() => setSidebar(true)}><Icon name="menu"/></button><Icon name="book" size={17}/><span>{activeWorkspace?.title ?? '我的研究空间'}</span><Icon name="chevron" size={12}/><strong>{NAV.find(item => item[0] === page)?.[2]}</strong></div><div className="top-actions"><a className="button compact" href="/demo"><Icon name="history" size={15}/>证据链演示</a><span className="field-tag">AI & NLP</span><button className="button compact" disabled={!wid || loading} onClick={() => setImportOpen(true)}><Icon name="plus" size={15}/>添加资料</button><button className={`icon-button ${inspector ? 'selected' : ''}`} aria-label={inspector ? '收起证据面板' : '打开证据面板'} onClick={() => setInspector(!inspector)}><Icon name="panel" size={19}/></button></div></header>
      {error && <div className="global-message error-box" role="alert"><Icon name="warning" size={17}/><span>{error}</span><button className="icon-button" aria-label="关闭错误提示" onClick={() => setError('')}><Icon name="close" size={15}/></button></div>}
      {notice && <div className="toast" role="status"><Icon name="check" size={16}/>{notice}</div>}
      <main key={`${wid}-${page}`} className="motion-main">
        {loading ? <div className="loading-page"><span className="spinner"/><p>正在打开研究空间…</p></div> : !wid ? <div className="first-workspace"><BrandMark/><span className="eyebrow">YOUR NEXT RESEARCH STARTS HERE</span><h1>为你的研究，留一条证据路径。</h1><p>创建课题，导入论文，把文献中的问题变成可追溯的研究报告。</p><button className="button primary" onClick={() => setNewWorkspace(true)}><Icon name="plus" size={18}/>创建第一个课题</button></div> : <>
          {page === 'research' && <div className={`research-page ${selectedRun ? 'with-run' : ''}`}>
            {!selectedRun ? <><section className="welcome"><div className="welcome-kicker"><span/>从文献到洞见，每一步都有据可循</div><h1>今天，想研究什么<span>？</span></h1><p>一起梳理论文、比较方法，找到值得继续追问的问题。</p><div className="workspace-summary"><span><Icon name="book" size={15}/>{sources.length} 份研究资料</span><i/><span><Icon name="evidence" size={15}/>{evidence.length >= 500 ? '500+' : evidence.length} 条原文证据</span><i/><span>{runs.filter(run => run.status === 'completed').length} 份报告草稿</span></div></section>
              {renderComposer()}
              <section className="starter-section"><div className="list-heading"><h3>从这些角度开始</h3><span>选择一个问题，补充你的研究范围</span></div><div className="starter-grid">{[
                { icon: 'book', title: '读懂一组论文', description: '梳理核心问题、方法与证据', question: '请根据当前课题中的论文，梳理它们研究的核心问题、主要方法和局限。每个结论请引用原文证据。' },
                { icon: 'database', title: '比较实验设置', description: '对照数据、指标与适用条件', question: '请比较当前课题中论文的实验设置：数据集、模型、评估指标和主要结果，并说明哪些结果可比、哪些不能直接比较。请给出原文证据。' },
                { icon: 'evidence', title: '为组会找证据', description: '生成带引用的讨论提纲', question: '请基于当前课题资料，整理一份组会讨论提纲：核心发现、尚不确定的结论，以及三个值得继续研究的问题。每项发现请给出原文证据。' },
              ].map(item => <button key={item.title} className="starter-card" onClick={() => { setQuestion(item.question); questionInput.current?.focus() }}><Icon name={item.icon} size={22}/><strong>{item.title}</strong><p>{item.description}</p><Icon name="arrow" size={17}/></button>)}</div></section>
            </> : <><div className="run-heading"><div><span className="eyebrow">RESEARCH SESSION</span><h1>{selectedRun.question}</h1><div className="run-meta"><span className={`pill ${selectedRun.status === 'completed' ? 'green' : isActive(selectedRun) ? 'blue' : 'neutral'}`}>{isActive(selectedRun) && <span className="spinner"/>}{selectedRun.status === 'completed' && selectedRun.source_coverage?.status === 'partial' ? '报告已生成 · 文献覆盖不足' : STATUS[selectedRun.status]}</span><span>{dateLabel(selectedRun.created_at)}</span><span>{STRATEGIES[selectedRun.retrieval_strategy]}</span></div></div>{isActive(selectedRun) ? <button className="button compact" disabled={busy} onClick={() => void cancelRun()}><Icon name="stop" size={14}/>停止研究</button> : selectedRun.report ? <button className="button compact" onClick={() => void exportReport()}><Icon name="download" size={15}/>导出报告</button> : null}</div>
              <div className="run-tabs" role="tablist" aria-label="研究内容">{([['report', '研究报告'], ['process', '研究过程'], ['conversation', '对话记录']] as const).map(([key, label]) => <button role="tab" aria-selected={tab === key} key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{label}{key === 'process' && events.length > 0 && <span>{events.length}</span>}</button>)}<span className="run-usage">{selectedRun.step} 轮 · {selectedRun.tokens_estimated ? '约 ' : ''}{selectedRun.tokens_used.toLocaleString()} tokens</span></div>
              <div className="run-content">{tab === 'report' ? selectedRun.report ? <ReportView report={selectedRun.report} sources={sources} evidence={evidence} onSelect={id => void showEvidence(id)}/> : <div className="report-empty"><span className={`empty-illustration ${isActive(selectedRun) ? 'working' : ''}`}><Icon name={isActive(selectedRun) ? 'research' : 'file'} size={32}/></span><h2>{isActive(selectedRun) ? '正在阅读资料，整理证据' : STATUS[selectedRun.status]}</h2><p>{selectedRun.error ?? (isActive(selectedRun) ? '报告生成后会出现在这里。你可以查看研究过程，或稍后回来。' : '本次没有生成报告，可以调整问题后重新发起研究。')}</p><button className="button" onClick={() => isActive(selectedRun) ? setTab('process') : setQuestion(selectedRun.question)}>{isActive(selectedRun) ? '查看研究过程' : '将问题放回输入框'}<Icon name="arrow" size={15}/></button></div> : tab === 'process' ? <div className="process-view"><div className="process-intro"><Icon name="research" size={22}/><div><h3>研究的每一步，都有记录</h3><p>展示实际工具调用、错误与报告状态，方便复盘资料使用过程。</p></div></div><ol className="timeline">{events.map(event => <li key={event.id} className={event.type.includes('error') || event.type.includes('failed') ? 'event-error' : ''}><span className="event-dot"><Icon name={event.type === 'report_ready' ? 'check' : event.type.includes('error') || event.type.includes('failed') ? 'warning' : 'search'} size={13}/></span><div><strong>{event.type === 'run_started' ? '开始研究' : event.type === 'report_ready' ? '研究报告已生成 · 待核验' : event.type === 'run_cancelled' ? '研究已取消' : event.type === 'run_failed' ? '研究执行失败' : TOOL_NAMES[String(event.data.name)] ?? String(event.data.name ?? event.type)}</strong><p>{event.type === 'run_started' ? `使用 ${String(event.data.model ?? selectedRun.model)}` : typeof event.data.error === 'string' ? event.data.error : event.type === 'report_ready' ? '请回到报告，逐项核对引用和实验条件。' : `工具调用${event.type === 'tool_error' ? '失败' : '完成'}`}</p><ToolTrace workspaceId={wid} callId={typeof event.data.tool_call_id === 'string' ? event.data.tool_call_id : undefined} fallback={event.data}/></div><time>{dateLabel(event.created_at)}</time></li>)}</ol>{isActive(selectedRun) && <div className="processing-line"><span className="spinner"/>正在继续研究…</div>}{!events.length && !isActive(selectedRun) && <p className="empty-text">本次任务没有过程记录。</p>}</div> : <div className="conversation"><div className="list-heading"><h3>本次会话 <span>{sessions.find(item => item.id === sessionId)?.title}</span></h3></div>{messages.map(message => <div className={`message ${message.role}`} key={message.id}><div className="message-avatar">{message.role === 'user' ? '我' : <Icon name="research" size={16}/>}</div><div><span className="message-label">{message.role === 'user' ? '研究问题' : '研究助手'}<time>{dateLabel(message.created_at)}</time></span><p>{message.role === 'assistant' && message.run_id && runs.find(run => run.id === message.run_id)?.report ? '已生成一份带引用的研究报告，请打开报告核对具体结论。' : message.content}</p>{message.role === 'assistant' && message.run_id && <button className="text-button" onClick={() => { const run = runs.find(item => item.id === message.run_id); if (run) openRun(run) }}>查看对应报告<Icon name="arrow" size={14}/></button>}</div></div>)}{!messages.length && <p className="empty-text">没有对话记录。</p>}</div>}</div>{renderComposer()}
            </>}
          </div>}
          {page === 'library' && <div className="page-body"><div className="page-heading"><div><span className="eyebrow">YOUR RESEARCH LIBRARY</span><h1>课题文献与资料</h1><p>保存原始资料，随时回到具体页码和文本位置。</p></div><button className="button primary" onClick={() => setImportOpen(true)}><Icon name="plus" size={17}/>添加资料</button></div><div className="library-stats"><span><strong>{sources.length}</strong> 份资料</span><span><strong>{sources.filter(item => item.kind === 'paper').length}</strong> 篇全文论文</span><span><strong>{indexed}</strong> 份语义索引</span><p>全部文本资料均可直接使用 Grep 检索</p></div><div className="filter-bar"><Icon name="search" size={17}/><input aria-label="筛选资料标题" placeholder="按标题筛选资料…" value={libraryFilter} onChange={e => setLibraryFilter(e.target.value)}/><span>{sources.filter(item => item.title.toLowerCase().includes(libraryFilter.toLowerCase())).length} 份资料</span></div><div className="source-list">{sources.filter(item => item.title.toLowerCase().includes(libraryFilter.toLowerCase())).map(source => <article className="source-card" key={source.id}><span className={`source-kind ${source.kind === 'abstract' || source.kind === 'web' ? 'clue' : ''}`}><Icon name="file" size={23}/><small>{source.kind === 'paper' ? 'PDF' : source.kind === 'slides' ? 'PPT' : source.kind === 'abstract' ? 'ABS' : 'TXT'}</small></span><div className="source-detail"><div><span className="pill neutral">{KINDS[source.kind] ?? source.kind}</span>{source.rag_indexed && <span className="index-label"><Icon name="check" size={12}/>语义索引已就绪</span>}</div><h3><button onClick={() => void readSource(source.id)}>{source.title}</button></h3><p>{source.line_count.toLocaleString()} 行 · 版本 {source.version} · {dateLabel(source.created_at)} 添加</p>{source.uri && safeUrl(source.uri) && <a className="source-uri" href={safeUrl(source.uri)} target="_blank" rel="noreferrer"><Icon name="link" size={12}/>{source.uri}</a>}</div><div className="source-actions"><button className="button compact" onClick={() => void readSource(source.id)}>阅读原文<Icon name="chevron" size={13}/></button><button className="text-button" disabled={source.rag_indexed || indexing.includes(source.id)} onClick={() => void indexSource(source)}>{indexing.includes(source.id) ? <><span className="spinner"/>正在建立索引…</> : source.rag_indexed ? '已建立语义索引' : '建立语义索引'}</button></div></article>)}{!sources.length && <div className="library-empty"><Icon name="book" size={36}/><h2>先放进第一篇论文</h2><p>支持全文、演示文稿、研究笔记与文献摘要。</p><button className="button primary" onClick={() => setImportOpen(true)}>添加研究资料<Icon name="plus" size={16}/></button></div>}{sources.length > 0 && !sources.some(item => item.title.toLowerCase().includes(libraryFilter.toLowerCase())) && <p className="empty-text">没有匹配的资料，请换一个标题关键词。</p>}</div></div>}
          {page === 'evidence' && <div className="page-body"><div className="page-heading"><div><span className="eyebrow">BACK TO THE SOURCE</span><h1>在原文中，找到依据</h1><p>检索课题资料，回看研究中产生的原文证据。</p></div></div><form className="evidence-search" onSubmit={e => { e.preventDefault(); void searchEvidence() }}><div className="search-form"><Icon name="search"/><input aria-label="原文检索词" placeholder="关键词、方法名、数据集，或你想找到的概念…" value={searchQuery} onChange={e => setSearchQuery(e.target.value)} maxLength={500}/><button className="button primary" disabled={searching || !searchQuery.trim()}>{searching ? '检索中…' : '检索原文'}</button></div><div className="search-options"><select aria-label="证据检索方式" value={strategy} onChange={e => setStrategy(e.target.value as Strategy)}>{Object.entries(STRATEGIES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select><span>关键词使用字面匹配，忽略大小写</span></div></form>{searchResult?.warnings.length ? <div className="warning-box">部分检索不可用：{searchResult.warnings.join('；')}</div> : null}{searchResult?.truncated && <p className="warning-text">结果已截断，请缩小查询范围。</p>}<div className="list-heading evidence-list-heading"><h3>{searchResult ? '检索结果' : '已保存的原文片段'} <span>{searchResult ? searchResult.hits.length : evidence.length >= 500 ? '500+' : evidence.length}</span></h3>{searchResult && <button className="text-button" onClick={() => setSearchResult(null)}>返回全部片段</button>}</div><div className="evidence-grid">{(searchResult?.hits ?? [...evidence].reverse()).map(item => <button className="evidence-card" key={item.id} onClick={() => void showEvidence(item.id)}><div><span className="pill neutral">{item.method === 'rag' ? '语义检索' : item.method === 'grep' ? '关键词检索' : '原文片段'}</span><Icon name="chevron" size={15}/></div><h3>{item.title}</h3><p>{item.quote.slice(0, 380)}{item.quote.length > 380 ? '…' : ''}</p><footer><Icon name="link" size={13}/>{locationLabel(item)}</footer></button>)}</div>{(searchResult?.hits ?? evidence).length === 0 && <p className="empty-text">{searchResult ? '没有找到原文匹配。可调整关键词，或为资料建立语义索引后重试。' : '研究或阅读原文后，证据片段会保存在这里。'}</p>}</div>}
          {page === 'history' && <div className="page-body"><div className="page-heading"><div><span className="eyebrow">YOUR RESEARCH TRAIL</span><h1>研究记录</h1><p>问题、工具调用与报告都保存在当前课题，随时回来继续。</p></div><button className="button primary" onClick={freshResearch}><Icon name="plus" size={17}/>发起新研究</button></div><div className="history-list">{runs.map(run => <button key={run.id} className="history-card" onClick={() => openRun(run)}><span className="history-icon"><Icon name={run.status === 'completed' ? 'file' : 'research'} size={22}/></span><div><span className={`pill ${run.status === 'completed' ? 'green' : 'neutral'}`}>{STATUS[run.status]}</span><h3>{run.question}</h3><p>{dateLabel(run.created_at)} · {STRATEGIES[run.retrieval_strategy]} · {run.step} 轮研究</p></div><Icon name="arrow" size={18}/></button>)}{!runs.length && <div className="library-empty"><Icon name="history" size={36}/><h2>你的研究记录，从这里开始</h2><p>发起一次研究后，这里会保留完整的过程与结果。</p><button className="button" onClick={freshResearch}>去研究工作台<Icon name="arrow" size={16}/></button></div>}</div></div>}
          {page === 'database' && <DatabaseView key={wid} workspaceId={wid}/>}
          {page === 'analysis' && <DataLab key={wid} workspaceId={wid} sources={sources} onSourcesChanged={refreshSources}/>}
          {page === 'translation' && <TranslationDesk key={wid} workspaceId={wid} onEvidence={id => void showEvidence(id)}/>}
        </>}
      </main>
    </div>
    {inspector && <><button className="inspector-scrim" aria-label="收起原文证据" onClick={() => setInspector(false)}/><aside className="evidence-inspector"><header><div><Icon name="evidence" size={18}/><strong>原文证据</strong></div><button className="icon-button" aria-label="关闭证据面板" onClick={() => setInspector(false)}><Icon name="close" size={17}/></button></header>{reading && <div className="reading-state"><span className="spinner"/>正在读取原文…</div>}{selectedEvidence ? <div className="inspector-content"><span className="eyebrow">SOURCE EXCERPT</span><h2>{selectedEvidence.title}</h2><span className="location-pill"><Icon name="link" size={13}/>{locationLabel(selectedEvidence)}</span><div className="excerpt"><span className="quote-mark">“</span><pre>{selectedEvidence.quote}</pre></div><div className="excerpt-pagination"><button className="button compact" disabled={reading || selectedEvidence.start_line <= 1} onClick={() => void readSource(selectedEvidence.source_id, Math.max(1, selectedEvidence.start_line - 40))}>上一段</button><button className="button compact" disabled={reading || selectedEvidence.end_line >= (sources.find(item => item.id === selectedEvidence.source_id)?.line_count ?? Infinity)} onClick={() => void readSource(selectedEvidence.source_id, selectedEvidence.end_line + 1)}>下一段<Icon name="chevron" size={13}/></button></div><div className="evidence-provenance"><strong>来源与定位</strong><p>该片段绑定导入时的资料版本，可通过页码和行号回到原文。</p><details><summary>查看版本标识</summary><code>SHA-256<br/>{selectedEvidence.source_hash}</code><small>证据 ID · {selectedEvidence.id}</small></details></div><div className="inspector-actions"><button className="button" disabled={reading} onClick={() => void originalSource()}><Icon name="download" size={15}/>下载原始附件</button>{safeUrl(selectedEvidence.uri) && <a className="button" href={safeUrl(selectedEvidence.uri)} target="_blank" rel="noreferrer"><Icon name="globe" size={15}/>打开来源网页</a>}</div></div> : !reading && <div className="inspector-empty"><span><Icon name="link" size={29}/></span><h3>让结论有处可查</h3><p>点击报告中的引用，或资料旁的“阅读原文”，在这里核对上下文。</p><div><Icon name="file" size={15}/>原文片段<Icon name="chevron" size={12}/><Icon name="evidence" size={15}/>研究结论</div></div>}</aside></>}
    {importOpen && wid && <ImportDialog key={wid} workspaceId={wid} onClose={() => setImportOpen(false)} onImported={refreshSources}/>}
    {appearanceOpen && <AppearanceSettings onClose={closeAppearance} motion={motion} onMotionChange={value => { setMotion(value); try { localStorage.setItem('research.motion', value ? 'on' : 'off') } catch { /* Use the preference for this session. */ } }}/>} 
    {newWorkspace && <div className="modal-backdrop"><form className="modal topic-modal" role="dialog" aria-modal="true" aria-labelledby="topic-title" onSubmit={e => { e.preventDefault(); void createWorkspace() }}><header className="modal-header"><div><span className="eyebrow">A SPACE FOR YOUR QUESTION</span><h2 id="topic-title">创建一个研究课题</h2></div><button type="button" className="icon-button" aria-label="关闭创建课题" disabled={busy} onClick={() => setNewWorkspace(false)}><Icon name="close"/></button></header><label>课题名称<input autoFocus value={topicTitle} onChange={e => setTopicTitle(e.target.value)} placeholder="例如：检索增强与科研 Agent" maxLength={200} required/></label><label>研究方向或核心问题 <span className="muted">（选填）</span><textarea value={topicQuestion} onChange={e => setTopicQuestion(e.target.value)} placeholder="记录你想持续探索的问题…" maxLength={4000}/></label><p className="muted small">每个课题独立保存资料、研究记录和证据。</p>{error && <div className="error-box">{error}</div>}<footer className="modal-footer"><button type="button" className="button" disabled={busy} onClick={() => setNewWorkspace(false)}>取消</button><button className="button primary" disabled={!topicTitle.trim() || busy}>{busy ? '创建中…' : '创建课题'}<Icon name="arrow" size={16}/></button></footer></form></div>}
  </div>
}
