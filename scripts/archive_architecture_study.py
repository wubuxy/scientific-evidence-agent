"""Publish only whitelisted development artifacts; never publish the local database."""
import argparse
import json
from pathlib import Path

from audit_architecture_study import ARTIFACTS, ROOT, load, require, sha, summarize


def archive(study):
    require('/' not in study and '\\' not in study and study not in {'.','..'}, 'Expected a study name')
    local, public = ROOT/'test-results'/study, ROOT/'docs/results'/study
    require(not (public/'summary.json').exists(), 'Results already archived')
    plan = load(public/'plan.json')
    for name, expected in plan['manifest_sha256'].items():
        require(sha(local/name/'manifest.json')==expected, 'Local manifest changed')
        manifest = load(local/name/'manifest.json')
        require(all(sha(local/name/p)==h for p,h in manifest['input_sha256'].items()), 'Input changed')
        require(sha(local/name/'rubric.json')==manifest['rubric_sha256'], 'Rubric changed')
        require(all(not (public/name/n).exists() for n in ARTIFACTS), 'Archive would overwrite artifacts')
        for filename in ARTIFACTS:
            (public/name/filename).write_bytes((local/name/'run'/filename).read_bytes())
    result = summarize(public)
    (public/'summary.json').write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps({k:v for k,v in result.items() if k!='results'}))


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('study')
    archive(parser.parse_args().study)
