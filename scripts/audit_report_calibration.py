"""Audit public cohort, denominators, costs and the pending-human-label boundary."""
import json
from pathlib import Path
from statistics import mean

ROOT=Path(__file__).resolve().parents[1]
load=lambda p:json.loads(p.read_text(encoding="utf-8"))


def main():
    plan=load(ROOT/"docs/experiments/report-calibration-v1/plan.json")
    cohort=load(ROOT/"docs/experiments/report-calibration-v1/manifest.json")
    summary=load(ROOT/"docs/results/report-calibration-v1/summary.json")
    records=load(ROOT/"docs/results/report-calibration-v1/mechanical-report-scores.json")["records"]
    assert len(set(cohort["document_ids"]))==summary["papers"]==12
    for split in ("dev","test"):
        excluded=load(ROOT/f"docs/dataset-manifests/qasper-v1-{split}.json")
        assert not set(cohort["document_ids"]) & set(excluded["document_ids"])
    assert len(plan["task_ids"])==summary["tasks"]==8
    assert plan["repeats"]==summary["repeats"]==2
    assert len(records)==summary["planned_reports"]==16
    assert len({(r["task_id"],r["repeat"]) for r in records})==16
    assert all("valid_reads" not in r and "units" not in r for r in records)
    mechanical=summary["generation"]
    assert mechanical["completed"]==sum(r["status"]=="completed" for r in records)
    assert mechanical["contract_pass_rate"]==sum(r["contract_pass"] for r in records)/16
    assert abs(mechanical["field_presence_rate"]-mean(r["field_presence_rate"] for r in records))<1e-12
    assert mechanical["total_known_tokens"]==sum(r["known_tokens_used"] for r in records)
    assert mechanical["semantic_success_rate"] is None and summary["human_accuracy"] is None
    assert summary["human_labels_completed"]==0 and summary["cohen_kappa"] is None
    counts=summary["sampling_counts"]
    assert sum(c["available"] for c in counts)==summary["natural_report_units"]==151
    assert sum(c["selected"] for c in counts)==summary["selected_human_units"]==92
    assert all(c["selected"]<=min(6,c["available"]) for c in counts)
    for model,record in summary["judges"].items():
        assert record["planned"]==record["recorded"]==92
        assert record["completed"]+sum(record["failures"].values())==92
        assert "verdict_counts" not in record
    assert summary["model_agreement_public"] is None
    assert summary["known_judge_returned_usage_tokens"]==sum(r["known_returned_usage_tokens"] for r in summary["judges"].values())
    recovery=load(ROOT/"docs/results/report-calibration-v1/format-recovery.json")
    assert recovery["post_hoc"] is True and recovery["new_model_calls"]==0
    for model,record in recovery["models"].items():
        assert record["original_sha256"]==summary["judges"][model]["reviews_sha256"]
        assert record["original_completed"]==summary["judges"][model]["completed"]
        assert record["original_completed"]+record["recovered_length_failures"]==record["completed_after_recovery"]==92
        assert record["semantic_accuracy"] is None
    print("PASS: 12 disjoint papers, 16 report attempts, 92 blind units, 184 judge records; no fabricated human scores or public candidate opinions.")


if __name__=="__main__":main()
