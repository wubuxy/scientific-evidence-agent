"""Fresh-paper scale/precision/verifier comparison. Test gold is scorer-only."""
import argparse
from collections import Counter
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import random
import time

from . import verifier_pilot as v1
from .trials import load, write_new

CONFIG = dict(seeds=[20261106, 20261107, 20261108], steps=128, accumulation=4,
              rank=8, alpha=16, dropout=.05, lr=.0002, weight_decay=.01,
              warmup=8, qwen_max_length=1536, minicheck_max_length=3072,
              test_target=128, batch_size=1, wall_seconds=10800, bootstrap_draws=5000)
MODEL_IDS = dict(small="Qwen/Qwen2.5-1.5B-Instruct", large="Qwen/Qwen2.5-7B-Instruct",
                 minicheck="bespokelabs/Bespoke-MiniCheck-7B")
MINI_SYSTEM = ('Determine whether the provided claim is consistent with the corresponding document. '
               'Consistency in this context implies that all information presented in the claim is '
               'substantiated by the document. If not, it should be considered inconsistent. Please '
               'assess the claim\'s consistency with the document by responding with either "Yes" or "No".')


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def code_files():
    root = Path(__file__).resolve().parents[4]
    names = ["backend/app/research_backend/benchmarks/verifier_scale.py",
             "backend/app/research_backend/benchmarks/verifier_pilot.py",
             "backend/app/research_backend/benchmarks/trials.py",
             "docs/experiments/SCIFACT_VERIFIER_SCALE_PILOT.md",
             "docs/dataset-manifests/verifier-scale-model-sources.json",
             "docs/dataset-manifests/verifier-scale-test-ids.json"]
    return {name: sha(root / name) for name in names}


def model_files(directory):
    directory = Path(directory)
    return {p.name: sha(p) for p in sorted(directory.iterdir()) if p.is_file()
            and p.suffix in {".json", ".safetensors", ".py", ".model", ".txt"}}


def mini_messages(case):
    document = "\n\n".join(e["quote"] for e in case["evidence"])
    return [dict(role="system", content=MINI_SYSTEM),
            dict(role="user", content="Document: " + document + "\nClaim: " + case["claim"])]


def token_ids(tokenizer, case, mini=False):
    return tokenizer.apply_chat_template(mini_messages(case) if mini else v1.messages(case),
                                         add_generation_prompt=True, tokenize=True)


def tokenizer_for(directory, mini=False):
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True,
                                               trust_remote_code=mini, use_fast=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    return tokenizer


def verify_official(directory, spec, mini=False):
    """Mirror weights must match official LFS hashes; custom Python is reviewed/pinned."""
    directory = Path(directory)
    for name, meta in spec["files"].items():
        if not name.endswith(".safetensors") and not (mini and name.endswith(".py")):
            continue
        path = directory / name
        if meta.get("sha256"):
            good = sha(path) == meta["sha256"]
        else:
            content = path.read_bytes()
            good = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest() == meta["git_blob_id"]
        if not good:
            raise ValueError("Official source hash mismatch: " + str(path))


def augment_groups(rows):
    """Also isolate duplicate abstracts with different titles/IDs and text casing."""
    result = []
    for row in rows:
        keys = set(row["group_keys"])
        keys.add("normalized-claim:" + v1.text_hash(row["claim"].lower()))
        for evidence in row["evidence"]:
            keys.add("paper-id:" + str(evidence["source_id"]))
            abstract = " ".join(evidence["quote"].splitlines()[1:])
            keys.add("normalized-abstract:" + v1.text_hash(abstract.lower()))
        result.append(dict(row, group_keys=sorted(keys)))
    return result


def prepare(raw, old_plan_dir, data_root, out):
    root = Path(__file__).resolve().parents[4]
    sources = load(root / "docs/dataset-manifests/verifier-scale-model-sources.json")
    directories = {key: str(Path(data_root) / "models" / model.split("/")[-1]) for key, model in MODEL_IDS.items()}
    fingerprints = {}
    for key, directory in directories.items():
        verify_official(directory, sources["models"][MODEL_IDS[key]], mini=key == "minicheck")
        fingerprints[key] = model_files(directory)
    tokenizers = {key: tokenizer_for(directory, key == "minicheck") for key, directory in directories.items()}
    training = augment_groups(load(Path(old_plan_dir) / "training.json")["cases"])
    calibration = augment_groups(load(Path(old_plan_dir) / "calibration-inputs.json")["cases"])
    calibration_gold = load(Path(old_plan_dir) / "calibration-gold.json")["labels"]
    old_test = augment_groups(load(Path(old_plan_dir) / "test-inputs.json")["cases"])
    data = v1.read_archive(raw)
    corpus = {str(r["doc_id"]): r for r in data["corpus"]}
    candidates = augment_groups(v1.scientific_pairs(data["claims_dev"], corpus, "official-dev"))
    observed = {k for r in old_test + calibration + training for k in r["group_keys"]}
    fresh = [r for group in v1.connected_groups(candidates)
             if not any(k in observed for r in group for k in r["group_keys"]) for r in group]
    excluded = []
    def fits(row):
        sizes = {key: len(token_ids(tok, row, key == "minicheck")) for key, tok in tokenizers.items()}
        good = all(n <= CONFIG["minicheck_max_length" if key == "minicheck" else "qwen_max_length"]
                   for key, n in sizes.items())
        if not good:
            excluded.append(dict(id=row["id"], lengths=sizes))
        return good
    eligible = [r for r in fresh if fits(r)]
    testing, _ = v1.take_groups(v1.ordered_groups(eligible, CONFIG["seeds"][0]), CONFIG["test_target"])
    frozen_ids = load(root / "docs/dataset-manifests/verifier-scale-test-ids.json")["case_ids"]
    if [r["id"] for r in testing] != frozen_ids:
        raise ValueError("Selection differs from the pre-published fresh test IDs")
    if len(testing) < 64 or set(r["label"] for r in testing) != set(v1.LABELS):
        raise ValueError("Insufficient fresh three-class test data")
    if not all(fits(r) for r in calibration + training):
        raise ValueError("Historical training/calibration no longer fits all models")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    write_new(out / "training.json", dict(cases=training))
    for name, rows, labels in [("calibration", calibration, calibration_gold),
                               ("test", testing, {r["id"]: r["label"] for r in testing})]:
        visible = [{k: value for k, value in r.items() if k != "label"} for r in rows]
        write_new(out / (name + "-inputs.json"), dict(cases=visible))
        write_new(out / (name + "-gold.json"), dict(labels=labels))
    challenges = load(Path(old_plan_dir) / "challenge-inputs.json")["cases"]
    write_new(out / "challenge-inputs.json", dict(cases=challenges))
    write_new(out / "challenge-gold.json", load(Path(old_plan_dir) / "challenge-gold.json"))
    partitions = dict(train=training, calibration=calibration, test=testing)
    key_sets = {name: {key for r in rows for key in r["group_keys"]} for name, rows in partitions.items()}
    overlap = {a + "__" + b: len(key_sets[a] & key_sets[b]) for a, b in [("train", "calibration"), ("train", "test"), ("calibration", "test")]}
    if any(overlap.values()) or any(k in observed for r in testing for k in r["group_keys"]):
        raise ValueError("Observed paper/claim leakage")
    plan = dict(format=2, config=CONFIG, model_ids=MODEL_IDS, model_dirs=directories,
                model_sha256=fingerprints, code_sha256=code_files(), raw_sha256=sha(raw),
                old_plan_sha256=sha(Path(old_plan_dir) / "plan.json"),
                old_test_ids=[r["id"] for r in old_test],
                case_ids={name: [r["id"] for r in rows] for name, rows in partitions.items()},
                counts={name: dict(cases=len(rows), groups=len(v1.connected_groups(rows))) for name, rows in partitions.items()},
                fresh_candidate_pairs=len(fresh), eligible_pairs=len(eligible), excluded_length=excluded,
                overlap=overlap, files={p.name: sha(p) for p in out.glob("*.json")})
    write_new(out / "plan.json", plan)
    return plan


def check_plan(directory, scoring=False):
    directory = Path(directory)
    plan = load(directory / "plan.json")
    if plan["config"] != CONFIG or plan["code_sha256"] != code_files():
        raise ValueError("Frozen code/config changed; create a new version")
    for name, expected in plan["files"].items():
        if name.endswith("-gold.json") and not scoring:
            continue
        if sha(directory / name) != expected:
            raise ValueError("Data hash mismatch: " + name)
    for key, directory in plan["model_dirs"].items():
        if model_files(directory) != plan["model_sha256"][key]:
            raise ValueError("Model files changed: " + key)
    return plan


def load_model(plan, key, precision):
    import torch
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig
    kwargs = dict(local_files_only=True, trust_remote_code=key == "minicheck",
                  use_safetensors=True, torch_dtype=torch.bfloat16, device_map={"": 0},
                  attn_implementation="eager" if key == "minicheck" else "sdpa")
    if precision == "nf4":
        kwargs["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(plan["model_dirs"][key], **kwargs)
    model.config.use_cache = False
    return model


def encode_batch(tokenizer, row, mini=False):
    import torch
    ids = token_ids(tokenizer, row, mini)
    maximum = CONFIG["minicheck_max_length" if mini else "qwen_max_length"]
    if len(ids) > maximum:
        raise ValueError("Input exceeds frozen limit")
    return dict(input_ids=torch.tensor([ids], device="cuda"),
                attention_mask=torch.ones((1, len(ids)), dtype=torch.long, device="cuda"))


def infer(model, tokenizer, rows, method, split, output, deadline, mini=False):
    import torch
    model.eval()
    letters = [tokenizer.encode(letter, add_special_tokens=False) for letter in v1.LETTERS] if not mini else []
    if letters and any(len(ids) != 1 for ids in letters):
        raise ValueError("Expected single-token letters")
    for row in rows:
        if time.monotonic() > deadline:
            raise TimeoutError("Frozen wall-time budget")
        torch.cuda.synchronize()
        started = time.monotonic()
        if mini:
            from nltk import sent_tokenize
            parts = [dict(row, claim=sentence) for sentence in sent_tokenize(row["claim"])]
            if not parts:
                raise ValueError("Empty MiniCheck claim")
        else:
            parts = [row]
        token_count, probabilities = 0, []
        with torch.inference_mode():
            for part in parts:
                batch = encode_batch(tokenizer, part, mini)
                token_count += batch["input_ids"].shape[1]
                logits = v1.last_logits(model, batch).float()[0]
                if not torch.isfinite(logits).all():
                    raise ValueError("Nonfinite inference")
                full_probs = logits.softmax(-1)
                if mini:
                    top = logits.topk(5).indices.tolist()
                    probabilities.append(sum(float(full_probs[i]) for i in top if tokenizer.decode([i]).lower() == "yes"))
                else:
                    choice = logits[[ids[0] for ids in letters]]
                    support = float(choice.softmax(-1)[0])
                    verdict = v1.LABELS[int(choice.argmax())]
                    mass = float(full_probs[[ids[0] for ids in letters]].sum())
            if mini:
                support = min(probabilities)
                verdict = "supported" if support > .5 else "unsupported"
                mass = None
        torch.cuda.synchronize()
        record = dict(id=row["id"], method=method, split=split, status="completed",
                      verdict=verdict, support_score=support, choice_mass=mass,
                      input_tokens=token_count, claim_sentences=len(parts), seconds=time.monotonic() - started)
        output.write(json.dumps(record) + "\n")
        output.flush()


def train(model, tokenizer, rows, seed, precision, directory, deadline, steps=None):
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import set_seed
    set_seed(seed)
    if precision == "nf4":
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True,
                                                gradient_checkpointing_kwargs={"use_reentrant": False})
    else:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model = get_peft_model(model, LoraConfig(r=CONFIG["rank"], lora_alpha=CONFIG["alpha"],
        lora_dropout=CONFIG["dropout"], target_modules=["q_proj", "v_proj"], task_type="CAUSAL_LM"))
    parameters = [p for p in model.parameters() if p.requires_grad]
    if not parameters or any("lora_" not in name for name, p in model.named_parameters() if p.requires_grad):
        raise ValueError("Only adapters may be trained")
    probe = next(p for name, p in model.named_parameters() if p.requires_grad and "lora_B" in name)
    initial_probe = probe.detach().clone()
    optimizer = torch.optim.AdamW(parameters, lr=CONFIG["lr"], weight_decay=CONFIG["weight_decay"])
    order = list(range(len(rows)))
    rng = random.Random(seed)
    rng.shuffle(order)
    cursor, losses = 0, []
    total = steps or CONFIG["steps"]
    target_ids = [tokenizer.encode(letter, add_special_tokens=False)[0] for letter in v1.LETTERS]
    torch.cuda.reset_peak_memory_stats()
    started = time.monotonic()
    model.train()
    for step in range(total):
        if time.monotonic() > deadline:
            raise TimeoutError("Frozen wall-time budget")
        optimizer.zero_grad(set_to_none=True)
        scale = min((step + 1) / CONFIG["warmup"], max(0, (total - step) / max(1, total - CONFIG["warmup"])))
        for group in optimizer.param_groups:
            group["lr"] = CONFIG["lr"] * scale
        value = 0
        for _ in range(CONFIG["accumulation"]):
            if cursor == len(order):
                rng.shuffle(order)
                cursor = 0
            row = rows[order[cursor]]
            cursor += 1
            logits = v1.last_logits(model, encode_batch(tokenizer, row)).float()
            target = torch.tensor([target_ids[v1.LABELS.index(row["label"])]], device="cuda")
            loss = torch.nn.functional.cross_entropy(logits, target)
            if not torch.isfinite(loss):
                raise ValueError("Nonfinite loss")
            (loss / CONFIG["accumulation"]).backward()
            value += float(loss.detach()) / CONFIG["accumulation"]
        torch.nn.utils.clip_grad_norm_(parameters, 1, error_if_nonfinite=True)
        optimizer.step()
        losses.append(value)
        if (step + 1) % 8 == 0:
            print(json.dumps(dict(seed=seed, step=step + 1, loss=value,
                                  elapsed=time.monotonic() - started)), flush=True)
    adapter_delta = float((probe.detach() - initial_probe).abs().max())
    if not math.isfinite(adapter_delta) or adapter_delta <= 0:
        raise ValueError("Adapter did not update")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    model.save_pretrained(directory, safe_serialization=True)
    info = dict(seed=seed, precision=precision, steps=total, examples_seen=total * CONFIG["accumulation"],
                trainable_parameters=sum(p.numel() for p in parameters), losses=losses,
                adapter_max_delta=adapter_delta,
                seconds=time.monotonic() - started, peak_allocated_mib=torch.cuda.max_memory_allocated() / 2**20,
                peak_reserved_mib=torch.cuda.max_memory_reserved() / 2**20,
                adapter_sha256={p.name: sha(p) for p in directory.iterdir() if p.is_file()})
    write_new(directory / "training.json", info)
    return model


def preflight(plan_dir, out):
    import torch
    from peft import PeftModel
    torch.set_num_threads(4)
    plan = check_plan(plan_dir)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    rows = load(Path(plan_dir) / "calibration-inputs.json")["cases"]
    training = load(Path(plan_dir) / "training.json")["cases"]
    deadline = time.monotonic() + CONFIG["wall_seconds"]
    errors, precision = [], None
    for candidate in ("bf16", "nf4"):
        model = None
        try:
            tokenizer = tokenizer_for(plan["model_dirs"]["large"])
            # Stress the longest training input; only training labels are supervised.
            worst = max(training, key=lambda r: len(token_ids(tokenizer, r)))
            model = load_model(plan, "large", candidate)
            model = train(model, tokenizer, [worst], CONFIG["seeds"][0], candidate,
                          out / (candidate + "-smoke"), deadline, steps=2)
            del model
            model = None
            gc.collect()
            torch.cuda.empty_cache()
            base = load_model(plan, "large", candidate)
            model = PeftModel.from_pretrained(base, out / (candidate + "-smoke"), is_trainable=False)
            with (out / (candidate + "-smoke.jsonl")).open("x") as output:
                infer(model, tokenizer, rows[:3], "smoke", "calibration", output, deadline)
            precision = candidate
            break
        except torch.OutOfMemoryError as error:
            errors.append(dict(precision=candidate, error=type(error).__name__, message=str(error)[:500]))
        finally:
            if model is not None:
                del model
            if "base" in locals():
                del base
            gc.collect()
            torch.cuda.empty_cache()
    if precision is None:
        write_new(out / "preflight.json", dict(complete=False, errors=errors))
        raise RuntimeError("Neither frozen training precision fits")
    with (out / "calibration-predictions.jsonl").open("x") as output:
        for key, dtype in [("small", "bf16"), ("small", "nf4"), ("large", "bf16"), ("large", "nf4"), ("minicheck", "bf16")]:
            model = load_model(plan, key, dtype)
            tokenizer = tokenizer_for(plan["model_dirs"][key], key == "minicheck")
            infer(model, tokenizer, rows, key + "-" + dtype, "calibration", output, deadline, key == "minicheck")
            del model
            gc.collect()
            torch.cuda.empty_cache()
    result = dict(complete=True, selected_training_precision=precision, errors=errors,
                  plan_sha256=sha(Path(plan_dir) / "plan.json"),
                  predictions_sha256=sha(out / "calibration-predictions.jsonl"))
    write_new(out / "preflight.json", result)
    return result


def run(plan_dir, preflight_path, out):
    import importlib.metadata
    import torch
    from peft import PeftModel
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    torch.set_num_threads(4)
    plan = check_plan(plan_dir)
    hardware = load(preflight_path)
    if not hardware["complete"] or hardware["plan_sha256"] != sha(Path(plan_dir) / "plan.json"):
        raise ValueError("Matching completed preflight required")
    precision = hardware["selected_training_precision"]
    if precision not in {"bf16", "nf4"}:
        raise ValueError("Invalid training precision")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    deadline = time.monotonic() + CONFIG["wall_seconds"]
    methods = [("small-bf16", "small", "bf16", None), ("small-nf4", "small", "nf4", None),
               ("large-bf16", "large", "bf16", None), ("large-nf4", "large", "nf4", None)]
    methods += [("large-lora-" + str(seed), "large", precision, seed) for seed in CONFIG["seeds"]]
    methods += [("minicheck-bf16", "minicheck", "bf16", None)]
    write_new(out / "protocol.json", dict(plan_sha256=sha(Path(plan_dir) / "plan.json"),
        preflight_sha256=sha(preflight_path), training_precision=precision, methods=[m[0] for m in methods],
        gpu=torch.cuda.get_device_name(), runtime_cuda=torch.version.cuda,
        packages={p: importlib.metadata.version(p) for p in ["torch", "transformers", "peft", "bitsandbytes"]}))
    data = {name: load(Path(plan_dir) / (name + "-inputs.json"))["cases"] for name in ["calibration", "test", "challenge"]}
    training = load(Path(plan_dir) / "training.json")["cases"]
    errors = []
    with (out / "predictions.jsonl").open("x") as output:
        for method, key, dtype, seed in methods:
            model = None
            try:
                if time.monotonic() > deadline:
                    raise TimeoutError("Frozen wall-time budget")
                print(json.dumps(dict(method=method, event="started")), flush=True)
                model = load_model(plan, key, dtype)
                tokenizer = tokenizer_for(plan["model_dirs"][key], key == "minicheck")
                if seed is not None:
                    model = train(model, tokenizer, training, seed, dtype, out / method, deadline)
                    del model
                    model = None
                    gc.collect()
                    torch.cuda.empty_cache()
                    base = load_model(plan, key, dtype)
                    model = PeftModel.from_pretrained(base, out / method, is_trainable=False)
                for split, rows in data.items():
                    infer(model, tokenizer, rows, method, split, output, deadline, key == "minicheck")
                empty = [dict(r, evidence=[]) for r in data["test"][:32]]
                infer(model, tokenizer, empty, method, "empty-evidence", output, deadline, key == "minicheck")
                print(json.dumps(dict(method=method, event="completed")), flush=True)
            except Exception as error:
                errors.append(dict(method=method, error=type(error).__name__, message=str(error)[:1000]))
                print(json.dumps(errors[-1]), flush=True)
            finally:
                if model is not None:
                    del model
                if "base" in locals():
                    del base
                gc.collect()
                torch.cuda.empty_cache()
    result = dict(complete=not errors, errors=errors)
    write_new(out / "run-summary.json", result)
    return result


def binary_metrics(gold, records):
    positives = sum(v == "supported" for v in gold.values())
    negatives = len(gold) - positives
    tp = sum(v == "supported" and records.get(i, {}).get("verdict") == "supported" for i, v in gold.items())
    fp = sum(v != "supported" and records.get(i, {}).get("verdict") == "supported" for i, v in gold.items())
    tn = sum(v != "supported" and records.get(i, {}).get("verdict") in {"contradicted", "insufficient", "unsupported"} for i, v in gold.items())
    precision = tp / (tp + fp) if tp + fp else None
    complete = set(records) == set(gold) and all(r.get("status") == "completed" for r in records.values())
    auc = None
    if complete and positives and negatives:
        ordered = sorted((records[i]["support_score"], gold[i] == "supported") for i in gold)
        rank_sum, start = 0, 0
        while start < len(ordered):
            end = start + 1
            while end < len(ordered) and ordered[end][0] == ordered[start][0]:
                end += 1
            rank_sum += sum(p for _, p in ordered[start:end]) * (start + 1 + end) / 2
            start = end
        auc = (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)
    return dict(cases=len(gold), supported_precision=precision,
                supported_recall=tp / positives if positives else None,
                unsupported_false_supported_rate=fp / negatives if negatives else None,
                balanced_accuracy=(tp / positives + tn / negatives) / 2 if positives and negatives else None,
                accuracy=(tp + tn) / len(gold), auroc=auc,
                missing_predictions=sum(i not in records or records[i].get("status") != "completed" for i in gold))


def score(plan_dir, run_dir):
    plan = check_plan(plan_dir, scoring=True)
    run_dir = Path(run_dir)
    protocol = load(run_dir / "protocol.json")
    if protocol["plan_sha256"] != sha(Path(plan_dir) / "plan.json"):
        raise ValueError("Run plan mismatch")
    expected_methods = ["small-bf16", "small-nf4", "large-bf16", "large-nf4"]
    expected_methods += ["large-lora-" + str(seed) for seed in CONFIG["seeds"]]
    expected_methods += ["minicheck-bf16"]
    if protocol["methods"] != expected_methods or protocol["training_precision"] not in {"bf16", "nf4"}:
        raise ValueError("Expected all frozen methods and valid precision")
    records = [json.loads(line) for line in (run_dir / "predictions.jsonl").read_text().splitlines()]
    keyed = {(r["method"], r["split"], r["id"]): r for r in records}
    if len(keyed) != len(records):
        raise ValueError("Duplicate prediction")
    results = {m: {} for m in protocol["methods"]}
    allowed = set()
    for split in ["calibration", "test", "challenge", "empty-evidence"]:
        inputs = load(Path(plan_dir) / (("test" if split == "empty-evidence" else split) + "-inputs.json"))["cases"]
        if split == "empty-evidence":
            gold = {r["id"]: "insufficient" for r in inputs[:32]}
        else:
            gold = load(Path(plan_dir) / (split + "-gold.json"))["labels"]
        for method in protocol["methods"]:
            allowed.update((method, split, i) for i in gold)
            rows = {i: keyed[method, split, i] for i in gold if (method, split, i) in keyed}
            visible = {i: r for i, r in rows.items() if r["status"] == "completed"}
            if any(not math.isfinite(r["support_score"]) or not 0 <= r["support_score"] <= 1 for r in visible.values()):
                raise ValueError("Invalid score")
            value = dict(binary=binary_metrics(gold, visible))
            if method != "minicheck-bf16":
                if any(r["verdict"] not in v1.LABELS for r in visible.values()):
                    raise ValueError("Invalid three-class verdict")
                value["three_class"] = v1.classification_metrics(gold, {i: r["verdict"] for i, r in visible.items()})
            else:
                if any(r["verdict"] not in {"supported", "unsupported"} for r in visible.values()):
                    raise ValueError("Invalid MiniCheck verdict")
            results[method][split] = value
    if set(keyed) - allowed:
        raise ValueError("Unexpected method/split/ID")
    testing = load(Path(plan_dir) / "test-inputs.json")["cases"]
    gold = load(Path(plan_dir) / "test-gold.json")["labels"]
    groups = v1.connected_groups(testing)
    loras = ["large-lora-" + str(seed) for seed in CONFIG["seeds"]]
    reference = "large-" + protocol["training_precision"]
    def contrasts(scores):
        return dict(scale=scores["large-bf16"] - scores["small-bf16"],
                    quantization=scores["large-nf4"] - scores["large-bf16"],
                    training=sum(scores[m] for m in loras) / len(loras) - scores[reference])
    points = contrasts({m: results[m]["test"]["three_class"]["macro_f1"] for m in results if m != "minicheck-bf16"})
    rng, draws = random.Random(CONFIG["seeds"][0]), {name: [] for name in points}
    for _ in range(CONFIG["bootstrap_draws"]):
        sampled = [r for group in rng.choices(groups, k=len(groups)) for r in group]
        truth = {str(i): gold[r["id"]] for i, r in enumerate(sampled)}
        scores = {}
        for method in results:
            if method == "minicheck-bf16":
                continue
            pred = {str(i): (lambda p: p.get("verdict") if p.get("status") == "completed" else None)(
                keyed.get((method, "test", r["id"]), {})) for i, r in enumerate(sampled)}
            scores[method] = v1.classification_metrics(truth, pred)["macro_f1"]
        for name, delta in contrasts(scores).items():
            draws[name].append(delta)
    intervals = {}
    for name, values in draws.items():
        values.sort()
        n = len(values)
        intervals[name] = dict(delta=points[name], pointwise_95=[values[int(n * .025)], values[int(n * .975)]],
                               bonferroni_98_333=[values[int(n * .05 / 6)], values[min(n - 1, int(n * (1 - .05 / 6)))]])
    trained = [results[m]["test"]["three_class"] for m in loras]
    absolute = (all(r["per_class"]["contradicted"]["recall"] >= .5 for r in trained)
                and sum(r["macro_f1"] for r in trained) / 3 >= .65
                and sum(r["unsupported_false_supported_rate"] for r in trained) / 3 <= .2
                and sum(r["per_class"]["supported"]["precision"] or 0 for r in trained) / 3 >= .8)
    paired_complete = all(results[m]["test"]["three_class"]["missing_predictions"] == 0
                          for m in [reference] + loras)
    train_counts = Counter(r["label"] for r in load(Path(plan_dir) / "training.json")["cases"])
    majority = max(v1.LABELS, key=lambda label: train_counts[label])
    majority_predictions = {i: majority for i in gold}
    return dict(results=results, primary_contrasts=intervals, independent_test_groups=len(groups),
                majority_baseline=dict(label_from_training=majority,
                    test_three_class=v1.classification_metrics(gold, majority_predictions)),
                training_comparison_complete=paired_complete,
                absolute_candidate_gate=absolute,
                training_success_gate=bool(paired_complete and absolute and points["training"] >= .03
                                          and intervals["training"]["bonferroni_98_333"][0] > 0),
                plan_sha256=sha(Path(plan_dir) / "plan.json"), predictions_sha256=sha(run_dir / "predictions.jsonl"),
                limitations=["Public SciFact dev; no assertion of absence from pretraining.",
                             "Fresh-paper verifier comparison, not full Agent quality.",
                             "MiniCheck binary output is not scored as a three-class prediction."])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    for flag in ["raw", "old-plan-dir", "data-root", "out"]:
        p.add_argument("--" + flag, type=Path, required=True)
    for name in ["preflight", "run", "score"]:
        p = sub.add_parser(name)
        p.add_argument("--plan-dir", type=Path, required=True)
        p.add_argument("--out", type=Path, required=True)
        if name == "run":
            p.add_argument("--preflight", type=Path, required=True)
        if name == "score":
            p.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.raw, args.old_plan_dir, args.data_root, args.out)
    elif args.command == "preflight":
        result = preflight(args.plan_dir, args.out)
    elif args.command == "run":
        result = run(args.plan_dir, args.preflight, args.out)
    else:
        result = score(args.plan_dir, args.run)
        write_new(args.out, result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
