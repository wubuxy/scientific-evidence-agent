import Icon from './Icons'
import { activeJob, type TranslationJob } from './labTypes'

export default function TranslationSegments({ job, showOriginal, onEvidence }: { job: TranslationJob; showOriginal: boolean; onEvidence: (id: string) => void }) {
  const completed = job.segments.filter(segment => segment.translated).length
  return <div className={`aligned-segments ${showOriginal ? '' : 'translated-only'}`}><div className="aligned-column-headings">{showOriginal && <span>原文 · ORIGINAL</span>}<span>{job.target_language === 'zh-CN' ? '中文译文' : 'ENGLISH TRANSLATION'}</span></div>{job.segments.map(segment => <section className={`aligned-segment ${segment.translated ? 'ready' : ''}`} key={segment.index}>
        {showOriginal && <div className="original-segment"><div className="segment-meta"><span>{String(segment.index).padStart(2, '0')}</span>{segment.evidence_id ? <button onClick={() => onEvidence(segment.evidence_id!)}><Icon name="link" size={12}/>{segment.locations.find(location => location.page)?.page ? `p.${segment.locations.find(location => location.page)?.page} · ` : ''}行 {segment.start_line}–{segment.end_line}{segment.start_char != null ? ' · 行内选段' : ''}</button> : <span>输入文本</span>}</div><p>{segment.original}</p></div>}<div className="translated-segment"><div className="segment-meta"><span>{showOriginal ? 'TRANSLATION' : `段落 ${segment.index}`}</span>{segment.translated && <Icon name="check" size={12}/>}</div>{segment.translated ? <p>{segment.translated}</p> : <div className={`translation-placeholder ${activeJob(job) && segment.index === completed + 1 ? 'translating' : ''}`}><i/><i/><i/><span>{activeJob(job) ? segment.index === completed + 1 ? '正在翻译这一段…' : '等待翻译' : '该段尚未完成'}</span></div>}</div>
      </section>)}</div>
}
