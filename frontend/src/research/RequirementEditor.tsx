import type { Source } from './types'
import type { RequirementDraft } from './requirements'
import './requirements.css'

export default function RequirementEditor({ sources, draft, onChange, disabled, error }: {
  sources: Source[]; draft: RequirementDraft; onChange: (value: RequirementDraft) => void
  disabled: boolean; error: string
}) {
  const count = draft.sourceIds.length * draft.fields.length
  return <details className="requirement-editor">
    <summary>运行前确认要求 <span>{draft.explicit ? `${count} 项必答要求` : '由模型提取，可改为自行指定'}</span></summary>
    <fieldset disabled={disabled}>
      <label className="requirement-toggle"><input type="checkbox" checked={draft.explicit} onChange={event => onChange({
        ...draft, explicit: event.target.checked,
        fields: draft.fields.length ? draft.fields : [{ name: '', criterion: '' }],
      })}/>自行指定必答论文与字段</label>
      {draft.explicit ? <>
        <p>每篇选中的论文都需回答以下字段。开始研究时固定要求；找不到依据时保留未核实项。</p>
        <div className="requirement-source-heading"><strong>必答论文</strong><button type="button" className="text-button" onClick={() => onChange({ ...draft,
          sourceIds: draft.sourceIds.length === sources.length ? [] : sources.map(source => source.id),
        })}>{draft.sourceIds.length === sources.length && sources.length ? '取消全选' : '选择全部'}</button></div>
        <div className="requirement-sources">{sources.map(source => <label key={source.id}>
          <input type="checkbox" checked={draft.sourceIds.includes(source.id)} onChange={event => onChange({ ...draft,
            sourceIds: event.target.checked ? [...draft.sourceIds, source.id] : draft.sourceIds.filter(id => id !== source.id),
          })}/><span>{source.title}</span>
        </label>)}</div>
        {!sources.length && <p>请先导入论文；联网发现的新资料可在后续研究中加入必答范围。</p>}
        <div className="requirement-fields">{draft.fields.map((field, index) => <div className="requirement-row" key={index}>
          <label>字段 {index + 1}<input aria-label={`字段 ${index + 1} 名称`} value={field.name} maxLength={100} placeholder="例如：训练数据"
            onChange={event => onChange({ ...draft, fields: draft.fields.map((item, i) => i === index ? { ...item, name: event.target.value } : item) })}/></label>
          <label>完成标准（可选）<textarea aria-label={`字段 ${index + 1} 完成标准`} value={field.criterion} maxLength={500} rows={2}
            placeholder="例如：区分训练集与评测集，说明是否使用额外数据"
            onChange={event => onChange({ ...draft, fields: draft.fields.map((item, i) => i === index ? { ...item, criterion: event.target.value } : item) })}/></label>
          <button type="button" className="text-button" aria-label={`移除字段 ${index + 1}`} onClick={() => onChange({ ...draft,
            fields: draft.fields.filter((_, i) => i !== index),
          })}>移除</button>
        </div>)}</div>
        <button type="button" className="button compact" disabled={draft.fields.length >= 96} onClick={() => onChange({ ...draft,
          fields: [...draft.fields, { name: '', criterion: '' }],
        })}>添加字段</button>
        <p className="requirement-note">已选 {draft.sourceIds.length} 篇 × {draft.fields.length} 个字段 = {count} 项（最多 96 项）。检索仍可使用课题中的其他资料。</p>
        {error && <p className="requirement-error" role="status">{error}</p>}
      </> : <p>模型会从研究问题中提取论文和字段，可能遗漏要求。重要的比较任务建议先自行指定。</p>}
    </fieldset>
  </details>
}
