"""Post-hoc format recovery: retain full rationale, never edit verdicts or evidence."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/"backend/app"))
from pydantic import ValidationError
from research_backend.benchmarks.claim_labels import checked_cases, validated_reviews
from research_backend.benchmarks.support_review import Review,resolve_review
from research_backend.benchmarks.trials import digest,load,write_new
from research_backend.providers import decode_tool_arguments


def recover(payload,case):
    try:
        Review.model_validate(payload)
    except ValidationError as exc:
        errors=exc.errors()
        if not errors or any(e["loc"] != ("rationale",) or e["type"] != "string_too_long" for e in errors):
            raise ValueError("Not an isolated rationale length violation") from exc
    else:
        raise ValueError("No rationale length failure to recover")
    # All remaining schema, evidence identity and canonical line checks still apply.
    return resolve_review(payload,case,allow_long_rationale=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("cases","judge","out"):
        parser.add_argument("--"+name,type=Path,required=True)
    args=parser.parse_args()
    bundle=load(args.cases);cases=checked_cases(bundle)
    original=args.judge/"reviews.json"
    validated_reviews(bundle,original)
    artifact=deepcopy(load(original));recovered=[];unchanged=[]
    by_id={r["id"]:r for r in artifact["records"]}
    for i,case in enumerate(cases):
        row=by_id[case["id"]]
        if row["status"]=="completed":continue
        checkpoint=load(args.judge/f"case-{i:03d}.json")
        try:
            if row["error"]!="ValidationError" or len(checkpoint["calls"])!=1:
                raise ValueError("No isolated response to recover")
            tc=checkpoint["calls"][0]["assistant"]["tool_calls"]
            if len(tc)!=1 or tc[0]["function"]["name"]!="review_claim":raise ValueError("Unexpected tool")
            definition=dict(type="function",function=dict(name="review_claim",parameters=Review.model_json_schema()))
            payload,_=decode_tool_arguments(tc[0],[definition])
            review=recover(payload,case)
            assert review["rationale"]==payload["rationale"] and review["verdict"]==payload["verdict"]
            row.update(status="completed",review=review,error=None,original_error="ValidationError")
            recovered.append(dict(id=case["id"],rationale_characters=len(review["rationale"])))
        except (ValueError,KeyError,TypeError,IndexError):
            unchanged.append(case["id"])
    artifact.update(rationale_policy="offline-retain-4000",format_recovery=dict(
        source_reviews_sha256=digest(original),script_sha256=digest(__file__),post_hoc=True,
        new_model_calls=0,recovered=recovered,unchanged_failed_ids=unchanged,
        rule="Only an isolated 800-character rationale violation; retain full text up to 4000, preserve verdict and validate exact cited lines"))
    write_new(args.out,artifact)
    validated_reviews(bundle,args.out)
    print(json.dumps(dict(recovered=len(recovered),remaining_failures=len(unchanged),new_model_calls=0)))


if __name__=="__main__":main()
