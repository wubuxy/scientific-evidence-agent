"""Exercise actual evaluation CLIs against a loopback fake model, without secrets."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/"backend/app"))
from research_backend.benchmarks.prepare import freeze
from research_backend.benchmarks.trials import digest, load, write_new


class Handler(BaseHTTPRequestHandler):
    calls = 0

    def log_message(self,*args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).calls += 1
        names = {d["function"]["name"] for d in payload["tools"]}
        observations = [json.loads(m["content"]) for m in payload["messages"] if m["role"]=="tool"]
        if names=={"review_claim"}:
            case = json.loads(payload["messages"][-1]["content"])
            evidence = case["evidence"][0]
            line = int(evidence["numbered_text"].split(":",1)[0])
            name,arguments = "review_claim",dict(verdict="supported",rationale="Synthetic endpoint fixture only",
                evidence_spans=[dict(evidence_id=evidence["evidence_id"],start_line=line,end_line=line)])
        elif names=={"review_answer"}:
            name,arguments = "review_answer",dict(support="insufficient",adequacy="partial",role="aligned",stage="aligned",
                gap="Missing final value",grep_query="final value",anchor_evidence_id="")
        elif names=={"propose_search"}:
            name,arguments = "propose_search",dict(grep_query="final value")
        elif "plan_research" in names:
            if not observations:
                sources = json.loads(payload["messages"][1]["content"].split("当前资料清单（元数据，不是证据）：",1)[1])["sources"]
                name,arguments = "plan_research",dict(source_ids=[s["id"] for s in sources],fields=["value"])
            else:
                reads = [o["data"] for o in observations if o.get("data",{}).get("quote")]
                if not reads:
                    name,arguments = "sources_read",dict(source_id=observations[-1]["data"]["source_ids"][0],start_line=45,line_count=1)
                else:
                    evidence = reads[-1]
                    name,arguments = "finish_report",dict(title="Fixture report",
                        claims=[dict(text="Final value is beta",evidence_ids=[evidence["id"]])],
                        comparison_rows=[dict(source_id=evidence["source_id"],method="fixture",attributes={"value":"beta"},evidence_ids=[evidence["id"]])],
                        limitations=["Synthetic fixture, not real model quality"])
        else:
            question = json.loads(payload["messages"][-1 if names=={"finish_report"} else 1]["content"])
            reads = question.get("evidence",[]) if names=={"finish_report"} else [o["data"] for o in observations if o.get("data",{}).get("quote")]
            if not reads:
                name,arguments = "sources_read",dict(source_id=question["sources"][0]["id"],start_line=1,line_count=1)
            else:
                evidence = reads[-1]
                name,arguments = "finish_report",dict(answers=[dict(target_id="answer",answer="beta" if "final value beta" in evidence["quote"] else "alpha",
                    unanswerable=False,evidence_ids=[evidence["id"]])])
        response = dict(id="fixture",object="chat.completion",created=1,model="offline-fixture",
            choices=[dict(index=0,finish_reason="tool_calls",message=dict(role="assistant",content=None,
                tool_calls=[dict(id="c",type="function",function=dict(name=name,arguments=json.dumps(arguments)))]))],
            usage=dict(prompt_tokens=200,completion_tokens=100,total_tokens=300))
        encoded = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def smoke(root):
    root = Path(root).resolve()
    root.mkdir(parents=True,exist_ok=False)
    task = dict(id="fixture-task",question="Which final value?",source_ids=["fixture-paper"],
        targets=[dict(id="answer",source_id="fixture-paper",field="value")])
    document = dict(id="fixture-paper",title="Synthetic fixture paper",
        text="alpha\n"+"\n".join(["filler"]*43)+"\nfinal value beta",paragraphs=[dict(id="p",start_line=45,end_line=45)])
    gold = dict(targets=[dict(id="answer",source_id="fixture-paper",references=[dict(answer="beta",unanswerable=False,evidence_paragraph_ids=["p"])])])
    freeze(root/"dataset","synthetic-software-check-not-real-accuracy","dev",[(task,gold)],{document["id"]:document},1,1,{}, {})
    write_new(root/"task_ids.json",[task["id"]])
    server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
    Handler.calls = 0
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    environment = dict(os.environ,PYTHONPATH=str(REPO/"backend/app"),PYTHONUTF8="1",NO_PROXY="127.0.0.1,localhost")
    env_file = root/".env.research"
    env_file.write_text(f"RESEARCH_LLM_API_KEY=offline-fixture\nRESEARCH_LLM_BASE_URL=http://127.0.0.1:{server.server_port}/v1\n",encoding="utf-8")
    def command(module,*arguments):
        result = subprocess.run([sys.executable,"-m","research_backend.benchmarks."+module,*map(str,arguments)],
            cwd=REPO/"backend",env=environment,capture_output=True,text=True,encoding="utf-8",timeout=120)
        with (root/"commands.log").open("a",encoding="utf-8") as log:
            log.write(result.stdout+result.stderr)
        if result.returncode:
            raise RuntimeError(result.stdout+result.stderr)
    try:
        for suite,methods in [("repair",["repair-baseline","repair-rewrite","repair-reread","repair-keyword","repair-search"]),
                              ("report",["report-grep"])]:
            command("trials","plan","--inputs",root/"dataset/inputs.json","--task-ids-file",root/"task_ids.json",
                "--methods",*methods,"--repeats","2","--model","offline-fixture","--max-steps","6","--out",root/(suite+"-plan.json"))
            command("trials","run","--plan",root/(suite+"-plan.json"),"--inputs",root/"dataset/inputs.json",
                    "--env-file",env_file,"--out",root/suite)
            command("trials","score","--inputs",root/"dataset/inputs.json","--gold",root/"dataset/gold.json",
                    "--run",root/suite,"--out",root/(suite+"-scores.json"))
        repair = load(root/"repair-scores.json")["summaries"]
        assert repair["repair-baseline"]["mean_answer_token_f1"]==0
        assert repair["repair-reread"]["mean_answer_token_f1"]==1
        assert repair["repair-keyword"]["mean_answer_token_f1"]==1
        assert repair["repair-search"]["mean_answer_token_f1"]==1
        usage = load(root/"repair/trial-000/usage.json")
        assert usage["measured_llm_tokens"]==2400 and usage["logical_policy_tokens"]==5100
        report = load(root/"report-scores.json")["summaries"]["report-grep"]
        assert report["completed"]==2 and report["contract_pass_rate"]==1 and report["semantic_success_rate"] is None
        command("reports","export","--inputs",root/"dataset/inputs.json","--run",root/"report","--out",root/"annotations")
        command("claim_labels","judge","--cases",root/"annotations/cases.json","--env-file",env_file,
                "--model","offline-fixture","--out",root/"reviews.json")
        command("trials","score","--inputs",root/"dataset/inputs.json","--gold",root/"dataset/gold.json","--run",root/"report",
                "--reviews",root/"reviews.json","--out",root/"report-reviewed-scores.json")
        for name in ("annotator-a","annotator-b","adjudication"):
            path = root/"annotations"/(name+".json")
            value = load(path)
            value["annotation_source"]="synthetic_fixture"
            for label in value["labels"]:
                label["verdict"] = None if name=="adjudication" else "supported"
            path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
        command("claim_labels","calibrate","--cases",root/"annotations/cases.json","--reviews",root/"reviews.json",
            "--labels-a",root/"annotations/annotator-a.json","--labels-b",root/"annotations/annotator-b.json",
            "--adjudication",root/"annotations/adjudication.json","--out",root/"calibration.json")
        calibration = load(root/"calibration.json")
        assert calibration["annotation_source"]=="synthetic_fixture" and calibration["judged_coverage"]==1
        result = dict(status="passed",paired_repair_attempts=10,production_report_attempts=2,
            reviewed_units=calibration["cases"],loopback_model_requests=Handler.calls,real_cloud_calls=0,
            note="Synthetic endpoint checks executable software paths, not real model quality.")
        write_new(root/"summary.json",result)
        return result
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__=="__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,required=True)
    print(json.dumps(smoke(parser.parse_args().out)))
