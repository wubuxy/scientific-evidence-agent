"""Freeze a four-attempt, two-mode development comparison. Makes no model calls."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from run_research_case import ROOT, prepare

STUDY = 'memory-ablation-dev001-v1'
SCHEDULE = [('legacy-01','legacy'),('evidence-01','evidence'),
            ('evidence-02','evidence'),('legacy-02','legacy')]


def main():
    local, public = ROOT/'test-results'/STUDY, ROOT/'docs/results'/STUDY
    public.mkdir(parents=True,exist_ok=False)
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    manifests = {}
    for name, mode in SCHEDULE:
        prepare(local/name,mode)
        (public/name).mkdir()
        (public/name/'manifest.json').write_bytes((local/name/'manifest.json').read_bytes())
        manifests[name] = json.loads((local/name/'manifest.json').read_text(encoding='utf-8'))
    first = local/SCHEDULE[0][0]
    for name, path in [('task.json',first/'inputs/task.json'),('rubric.json',first/'rubric.json')]:
        (public/name).write_bytes(path.read_bytes())
    reference = manifests[SCHEDULE[0][0]]
    assert all({k:v for k,v in m.items() if k!='memory_mode'} ==
               {k:v for k,v in reference.items() if k!='memory_mode'} for m in manifests.values())
    plan = dict(id=STUDY,prepared_at=datetime.now(timezone.utc).isoformat(),
        code_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
        scope='One previously observed development task, two repetitions per condition. Exploratory architecture diagnosis, not a generalization estimate.',
        schedule=[dict(case=n,memory_mode=m) for n,m in SCHEDULE],
        independent_variable='memory_mode only; production Runtime, read controller, task, tools, output contract, temperature and budgets are shared.',
        model=reference['model'],temperature=.1,extra_body=reference['extra_body'],
        max_tokens_per_run=30000,max_steps_per_run=18,max_seconds_per_run=600,
        logical_token_ceiling_total=120000,sdk_transport_retries=1,application_result_retries=0,
        stopping_rule='Run the four scheduled attempts in order; no replacement for task failures. Stop after an external service fault or unknown billing and retain missing attempts.',
        hypotheses=['Evidence memory can reduce input-token accumulation without reducing structurally valid delivery.',
            'Evidence memory may reduce repeated reading and permit deeper reading; depth is not relevance or correctness.'],
        primary_metrics=['contract_pass','returned_tokens'],
        diagnostic_metrics=['read_calls','unique_read_lines','repeated_line_fraction','unique_lines_after_80',
            'field_presence_rate','rejected_drafts','phase_sequence','max_input_tokens'],
        human_metrics=['six frozen research rubrics','claim support','condition preservation','proposal feasibility'],
        human_scores=None,semantic_accuracy=None,held_out_data_used=False,
        adoption_rule='No automatic default switch from token or completion counts. Preserve quality as unmeasured until independent review. New-paper confirmation is required.',
        manifest_sha256={n:sha(public/n/'manifest.json') for n,_ in SCHEDULE},
        input_sha256=reference['input_sha256'],rubric_sha256=reference['rubric_sha256'],
        preparation_script_sha256=sha(Path(__file__)))
    (public/'plan.json').write_bytes((json.dumps(plan,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps(dict(study=STUDY,attempts=4,logical_token_ceiling_total=120000,model_calls=0)))


if __name__=='__main__':
    main()
