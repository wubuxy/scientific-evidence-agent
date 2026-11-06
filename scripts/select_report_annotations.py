"""Select report units before judging; do not rank by predictions or labels."""
import argparse
from collections import defaultdict
import hashlib
from pathlib import Path
import sys

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/"backend/app"))
from research_backend.benchmarks.reports import export_cases, export_annotation_bundle
from research_backend.benchmarks.trials import digest, load, write_new


def select(bundle, tasks):
    fields={t["id"]:{target["field"] for target in t["targets"]} for t in tasks}
    groups=defaultdict(list)
    for case in bundle["cases"]:
        groups[(case["task_id"],case["repeat"],case["method"])].append(case)
    selected,counts=[],[]
    rank=lambda c:hashlib.sha256(("report-annotation-v1:20261107:"+c["id"]).encode()).hexdigest()
    for key,rows in sorted(groups.items()):
        rows=sorted(rows,key=rank)
        claims=[c for c in rows if c["unit_id"].startswith("claim-")][:3]
        cells=[c for c in rows if any(c["unit_id"].endswith("-attribute:"+f) for f in fields[key[0]])][:3]
        chosen=claims+cells
        used={c["id"] for c in chosen}
        chosen += [c for c in rows if c["id"] not in used][:6-len(chosen)]
        selected.extend(chosen)
        counts.append(dict(task_id=key[0],repeat=key[1],method=key[2],available=len(rows),selected=len(chosen)))
    return dict(bundle,cases=selected,selection=dict(version=1,maximum_per_report=6,
        policy="up to 3 narrative claims and 3 requested attribute cells; fill with remaining hashed units; no verdict access",
        source_case_count=len(bundle["cases"]),report_counts=counts))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("inputs","run","out"):
        p.add_argument("--"+name,type=Path,required=True)
    args=p.parse_args()
    original=export_cases(args.inputs,args.run)
    selected=select(original,load(args.inputs)["tasks"])
    export_annotation_bundle(selected,args.out)
    write_new(args.out/"coordinator-all-cases.json",original)
    write_new(args.out/"selection-summary.json",dict(selected["selection"],selected_cases=len(selected["cases"]),
        selected_case_ids=[c["id"] for c in selected["cases"]],cases_sha256=digest(args.out/"cases.json"),
        selector_sha256=digest(__file__),completed_human_labels=0))
    print(dict(selected_cases=len(selected["cases"]),natural_units=len(original["cases"])))


if __name__=="__main__":
    main()
