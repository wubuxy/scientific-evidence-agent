import hashlib

import pytest

from research_backend.benchmarks import verifier_scale as scale
from research_backend.benchmarks.trials import write_new


def prediction(verdict, score):
    return dict(verdict=verdict, support_score=score, status="completed")


def test_missing_negative_is_wrong_and_prevents_auc():
    value = scale.binary_metrics({"yes": "supported", "no": "insufficient"},
                                 {"yes": prediction("supported", .9)})
    assert value["accuracy"] == .5
    assert value["balanced_accuracy"] == .5
    assert value["missing_predictions"] == 1
    assert value["auroc"] is None


def test_auc_ties_use_average_ranks():
    gold = {"a": "supported", "b": "contradicted", "c": "insufficient"}
    records = {i: prediction("supported", .5) for i in gold}
    assert scale.binary_metrics(gold, records)["auroc"] == .5


def test_binary_baseline_does_not_invent_contradiction_labels():
    gold = {"a": "supported", "b": "contradicted", "c": "insufficient"}
    records = {"a": prediction("supported", .9),
               "b": prediction("unsupported", .1), "c": prediction("unsupported", .2)}
    value = scale.binary_metrics(gold, records)
    assert value["balanced_accuracy"] == 1
    assert value["auroc"] == 1
    assert records["b"]["verdict"] == "unsupported"


def test_native_minicheck_projection_excludes_gold_and_metadata():
    row = dict(claim="claim text", question="question sentinel", evidence=[dict(quote="raw evidence")],
               label="gold sentinel", group_keys=["secret group"], id="private metadata")
    rendered = str(scale.mini_messages(row))
    assert "claim text" in rendered and "raw evidence" in rendered
    assert all(text not in rendered for text in ["gold sentinel", "question sentinel", "secret group", "private metadata"])


def test_custom_python_is_checked_without_executing(tmp_path):
    path = tmp_path / "modeling.py"
    content = b'raise RuntimeError("must not execute")\n'
    path.write_bytes(content)
    blob = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
    spec = dict(files={"modeling.py": dict(git_blob_id=blob)})
    scale.verify_official(tmp_path, spec, mini=True)
    path.write_bytes(content + b"# changed\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        scale.verify_official(tmp_path, spec, mini=True)


def test_generation_plan_check_does_not_open_test_gold(tmp_path, monkeypatch):
    monkeypatch.setattr(scale, "code_files", lambda: {})
    write_new(tmp_path / "test-inputs.json", dict(cases=[]))
    write_new(tmp_path / "plan.json", dict(config=scale.CONFIG, code_sha256={}, model_dirs={},
        files={"test-inputs.json": scale.sha(tmp_path / "test-inputs.json"), "test-gold.json": "not-readable"}))
    scale.check_plan(tmp_path)
    with pytest.raises(FileNotFoundError):
        scale.check_plan(tmp_path, scoring=True)
