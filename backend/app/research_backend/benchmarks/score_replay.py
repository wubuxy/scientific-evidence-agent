"""Scorer-only labels and paired analysis for fixed-read answer replays."""
import argparse
import json
from pathlib import Path
import random
from statistics import mean
from .download import sha256
from .score import score_prediction


def merge(intervals):
    out=[]
    for a,b in sorted(intervals):
        if out and a<=out[-1][1]:
            out[-1]=(out[-1][0],max(out[-1][1],b))
        else:
            out.append((a,b))
    return out


def intersect_size(left,right):
    return sum(max(0,min(b,d)-max(a,c)) for a,b in merge(left) for c,d in merge(right))


def evidence_retention(prediction,labels,documents):
    """Character overlap with union of annotated paragraphs; not entailment."""
    numerator=denominator=0
    for target in labels["targets"]:
        sid=target["source_id"];doc=documents[sid]
        lines=doc["text"].splitlines();offsets=[0]
        for line in lines:
            offsets.append(offsets[-1]+len(line)+1)
        pids={pid for ref in target["references"] for pid in (ref.get("evidence_paragraph_ids") or [])}
        gold=[(offsets[p["start_line"]-1],offsets[p["end_line"]]-1) for p in doc["paragraphs"] if p["id"] in pids]
        def spans(items):
            found=[]
            for e in items:
                if e["source_id"]!=sid or not e["quote"]:
                    continue
                a=offsets[e["start_line"]-1];b=a+len(e["quote"])
                if doc["text"][a:b]!=e["quote"]:
                    raise ValueError("Displayed evidence is not the claimed canonical span")
                found.append((a,b))
            return found
        denominator+=intersect_size(gold,spans(prediction.get("reads",[])))
        numerator+=intersect_size(gold,spans(prediction.get("visible_evidence",[])))
    return numerator/denominator if denominator else None


def paired_interval(differences,seed=20261104):
    if not differences:return None
    rng=random.Random(seed)
    draws=sorted(mean(rng.choices(differences,k=len(differences))) for _ in range(5000))
    return dict(mean_difference=mean(differences),bootstrap_95=[draws[125],draws[4874]],tasks=len(differences),
                improved=sum(d>0 for d in differences),worse=sum(d<0 for d in differences),tied=sum(d==0 for d in differences))


def score(inputs_path,gold_path,run,reference=None):
    inputs=json.loads(inputs_path.read_text("utf-8"));gold=json.loads(gold_path.read_text("utf-8"))
    manifest=json.loads((inputs_path.parent/"manifest.json").read_text("utf-8"))
    protocol=json.loads((run/"protocol.json").read_text("utf-8"))
    if sha256(inputs_path)!=manifest["inputs_sha256"] or sha256(gold_path)!=manifest["gold_sha256"] or protocol["inputs_sha256"]!=manifest["inputs_sha256"]:
        raise ValueError("Frozen data mismatch")
    rows=[json.loads(s) for s in (run/"predictions.jsonl").read_text("utf-8").splitlines()]
    if reference:
        ref=json.loads((reference/"protocol.json").read_text("utf-8"))
        for key in ("inputs_sha256","task_ids","model","temperature","extra_body","repeats","selections_sha256"):
            if ref[key]!=protocol[key]:raise ValueError("Reference experiment mismatch: "+key)
        if set(ref["methods"])&set(protocol["methods"]):raise ValueError("Overlapping reference conditions")
        protocol=dict(protocol,methods=ref["methods"]+protocol["methods"])
        rows.extend(json.loads(s) for s in (reference/"predictions.jsonl").read_text("utf-8").splitlines())
    by_key={(r["task_id"],r["method"],r["repeat"]):r for r in rows}
    if len(by_key)!=len(rows):raise ValueError("Duplicate attempt")
    tasks={t["id"]:t for t in inputs["tasks"]};documents={d["id"]:d for d in inputs["documents"]}
    records=[]
    for method in protocol["methods"]:
        for tid in protocol["task_ids"]:
            for repeat in range(protocol["repeats"]):
                p=by_key.get((tid,method,repeat),dict(task_id=tid,method=method,status="missing",answers=[],reads=[]))
                result=score_prediction(p,tasks[tid],gold["tasks"][tid],documents)
                result.update(repeat=repeat,evidence_character_retention=evidence_retention(p,gold["tasks"][tid],documents),
                    selection_tokens=p.get("selection_tokens"),total_pipeline_tokens=p.get("total_pipeline_tokens"),visible_chars=p.get("visible_chars"))
                records.append(result)
    summaries={};per_task={}
    for method in protocol["methods"]:
        group=[r for r in records if r["method"]==method]
        per_task[method]={tid:mean(r["answer_token_f1"] for r in group if r["task_id"]==tid) for tid in protocol["task_ids"]}
        summary=dict(attempts=len(group),completed=sum(r["status"]=="completed" for r in group))
        for key in ["answer_token_f1","evidence_character_retention","tokens_used","selection_tokens","total_pipeline_tokens","visible_chars","elapsed_seconds"]:
            values=[r[key] for r in group if r[key] is not None]
            summary[key]=mean(values) if values else None;summary[key+"_n"]=len(values)
        summaries[method]=summary
    comparisons={}
    for policy in ("selected","lexical"):
        for cap in (3500,9000):
            a,b=f"{policy}-{cap}",f"prefix-{cap}"
            if a in per_task and b in per_task:
                comparisons[f"{a}_minus_{b}"]=paired_interval([per_task[a][t]-per_task[b][t] for t in protocol["task_ids"]])
    result=dict(summaries=summaries,paired_answer_f1=comparisons,records=records,
        protocol_sha256=sha256(run/"protocol.json"),reference_protocol_sha256=sha256(reference/"protocol.json") if reference else None,
        limitations=["Development fixed-read diagnostic, not end-to-end retrieval or held-out evaluation.",
            "Task-level bootstrap; repeats of a task are averaged, not treated as independent tasks.",
            "Full-read condition has a larger context allowance; not an equal-budget baseline.",
            "Selection cost is included per hypothetical selected-answer pipeline; actual shared experiment spend must be counted separately.",
            "Evidence character overlap is a mechanical proxy, not sufficient support or entailment."])
    (run/"scores.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k!="records"},indent=2))
    return result


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("inputs","gold","run"):parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--reference",type=Path)
    a=parser.parse_args();score(a.inputs,a.gold,a.run,a.reference)
