"""Failure artifacts cannot be changed or assigned fabricated human scores."""
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("context_gate_audit",ROOT/"scripts/audit_context_gate.py")
audit_module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


@pytest.mark.parametrize("fault",["artifact","human_score"])
def test_audit_rejects_modified_failure_or_fabricated_score(tmp_path,fault):
    directory=tmp_path/"gate"
    shutil.copytree(ROOT/"docs/results/context-gate-dev001-v1",directory)
    initial=audit_module.audit(directory)
    assert initial["audit"]=="passed" and initial["task_gate"]=="failed"
    if fault=="artifact":
        (directory/"repeat-01/reads.json").write_text('[{}]\n',encoding="utf-8")
    else:
        path=directory/"summary.json"
        value=json.loads(path.read_text(encoding="utf-8")); value["semantic_accuracy"]=1.0
        path.write_text(json.dumps(value),encoding="utf-8")
    with pytest.raises(ValueError,match="Artifact changed|Fabricated aggregate"):
        audit_module.audit(directory)
