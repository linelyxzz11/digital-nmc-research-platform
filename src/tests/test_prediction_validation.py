"""Blind ordering, real evaluation budgets, resume and truthful aggregation."""
import json
from pathlib import Path
import runpy

import pytest


@pytest.fixture
def api(tmp_path):
    root=Path(__file__).resolve().parents[2]
    scope=runpy.run_path(str(root/'tools/experiments/prediction_validation.py'))
    protocol=json.loads((root/'ini/prediction_v0_4.json').read_text())
    protocol.update(mesh=[4,4],grids=[[1,4],[4,1],[2,2]],fresh_shapes=[[5,19]],budgets=[1,2])
    path=tmp_path/'protocol.json';path.write_text(json.dumps(protocol))
    return scope,path


def test_all_rankings_precede_numerics_and_report_proxy_cost(api,tmp_path,monkeypatch):
    functions,protocol=api;scope=functions['fresh_case'].__globals__
    original_rank,original_run=scope['rank'],scope['run_grid']
    ranked=[];calls=[]
    def ranking(plans,policy):
        assert not calls
        ranked.append(policy)
        return original_rank(plans,policy)
    def evaluate(plan,*args,**kwargs):
        assert len(ranked)==3
        calls.append(plan.digest)
        return original_run(plan,*args,**kwargs)
    monkeypatch.setitem(scope,'rank',ranking);monkeypatch.setitem(scope,'run_grid',evaluate)
    record=functions['fresh_case'](5,19,tmp_path/'cases',protocol)
    assert record['numerical_calls']==len(calls)
    assert record['policies']['noc-v04']['packet_proxy_calls']==12
    assert record['policies']['unindp-compact']['packet_proxy_calls']==0
    assert all(row['exact_evaluations']<=row['budget'] and row['search_seconds']>=row['ranking_seconds']
               for row in record['quality'])
    again=functions['fresh_case'](5,19,tmp_path/'cases',protocol)
    assert again['result_sha256']==record['result_sha256']


def test_fresh_results_merge_and_resealed_false_numerics_fail(api,tmp_path):
    functions,protocol=api;directory=tmp_path/'cases'
    record=functions['fresh_case'](5,19,directory,protocol)
    summary=functions['summarize']([directory],tmp_path/'merged',protocol_path=protocol)
    assert summary['fresh_cases']==1 and summary['feasible']==12 and len(summary['groups'])==6
    with pytest.raises(ValueError,match='duplicate'):
        functions['summarize']([directory,directory],tmp_path/'duplicate',protocol_path=protocol)
    body=dict(record);body.pop('result_sha256')
    identity=next(iter(body['measured']));body['measured'][identity]['metrics']['y'][0]+=1
    functions['save'](directory/'fresh-5x19.result.json',functions['seal'](body,'result_sha256'))
    with pytest.raises(ValueError,match='numerical/completion'):
        functions['summarize']([directory],tmp_path/'bad',protocol_path=protocol)


def test_changed_sources_cannot_resume_and_missing_cases_cannot_merge(api,tmp_path):
    functions,protocol=api;directory=tmp_path/'cases'
    functions['fresh_case'](5,19,directory,protocol)
    data=json.loads(protocol.read_text());data['seed']+=1;protocol.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='mix predictor'):
        functions['fresh_case'](5,19,directory,protocol)
    with pytest.raises(ValueError,match='incomplete'):
        functions['summarize']([],tmp_path/'missing',protocol_path=protocol)
