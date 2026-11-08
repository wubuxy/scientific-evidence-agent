"""Create two independent report-rubric packets without condition names or model reviews.

The coordinator mapping stays in the local output, outside either distribution ZIP.
Only delivered reports receive semantic review; failures remain in the study denominator.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import uuid
import zipfile

from audit_architecture_study import ROOT, audit, load, require


def write(path,value):
    path.write_bytes((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode())


def package(study,out):
    audit(study)
    summary,task,rubric=[load(study/n) for n in ('summary.json','task.json','rubric.json')]
    out.mkdir(parents=True,exist_ok=False)
    cases, mapping = [], []
    for row in summary['results']:
        if not row['contract_pass']:
            continue
        folder=study/row['case']; identifier='report-'+uuid.uuid4().hex[:10]
        reads={r['id']:r for r in load(folder/'reads.json')}
        sources={s['id']:s for s in load(folder/'sources.json')}
        evidence=[]
        for read in reads.values():
            evidence.append(dict(id=read['id'],title=sources[read['source_id']]['title'],
                start_line=read['start_line'],end_line=read['end_line'],quote=read['quote']))
        cases.append(dict(id=identifier,markdown=(folder/'report.md').read_text(encoding='utf-8'),evidence=evidence))
        mapping.append(dict(id=identifier,case=row['case'],condition=row['memory_mode']))
    guide='''# 独立报告评审

请只使用本包，不查看仓库中对应实验结果、条件或另一位评审者的标注。
先阅读任务及六条预先固定的标准，再逐份检查报告和所引原文。每条标准填写 0 或 1，并写明理由；无法判断保留 null 并说明原因，不能默认通过。
复合标准全部满足才能给 1；事实与推断需有足够原文支持，方案建议需区分假设与已验证事实。
不要根据文笔、字段填满或引用存在直接判断正确。可查看 papers 中的全文来核查任务事实，但报告没有引用的内容不能补算成它已经提供的引用支持。
在 labels.json 填写自己的 annotator 标识及各项分数、理由、证据行号；独立完成前不要交流。所有初始标签均为空，本包没有机器评分。
本包只含已交付报告。未交付尝试仍保留在实验分母，不能用已交付报告的语义评分宣称总体任务成功率。
这是一个已见任务的开发诊断，不代表未见论文效果。
'''
    if not cases:
        raise ValueError('No delivered reports to review')
    random.SystemRandom().shuffle(cases)
    write(out/'coordinator-mapping.json',dict(study=study.name,planned_attempts=summary['planned_attempts'],
        undelivered=[r['case'] for r in summary['results'] if not r['contract_pass']],mapping=mapping))
    packet = '# 任务与评价标准\n\n'+task['question']+'\n\n'
    packet += '\n'.join(f"- {item['id']}: {item['criterion']}" for item in rubric['items'])+'\n\n'
    for case in cases:
        packet += f"# {case['id']}\n\n{case['markdown']}\n\n## 所读原文\n\n"
        for evidence in case['evidence']:
            packet += f"### {evidence['id']} — {evidence['title']}\n\n"
            packet += '\n'.join(f"> {i}: {line}" for i,line in enumerate(evidence['quote'].splitlines(),evidence['start_line']))+'\n\n'
    names=[]
    for slot in ('annotator-a','annotator-b'):
        labels=dict(annotation_source='human',annotator=None,slot=slot,
            reports=[dict(id=c['id'],items=[dict(rubric_id=r['id'],score=None,rationale=None,evidence_spans=[])
                for r in rubric['items']]) for c in cases])
        name=slot+'.zip'; names.append(name)
        with zipfile.ZipFile(out/name,'x',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('instructions.md',guide.encode())
            archive.writestr('reports.md',packet.encode())
            archive.writestr('labels.json',json.dumps(labels,ensure_ascii=False,indent=2).encode())
            for source in task['sources']:
                archive.write(ROOT/'frontend/public/demo/papers'/(source['id']+'.txt'),'papers/'+source['id']+'.txt')
            archive.write(ROOT/'frontend/public/demo/ATTRIBUTION.md','papers/ATTRIBUTION.md')
    manifest=dict(study=study.name,planned_attempts=summary['planned_attempts'],delivered_reports=len(cases),
        rubric_items_per_report=len(rubric['items']),independent_annotators=2,completed_human_labels=0,
        semantic_accuracy=None,human_rubric_score=None,coordinator_mapping_included=False,
        packets={n:hashlib.sha256((out/n).read_bytes()).hexdigest() for n in names})
    write(out/'packet-manifest.json',manifest)
    print(json.dumps(manifest))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    package(args.root,args.out)
