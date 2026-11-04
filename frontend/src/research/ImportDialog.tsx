import { useRef, useState } from 'react'
import { errorText, post, request, safeUrl, workspacePath } from './api'
import Icon from './Icons'
import type { CollectionResult, DiscoveryItem, DiscoveryResult, Source, ToolResult } from './types'

export default function ImportDialog({ workspaceId, onClose, onImported }: { workspaceId: string; onClose: () => void; onImported: () => Promise<void> }) {
  const [tab, setTab] = useState<'upload' | 'papers' | 'web'>('upload')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<DiscoveryResult | null>(null)
  const [collection, setCollection] = useState<CollectionResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [saving, setSaving] = useState<string | null>(null)
  const [saved, setSaved] = useState<string[]>([])
  const [error, setError] = useState('')
  const [progress, setProgress] = useState('')
  const input = useRef<HTMLInputElement>(null)
  async function upload(files: FileList | null) {
    if (!files?.length || busy) return
    setBusy(true); setError('')
    const failures: string[] = []
    let succeeded = 0
    for (const [index, file] of Array.from(files).entries()) {
      setProgress(`正在导入 ${index + 1}/${files.length} · ${file.name}`)
      try {
        if (file.size > 20 * 1024 * 1024) throw new Error('文件不能超过 20 MB')
        const body = new FormData(); body.append('file', file)
        await request<Source>(workspacePath(workspaceId, '/sources/upload'), { method: 'POST', body })
        succeeded++
      } catch (err) { failures.push(`${file.name}：${errorText(err)}`) }
    }
    setProgress(`已导入 ${succeeded} 份资料${failures.length ? `，${failures.length} 份失败` : '，可以开始研究了'}`)
    setError(failures.join('；')); setBusy(false)
    if (input.current) input.current.value = ''
    await onImported()
  }
  async function search() {
    if (!query.trim() || busy) return
    setBusy(true); setError(''); setResults(null)
    try {
      const result = await post<ToolResult<DiscoveryResult>>(workspacePath(workspaceId, `/discovery/${tab}/search`), { query: query.trim(), limit: 6 })
      if (result.status === 'failed') throw new Error(result.error ?? '查询失败')
      setResults(result.data)
    } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  async function collect() {
    if (!query.trim() || busy) return
    setBusy(true); setError(''); setCollection(null); setResults(null)
    setProgress('正在搜索、去重并下载 arXiv 候选全文…')
    try {
      const result = await post<ToolResult<CollectionResult>>(workspacePath(workspaceId, '/discovery/collect'), { query: query.trim(), limit: 3, index_rag: false })
      setCollection(result.data); await onImported()
    } catch (err) { setError(errorText(err)) } finally { setBusy(false); setProgress('') }
  }
  async function save(item: DiscoveryItem) {
    setSaving(item.uri); setError('')
    try {
      await post(workspacePath(workspaceId, '/sources'), {
        title: item.title.slice(0, 200), kind: tab === 'papers' ? 'abstract' : 'web', uri: item.uri,
        blocks: [{ text: `${tab === 'papers' ? '论文摘要（非全文）' : '网络搜索摘要（仅为线索）'}\n${item.title}\n${item.abstract || item.snippet || item.uri}` }],
      })
      setSaved(previous => [...previous, item.uri]); await onImported()
    } catch (err) { setError(errorText(err)) } finally { setSaving(null) }
  }
  return <div className="modal-backdrop"><section role="dialog" aria-modal="true" aria-labelledby="import-title" className="modal import-modal" onKeyDown={e => { if (e.key === 'Escape' && !busy && !saving) onClose() }}>
    <header className="modal-header"><div><span className="eyebrow">BUILD YOUR LIBRARY</span><h2 id="import-title">添加研究资料</h2></div><button className="icon-button" aria-label="关闭导入" disabled={busy || !!saving} onClick={onClose}><Icon name="close"/></button></header>
    <div className="segmented">{([['upload', '本地文件'], ['papers', '查找论文'], ['web', '网络搜索']] as const).map(([key, label]) => <button key={key} disabled={busy || !!saving} className={tab === key ? 'active' : ''} onClick={() => { setTab(key); setResults(null); setError('') }}>{label}</button>)}</div>
    {tab === 'upload' ? <>
      <button className="upload-zone" disabled={busy} onClick={() => input.current?.click()}><span className="upload-icon"><Icon name="upload" size={28}/></span><strong>{busy ? '正在处理资料…' : '选择论文、讲义或研究笔记'}</strong><span>PDF、PPTX、Markdown、TXT · 每份不超过 20 MB</span></button>
      <input ref={input} type="file" accept=".pdf,.pptx,.md,.txt" multiple hidden onChange={e => void upload(e.target.files)}/>
      <div className="info-note"><Icon name="book" size={18}/><p>当前支持提取文件中的文本。扫描页、图表和视频需先提供 OCR 或转写文本。导入后可直接进行关键词检索；语义检索需在资料页建立索引。</p></div>
      {progress && <p className="progress-label" role="status">{busy && <span className="spinner"/>}{progress}</p>}
    </> : <>
      <form className="search-form" onSubmit={e => { e.preventDefault(); void search() }}><Icon name="search"/><input aria-label={tab === 'papers' ? '论文搜索词' : '网络搜索词'} placeholder={tab === 'papers' ? '例如：retrieval augmented generation' : '输入希望查找的研究线索'} value={query} onChange={e => setQuery(e.target.value)} maxLength={500}/><button className="button primary" disabled={!query.trim() || busy}>{busy ? '搜索中…' : '搜索'}</button></form>
      <p className="muted small">{tab === 'papers' ? '从 arXiv 检索元数据和摘要。阅读全文后，才能支持论文中的具体结论。' : '网络结果作为发现线索保存，搜索摘要不能代替全文证据。'}</p>
      {tab === 'papers' && <div className="info-note"><Icon name="book" size={18}/><div><button className="button compact" disabled={!query.trim() || busy || !!saving} onClick={() => void collect()}>{busy ? '正在处理…' : '自动收集候选全文（最多 3 篇）'}</button><p>支持部分中文术语改写、版本去重和下载失败记录。候选需自行筛选；导入后可在文献资料中建立语义索引。</p></div></div>}
      {busy && progress && <p className="progress-label" role="status"><span className="spinner"/>{progress}</p>}
      {tab === 'papers' && collection && <div className="discovery-results"><p className="muted small">已导入或复用 {collection.imported_or_present} 篇 · {collection.status === 'completed' ? '采集完成' : '存在未完成项目'} · 查询：{collection.variants.join(' / ')}</p>{collection.papers.map(item => <article className="discovery-card" key={item.arxiv_id}><h3>{item.title}</h3><p>{item.status === 'imported' ? '全文已导入' : item.status === 'already_present' ? '当前课题已有全文，已复用' : '下载或解析失败'} · {item.arxiv_id}</p>{(item.error || item.index_error) && <p className="warning-text">{item.error || item.index_error}</p>}</article>)}{collection.warnings.length > 0 && <p className="warning-text">{collection.warnings.join('；')}</p>}</div>}
      <div className="discovery-results">{results?.results.map((item, index) => <article className="discovery-card" key={`${item.uri}-${index}`}><span className="pill neutral">{tab === 'papers' ? '论文摘要' : '搜索线索'}</span><h3>{item.title}</h3>{item.authors && <p className="muted small">{item.authors.slice(0, 4).join(' · ')}{item.published_at ? ` / ${item.published_at.slice(0, 10)}` : ''}</p>}<p className="discovery-abstract">{item.abstract || item.snippet}</p><footer>{safeUrl(item.uri) && <a href={safeUrl(item.uri)} target="_blank" rel="noreferrer">查看来源 <Icon name="arrow" size={14}/></a>}<button className="button compact" disabled={!!saving || saved.includes(item.uri)} onClick={() => void save(item)}>{saved.includes(item.uri) ? '已保存' : saving === item.uri ? '保存中…' : tab === 'papers' ? '保存摘要' : '保存线索'}</button></footer></article>)}{results?.results.length === 0 && <p className="empty-text">没有找到结果，试试英文关键词或更具体的题目。</p>}</div>
      {results?.warnings?.length ? <p className="warning-text">{results.warnings.join('；')}</p> : null}
    </>}
    {error && <div className="error-box" role="alert">{error}</div>}
    <footer className="modal-footer"><span className="muted small">资料只添加到当前课题</span><button className="button" disabled={busy || !!saving} onClick={onClose}>完成</button></footer>
  </section></div>
}
