"""Package an existing blind diagnostic sample for two independent human reviews."""
import argparse
import json
from pathlib import Path
import sys
import zipfile


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend/app"))
from research_backend.benchmarks.reports import export_annotation_bundle
from research_backend.benchmarks.trials import digest, load


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = load(args.inputs)["cases"]
    if not rows:
        raise ValueError("Empty diagnostic packet")
    cases = []
    for row in rows:
        if not row["id"].startswith("review-"):
            raise ValueError("Expected opaque diagnostic review IDs")
        evidence = [dict(id=e["id"], source_id=e["source_id"], quote=e["quote"],
                         start_line=1, end_line=len(e["quote"].splitlines())) for e in row["evidence"]]
        if not evidence:
            raise ValueError("Diagnostic abstract missing")
        cases.append(dict(id=row["id"], question="Does the supplied evidence support the entire claim?",
            claim=row["claim"], source_ids=list(dict.fromkeys(e["source_id"] for e in evidence)),
            evidence=evidence, citation_contract_valid=True))
    bundle = dict(format=1, purpose="unannotated error-enriched diagnostic sample; not natural task accuracy",
        original_packet_sha256=digest(args.inputs),
        line_numbering="Local transcript lines, including blanks; not original SciFact sentence indices",
        cases=cases)
    export_annotation_bundle(bundle, args.out)
    guide = (REPO / "docs/HUMAN_REVIEW_PROTOCOL.md").read_bytes()
    (args.out / "instructions.md").write_bytes(guide)
    for name in ("annotator-a", "annotator-b"):
        with zipfile.ZipFile(args.out / (name + ".zip"), "x", zipfile.ZIP_DEFLATED) as packet:
            for filename in ("human-cases.json", name + ".json", "instructions.md"):
                packet.write(args.out / filename, arcname=filename)
    print(json.dumps(dict(cases=len(cases), completed_human_labels=0,
        source_sha256=bundle["original_packet_sha256"],
        distribute=["annotator-a.zip", "annotator-b.zip"],
        status="ready_for_independent_human_review")))


if __name__ == "__main__":
    main()
