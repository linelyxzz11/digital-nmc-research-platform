"""Independent arithmetic and rejection of resealed false numerical results."""
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.experiments.validation_campaign import independent_golden,rankings,case
from tools.experiments.summarize_validation_campaign import audit
from tools.experiments.scalable_experiments import save,seal
from compiler.mapping_search import catalogue
from simulator.system.tile_config import TileComputeConfig
from simulator.system.root_config import RootTiming

def test_independent_golden_uses_wide_products_and_explicit_twos_complement():
    assert independent_golden([[-128,127],[0,-1]],[-128,127])==[32513,-127]
    assert independent_golden([[1<<31]],[1])==[-(1<<31)]
    assert independent_golden([[-(1<<31)-1]],[1])==[(1<<31)-1]

def test_audit_rejects_resealed_false_outputs_and_missing_cases(tmp_path):
    protocol=seal(dict(seed=1,shapes=[[5,19]],application_shapes=[],sensitivity_shapes=[],budgets=[1,2,4]))
    ranked=rankings(catalogue(5,19),TileComputeConfig(),RootTiming())
    save(tmp_path/'default-5x19.ranking.json',seal(dict(protocol_sha256=protocol['sha256'],policies=ranked)))
    record=case(5,19,'default',tmp_path,protocol)
    assert len(audit(tmp_path,protocol))==1
    save(tmp_path/'duplicate.json',record)
    with pytest.raises(AssertionError,match='duplicate'):audit(tmp_path,protocol)
    (tmp_path/'duplicate.json').unlink()
    body=dict(record);body.pop('sha256');identity=next(iter(body['measured']))
    body['measured'][identity]['metrics']['y'][0]+=1
    save(tmp_path/'default-5x19.json',seal(body))
    with pytest.raises(AssertionError):audit(tmp_path,protocol)
    (tmp_path/'default-5x19.json').unlink()
    with pytest.raises(AssertionError,match='missing/unknown'):audit(tmp_path,protocol)
