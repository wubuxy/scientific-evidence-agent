"""Offline Agent fault matrix using production-runtime regression cases.

No network or model credential is used. Passed cases indicate asserted software
behavior, never semantic accuracy. A new output directory preserves every run.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
TEST = "tests/test_field_state.py::"
CASES = [
    ("requirements", "test_requirements_cannot_disappear_and_reading_is_not_verification", "Cannot delete requirements; reading is not verification"),
    ("explicit_request", "test_explicit_user_criteria_reach_first_model_call_and_persist", "User criteria reach the model and persist with the run"),
    ("missing_evidence", "test_field_candidate_requires_current_same_paper_evidence[unknown-s1]", "Reject evidence not read in this run"),
    ("foreign_evidence", "test_field_candidate_requires_current_same_paper_evidence[e1-s2]", "Reject another paper's evidence"),
    ("stale_review", "test_review_is_version_bound_and_never_semantic_success", "Invalidate old review after evidence changes"),
    ("malformed_review", "test_bad_review_batch_has_no_partial_commit[span]", "Invalid spans reject the entire review batch"),
    ("repair", "test_complete_runtime_repairs_gap_preserves_answers_counts_review_and_exposes_state", "Fill gap while preserving original answers and accounting review cost"),
    ("no_progress", "test_modes_and_no_progress_terminate_without_fabricated_success[bounded-2]", "Stop without progress; keep unresolved field"),
    ("review_failure", "test_bad_semantic_review_keeps_report_and_cost_but_not_verdict", "Keep report and cost, reject invalid review verdict"),
    ("finalization_feedback", "test_finalization_keeps_validation_feedback_after_context_rebuild", "Preserve report validation feedback when rebuilding final context"),
    ("step_budget", "test_last_step_skips_repair_and_review_and_reports_gap", "Stop at step cap with explicit unresolved field"),
    ("unknown_usage", "test_unknown_generation_usage_skips_additional_reviewer", "Do not spend more on review when prior usage is unknown"),
    ("token_overrun", "test_review_over_budget_fails_run_and_records_returned_usage", "Returned usage above cap fails the run and remains recorded"),
    ("service_failure", "test_failed_repair_preserves_valid_report_and_explicit_gap[service_error]", "Service error preserves checkpoint and explicit gap"),
    ("provider_timeout", "test_failed_repair_preserves_valid_report_and_explicit_gap[timeout]", "Provider timeout preserves checkpoint; not a wall-clock load test"),
    ("cancellation", "test_cancel_during_repair_never_promotes_checkpoint_to_success", "Cancellation stays cancelled even with a valid checkpoint"),
    ("workspace_isolation", "test_explicit_requirements_cannot_reference_another_workspace", "Foreign workspace requirement rejected before any model call"),
]
CONTRACT_CASES = {"requirements", "missing_evidence", "foreign_evidence", "stale_review", "malformed_review", "workspace_isolation"}


def summarize(junit, returncode):
    """Every planned case remains in the denominator, even after collection failure."""
    found, errors = {}, []
    if junit.exists():
        try:
            for case in ET.parse(junit).iter("testcase"):
                found.setdefault(case.get("name"), []).append(case)
        except ET.ParseError as exc:
            errors.append(f"Invalid JUnit XML: {exc}")
    else:
        errors.append("JUnit report missing")
    rows = []
    for case_id, name, expected in CASES:
        matches = found.pop(name, [])
        row = dict(id=case_id, nodeid=TEST+name, expected=expected, status="missing", observations=[])
        if len(matches) > 1:
            row["status"] = "invalid"
            errors.append(f"Duplicate testcase: {name}")
        elif matches:
            item = matches[0]
            row["status"] = "failed" if item.find("failure") is not None or item.find("error") is not None else "skipped" if item.find("skipped") is not None else "passed"
            row["duration_seconds"] = float(item.get("time", 0))
            for prop in item.findall("./properties/property"):
                if prop.get("name") == "runtime_outcome":
                    try:
                        row["observations"].append(json.loads(prop.get("value")))
                    except (ValueError, TypeError) as exc:
                        row["status"] = "invalid"
                        errors.append(f"Invalid runtime observation: {name}: {exc}")
            if row["status"] == "passed" and case_id not in CONTRACT_CASES and len(row["observations"]) != 1:
                row["status"] = "invalid"
                errors.append(f"Expected exactly one runtime outcome: {name}")
            if any(o.get("semantic_success") is not None for o in row["observations"]):
                row["status"] = "failed"
                errors.append(f"Synthetic runtime claimed semantic success: {name}")
        rows.append(row)
    if found:
        errors.append("Unexpected testcases: " + ", ".join(sorted(found)))
    counts = dict(Counter(row["status"] for row in rows))
    return dict(status="passed" if returncode == 0 and not errors and counts.get("passed") == len(CASES) else "failed",
                planned=len(CASES), counts=counts, pytest_exit_code=returncode, errors=errors, cases=rows,
                semantic_accuracy=None, model_calls_paid=0,
                scope="synthetic fault regression; passing is not research quality or production availability")


def main(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONUTF8="1", AGENT_FAULT_RECORD="1")
    env.pop("PYTEST_ADDOPTS", None)
    command = [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "-o", "junit_family=xunit1",
               "--junitxml="+str(out/"junit.xml")] + [TEST+name for _, name, _ in CASES]
    files = [ROOT/"scripts/eval_agent_faults.py", ROOT/"backend/tests/test_field_state.py", ROOT/"backend/tests/conftest.py"]
    files += sorted((ROOT/"backend/app/research_backend").glob("*.py"))
    manifest = dict(created_at=datetime.now(timezone.utc).isoformat(), command=command, python=sys.version,
                    source_sha256={str(p.relative_to(ROOT)).replace("\\", "/"):hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    (out/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
    with (out/"pytest.log").open("w", encoding="utf-8") as log:
        try:
            result = subprocess.run(command, cwd=ROOT/"backend", env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
    summary = summarize(out/"junit.xml", code)
    (out/"summary.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    lines = ["# Offline Agent fault matrix", "", summary["scope"], "", f"Planned: {summary['planned']} · Outcome: {summary['status']}",
             "", "| Case | Assertion | Test | Runtime observation |", "|---|---|---|---|"]
    for row in summary["cases"]:
        observations = "; ".join(f"{o['status']} / stop={o['stop_reason']} / gaps={o['unresolved']} / tokens={o['tokens_used']} (estimated={o['tokens_estimated']})"
                                 for o in row["observations"])
        if not observations:
            observations = "No runtime: contract assertion" if row["id"] in CONTRACT_CASES else "Runtime observation unavailable"
        lines.append(f"| {row['id']} | {row['expected']} | {row['status']} | {observations} |")
    lines += ["", "Missing or skipped cases are not passes. Synthetic reviewer output is not a human label.",
              "Provider-timeout injection does not measure wall-clock scheduling or load."]
    (out/"report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps(dict(status=summary["status"], planned=summary["planned"], counts=summary["counts"], output=str(out))))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="New output directory; existing paths are rejected")
    raise SystemExit(main(parser.parse_args()))
