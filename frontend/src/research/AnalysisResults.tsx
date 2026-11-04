import { lazy, Suspense } from 'react'
import { download, errorText, post, workspacePath } from './api'
import Icon from './Icons'
import { formatNumber, type AnalysisJob, type Dataset } from './labTypes'

const AnalysisChart = lazy(() => import('./AnalysisChart'))

export default function AnalysisResults({ job, dataset, workspaceId, onPublished, onError }: { job: AnalysisJob; dataset?: Dataset; workspaceId: string; onPublished: () => void; onError: (error: string) => void }) {
  const result = job.result
  if (!result) return null
  async function exportResult(format: 'markdown' | 'json' = 'markdown') {
    try {
      const response = await fetch(workspacePath(workspaceId, `/analyses/${job.id}/export?format=${format}`))
      if (!response.ok) throw new Error('分析结果导出失败')
      download(await response.text(), `analysis-${job.id}.${format === 'json' ? 'json' : 'md'}`, format === 'json' ? 'application/json' : 'text/markdown;charset=utf-8')
    } catch (err) { onError(errorText(err)) }
  }
  async function publish() {
    try { await post(workspacePath(workspaceId, `/analyses/${job.id}/publish`), {}); onPublished() } catch (err) { onError(errorText(err)) }
  }
  const values = result.metrics ? [['测试 RMSE', result.metrics.rmse], ['基线 RMSE', result.baseline_metrics?.rmse], ['测试 MAE', result.metrics.mae], ['测试点', result.test_points]] : result.pearson ? [['Pearson r', result.pearson.coefficient], ['Pearson p', result.pearson.p_value], ['Spearman ρ', result.spearman?.coefficient], ['完整样本', result.sample_size]] : result.slope != null ? [['回归斜率', result.slope], ['截距', result.intercept], ['样本内 R²', result.r_squared], ['斜率 p 值', result.p_value]] : result.p_value != null ? [['检验统计量', result.statistic], ['p 值', result.p_value], ['效应量', result.effect_size?.value], ['有效样本', result.sample_size ?? dataset?.row_count]] : []
  return <section className="lab-results"><div className="result-heading"><div><span className="eyebrow">COMPUTED FROM YOUR DATA</span><h2>{result.title}</h2></div><span className="pill green"><Icon name="check" size={12}/>计算完成</span></div>
    {values.length > 0 && <div className="metric-grid">{values.map(([label, value]) => <div key={String(label)}><span>{label}</span><strong>{formatNumber(value as number | undefined)}</strong></div>)}</div>}
    {result.metrics && <div className={`analysis-verdict ${result.beats_baseline ? 'positive' : ''}`}><Icon name={result.beats_baseline ? 'check' : 'warning'} size={18}/><p>{result.beats_baseline ? '本次留出测试集上，LSTM 的 RMSE 低于简单持久性基线。' : '本次留出测试集上，LSTM 没有超过简单持久性基线。'}<small>按时间顺序划分 · {result.train_points} 个训练点 / {result.test_points} 个测试点 · 滚动单步评估</small></p></div>}
    {result.interpretation && <p className="analysis-interpretation">{result.interpretation}</p>}
    {result.confidence_interval && <p className="confidence-line">{formatNumber(result.confidence_interval.level * 100)}% 置信区间：<strong>[{formatNumber(result.confidence_interval.low)}, {formatNumber(result.confidence_interval.high)}]</strong>{job.method === 'linear_regression' ? '（斜率）' : '（A − B 的均值差）'}</p>}
    {result.effect_size && <p className="confidence-line">效应量：{result.effect_size.name} = <strong>{formatNumber(result.effect_size.value)}</strong></p>}
    {result.chart && <Suspense fallback={<div className="loading-page"><span className="spinner"/>正在加载图表…</div>}><AnalysisChart result={result}/></Suspense>}
    {result.tables.map((table, i) => <div className="analysis-table" key={i}><h3>{table.title}</h3><div className="table-scroll"><table><thead><tr>{table.columns.map((name, j) => <th key={j}>{name}</th>)}</tr></thead><tbody>{table.rows.map((row, j) => <tr key={j}>{row.map((value, k) => <td key={k}>{typeof value === 'number' ? formatNumber(value) : value ?? '—'}</td>)}</tr>)}</tbody></table></div></div>)}
    {result.forecast && <div className="forecast-values"><h3>未来 {result.forecast.length} 步 · 递归预测</h3><div>{result.forecast.map((value, i) => <span key={i}><small>+{i + 1}</small>{formatNumber(value)}</span>)}</div><p className="muted small">未提供预测区间；这些未来值不属于测试集评估。</p></div>}
    {(result.warnings.length > 0 || result.assumptions.length > 0) && <div className="lab-caveats"><h3><Icon name="evidence" size={16}/>解释结果前，请核对</h3><ul>{[...result.warnings, ...result.assumptions].map((warning, i) => <li key={i}>{warning}</li>)}</ul></div>}
    <details className="reproducibility"><summary><Icon name="link" size={14}/>数据与运行版本</summary><p>数据：{dataset?.title ?? job.dataset_id} · {result.excluded_rows ?? 0} 行因缺失排除（适用时）</p><code>SHA-256 · {job.dataset_hash}</code><pre>{JSON.stringify({ parameters: job.parameters, software: result.software }, null, 2)}</pre></details>
    <footer className="analysis-result-actions"><button className="button compact" onClick={() => void exportResult()}><Icon name="download" size={14}/>导出报告</button><button className="button compact" onClick={() => void exportResult('json')}>JSON 记录</button>{result.model_artifact && <a className="button compact" href={workspacePath(workspaceId, `/analyses/${job.id}/artifacts/model`)} download>模型权重</a>}<button className="button primary compact" disabled={!!job.published_source_id} onClick={() => void publish()}><Icon name="plus" size={14}/>{job.published_source_id ? '已加入研究资料' : '加入研究资料'}</button></footer>
  </section>
}
