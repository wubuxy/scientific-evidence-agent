"""Preregister, execute and independently audit a bounded six-arm Agent sweep.

No defaults are adopted automatically. Failed and missing attempts remain in the
denominator. Do not edit code, inputs or criteria between frozen attempts.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from run_research_case import ROOT, prepare, run_case, QUESTION, FIELDS, RUBRIC
from audit_architecture_study import ARTIFACTS, inspect_case, load, require, sha

STUDY = 'strategy-sweep-dev-v1'
BASE = dict(read_guidance=False,memory_priority='recent',writer_context='prefix',report_schema='full')
ARMS = {
    'baseline': BASE,
    'read-guide': dict(BASE,read_guidance=True),
    'read-first': dict(BASE,memory_priority='read_first'),
    'writer-select': dict(BASE,writer_context='lexical'),
    'compact': dict(BASE,report_schema='compact'),
    'combined': dict(read_guidance=True,memory_priority='read_first',writer_context='lexical',report_schema='compact'),
}
TASKS = {
    'design': dict(question=QUESTION,fields=FIELDS,rubric=RUBRIC),
    'conditions': dict(question='''我们要决定哪些训练和评测做法可以迁移到论文研究 Agent。仅根据提供的 QASPER 与 ArxivDIGESTables 两篇论文，按指定字段逐篇比较。
明确区分数据集构建、模型训练与推理评测阶段；尤其区分题目作者看到了什么、回答者或系统实际收到什么，和不同 oracle / non-oracle 条件。
哪些分数具有可比前提，哪些不能相互排名？训练是否需要新增标注，论文证据足不足以据此为我们的 Agent 选择微调？
在 claims 中给出有引用的判断，在 proposals 中提出一个最小、尚未执行的控制实验，写清固定项、改动项、失败计数和停止条件。
重要事实必须引用本次回读的原文，证据不够明确写本次未核实；不要用发表时结果推断当前最强模型，不读取论文外的数据集。''',
        fields=[dict(name='角色与输入',criterion='区分数据构建者、标注者、回答系统及其输入权限，不能把题目作者的视野当成回答系统的输入'),
                dict(name='训练与数据划分',criterion='区分任务数据划分、模型训练或微调、推理使用；找不到时明确未知，不把使用模型等同于训练模型'),
                dict(name='评测条件与指标',criterion='说明指标对象、参考依赖及 oracle/non-oracle 等适用条件；无法直接比较时解释原因'),
                dict(name='迁移条件与未知',criterion='把论文已验证事实、对产品的迁移推断及未解决问题分开，有引用且不夸大外推')],
        rubric=['角色和输入权限区分正确，未把出题者只见标题摘要误写为回答者或系统只见标题摘要。',
                '训练、微调、推理和数据划分区分清楚，未把使用模型等同于训练了模型。',
                '关键指标、参考依赖和 oracle/non-oracle 条件保留正确，未跨任务直接排名。',
                '引用原文确实支持关键事实；未把片段省略解释为论文未报告。',
                '迁移建议区分假设与事实，微调建议有条件，证据不足时保留未知。',
                '最小实验写清固定项、改动项、失败计数和停止条件，且未声称已经执行。']),
}


def write(path, value):
    path.write_bytes((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode())


def paths():
    return ROOT/'test-results'/STUDY, ROOT/'docs/results'/STUDY


def schedule():
    arms=list(ARMS)
    # Each task/repeat contains all arms. Reverse and rotate order to reduce a
    # simple time/order confound; there is no claim of statistical power.
    rows=[]
    for repeat in (1,2):
        for task_index, task in enumerate(TASKS):
            order=arms if repeat==1 else list(reversed(arms))
            order=order[task_index*3:]+order[:task_index*3]
            rows.extend(dict(case=f'{task}-{arm}-{repeat:02d}',task=task,arm=arm,repeat=repeat) for arm in order)
    return rows


def prepare_study():
    local,public=paths()
    require(not local.exists() and not public.exists(),'Study already exists; frozen records are immutable')
    public.mkdir(parents=True)
    manifest_hashes={}
    for row in schedule():
        case=row['case']; spec=TASKS[row['task']]
        prepare(local/case,'evidence',task_id='strategy-dev-'+row['task'],strategies=ARMS[row['arm']],**spec)
        (public/case).mkdir()
        for name,source in [('manifest.json','manifest.json'),('task.json','inputs/task.json'),('rubric.json','rubric.json')]:
            (public/case/name).write_bytes((local/case/source).read_bytes())
        manifest_hashes[case]=sha(public/case/'manifest.json')
    plan=dict(id=STUDY,prepared_at=datetime.now(timezone.utc).isoformat(),
        code_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
        execution_script_sha256=sha(Path(__file__)),schedule=schedule(),manifest_sha256=manifest_hashes,
        scope='Two development tasks on the same two already-observed public papers, two repeats per arm. Paper-dependent exploratory diagnosis; not a held-out or powered evaluation.',
        arms=ARMS,model='qwen3.5-27b',temperature=.1,enable_thinking=False,
        max_tokens_per_attempt=30000,max_steps_per_attempt=18,max_seconds_per_attempt=600,
        logical_token_ceiling_total=720000,sdk_transport_retries=1,application_result_retries=0,
        frozen_variables='Same model, task-specific requirements, temperature, grep tools, source text and total token/step/time ceilings. One switch differs per single arm; combined changes all four.',
        hypotheses=['Read navigation reduces repeated lines without blocking necessary rereads.',
            'Read-first memory preserves actual evidence ahead of uncitable search leads.',
            'Lexical writer selection preserves useful late conditions under a fixed context cap.',
            'Compact report schema reduces formatting failures and delivery token use.',
            'Combined effects may interact; a package result cannot identify a single mechanism.'],
        primary_metrics=['contract_pass','field_presence_rate','returned_tokens'],
        diagnostic_metrics=['repeated_line_fraction','unique_read_lines','rejected_drafts','invalid_read_calls','final_phase_calls','length_stops'],
        stopping_rule='Run scheduled attempts once in order. Keep task failures. Stop the entire batch on any unknown usage or external model exception; no replacement. Missing attempts remain in the denominator.',
        human_review='All delivered reports packaged for two independent reviewers; rubric fixed per task before running. No semantic scores until labels return.',
        adoption_rule='Keep product defaults unchanged. Structural completion/costs alone do not authorize a quality claim or automatic adoption; human review and new-paper confirmation remain required.',
        semantic_accuracy=None,human_rubric_score=None,held_out_data_used=False)
    write(public/'plan.json',plan)
    print(json.dumps(dict(study=STUDY,planned_attempts=24,logical_token_ceiling=720000,model_calls=0)))


def verify_case(public,local,row,plan):
    case=row['case']; expected=plan['manifest_sha256'][case]
    require(sha(public/case/'manifest.json')==expected==sha(local/case/'manifest.json'),'Manifest changed')
    manifest=load(public/case/'manifest.json')
    require(manifest['strategies']==plan['arms'][row['arm']],'Condition drift')
    require(sha(public/case/'task.json')==manifest['input_sha256']['inputs/task.json'],'Task changed')
    require(sha(public/case/'rubric.json')==manifest['rubric_sha256'],'Rubric changed')
    return manifest


async def execute(env_file):
    local,public=paths(); plan=load(public/'plan.json')
    require(sha(Path(__file__))==plan['execution_script_sha256'],'Execution script changed')
    for index,row in enumerate(plan['schedule']):
        verify_case(public,local,row,plan)
        folder=local/row['case']
        require(not (folder/'run').exists(),'Attempt already started; never overwrite or silently replace it')
        print(json.dumps(dict(start=index+1,total=len(plan['schedule']),**row)),flush=True)
        await run_case(folder,env_file)
        trace=load(folder/'run/trace.json')
        unknown=any(c['tokens_used'] is None for c in trace['calls'])
        if unknown:
            print(json.dumps(dict(stopped=True,reason='unknown_usage_or_external_model_failure',case=row['case'])),flush=True)
            break
        print(json.dumps(dict(finished=index+1,case=row['case'])),flush=True)


def summarize(public):
    plan=load(public/'plan.json'); rows=[]
    for row in plan['schedule']:
        case=row['case']; folder=public/case
        require(sha(folder/'manifest.json')==plan['manifest_sha256'][case],'Manifest changed')
        manifest=load(folder/'manifest.json')
        require(manifest['strategies']==plan['arms'][row['arm']],'Frozen strategy drift')
        require(sha(folder/'task.json')==manifest['input_sha256']['inputs/task.json'],'Task drift')
        require(sha(folder/'rubric.json')==manifest['rubric_sha256'],'Rubric drift')
        if not (folder/'run.json').exists():
            require(not any((folder/n).exists() for n in ARTIFACTS),'Partial archive')
            rows.append(dict(**row,status='missing',contract_pass=False,known_tokens=0,returned_tokens=None,
                             semantic_accuracy=None,human_rubric_score=None)); continue
        result=inspect_case(folder,load(folder/'task.json'),manifest)
        run,trace,calls=[load(folder/n) for n in ('run.json','trace.json','tool-calls.json')]
        require(all(run[k]==v for k,v in manifest['strategies'].items()),'Runtime did not use frozen strategies')
        events=[e['data'] for e in trace['events'] if e['type']=='model_usage']
        result.update(**row,invalid_read_calls=sum(c['name']=='sources_read' and c['status']!='succeeded' for c in calls),
            final_phase_calls=sum(p=='final' for p in result['phase_sequence']),
            length_stops=sum(e.get('finish_reason')=='length' for e in events),
            artifact_sha256={n:sha(folder/n) for n in ('manifest.json','task.json','rubric.json')+ARTIFACTS})
        rows.append(result)
    known=sum(row['known_tokens'] for row in rows)
    return dict(study=plan['id'],planned_attempts=len(rows),executed_attempts=sum(r['status']!='missing' for r in rows),
        contract_passes=sum(r['contract_pass'] for r in rows),known_tokens=known,
        returned_tokens=known if all(r['returned_tokens'] is not None for r in rows) else None,
        model_calls=sum(r.get('model_calls',0) for r in rows),semantic_accuracy=None,human_rubric_score=None,
        results=rows)


def archive():
    local,public=paths(); plan=load(public/'plan.json')
    require(not (public/'summary.json').exists(),'Archive already exists')
    for row in plan['schedule']:
        manifest=verify_case(public,local,row,plan); folder=local/row['case']
        require(all(sha(folder/p)==h for p,h in manifest['input_sha256'].items()),'Local input changed')
        if not (folder/'run').exists(): continue
        for name in ARTIFACTS:
            require(not (public/row['case']/name).exists(),'Refusing to overwrite artifact')
            (public/row['case']/name).write_bytes((folder/'run'/name).read_bytes())
    write(public/'summary.json',summarize(public))
    audit(public)


def audit(public):
    result=summarize(public)
    require(result==load(public/'summary.json'),'Published summary does not match archived traces')
    print(json.dumps({k:v for k,v in result.items() if k!='results'}))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','run','archive','audit'])
    parser.add_argument('--env-file',type=Path)
    args=parser.parse_args()
    if args.action=='prepare': prepare_study()
    elif args.action=='run':
        if args.env_file is None: parser.error('run requires --env-file')
        asyncio.run(execute(args.env_file.resolve()))
    elif args.action=='archive': archive()
    else: audit(paths()[1])
