"""Runner checks supplement, rather than replace, actual RTL regressions."""
from pathlib import Path
import json,runpy
import pytest

@pytest.fixture
def runner():
    return runpy.run_path(str(Path(__file__).resolve().parents[2]/'tools/rtl/run_mesh_rtl.py'))

def test_wide_independent_golden_and_twos_complement(runner):
    assert runner['golden_gemv']([[-128,127],[0,-1]],[-128,127])==[32513,-127]
    assert runner['int32_wrap'](2**31)==-2**31
    assert runner['int32_wrap'](-2**31-1)==2**31-1

def test_same_workload_is_independent_of_worker_grid(runner,tmp_path):
    common=dict(name='seeded',m=5,k=7,reduction='tree',placement='compact',workload_seed=20261031)
    _,w1,x1,_=runner['prepare'](dict(common,grid=(1,4)),tmp_path/'k')
    _,w2,x2,_=runner['prepare'](dict(common,grid=(2,2)),tmp_path/'grid')
    assert (w1,x1)==(w2,x2)
    a=json.loads((tmp_path/'k/workload.json').read_text())
    b=json.loads((tmp_path/'grid/workload.json').read_text())
    assert a['sha256']==b['sha256']

def test_plan_and_workload_tampering_are_rejected(runner,tmp_path):
    common=dict(name='seeded',m=5,k=7,grid=(2,2),reduction='tree',placement='compact',workload_seed=20261031)
    with pytest.raises(ValueError,match='plan differs'):
        runner['prepare'](dict(common,expected_plan_sha256='0'*64),tmp_path/'bad_plan')
    with pytest.raises(ValueError,match='workload differs'):
        runner['prepare'](dict(common,historical_workload_sha256='0'*64),tmp_path/'bad_workload')
