"""Freeze natural report tasks from QASPER train, without held-out text or labels."""
import argparse
from collections import defaultdict
import hashlib
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO/"backend/app"))
from research_backend.benchmarks.prepare import freeze, qasper_candidates
from research_backend.benchmarks.trials import digest, load, write_new

SEED = "report-calibration-v1:20261107:"


def ranked(value):
    return hashlib.sha256((SEED+value).encode()).hexdigest()


def build(candidates, documents, excluded_ids, excluded_hashes):
    by_paper = defaultdict(list)
    for task, gold in candidates:
        sid = task["source_ids"][0]
        if sid not in excluded_ids and documents[sid]["text_sha256"] not in excluded_hashes:
            by_paper[sid].append((task, gold))
    eligible = sorted((sid for sid, rows in by_paper.items() if len(rows)>=3), key=ranked)
    if len(eligible)<12:
        raise ValueError("Need twelve unseen eligible training-split papers")
    selected = eligible[:12]
    groups = [[sid] for sid in selected[:4]] + [selected[i:i+2] for i in range(4,12,2)]
    tasks, mapping = [], {}
    for index, group in enumerate(groups):
        tid = f"report-cal-{index+1:02d}"
        question = ["Write an English research report using only the supplied papers. "
            "Answer the questions below for each paper, and put each answer in the comparison row's "
            "attributes using the exact keys Q1, Q2, Q3. Include evidence citations and limitations. "
            "Keep each paper's answers separate; different papers have different questions."]
        targets, labels, origin = [], [], []
        for sid in group:
            question.append("Paper title: "+documents[sid]["title"])
            for j,(task,gold) in enumerate(sorted(by_paper[sid],key=lambda row:ranked(row[0]["id"]))[:3],1):
                key = f"Q{j}"
                identifier = f"{sid}-{key}"
                question.append(key+": "+task["question"])
                targets.append(dict(id=identifier,source_id=sid,field=key))
                labels.append(dict(gold["targets"][0],id=identifier))
                origin.append(dict(source_id=sid,field=key,original_task_id=task["id"]))
        tasks.append((dict(id=tid,question="\n".join(question),source_ids=group,targets=targets),
                      dict(targets=labels,evidence_status="official_paragraph_annotations")))
        mapping[tid]=origin
    return tasks, mapping, len(eligible)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--public",type=Path,required=True)
    args=parser.parse_args()
    manifests=[REPO/f"docs/dataset-manifests/qasper-v1-{s}.json" for s in ("dev","test")]
    excluded={sid for path in manifests for sid in load(path)["document_ids"]}
    prior_inputs=REPO/"benchmark-data/qasper-repair-v1/dev/inputs.json"
    hashes={d["text_sha256"] for d in load(prior_inputs)["documents"]}
    candidates,documents,exclusions=qasper_candidates(args.raw,"train")
    tasks,mapping,eligible=build(candidates,documents,excluded,hashes)
    manifest=freeze(args.out,"qasper-report-calibration-v1","dev",tasks,documents,8,20261107,
        dict(official_split="train",archive_sha256=digest(args.raw/"qasper-train-dev-v0.3.tgz"),
             source="https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-train-dev-v0.3.tgz",
             excluded_manifest_sha256={p.name:digest(p) for p in manifests},
             builder_sha256=digest(__file__),eligible_papers=eligible,
             selection="SHA256(report-calibration-v1:20261107: + ID), 12 papers with >=3 eligible questions; first 4 single, remaining 8 paired; first 3 hashed questions per paper"),exclusions)
    write_new(args.public/"manifest.json",manifest)
    write_new(args.public/"question-map.json",mapping)
    write_new(args.public/"task_ids.json",manifest["task_ids"])
    print({k:manifest[k] for k in ("task_count","document_count","inputs_sha256")})


if __name__=="__main__":
    main()
