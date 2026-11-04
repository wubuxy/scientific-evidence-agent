import { useCallback, useEffect, useRef, useState } from 'react'
import { errorText, post, request, workspacePath } from './api'
import AnalysisResults from './AnalysisResults'
import Icon from './Icons'
import { activeJob, formatNumber, JOB_STATUS, type AnalysisJob, type Dataset, type Method } from './labTypes'
import type { Source } from './types'

const METHODS: { id: Method; title: string; subtitle: string; icon: string }[] = [
  { id: 'descriptive', title: '描述统计', subtitle: '分布、均值与缺失概况', icon: 'chart' },
  { id: 'paired_t', title: '配对 t 检验', subtitle: '同一对象上的两组结果', icon: 'compare' },
  { id: 'welch_t', title: '独立样本 t 检验', subtitle: '两组独立样本的均值', icon: 'compare' },
  { id: 'chi_square', title: '卡方检验', subtitle: '分类变量与频数关联', icon: 'database' },
  { id: 'correlation', title: '相关性分析', subtitle: 'Pearson 与 Spearman', icon: 'chart' },
  { id: 'linear_regression', title: '线性回归', subtitle: '一个变量与响应的关系', icon: 'chart' },
  { id: 'hotelling_t2', title: 'Hotelling T²', subtitle: '多变量均值向量与零比较', icon: 'compare' },
  { id: 'lstm', title: 'LSTM 序列预测', subtitle: '按时间留出测试与基线', icon: 'wave' },
]

export default function DataLab({ workspaceId, sources, onSourcesChanged }: { workspaceId: string; sources: Source[]; onSourcesChanged: () => Promise<void> }) {
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [jobs, setJobs] = useState<AnalysisJob[]>([])
  const [datasetId, setDatasetId] = useState('')
  const [job, setJob] = useState<AnalysisJob | null>(null)
  const [method, setMethod] = useState<Method>('descriptive')
  const [columns, setColumns] = useState<string[]>([])
  const [weight, setWeight] = useState('')
  const [time, setTime] = useState('')
  const [alpha, setAlpha] = useState(.05)
  const [windowSize, setWindowSize] = useState(12)
  const [epochs, setEpochs] = useState(40)
  const [horizon, setHorizon] = useState(12)
  const [trainFraction, setTrainFraction] = useState(.8)
  const [correction, setCorrection] = useState(true)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadTitle, setUploadTitle] = useState('')
  const [uploadSource, setUploadSource] = useState('')
  const [uploadPage, setUploadPage] = useState('')
  const [uploadNote, setUploadNote] = useState('')
  const [paste, setPaste] = useState(false)
  const [csv, setCsv] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [preview, setPreview] = useState(false)
  const [capabilities, setCapabilities] = useState<{ statistics: boolean; lstm: boolean } | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const alive = useRef(true)
  const selected = datasets.find(item => item.id === datasetId)
  const numeric = selected?.columns.filter(column => column.kind === 'numeric').map(column => column.name) ?? []
  const options = method === 'chi_square' ? selected?.columns.map(column => column.name) ?? [] : numeric
  const multi = method === 'descriptive' || method === 'hotelling_t2'

  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const reload = useCallback(async () => {
    const [data, analyses] = await Promise.all([request<{ items: Dataset[] }>(workspacePath(workspaceId, '/datasets')), request<{ items: AnalysisJob[] }>(workspacePath(workspaceId, '/analyses'))])
    if (!alive.current) return
    setDatasets(data.items); setJobs(analyses.items)
    setDatasetId(previous => previous || data.items[0]?.id || '')
  }, [workspaceId])
  useEffect(() => {
    reload().catch(err => { if (alive.current) setError(errorText(err)) }).finally(() => { if (alive.current) setLoading(false) })
    request<{ statistics: boolean; lstm: boolean }>(workspacePath(workspaceId, '/lab/capabilities')).then(data => { if (alive.current) setCapabilities(data) }).catch(() => undefined)
  }, [reload, workspaceId])
  useEffect(() => {
    const names = method === 'chi_square' ? selected?.columns.map(column => column.name) ?? [] : selected?.columns.filter(column => column.kind === 'numeric').map(column => column.name) ?? []
    setColumns(names.slice(0, method === 'lstm' ? 1 : multi ? 6 : 2)); setWeight(''); setTime('')
    // Column defaults are tied to a dataset and method, not result polling.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetId, method])
  useEffect(() => {
    if (!job || !activeJob(job)) return
    let stopped = false, pending = false
    const id = job.id
    const poll = async () => {
      if (pending || stopped) return
      pending = true
      try {
        const value = await request<AnalysisJob>(workspacePath(workspaceId, `/analyses/${id}`))
        if (!stopped) { setJob(value); setJobs(previous => [value, ...previous.filter(item => item.id !== id)]); if (!activeJob(value)) clearInterval(timer) }
      } catch (err) { if (!stopped) setError(errorText(err)) } finally { pending = false }
    }
    const timer = setInterval(() => void poll(), 1000)
    void poll()
    return () => { stopped = true; clearInterval(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, workspaceId])

  async function upload(file: File) {
    setBusy(true); setError('')
    try {
      const form = new FormData(); form.append('file', file)
      if (uploadTitle.trim()) form.append('title', uploadTitle.trim())
      if (uploadSource) form.append('source_id', uploadSource)
      if (uploadSource && uploadPage) form.append('page', uploadPage)
      if (uploadNote.trim()) form.append('note', uploadNote.trim())
      const data = await request<Dataset>(workspacePath(workspaceId, '/datasets/upload'), { method: 'POST', body: form })
      if (!alive.current) return
      setDatasets(previous => [data, ...previous]); setDatasetId(data.id); setJob(null); setUploadOpen(false); setPreview(true); setUploadTitle(''); setCsv(''); setNotice('数据已导入，先检查列类型、缺失值和样本单位，再选择分析方法。')
    } catch (err) { if (alive.current) setError(errorText(err)) } finally { if (alive.current) setBusy(false); if (fileInput.current) fileInput.current.value = '' }
  }
  async function loadExample(series = false) {
    setError('')
    try {
      const response = await fetch(`/examples/${series ? 'time-series' : 'experiment-runs'}.csv`)
      if (!response.ok) throw new Error('示例读取失败')
      setUploadSource(''); setUploadPage(''); setUploadNote('')
      // Examples are explicitly synthetic and are never linked to a paper.
      const form = new FormData(); form.append('file', new File([await response.text()], series ? '演示合成时间序列.csv' : '演示合成实验结果.csv', { type: 'text/csv' }))
      form.append('note', '程序生成的合成演示数据，不是任何论文的实验结果。')
      setBusy(true)
      const data = await request<Dataset>(workspacePath(workspaceId, '/datasets/upload'), { method: 'POST', body: form })
      if (!alive.current) return
      setDatasets(previous => [data, ...previous]); setDatasetId(data.id); setJob(null); setMethod(series ? 'lstm' : 'paired_t'); setPreview(true)
      setNotice('已载入合成演示数据。它用于体验功能，不代表论文实验或模型评测成绩。')
    } catch (err) { if (alive.current) setError(errorText(err)) } finally { if (alive.current) setBusy(false) }
  }
  async function analyze() {
    if (!selected || !columns.length) return
    setBusy(true); setError(''); setNotice('')
    try {
      const value = await post<AnalysisJob>(workspacePath(workspaceId, '/analyses'), { dataset_id: selected.id, method, columns, alpha, weight_column: method === 'chi_square' && weight ? weight : null,
        continuity_correction: correction, time_column: method === 'lstm' && time ? time : null, window: windowSize, epochs, horizon, train_fraction: trainFraction, hidden_size: 24, seed: 42 })
      if (!alive.current) return
      setJob(value); setJobs(previous => [value, ...previous]); setPreview(false)
    } catch (err) { if (alive.current) setError(errorText(err)) } finally { if (alive.current) setBusy(false) }
  }
  async function cancel() {
    if (!job) return
    try { setJob(await post<AnalysisJob>(workspacePath(workspaceId, `/analyses/${job.id}/cancel`), {})) } catch (err) { setError(errorText(err)) }
  }
  function chooseJob(value: AnalysisJob) { setJob(value); setDatasetId(value.dataset_id); setPreview(false) }
  return <div className="page-body lab-page"><section className="feature-hero"><div className="hero-orbits" aria-hidden="true"><i/><i/><i/><span/><b/></div><div className="hero-copy"><span className="eyebrow">RESEARCH · EXPERIMENT · EVIDENCE</span><h1>让数据，接上你的研究。</h1><p>从论文表格到原始观测，保留每次计算的输入、参数和结果。</p><div className="hero-pills"><span><Icon name="chart" size={13}/>统计检验</span><span><Icon name="wave" size={13}/>序列预测</span><span><Icon name="link" size={13}/>可追溯记录</span></div></div><button className="button hero-action" onClick={() => { setError(''); setUploadOpen(true) }}><Icon name="upload" size={17}/>上传研究数据</button></section>
    {error && <div className="error-box lab-message" role="alert">{error}</div>}{notice && <div className="lab-notice" role="status"><Icon name="check" size={16}/>{notice}</div>}
    <div className="lab-layout"><aside className="lab-sidebar"><div className="list-heading"><h3>研究数据 <span>{datasets.length}</span></h3><button className="icon-button" aria-label="添加数据集" onClick={() => setUploadOpen(true)}><Icon name="plus" size={16}/></button></div><div className="dataset-list">{datasets.map(item => <button key={item.id} className={`dataset-item ${item.id === datasetId ? 'active' : ''}`} onClick={() => { setDatasetId(item.id); setJob(null); setPreview(true); setError('') }}><span><Icon name="database" size={18}/></span><div><strong>{item.title}</strong><small>{item.row_count.toLocaleString()} 行 · {item.columns.length} 列{item.source_id ? ' · 已关联文献' : ''}</small></div></button>)}</div>{loading && <p className="small muted">正在读取数据…</p>}{!datasets.length && !loading && <p className="lab-empty-note">导入 CSV、TSV 或 XLSX，开始一次可追溯的分析。</p>}<div className="sample-datasets"><span className="eyebrow">TRY A SMALL EXPERIMENT</span><button disabled={busy} onClick={() => void loadExample()}><Icon name="compare" size={16}/><span>实验对照数据<small>合成示例 · 配对检验</small></span><Icon name="arrow" size={14}/></button><button disabled={busy} onClick={() => void loadExample(true)}><Icon name="wave" size={16}/><span>时间序列数据<small>合成示例 · LSTM</small></span><Icon name="arrow" size={14}/></button></div>
      <div className="lab-history"><h3>分析记录 <span>{jobs.length}</span></h3>{jobs.slice(0, 15).map(item => <button key={item.id} className={job?.id === item.id ? 'active' : ''} onClick={() => chooseJob(item)}><span className={`tiny-dot ${item.status === 'completed' ? 'done' : activeJob(item) ? 'live' : ''}`}/><div><strong>{METHODS.find(method => method.id === item.method)?.title}</strong><small>{datasets.find(data => data.id === item.dataset_id)?.title} · {JOB_STATUS[item.status]}</small></div></button>)}</div></aside>
      <div className="lab-content">{!selected ? <div className="lab-welcome"><div className="science-emblem"><Icon name="chart" size={36}/><i/></div><span className="eyebrow">FROM NUMBERS TO FINDINGS</span><h2>先看清数据，再解释差异。</h2><p>支持实验结果对照、分类频数关联与时间序列预测。论文中的表格可先整理为 CSV / XLSX，再关联文献与页码。</p><button className="button primary" onClick={() => setUploadOpen(true)}>添加第一份数据<Icon name="plus" size={16}/></button><div className="workflow-steps"><span>01 导入与核对</span><Icon name="arrow" size={13}/><span>02 计算与比较</span><Icon name="arrow" size={13}/><span>03 加入研究资料</span></div></div> : <>
        <div className="dataset-heading"><div><span className="eyebrow">YOUR DATA, YOUR EXPERIMENT</span><h2>{selected.title}</h2><p>{selected.row_count.toLocaleString()} 个观测行 · {numeric.length} 个数值列 · {selected.columns.reduce((sum, column) => sum + column.missing, 0)} 个缺失单元格</p></div><button className="button compact" onClick={() => setPreview(!preview)}><Icon name="database" size={14}/>{preview ? '收起数据' : '数据预览'}</button></div>
        {selected.source_id && <div className="data-provenance"><Icon name="link" size={14}/>关联文献：{sources.find(source => source.id === selected.source_id)?.title ?? '原文'}{selected.page ? ` · 第 ${selected.page} 页` : ''}<span>由上传者关联，未自动核验数据提取</span></div>}
        {preview && <div className="dataset-preview"><div className="column-profile">{selected.columns.map(column => <span key={column.name}><strong>{column.name}</strong><small>{column.kind === 'numeric' ? '数值' : '分类'} · 缺失 {column.missing}</small></span>)}</div><div className="table-scroll"><table><thead><tr>{selected.columns.map(column => <th key={column.name}>{column.name}</th>)}</tr></thead><tbody>{selected.preview.map((row, i) => <tr key={i}>{row.map((value, j) => <td key={j}>{value == null ? <span className="muted">缺失</span> : typeof value === 'number' ? formatNumber(value) : value}</td>)}</tr>)}</tbody></table></div><p>预览前 {selected.preview.length} 行。{selected.warnings.join(' ')} {selected.note}</p><a className="text-button" href={workspacePath(workspaceId, `/datasets/${selected.id}/original`)} download><Icon name="download" size={13}/>下载原始数据</a></div>}
        <section className="analysis-configuration"><div className="list-heading"><h3>选择分析方法</h3><span>计算在本地后端执行</span></div><div className="method-grid">{METHODS.map(item => <button key={item.id} className={method === item.id ? 'active' : ''} disabled={busy || activeJob(job) || (item.id === 'lstm' && capabilities?.lstm === false)} onClick={() => setMethod(item.id)}><Icon name={item.icon} size={19}/><strong>{item.title}</strong><small>{item.subtitle}</small>{method === item.id && <span><Icon name="check" size={11}/></span>}</button>)}</div>
          <div className="analysis-fields">{multi ? <fieldset className="column-checkboxes"><legend>{method === 'hotelling_t2' ? '均值向量与全零基准比较 · 至少两列' : '选择数值列 · 最多 20 列'}</legend>{numeric.map(name => <label key={name}><input type="checkbox" checked={columns.includes(name)} disabled={activeJob(job)} onChange={e => setColumns(previous => e.target.checked ? [...previous, name].slice(0, 20) : previous.filter(value => value !== name))}/>{name}</label>)}</fieldset> : <>{(method === 'lstm' ? [0] : [0, 1]).map((_, i) => <label key={i}>{method === 'lstm' ? '预测目标列' : method === 'linear_regression' ? i === 0 ? '自变量 X' : '响应变量 Y' : method === 'chi_square' ? i === 0 ? '分类变量 A' : '分类变量 B' : `数值列 ${i === 0 ? 'A' : 'B'}`}<select value={columns[i] ?? ''} disabled={activeJob(job)} onChange={e => setColumns(previous => { const next = [...previous]; next[i] = e.target.value; return next })}><option value="" disabled>请选择一列</option>{options.map(name => <option key={name} value={name}>{name}</option>)}</select></label>)}</>}
            {method === 'lstm' ? <><label>时间列<select value={time} disabled={activeJob(job)} onChange={e => setTime(e.target.value)}><option value="">按原始行顺序（等间隔）</option>{selected.columns.filter(column => column.name !== columns[0]).map(column => <option key={column.name} value={column.name}>{column.name}</option>)}</select></label><label>回看窗口<input type="number" min={3} max={128} value={windowSize} onChange={e => setWindowSize(Number(e.target.value))}/></label><label>训练轮数<input type="number" min={5} max={150} value={epochs} onChange={e => setEpochs(Number(e.target.value))}/></label><label>未来预测步数<input type="number" min={1} max={100} value={horizon} onChange={e => setHorizon(Number(e.target.value))}/></label><label>训练数据占比<select value={trainFraction} onChange={e => setTrainFraction(Number(e.target.value))}><option value={.8}>前 80% 训练 / 后 20% 测试</option><option value={.7}>前 70% 训练 / 后 30% 测试</option></select></label></> : method !== 'descriptive' && method !== 'correlation' ? <label>显著性水平 α<select value={alpha} disabled={activeJob(job)} onChange={e => setAlpha(Number(e.target.value))}><option value={.05}>0.05</option><option value={.01}>0.01</option><option value={.1}>0.10</option></select></label> : null}
            {method === 'chi_square' && <><label>频数权重（汇总频数表可选）<select value={weight} onChange={e => setWeight(e.target.value)}><option value="">每行代表一个观测</option>{numeric.filter(name => !columns.includes(name)).map(name => <option key={name} value={name}>{name}</option>)}</select></label><label className="lab-checkbox"><input type="checkbox" checked={correction} onChange={e => setCorrection(e.target.checked)}/>2×2 表使用 Yates 连续性校正</label></>}
          </div><div className="analysis-submit"><p>{method === 'paired_t' ? '每一行必须是一对真实对应观测。' : method === 'welch_t' ? '两列须代表独立样本；分别使用非缺失值。' : method === 'lstm' ? '48–5000 个时间点 · 固定随机种子 42 · 隐藏单元 24 · 测试集不参与标准化或训练' : method === 'hotelling_t2' ? '用于检验所选变量的均值向量是否为零；不同基准需先在数据中作差。' : '保留缺失处理、参数和数值结果，便于核查。'}</p>{activeJob(job) ? <button className="button" onClick={() => void cancel()}><Icon name="stop" size={14}/>停止分析</button> : <button className="button primary" disabled={busy || !columns.length || capabilities?.statistics === false} onClick={() => void analyze()}><Icon name="research" size={16}/>{busy ? '提交中…' : '运行分析'}</button>}</div>
        </section>
        {job && activeJob(job) && <div className="analysis-running" role="status"><div className="running-orbit"><Icon name="wave" size={26}/></div><h3>{job.method === 'lstm' ? '正在训练与评估 LSTM' : '正在执行数值分析'}</h3><p>{job.progress.epoch ? `第 ${job.progress.epoch} / ${job.progress.epochs} 轮 · 训练 MSE ${formatNumber(job.progress.loss)}` : '正在准备数据并启动独立计算进程…'}</p>{job.progress.epoch && <progress value={job.progress.epoch} max={job.progress.epochs}/>}<span>结果和运行记录将保存在当前课题。</span></div>}
        {job && !activeJob(job) && job.status !== 'completed' && <div className="warning-box">{JOB_STATUS[job.status]}：{job.error ?? '没有生成分析结果，请调整数据或参数后重试。'}</div>}
        {job?.result && <AnalysisResults job={job} dataset={datasets.find(data => data.id === job.dataset_id)} workspaceId={workspaceId} onError={setError} onPublished={() => { void onSourcesChanged(); void request<AnalysisJob>(workspacePath(workspaceId, `/analyses/${job.id}`)).then(setJob); setNotice('分析记录已加入课题资料，可以在研究工作台引用和追问。') }}/>} 
      </>}</div></div>
    {uploadOpen && <div className="modal-backdrop"><section className="modal data-upload-modal" role="dialog" aria-modal="true" aria-labelledby="data-upload-title"><header className="modal-header"><div><span className="eyebrow">KEEP THE ORIGINAL, TRACK THE RESULT</span><h2 id="data-upload-title">导入研究数据</h2></div><button className="icon-button" disabled={busy} aria-label="关闭数据导入" onClick={() => setUploadOpen(false)}><Icon name="close"/></button></header><div className="segmented"><button className={!paste ? 'active' : ''} onClick={() => setPaste(false)}>上传数据文件</button><button className={paste ? 'active' : ''} onClick={() => setPaste(true)}>粘贴 CSV 表格</button></div><label>数据名称（选填）<input value={uploadTitle} onChange={e => setUploadTitle(e.target.value)} maxLength={200} placeholder="例如：不同随机种子下的实验结果"/></label>{paste ? <textarea className="csv-paste" aria-label="CSV 数据" value={csv} onChange={e => setCsv(e.target.value)} placeholder={'seed,baseline_score,new_score\n1,81.2,82.1\n2,80.7,81.8'} maxLength={1000000}/> : <p className="data-upload-help">支持 CSV、TSV、XLSX，首行为唯一列名。每份最多 20 MB、50,000 行、100 列；不执行文件中的代码或公式。</p>}<div className="upload-provenance"><label>关联文献（可选）<select value={uploadSource} onChange={e => setUploadSource(e.target.value)}><option value="">独立上传的原始数据</option>{sources.filter(source => source.kind === 'paper' || source.kind === 'document').map(source => <option key={source.id} value={source.id}>{source.title}</option>)}</select></label>{uploadSource && <label>原文页码（可选）<input type="number" min={1} max={10000} value={uploadPage} onChange={e => setUploadPage(e.target.value)}/></label>}<label>数据说明（选填）<input value={uploadNote} onChange={e => setUploadNote(e.target.value)} maxLength={1000} placeholder="样本单位、实验来源或表格编号"/></label></div><p className="muted small">论文中的表格请先核对并整理为数据文件。关联文献仅记录来源，不表示已自动验证表格提取。</p>{error && <div className="error-box">{error}</div>}<input type="file" ref={fileInput} accept=".csv,.tsv,.xlsx" hidden onChange={e => { if (e.target.files?.[0]) void upload(e.target.files[0]) }}/><footer className="modal-footer"><span className="muted small">原始文件与数据版本分别保存</span><button className="button primary" disabled={busy || (paste && !csv.trim())} onClick={() => paste ? void upload(new File([csv], 'pasted-data.csv', { type: 'text/csv' })) : fileInput.current?.click()}><Icon name="upload" size={16}/>{busy ? '正在读取…' : paste ? '导入表格' : '选择文件并导入'}</button></footer></section></div>}
  </div>
}
