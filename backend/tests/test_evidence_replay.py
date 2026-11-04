import pytest
from research_backend.benchmarks.evidence_replay import validate_selections, selection_messages


def fixture():
    task = dict(question="What is trained?", targets=[dict(id="training", source_id="paper", field="training")])
    reads = [dict(id="ev", source_id="paper", start_line=10, end_line=12,
                  quote="The encoder is frozen.\nOnly the adapter\nis trained.")]
    return task, reads


def test_address_selection_copies_original_newlines_and_does_not_accept_rewriting():
    task, reads = fixture()
    payload = dict(entries=[dict(target_id="training", evidence_id="ev", start_line=11, end_line=12)])
    pins = validate_selections(payload,"lines",task,reads)
    assert pins[0]["quote"] == "Only the adapter\nis trained."
    with pytest.raises(ValueError, match="quote_not_exact"):
        validate_selections(dict(entries=[dict(target_id="training",evidence_id="ev",quote="Only the adapter is trained.")]),"quote",task,reads)


@pytest.mark.parametrize("update,error", [({"start_line":9},"range_outside_read"),({"end_line":13},"range_outside_read"),
    ({"end_line":10},"range_outside_read"),({"evidence_id":"invented"},"unknown_target"),({"target_id":"invented"},"unknown_target")])
def test_address_selection_rejects_unread_ranges_and_unknown_identifiers(update,error):
    task, reads = fixture()
    item=dict(target_id="training",evidence_id="ev",start_line=11,end_line=12);item.update(update)
    with pytest.raises(ValueError,match=error):
        validate_selections(dict(entries=[item]),"lines",task,reads)


def test_wrong_paper_is_rejected_and_empty_selection_is_not_a_claim():
    task, reads=fixture();reads[0]["source_id"]="other"
    with pytest.raises(ValueError,match="wrong_paper"):
        validate_selections(dict(entries=[dict(target_id="training",evidence_id="ev",start_line=11,end_line=12)]),"lines",task,reads)
    assert validate_selections(dict(entries=[]),"lines",task,reads)==[]


def test_comparison_exposes_same_question_and_source_text_to_both_methods():
    task, reads=fixture()
    assert selection_messages(task,reads,"quote")[1] == selection_messages(task,reads,"lines")[1]


def test_replay_resolves_original_read_and_rejects_tampered_saved_selection():
    from research_backend.benchmarks.answer_replay import build_context
    task,reads=fixture()
    pins=validate_selections(dict(entries=[dict(target_id="training",evidence_id="ev",start_line=11,end_line=12)]),"lines",task,reads)
    _,visible=build_context(task,reads,pins,"selected",3500)
    assert visible[0]["quote"]=="Only the adapter\nis trained."
    assert visible[0]["id"]=="ev"
    pins[0]["quote"]="invented"
    with pytest.raises(ValueError,match="canonical"):
        build_context(task,reads,pins,"selected",3500)


def test_retention_scores_actual_visible_text_not_full_metadata_range():
    from research_backend.benchmarks.score_replay import evidence_retention,intersect_size
    doc=dict(text="intro\nimportant fact\nend",paragraphs=[dict(id="p",start_line=2,end_line=2)])
    labels=dict(targets=[dict(source_id="p",references=[dict(evidence_paragraph_ids=["p"])])])
    read=dict(source_id="p",start_line=1,end_line=3,quote=doc["text"])
    prediction=dict(reads=[read],visible_evidence=[dict(read,quote="intro\n")])
    assert evidence_retention(prediction,labels,{"p":doc})==0
    prediction["visible_evidence"]=[read]
    assert evidence_retention(prediction,labels,{"p":doc})==1
    assert intersect_size([(0,5),(2,6)],[(1,3),(2,5)])==4


def test_lexical_control_reads_no_new_text_and_preserves_tail_match():
    from research_backend.benchmarks.answer_replay import build_context
    task=dict(question="What adapter is trained?",targets=[dict(id="t",source_id="p",field="adapter training")])
    text="\n".join(["unrelated introduction "*6]*20+["Only adapter training is enabled.","The encoder stays frozen."])
    reads=[dict(id="ev",source_id="p",start_line=10,end_line=31,quote=text)]
    _,visible=build_context(task,reads,[],"lexical",1000)
    assert any("Only adapter training" in e["quote"] for e in visible)
    for e in visible:
        assert e["quote"] in text
        assert e["id"]=="ev"


@pytest.mark.asyncio
async def test_failed_pins_receive_feedback_and_cannot_repeat_until_budget_exhaustion(tmp_path):
    import json
    from research_backend.benchmarks.prepare import document
    from research_backend.benchmarks.run import run_case
    from research_backend.config import Settings
    doc=document("p","Study",[("Method",["The encoder is frozen; only the adapter is trained."])])
    task=dict(id="case",question="What is trained?",source_ids=["p"],targets=[dict(id="t",source_id="p",field="training")])
    class Model:
        calls=0
        pins=0
        eid=None
        async def complete(self,messages,definitions,max_tokens):
            self.calls+=1
            if self.calls==1:
                source=json.loads(messages[1]["content"])["sources"][0]["id"]
                name="sources_read";args=dict(source_id=source,start_line=1,line_count=10)
            elif [d["function"]["name"] for d in definitions]==["pin_evidence"]:
                self.pins+=1
                self.eid=json.loads(messages[-1]["content"].split("\n",1)[1])["evidence"][0]["id"]
                if self.pins==2:
                    assert "Previous pin validation failed" in messages[0]["content"]
                assert self.pins<=2
                name="pin_evidence";args=dict(entries=[dict(target_id="t",evidence_id=self.eid,quote="rewritten unsupported quote")])
            else:
                name="finish_report";args=dict(answers=[dict(target_id="t",answer="adapter",unanswerable=False,evidence_ids=[self.eid])])
            return dict(message=dict(role="assistant",tool_calls=[dict(id=str(self.calls),type="function",function=dict(name=name,arguments=json.dumps(args)))]),tokens=300,prompt_tokens=200,completion_tokens=100)
    model=Model()
    result=await run_case(task,{"p":doc},Settings(),"grep-ledger",tmp_path,lambda texts:[],30000,8,model)
    assert result["status"]=="completed"
    assert model.pins==2 and result["model_calls"]==4
    assert result["ledger_rejected_reads"]==1
    assert result["tokens_used"]==1200


def test_support_review_excludes_labels_and_copies_only_visible_lines():
    import json
    from research_backend.benchmarks.support_review import review_messages, resolve_review
    _, reads = fixture()
    case = dict(id="c", question="What is trained?", claim="Only the adapter is trained.",
                source_id="paper", evidence=reads, expected="DO_NOT_SHOW_THIS_LABEL")
    assert "DO_NOT_SHOW_THIS_LABEL" not in json.dumps(review_messages(case))
    result = resolve_review(dict(verdict="supported", rationale="The source states this directly.",
        evidence_spans=[dict(evidence_id="ev", start_line=11, end_line=12)]), case)
    assert result["evidence_spans"][0]["quote"] == "Only the adapter\nis trained."
    assert result["needs_human_review"] is True


@pytest.mark.parametrize("span", [dict(evidence_id="ev", start_line=9, end_line=12),
                                  dict(evidence_id="other", start_line=10, end_line=12)])
def test_support_review_rejects_unseen_evidence(span):
    from research_backend.benchmarks.support_review import resolve_review
    _, reads = fixture()
    with pytest.raises(ValueError, match="unseen"):
        resolve_review(dict(verdict="supported", rationale="claim", evidence_spans=[span]), dict(evidence=reads))


def test_support_review_requires_source_alignment_and_evidence_for_decisive_verdict():
    from research_backend.benchmarks.support_review import review_messages, resolve_review
    _, reads = fixture()
    with pytest.raises(ValueError, match="different source"):
        review_messages(dict(source_id="wrong", evidence=reads))
    with pytest.raises(ValueError, match="requires evidence"):
        resolve_review(dict(verdict="supported", rationale="claim", evidence_spans=[]), dict(evidence=reads))


@pytest.mark.asyncio
async def test_answer_replay_without_selector_records_all_controls(tmp_path, monkeypatch):
    import json
    from argparse import Namespace
    from research_backend.benchmarks import answer_replay as replay
    from research_backend.config import Settings
    task, reads = fixture()
    task.update(id="case", source_ids=["paper"])
    inputs = tmp_path / "inputs.json"
    inputs.write_text("{}", encoding="utf-8")
    prior = tmp_path / "prior"
    prior.mkdir()
    (prior / "protocol.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(replay, "load_environment", lambda: None)
    monkeypatch.setattr(replay.Settings, "from_env", lambda: Settings(llm_api_key="offline-test"))
    monkeypatch.setattr(replay, "load_cases", lambda *args: [dict(task=task, reads=reads, prediction_sha256="frozen")])
    class Model:
        def __init__(self, settings):
            pass
        async def complete(self, messages, definitions, max_tokens):
            payload = dict(answers=[dict(target_id="training", answer="adapter", unanswerable=False, evidence_ids=["ev"])])
            return dict(tokens=100, prompt_tokens=80, completion_tokens=20, response_model="fake",
                message=dict(tool_calls=[dict(function=dict(name="finish_report", arguments=json.dumps(payload)))]))
    monkeypatch.setattr(replay, "ChatModel", Model)
    out = tmp_path / "out"
    await replay.run(Namespace(env_file=None, model="fake", selections=None, inputs=inputs, prior=prior,
        source_method="grep", limit=0, policies=["prefix", "lexical", "full"], out=out, repeats=1, concurrency=1))
    rows = [json.loads(line) for line in (out / "predictions.jsonl").read_text("utf-8").splitlines()]
    assert len(rows) == 5 and all(r["status"] == "completed" for r in rows)
    assert all(r["selection_tokens"] == 0 and r["total_pipeline_tokens"] == 100 for r in rows)
    protocol = json.loads((out / "protocol.json").read_text("utf-8"))
    assert protocol["selections_sha256"] is None
    assert protocol["prior_predictions"] == {"case": "frozen"}
