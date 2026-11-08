import type { FieldState, Source } from './types'
import './field-progress.css'

const stopLabels: Record<string, string> = {
  coverage_complete_review_pending: '字段已填写，语义仍待核验', unresolved_fields: '仍有未解决字段',
  repair_limit: '已达到补读次数上限', no_progress: '补读后仍缺少依据', max_steps: '已达到研究轮数上限',
  final_reserve: '为报告预留预算后收尾', time_reserve: '接近时间上限，交付已有结果',
  no_requirements: '未建立字段要求',
  repair_failed: '补救未完成，已保留此前结果', token_budget_exceeded: '核验返回用量超限，任务已停止',
  cancelled_or_interrupted: '核验已取消或中断',
}
const coverageLabels: Record<string, string> = { missing: '尚未填写', abstained: '本次未核实', unbound: '缺少对应证据', present: '已有候选答案' }
const supportLabels: Record<string, string> = { supported: '支持', contradicted: '存在反证', insufficient: '证据不足' }
const adequacyLabels: Record<string, string> = { complete: '回答完整', partial: '回答不完整', unknown: '完整性待判断' }

export default function FieldProgress({ state, sources, onSelect }: {
  state?: FieldState; sources: Source[]; onSelect: (id: string) => void
}) {
  if (!state?.version) return null
  return <section className="field-progress" aria-label="字段研究进度">
    <div className="field-progress-heading"><div><span className="field-eyebrow">RESEARCH CHECKLIST</span><h2>每个问题，都有去向</h2></div>
      <span className="field-count"><strong>{state.present}</strong> / {state.total} 已填写</span></div>
    <div className="field-meter" role="progressbar" aria-label="字段填写进度" aria-valuenow={state.present} aria-valuemin={0} aria-valuemax={state.total || 1}>
      <span style={{ width: `${state.total ? state.present / state.total * 100 : 0}%` }}/></div>
    <p className="field-caption">{state.stop_reason ? stopLabels[state.stop_reason] ?? state.stop_reason : '正在收集依据；读过论文不代表字段已经解决。'}
      {state.repair_rounds > 0 && ` · 已补读 ${state.repair_rounds} 轮`}</p>
    <details className="field-details" open={state.total <= 8}>
      <summary>查看 {state.total} 个字段 · {state.unresolved} 个仍待补齐</summary>
      <div className="field-grid">{state.fields.map(field => <article className={`field-card ${field.coverage === 'present' ? 'has-answer' : 'has-gap'}`} key={field.id}>
        <div className="field-card-top"><span>{sources.find(s => s.id === field.source_id)?.title ?? '研究资料'}</span>
          <span className="field-status">{coverageLabels[field.coverage] ?? field.coverage}</span></div>
        <h3>{field.name}</h3><p className="field-answer">{field.answer || (field.activity === 'source_read' ? '已阅读这篇论文，尚未登记该字段答案。' : '等待定位相关证据。')}</p>
        {field.criterion && <details className="field-review"><summary>完成标准</summary><p>{field.criterion}</p></details>}
        <div className="field-evidence">{field.evidence_ids.map((id, i) => <button key={id} onClick={() => onSelect(id)} aria-label={`${field.name}的证据 ${i+1}`}>原文 {i+1} ↗</button>)}
          {field.revision > 0 && <span>版本 {field.revision}</span>}</div>
        {field.review && <div className="field-review"><strong>模型核验意见 · 待人工确认</strong>
          <p>{supportLabels[field.review.support]} · {adequacyLabels[field.review.adequacy]}</p><p>{field.review.rationale}</p></div>}
      </article>)}</div>
    </details>
    <p className="field-footnote">填写进度不代表正确率。{state.requirement_origin === 'model_plan' ? '字段由模型从问题中提取，可能遗漏要求。' : '字段按请求预先指定。'}
      {state.reviewed > 0 ? ` 已记录 ${state.reviewed} 项模型意见。` : ' 尚无可用的语义核验意见。'}</p>
  </section>
}
