"""Build self-contained human-only HTML and separate A/B distribution archives."""
import argparse
import json
from pathlib import Path
import zipfile

REPO=Path(__file__).resolve().parents[1]


def human_view(human):
    # Keep a strict projection even if coordinator fields enter an input file.
    return dict(cases=[dict(id=c["id"],question=c["question"],claim=c["claim"],
        evidence=[{k:e[k] for k in ("id","source_id","start_line","end_line","quote")} for e in c["evidence"]])
        for c in human["cases"]])


def page(human, labels, slot):
    clean=human_view(human)
    payload=json.dumps(dict(human=clean,labels=labels,slot=slot),ensure_ascii=False)
    payload=payload.replace("&","\\u0026").replace("<","\\u003c").replace(">","\\u003e")
    return (REPO/"scripts/annotation_page.html").read_text(encoding="utf-8").replace("__PACKET_JSON__",payload)


def package(directory):
    directory=Path(directory)
    human=json.loads((directory/"human-cases.json").read_text(encoding="utf-8"))
    if not human["cases"]:raise ValueError("No natural report cases available")
    guide=(REPO/"docs/HUMAN_REVIEW_PROTOCOL.md").read_bytes()
    (directory/"instructions.md").write_bytes(guide)
    for slot in ("annotator-a","annotator-b"):
        labels=json.loads((directory/(slot+".json")).read_text(encoding="utf-8"))
        if any(r["verdict"] is not None or r.get("rationale") is not None or r["evidence_spans"] for r in labels["labels"]):
            raise ValueError("Distribution must begin with blank independent labels")
        content=page(human,labels,slot)
        (directory/(slot+".html")).write_text(content,encoding="utf-8")
        with zipfile.ZipFile(directory/(slot+".zip"),"x",zipfile.ZIP_DEFLATED) as z:
            z.writestr("start.html",content.encode("utf-8"))
            z.writestr("human-cases.json",json.dumps(human_view(human),ensure_ascii=False,indent=2).encode("utf-8"))
            for name in (slot+".json","instructions.md"):
                z.write(directory/name,name)
    return dict(cases=len(human["cases"]),human_labels=0,packets=["annotator-a.zip","annotator-b.zip"])


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--directory",type=Path,required=True)
    print(json.dumps(package(parser.parse_args().directory)))
