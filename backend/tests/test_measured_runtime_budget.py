"""Returned over-budget usage must survive both Agent and reviewer boundaries."""
from types import SimpleNamespace

import pytest

from research_backend.benchmarks.measurement import MeasuredModel
from research_backend.runtime import update_token_calibration, BudgetExceeded
from tests.test_field_state import LoopModel, run_loop


class BoundaryModel(LoopModel):
    def __init__(self,overrun_action=None,review=False):
        super().__init__()
        self.overrun_action,self.review=overrun_action,review
        self.measured=MeasuredModel(SimpleNamespace(complete=self.raw),50000)

    async def raw(self,messages,tools,max_tokens):
        result=await super().complete(messages,tools,max_tokens)
        overrun=(tools[0]['function']['name']=='review_fields') if self.review else self.actions==self.overrun_action
        result['tokens']=50000 if overrun else 100
        result.update(prompt_tokens=result['tokens']-50,completion_tokens=50,finish_reason='tool_calls')
        return result

    async def complete(self,messages,tools,max_tokens):
        return await self.measured.complete(messages,tools,max_tokens)


@pytest.mark.parametrize('action',[2,4])
def test_agent_overrun_is_fully_counted_and_cannot_recover_to_old_checkpoint(tmp_path,action):
    model=BoundaryModel(overrun_action=action)
    run,_,events=run_loop(tmp_path,model)
    assert run['status']=='failed' and run['tokens_used']==model.measured.tokens==50000+(action-1)*100
    usage=[e['data'] for e in events if e['type']=='model_usage']
    assert sum(e['total_tokens'] for e in usage)==run['tokens_used']
    assert usage[-1]['total_tokens']==50000
    assert not any(e['type']=='report_ready' for e in events)


def test_review_overrun_keeps_charge_but_never_applies_judgments(tmp_path):
    model=BoundaryModel(review=True)
    run,reviews,_=run_loop(tmp_path,model,mode='observe')
    assert run['status']=='failed' and run['tokens_used']==model.measured.tokens==50300
    assert reviews[0]['status']=='failed' and reviews[0]['tokens_used']==50000
    assert run['field_state']['reviewed']==0 and run['field_state']['semantic_success'] is None


def test_calibration_keeps_a_conservative_high_water_across_phases():
    assert update_token_calibration(1,.7*1000,1000)==1
    assert update_token_calibration(1.3,900,1000)==1.3
    assert update_token_calibration(1.3,1500,1000)==1.5


@pytest.mark.asyncio
async def test_exhausted_boundary_blocks_further_provider_calls():
    async def complete(messages,tools,max_tokens):
        return dict(tokens=100,prompt_tokens=80,completion_tokens=20,message={})
    measured=MeasuredModel(SimpleNamespace(complete=complete),50)
    for _ in range(2):
        with pytest.raises(BudgetExceeded): await measured.complete([],[],20)
    assert len(measured.calls)==1 and measured.tokens==100
