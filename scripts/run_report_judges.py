"""Checkpoint two gold-blind judge passes; unknown billing stops further calls."""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/"backend/app"))
from research_backend.benchmarks.claim_labels import checked_cases, fingerprint, judge_bundle
from research_backend.benchmarks.trials import digest, load, write_new
from research_backend.config import Settings
from research_backend.providers import ChatModel


async def evaluate(bundle, model, directory, ceiling, model_name):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=False)
    records,calls=[],[]
    known,unknown=0,False
    for index,case in enumerate(checked_cases(bundle)):
        write_new(directory/f"started-{index:03d}.json",dict(id=case["id"],attempt=1))
        if unknown:
            result=dict(records=[dict(id=case["id"],status="failed",review=None,error="prior_unknown_usage")],
                        calls=[],tokens_used=0,known_tokens_used=0)
        else:
            result=await judge_bundle(dict(format=1,cases=[case]),model,max(0,ceiling-known))
        write_new(directory/f"case-{index:03d}.json",result)
        known+=result["known_tokens_used"]
        unknown=unknown or result["tokens_used"] is None
        records.extend(result["records"]);calls.extend(result["calls"])
        print(json.dumps(dict(model=model_name,completed=index+1,total=len(bundle["cases"]),
                              last_status=result["records"][0]["status"],known_tokens=known)),flush=True)
    artifact=dict(format=1,cases_fingerprint=fingerprint(bundle),purpose="candidate judgements, not human truth",
        model=model_name,records=records,calls=calls,tokens_used=None if unknown else known,known_tokens_used=known)
    write_new(directory/"reviews.json",artifact)
    return artifact


async def run(args):
    plan=load(args.plan)
    if plan["runner_sha256"]!=digest(__file__) or plan["cases_sha256"]!=digest(args.cases):
        raise ValueError("Judge code or cases changed after freezing")
    if set(plan["models"])!={"qwen3.5-27b","qwen3.5-397b-a17b"} or plan["max_tokens_per_model"]!=750000:
        raise ValueError("Unexpected frozen judge configuration")
    for relative,expected in plan["judge_dependencies_sha256"].items():
        if digest(REPO/relative)!=expected:raise ValueError("Judge dependency changed")
    from dotenv import load_dotenv
    load_dotenv(args.env_file,override=True);load_dotenv(args.env_file.parent/".env")
    settings=Settings.from_env()
    if not settings.llm_api_key:raise ValueError("Missing existing judge credential")
    bundle=load(args.cases);checked_cases(bundle)
    args.out.mkdir(parents=True,exist_ok=False)
    (args.out/"plan.json").write_bytes(args.plan.read_bytes())
    await asyncio.gather(*(evaluate(bundle,ChatModel(replace(settings,llm_model=name,
        llm_extra_body={"enable_thinking":False})),args.out/name,plan["max_tokens_per_model"],name)
        for name in plan["models"]))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("plan","cases","out","env-file"):
        parser.add_argument("--"+name,type=Path,required=True)
    asyncio.run(run(parser.parse_args()))
