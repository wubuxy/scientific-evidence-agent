"""Scientific experiment integrity checks; no model dependencies or cloud calls."""
import json
from pathlib import Path

import pytest

from research_backend.benchmarks import verifier_pilot as pilot


def test_scifact_pairs_use_only_cited_or_explicitly_annotated_abstracts():
    corpus={str(i):dict(title=f"paper {i}",abstract=[f"evidence {i}"]) for i in (1,2,3)}
    claims=[dict(id=1,claim="claim",cited_doc_ids=[1,2],evidence={"1":[dict(label="SUPPORT",sentences=[0])]})]
    rows=pilot.scientific_pairs(claims,corpus,"train")
    assert [r["label"] for r in rows]==["supported","insufficient"]
    assert {r["evidence"][0]["source_id"] for r in rows}=={"1","2"}
    assert all("paper:" in " ".join(r["group_keys"]) for r in rows)
    claims[0]["evidence"]["1"].append(dict(label="CONTRADICT",sentences=[0]))
    with pytest.raises(ValueError,match="Ambiguous"):
        pilot.scientific_pairs(claims,corpus,"train")


def test_paper_claim_connected_groups_prevent_indirect_train_test_leakage():
    train=[dict(id="a",group_keys=["paper:1","claim:a"]),
           dict(id="b",group_keys=["paper:1","paper:2","claim:b"])]
    train += [dict(id=f"safe-{i}",group_keys=[f"paper:{i}",f"claim:{i}"]) for i in range(3,9)]
    test=[dict(id="reserved",group_keys=["paper:2","claim:test"])]
    config=dict(pilot.CONFIG,train_target=100,calibration_target=1,test_target=1)
    training,calibration,testing=pilot.partition(train,test,config)
    assert not {"a","b"}&{r["id"] for r in training+calibration}
    assert len(training)==5 and len(calibration)==len(testing)==1
    assert not {k for r in training for k in r["group_keys"]}&{k for r in calibration+testing for k in r["group_keys"]}


def test_prompt_excludes_labels_annotation_metadata_and_expected_answers():
    row=dict(question="q",claim="claim",evidence=[dict(quote="excerpt")],
             label="SECRET_LABEL",expected="SECRET_GOLD",group_keys=["SECRET_GROUP"])
    visible=json.dumps(pilot.messages(row))
    assert "excerpt" in visible and "claim" in visible
    assert "SECRET_" not in visible


def test_missing_predictions_count_as_errors_and_empty_precision_is_not_one():
    labels=dict(a="supported",b="contradicted",c="insufficient")
    result=pilot.classification_metrics(labels,dict(a="supported"))
    assert result["accuracy"]==pytest.approx(1/3)
    assert result["macro_f1"]==pytest.approx(1/3)
    assert result["missing_predictions"]==2
    assert result["per_class"]["contradicted"]["precision"] is None
    assert result["confusion"]["contradicted"]=={"missing":1}


def test_generation_does_not_open_gold_but_independent_scoring_checks_its_hash(tmp_path,monkeypatch):
    files={}
    for name in ["test-inputs.json","test-gold.json","training.json"]:
        path=tmp_path/name
        path.write_text('{}',encoding="utf-8")
        files[name]=pilot.digest(path)
    plan=dict(code_sha256=pilot.digest(pilot.__file__),config=pilot.CONFIG,files=files,
        protocol_sha256=pilot.digest(Path(pilot.__file__).resolve().parents[4]/"docs/experiments/SCIFACT_VERIFIER_PILOT.md"),
        model_dir="fake",model_sha256={})
    (tmp_path/"plan.json").write_text(json.dumps(plan),encoding="utf-8")
    monkeypatch.setattr(pilot,"model_hashes",lambda path:{})
    original_digest=pilot.digest
    opened=[]
    def tracked_digest(path):
        opened.append(Path(path).name)
        return original_digest(path)
    monkeypatch.setattr(pilot,"digest",tracked_digest)
    pilot.check_plan(tmp_path)
    assert "test-gold.json" not in opened
    (tmp_path/"test-gold.json").write_text('{"changed":true}',encoding="utf-8")
    with pytest.raises(ValueError,match="hash mismatch"):
        pilot.check_plan(tmp_path,scoring=True)
