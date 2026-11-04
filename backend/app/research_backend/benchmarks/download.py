"""Download versioned public benchmark inputs; never executes dataset scripts."""
import argparse
import hashlib
import json
from pathlib import Path
import httpx

QASPER_REVISION = "fdc9d8214fbab5dd782958601db4d678e6934a54"
DIGEST_REVISION = "3f8eb69470189d79fb9dba7dd2764a2328b7b0c1"
FILES = {
    "qasper-train-dev-v0.3.tgz": "https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-train-dev-v0.3.tgz",
    "qasper-test-and-evaluator-v0.3.tgz": "https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-test-and-evaluator-v0.3.tgz",
    **{name: f"https://huggingface.co/datasets/blnewman/arxivDIGESTables/resolve/{DIGEST_REVISION}/{name}"
       for name in ("tables.jsonl", "papers.jsonl", "full_texts.jsonl.gz")},
}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(directory, datasets=("qasper", "digestables")):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "downloads.json"
    records = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() else {}
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        for name, url in FILES.items():
            dataset = "qasper" if name.startswith("qasper") else "digestables"
            if dataset not in datasets:
                continue
            target = directory / name
            if target.exists():
                if name not in records or sha256(target) != records[name]["sha256"]:
                    raise ValueError(f"Unverified or modified download: {name}")
                continue
            temporary = target.with_suffix(target.suffix + ".partial")
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with temporary.open("wb") as handle:
                    for block in response.iter_bytes():
                        handle.write(block)
            temporary.replace(target)
            records[name] = dict(url=url, sha256=sha256(target), bytes=target.stat().st_size,
                                 revision="v0.3" if dataset == "qasper" else DIGEST_REVISION)
            manifest_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
            print(f"Downloaded {name}: {target.stat().st_size} bytes", flush=True)
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dataset", choices=["qasper", "digestables", "all"], default="all")
    args = parser.parse_args()
    download(args.out, ("qasper", "digestables") if args.dataset == "all" else (args.dataset,))
