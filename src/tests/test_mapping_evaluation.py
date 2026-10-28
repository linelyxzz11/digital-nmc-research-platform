"""Quality aggregation, ties, corrupt/missing shards and resume contracts."""
from pathlib import Path
import runpy

import pytest

from compiler.mapping_search import catalogue


@pytest.fixture
def api(monkeypatch):
    root=Path(__file__).resolve().parents[2]
    functions=runpy.run_path(str(root/'tools/experiments/mapping_evaluation.py'))
    scope=functions['one_case'].__globals__
    monkeypatch.setitem(scope,'CALIBRATION',((5,19),(6,17)))
    monkeypatch.setitem(scope,'HOLDOUT',())
    monkeypatch.setitem(scope,'BUDGETS',(1,2))
    monkeypatch.setitem(scope,'MAIN_GRIDS',((1,4),(4,1),(2,2)))
    monkeypatch.setitem(scope,'catalogue',lambda m,k:catalogue(m,k,grids=scope['MAIN_GRIDS']))
    return functions


def test_tie_aware_ranking_and_constant_prediction(api):
    assert api['average_ranks']([1,1,4,2])==[0.5,0.5,3,2]
    assert api['spearman']([1,1,2],[2,2,3])==pytest.approx(1)
    assert api['spearman']([1,1],[2,3]) is None


def test_holdout_pipeline_resumes_and_aggregates_all_budget_prefixes(api,tmp_path):
    directory=tmp_path/'cases'
    first=api['one_case'](5,19,'calibration',directory)
    again=api['one_case'](5,19,'calibration',directory)
    assert first['result_sha256']==again['result_sha256']
    api['one_case'](6,17,'calibration',directory)
    report=api['merge']([directory],tmp_path/'merged')
    assert report['cases']==2 and report['candidate_runs']==24
    assert len(report['groups'])==8
    assert all(row['actual_evaluations']<=row['budget'] for row in report['results'])
    with pytest.raises(ValueError,match='duplicate'):
        api['merge']([directory,directory],tmp_path/'duplicates')
    path=directory/'calibration-6x17.result.json'
    path.unlink()
    with pytest.raises(ValueError,match='incomplete'):
        api['merge']([directory],tmp_path/'missing')


def test_changed_policy_source_and_corruption_cannot_resume(api,tmp_path,monkeypatch):
    directory=tmp_path/'cases'
    api['one_case'](5,19,'calibration',directory)
    scope=api['one_case'].__globals__
    original=scope['provenance']
    monkeypatch.setitem(scope,'provenance',lambda:dict(original(),new_assumption=True))
    with pytest.raises(ValueError,match='mix model/policy'):
        api['one_case'](5,19,'calibration',directory)
    monkeypatch.setitem(scope,'provenance',original)
    path=directory/'calibration-5x19.result.json'
    path.write_text(path.read_text().replace('"golden_ok": true','"golden_ok": false'))
    with pytest.raises(ValueError,match='integrity'):
        api['one_case'](5,19,'calibration',directory)
