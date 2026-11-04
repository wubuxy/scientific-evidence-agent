import Icon from './Icons'
import type { Evidence, Report, Source } from './types'

export function CitationChips({ ids, evidence, onSelect }: { ids: string[]; evidence: Evidence[]; onSelect: (id: string) => void }) {
  return <span className="citations">{ids.map((id, index) => {
    const item = evidence.find(e => e.id === id)
    const page = item?.locations.find(loc => loc.page)?.page
    return <button className="citation" key={id} onClick={() => onSelect(id)} title={item ? `${item.title} · ${page ? `第 ${page} 页 · ` : ''}行 ${item.start_line}–${item.end_line}` : '加载引用原文'}><Icon name="link" size={12}/>{page ? `p.${page}` : `证据 ${index + 1}`}</button>
  })}</span>
}

export default function ReportView({ report, evidence, sources, onSelect }: { report: Report; evidence: Evidence[]; sources: Source[]; onSelect: (id: string) => void }) {
  const columns = [...new Set(report.comparison_rows.flatMap(row => Object.keys(row.attributes ?? {})))]
  return <article className="report">
    <div className="report-eyebrow"><span className="pill amber"><Icon name="evidence" size={13}/>待人工核验</span><span>RESEARCH BRIEF</span></div>
    <h2>{report.title}</h2>
    <p className="report-note">以下为模型生成的研究草稿。点击引用回到原文，核对结论及实验条件后再使用。</p>
    <div className="section-label"><span>01</span><h3>核心发现</h3></div>
    {report.claims.length === 0 && <p className="muted">本次没有形成可引用的结论。</p>}
    {report.claims.map((claim, index) => <div className="claim" key={index}>
      <span className="claim-number">{String(index + 1).padStart(2, '0')}</span>
      <div><p>{claim.text}</p><CitationChips ids={claim.evidence_ids} evidence={evidence} onSelect={onSelect}/>{claim.qualification && <p className="qualification">适用范围 · {claim.qualification}</p>}</div>
    </div>)}
    {report.comparison_rows.length > 0 && <>
      <div className="section-label"><span>02</span><h3>跨论文对照</h3></div>
      <p className="muted small">缺少证据的字段应标记“本次未核实”；不同任务的数值不宜直接排名。</p>
      {columns.length > 0 ? <div className="table-scroll"><table className="comparison-table"><thead><tr><th>论文 / 方法</th>{columns.map(column => <th key={column}>{column.replace(/_/g, ' ')}</th>)}<th>证据</th></tr></thead><tbody>
        {report.comparison_rows.map((row, index) => <tr key={index}><td><strong>{row.method}</strong><span className="cell-note">{sources.find(s => s.id === row.source_id)?.title ?? '论文原文'}</span></td>{columns.map(column => <td key={column}>{row.attributes?.[column] ?? '本次未核实'}</td>)}<td><CitationChips ids={row.evidence_ids} evidence={evidence} onSelect={onSelect}/></td></tr>)}
      </tbody></table></div> : <div className="table-scroll"><table className="comparison-table"><thead><tr><th>论文 / 方法</th><th>数据与模型</th><th>指标与结果</th><th>条件与可比性</th><th>证据</th></tr></thead><tbody>
        {report.comparison_rows.map((row, index) => <tr key={index}>
          <td><strong>{row.method}</strong><span className="cell-note">{sources.find(s => s.id === row.source_id)?.title ?? '论文原文'}</span></td>
          <td>{row.dataset}<span className="cell-note">模型：{row.model}</span></td>
          <td>{row.metric}<span className="cell-note">{row.result}</span></td>
          <td><span className={`pill ${row.comparability === 'comparable' ? 'green' : 'neutral'}`}>{({ comparable: '条件可比', qualified: '有条件可比', unknown: '待判断' } as Record<string, string>)[row.comparability] ?? '待判断'}</span><span className="cell-note">{row.conditions}</span></td>
          <td><CitationChips ids={row.evidence_ids} evidence={evidence} onSelect={onSelect}/></td>
        </tr>)}
      </tbody></table></div>}
    </>}
    <div className="section-label"><span>{report.comparison_rows.length ? '03' : '02'}</span><h3>局限与待核验项</h3></div>
    <div className="limitations"><Icon name="warning" size={20}/><ul>{report.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul></div>
    <div className="report-footer"><Icon name="book" size={16}/>让每一个结论，都能回到原文。</div>
  </article>
}
