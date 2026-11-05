"""Post-hoc calibration-only diagnostic; never opens test inputs or evaluation gold.

Run from backend with PYTHONPATH=app and the pilot GPU environment. This does not
change the frozen v1 results, train adapters, or select a deployment checkpoint.
"""
import argparse
from collections import Counter
import gc
import hashlib
import json
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from research_backend.benchmarks import verifier_pilot as pilot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    plan = pilot.check_plan(args.plan_dir)
    rows = pilot.load(args.plan_dir / "calibration-inputs.json")["cases"]
    tokenizer = AutoTokenizer.from_pretrained(plan["model_dir"], local_files_only=True,
                                               trust_remote_code=False)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    torch.set_num_threads(4)
    result = {
        "scope": "post-hoc calibration diagnostic; no test predictions or gold read",
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "plan_sha256": pilot.digest(args.plan_dir / "plan.json"),
        "case_ids": [r["id"] for r in rows],
        "free_generation_selection": "first 12 calibration input rows; no label selection",
        "methods": {},
    }
    for precision in ("nf4", "bf16"):
        model = pilot.load_quantized(plan) if precision == "nf4" else AutoModelForCausalLM.from_pretrained(
            plan["model_dir"], local_files_only=True, trust_remote_code=False,
            use_safetensors=True, torch_dtype=torch.bfloat16, device_map={"": 0},
            attn_implementation="sdpa")
        model.eval()
        model.config.use_cache = True
        predictions = []
        generated = []
        with torch.inference_mode():
            for start in range(0, len(rows), 4):
                group = rows[start:start + 4]
                logits = pilot.last_logits(model, pilot.batch_tokens(tokenizer, group)).float()
                if not torch.isfinite(logits).all():
                    raise ValueError("Nonfinite logits")
                selected = logits[:, plan["label_token_ids"]].argmax(-1).tolist()
                full = logits.argmax(-1).tolist()
                for row, index, token in zip(group, selected, full):
                    predictions.append({"id": row["id"], "verdict": pilot.LABELS[index],
                                        "unrestricted_first_token": tokenizer.decode([token])})
            for row in rows[:12]:
                batch = pilot.batch_tokens(tokenizer, [row])
                output = model.generate(**batch, do_sample=False, max_new_tokens=8,
                                        pad_token_id=tokenizer.pad_token_id)
                generated.append({"id": row["id"], "text": tokenizer.decode(
                    output[0, batch["input_ids"].shape[1]:], skip_special_tokens=True)})
        result["methods"][precision] = {
            "counts": dict(Counter(p["verdict"] for p in predictions)),
            "predictions": predictions, "free_generation": generated,
        }
        print(json.dumps({"precision": precision, "counts": result["methods"][precision]["counts"]}), flush=True)
        del model
        gc.collect()
        torch.cuda.empty_cache()
    with args.out.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
