import { useState } from 'react'
import { download, errorText, post, workspacePath } from './api'
import Icon from './Icons'
import type { QueryResult, ToolResult } from './types'

export default function DatabaseView({ workspaceId }: { workspaceId: string }) {
  const [sql, setSql] = useState('SELECT title, kind, version FROM sources LIMIT 20')
  const [result, setResult] = useState<QueryResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function execute() {
    setBusy(true); setError(''); setResult(null)
    try {
      const response = await post<ToolResult<QueryResult>>(workspacePath(workspaceId, '/database/query'), { sql, limit: 100 })
      if (response.status === 'failed') throw new Error(response.error ?? '查询失败')
      setResult(response.data)
    } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  function exportCsv() {
    if (!result) return
    // Neutralize spreadsheet formulas in untrusted paper titles and quotes.
    const cell = (value: unknown) => { let text = String(value ?? ''); if (/^[=+@\-\t\r]/.test(text)) text = `'${text}`; return `"${text.replace(/"/g, '""')}"` }
    download('\uFEFF' + [result.columns, ...result.rows].map(row => row.map(cell).join(',')).join('\r\n'), 'research-query.csv', 'text/csv;charset=utf-8')
  }
  return <div className="page-body"><div className="page-heading"><div><span className="eyebrow">QUERY YOUR RESEARCH</span><h1>资料数据查询</h1><p>查看当前课题的资料与引用记录，支持只读 SQL。</p></div><span className="pill green">当前课题隔离</span></div>
    <div className="schema-grid"><div><strong>sources</strong><p>id · title · kind · uri · version · source_hash</p></div><div><strong>evidence</strong><p>id · source_id · quote · start_line · end_line</p></div></div>
    <div className="query-card"><label htmlFor="sql-editor">SQL 查询</label><textarea id="sql-editor" className="sql-editor" value={sql} onChange={e => setSql(e.target.value)} spellCheck={false} maxLength={8000}/><footer><button className="text-button" onClick={() => setSql('SELECT s.title, COUNT(e.id) AS evidence_count FROM sources s LEFT JOIN evidence e ON s.id = e.source_id GROUP BY s.id, s.title ORDER BY evidence_count DESC')}>使用引用统计示例</button><button className="button primary" disabled={busy || !sql.trim()} onClick={() => void execute()}><Icon name="database" size={16}/>{busy ? '查询中…' : '执行查询'}</button></footer></div>
    {error && <div className="error-box" role="alert">{error}</div>}
    {result && <div className="query-results"><div className="list-heading"><h3>查询结果 <span>{result.rows.length} 行{result.truncated ? '（已截断）' : ''}</span></h3><button className="button compact" onClick={exportCsv}><Icon name="download" size={15}/>导出 CSV</button></div><div className="table-scroll"><table><thead><tr>{result.columns.map(column => <th key={column}>{column}</th>)}</tr></thead><tbody>{result.rows.map((row, i) => <tr key={i}>{row.map((value, j) => <td key={j}>{String(value ?? '—')}</td>)}</tr>)}</tbody></table>{result.rows.length === 0 && <p className="empty-text">查询没有返回记录。</p>}</div></div>}
  </div>
}
