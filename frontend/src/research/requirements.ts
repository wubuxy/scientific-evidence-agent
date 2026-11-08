import type { FieldRequirement, Source } from './types'

export interface RequirementDraft {
  explicit: boolean
  sourceIds: string[]
  fields: { name: string; criterion: string }[]
}

export const emptyRequirements = (): RequirementDraft => ({ explicit: false, sourceIds: [], fields: [] })

export function prepareRequirements(draft: RequirementDraft, sources: Pick<Source, 'id'>[], fieldMode: string):
  { fields: FieldRequirement[]; error: string } {
  const invalid = (error: string) => ({ fields: [], error })
  if (!draft.explicit) return { fields: [], error: '' }
  if (fieldMode === 'off') return invalid('已指定必答要求，请在研究设置中启用字段检查。')
  if (!draft.sourceIds.length) return invalid('请至少选择一篇必答论文。')
  const allowed = new Set(sources.map(source => source.id))
  if (draft.sourceIds.some(id => !allowed.has(id))) return invalid('已选资料发生变化，请重新选择必答论文。')
  if (!draft.fields.length) return invalid('请至少添加一个必答字段。')
  const fields = draft.fields.map(field => ({ name: field.name.trim(), criterion: field.criterion.trim() }))
  if (fields.some(field => !field.name)) return invalid('请填写每个字段的名称，或移除空字段。')
  if (new Set(fields.map(field => field.name)).size !== fields.length) return invalid('字段名称重复，请合并或重命名。')
  if (fields.some(field => [...field.name].length > 100 || [...field.criterion].length > 500)) return invalid('字段名称最多 100 字，完成标准最多 500 字。')
  const ids = [...new Set(draft.sourceIds)]
  if (ids.length * fields.length > 96) return invalid('论文数 × 字段数最多 96 项，请缩小必答范围。')
  return { error: '', fields: ids.flatMap(source_id => fields.map(field => ({ source_id, name: field.name,
    criterion: field.criterion || '完整回答该字段，保留对象、实验阶段和适用条件' }))) }
}
