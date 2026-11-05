"""Gold-blind joint-source judging and explicit two-annotator calibration."""
import argparse
import asyncio
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path

from ..config import Settings, load_environment
from ..providers import ChatModel, decode_tool_arguments
from .measurement import MeasuredModel
from .support_review import SYSTEM, Review, resolve_review
from .trials import digest, load, write_new


VERDICTS = ("supported", "contradicted", "insufficient")


def fingerprint(bundle):
    return hashlib.sha256(json.dumps(bundle,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def checked_cases(bundle):
    cases = bundle["cases"]
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Duplicate review case")
    for case in cases:
        evidence = case["evidence"]
        if len({e["id"] for e in evidence}) != len(evidence):
            raise ValueError("Duplicate review evidence")
        for item in evidence:
            if (item["source_id"] not in case["source_ids"] or type(item["start_line"]) is not int
                    or type(item["end_line"]) is not int or not 1 <= item["start_line"] <= item["end_line"]
                    or len(item["quote"].splitlines()) != item["end_line"]-item["start_line"]+1):
                raise ValueError("Invalid joint evidence mapping")
    return cases


def joint_messages(case):
    # Projection deliberately excludes human labels, earlier reviews and metrics.
    payload = dict(question=case["question"],claim=case["claim"],evidence=[dict(
        evidence_id=e["id"],source_id=e["source_id"],numbered_text="\n".join(
            f"{e['start_line']+i}: {line}" for i,line in enumerate(e["quote"].splitlines()))) for e in case["evidence"]])
    return [dict(role="system",content=SYSTEM + "\nConsider all supplied sources jointly. Partial support from one source does not establish the entire claim."),
            dict(role="user",content=json.dumps(payload,ensure_ascii=False))]


async def judge_bundle(bundle, model, ceiling):
    cases = checked_cases(bundle)
    measured, records = MeasuredModel(model,ceiling,preflight=True), []
    definition = dict(type="function",function=dict(name="review_claim",description="Review joint evidence support",
                                                   parameters=Review.model_json_schema()))
    for case in cases:
        record = dict(id=case["id"],status="failed",review=None,error=None)
        if not case["citation_contract_valid"]:
            record["error"] = "invalid_citation_contract"
        else:
            try:
                response = await measured.complete(joint_messages(case),[definition],700)
                calls = response["message"].get("tool_calls",[])
                if len(calls)!=1 or calls[0]["function"]["name"]!="review_claim":
                    raise ValueError("Expected one review_claim")
                payload,_ = decode_tool_arguments(calls[0],[definition])
                record.update(status="completed",review=resolve_review(payload,case))
            except Exception as exc:
                record["error"] = type(exc).__name__
        records.append(record)
    return dict(format=1,cases_fingerprint=fingerprint(bundle),purpose="candidate judgements, not human truth",
        records=records,calls=measured.calls,tokens_used=measured.tokens,known_tokens_used=measured.known_tokens)


def validated_reviews(bundle, path):
    artifact = load(path)
    cases = {c["id"]:c for c in checked_cases(bundle)}
    if artifact["cases_fingerprint"] != fingerprint(bundle):
        raise ValueError("Reviews do not match the exact evidence/claims bundle")
    records = artifact["records"]
    keyed = {r["id"]:r for r in records}
    if len(keyed)!=len(records) or set(keyed)-set(cases):
        raise ValueError("Duplicate or unexpected review")
    for identifier,record in keyed.items():
        if record["status"]!="completed":
            continue
        if not cases[identifier]["citation_contract_valid"]:
            raise ValueError("Completed review has invalid citations")
        review = record["review"]
        payload = {k:review[k] for k in ("verdict","rationale")}
        payload["evidence_spans"] = [{k:s[k] for k in ("evidence_id","start_line","end_line")} for s in review["evidence_spans"]]
        canonical = resolve_review(payload,cases[identifier])
        if canonical != review:
            raise ValueError("Review spans differ from the supplied canonical evidence")
    return keyed


def labels(path,cases_path,identifiers,partial=False):
    artifact = load(path)
    if artifact["cases_sha256"] != digest(cases_path):
        raise ValueError("Annotation belongs to a different cases file")
    if artifact["annotation_source"] not in {"human","synthetic_fixture"} or not artifact.get("annotator","").strip():
        raise ValueError("Explicit annotation source and annotator are required")
    rows = artifact["labels"]
    by_id = {r["id"]:r["verdict"] for r in rows}
    if len(by_id)!=len(rows) or set(by_id)!=set(identifiers):
        raise ValueError("Annotation must contain every case exactly once")
    if any(v not in VERDICTS and not(partial and v is None) for v in by_id.values()):
        raise ValueError("Incomplete or invalid annotation; blank templates are not truth")
    return artifact,by_id


def calibrate(cases_path,reviews_path,a_path,b_path,adjudication_path):
    bundle = load(cases_path)
    identifiers = [c["id"] for c in checked_cases(bundle)]
    if not identifiers:
        raise ValueError("Calibration requires annotated cases")
    aa,a = labels(a_path,cases_path,identifiers)
    bb,b = labels(b_path,cases_path,identifiers)
    jj,adjudication = labels(adjudication_path,cases_path,identifiers,partial=True)
    if len({aa["annotation_source"],bb["annotation_source"],jj["annotation_source"]})!=1 or aa["annotator"]==bb["annotator"]:
        raise ValueError("Independent annotators with one explicit data origin are required")
    gold = {}
    for identifier in identifiers:
        if a[identifier]!=b[identifier] and adjudication[identifier] is None:
            raise ValueError("Every annotator disagreement requires adjudication")
        if a[identifier]==b[identifier] and adjudication[identifier] not in {None,a[identifier]}:
            raise ValueError("Adjudication conflicts with an agreed label")
        gold[identifier] = a[identifier] if a[identifier]==b[identifier] else adjudication[identifier]
    n = len(identifiers)
    agreement = sum(a[i]==b[i] for i in identifiers)/n
    expected = sum(sum(v==label for v in a.values())*sum(v==label for v in b.values()) for label in VERDICTS)/(n*n)
    kappa = (agreement-expected)/(1-expected) if expected<1 else None
    reviews = validated_reviews(bundle,reviews_path)
    predictions = {i:reviews[i]["review"]["verdict"] if i in reviews and reviews[i]["status"]=="completed" else None for i in identifiers}
    per_class = {}
    for label in VERDICTS:
        tp = sum(gold[i]==label and predictions[i]==label for i in identifiers)
        fp = sum(gold[i]!=label and predictions[i]==label for i in identifiers)
        fn = sum(gold[i]==label and predictions[i]!=label for i in identifiers)
        per_class[label] = dict(precision=tp/(tp+fp) if tp+fp else None,recall=tp/(tp+fn) if tp+fn else None,
                               f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0,support=sum(v==label for v in gold.values()))
    non_supported = sum(v!="supported" for v in gold.values())
    return dict(annotation_source=aa["annotation_source"],cases=len(identifiers),
        cases_sha256=digest(cases_path),reviews_sha256=digest(reviews_path),
        annotation_sha256={name:digest(path) for name,path in [("a",a_path),("b",b_path),("adjudication",adjudication_path)]},
        inter_annotator_agreement=agreement,cohen_kappa=kappa,adjudicated_disagreements=sum(a[i]!=b[i] for i in identifiers),
        judged_coverage=sum(v is not None for v in predictions.values())/n,
        accuracy_all_cases=sum(predictions[i]==gold[i] for i in identifiers)/n,
        macro_f1=sum(r["f1"] for r in per_class.values())/len(VERDICTS),per_class=per_class,
        unsupported_false_supported_rate=sum(gold[i]!="supported" and predictions[i]=="supported" for i in identifiers)/non_supported if non_supported else None,
        confusion={label:dict(Counter(predictions[i] or "missing" for i in identifiers if gold[i]==label)) for label in VERDICTS},
        note="Synthetic fixture results are software checks. Human calibration applies to this distribution, not all future reports.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command",required=True)
    judge = sub.add_parser("judge")
    judge.add_argument("--cases",type=Path,required=True)
    judge.add_argument("--out",type=Path,required=True)
    judge.add_argument("--env-file",type=Path)
    judge.add_argument("--model",default="qwen3.5-27b")
    judge.add_argument("--max-tokens",type=int,default=20000)
    calibration = sub.add_parser("calibrate")
    for name in ("cases","reviews","labels-a","labels-b","adjudication","out"):
        calibration.add_argument("--"+name,type=Path,required=True)
    args = parser.parse_args()
    if args.command=="judge":
        if args.out.exists():
            raise FileExistsError(args.out)
        if not 2000<=args.max_tokens<=1000000:
            raise ValueError("Judge token ceiling must be between 2000 and 1000000")
        if args.env_file:
            from dotenv import load_dotenv
            load_dotenv(args.env_file,override=True)
        load_environment()
        settings = replace(Settings.from_env(),llm_model=args.model,llm_extra_body={"enable_thinking":False})
        if not settings.llm_api_key:
            raise ValueError("Missing model credential")
        result = asyncio.run(judge_bundle(load(args.cases),ChatModel(settings),args.max_tokens))
        result.update(model=args.model,extra_body=settings.llm_extra_body,cases_sha256=digest(args.cases),
                      code_sha256=digest(__file__))
    else:
        result = calibrate(args.cases,args.reviews,args.labels_a,args.labels_b,args.adjudication)
    write_new(args.out,result)
    print(json.dumps({k:v for k,v in result.items() if k not in {"calls","records","confusion","per_class","annotation_sha256"}}))


if __name__ == "__main__":
    main()
