import test from 'node:test'
import assert from 'node:assert/strict'
import { emptyRequirements, prepareRequirements } from '../src/research/requirements.ts'

const sources = [{ id: 'a' }, { id: 'b' }]
const draft = { explicit: true, sourceIds: ['b', 'a'], fields: [{ name: ' 数据 ', criterion: ' 区分训练与评测 ' }] }

test('explicit requirements preserve the selected papers and user criterion', () => {
  assert.deepEqual(prepareRequirements(draft, sources, 'bounded'), { error: '', fields: [
    { source_id: 'b', name: '数据', criterion: '区分训练与评测' },
    { source_id: 'a', name: '数据', criterion: '区分训练与评测' },
  ] })
})
test('workspace changes and disabled tracking cannot silently discard explicit requirements', () => {
  for (const [available, mode] of [[[{ id: 'other' }], 'bounded'], [sources, 'off']]) {
    const result = prepareRequirements(draft, available, mode)
    assert.ok(result.error)
    assert.deepEqual(result.fields, [])
  }
})
test('empty or duplicate names are rejected before any run is created', () => {
  for (const fields of [[], [{ name: ' ', criterion: '' }], [{ name: '数据', criterion: '' }, { name: ' 数据 ', criterion: '' }]]) {
    assert.ok(prepareRequirements({ ...draft, fields }, sources, 'observe').error)
  }
})
test('96 paper-field pairs are allowed but 98 are rejected', () => {
  const fields = Array.from({ length: 48 }, (_, i) => ({ name: `Field ${i}`, criterion: '' }))
  assert.equal(prepareRequirements({ ...draft, fields }, sources, 'bounded').fields.length, 96)
  assert.ok(prepareRequirements({ ...draft, fields: [...fields, { name: 'Extra', criterion: '' }] }, sources, 'bounded').error)
})
test('automatic extraction is explicit and a new draft has no previous workspace selection', () => {
  assert.deepEqual(prepareRequirements({ ...draft, explicit: false }, sources, 'off'), { fields: [], error: '' })
  const first = emptyRequirements()
  first.sourceIds.push('a')
  assert.deepEqual(emptyRequirements(), { explicit: false, sourceIds: [], fields: [] })
})
