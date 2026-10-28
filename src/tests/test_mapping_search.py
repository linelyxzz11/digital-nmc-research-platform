"""Mapping choices must precede labels and obey exact evaluation budgets."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from compiler.auto_mapper_v0_1 import MappingError
from compiler.mapping_search import catalogue,predict,search,xy_path
from compiler.unindp_adapter import allowed_grids,partitions
from simulator.system.root_config import RootTiming
from simulator.system.scalable import run_grid


def workload(m,k):
    return [[(r*3+c)%9-4 for c in range(k)] for r in range(m)], [c%7-3 for c in range(k)]


def test_author_axis_translation_and_saturated_parallelism():
    result = partitions(32,128)
    assert (4,8) in allowed_grids(32,128)
    assert {p.budget for p in result} == {16,32}
    for item in result:
        assert item.author_pu_partition == (1,item.grid[1],item.grid[0],1)
        assert item.author_pu_ids == tuple(range(item.budget))
    assert not partitions(1,4)


def test_directed_xy_paths_and_local_ejection():
    assert xy_path(10,17,8) == ((10,9),(9,17),(17,'LOCAL'))
    assert xy_path(0,0,8) == ((0,'LOCAL'),)


def test_catalogue_keeps_infeasibility_and_rejects_duplicates():
    result = catalogue(17,257)
    assert result.attempted == 28
    assert result.rejected and result.plans
    with pytest.raises(MappingError,match='duplicate'):
        catalogue(32,32,grids=((1,16),(1,16)))


def test_topology_blind_prediction_is_placement_invariant():
    plans = catalogue(32,128).plans
    compact = next(p for p in plans if p.grid==(4,4) and p.placement=='compact' and p.reduction=='central')
    spread = next(p for p in plans if p.grid==(4,4) and p.placement=='spread' and p.reduction=='central')
    assert predict(compact,communication=False).cycles == predict(spread,communication=False).cycles
    assert predict(compact).input_cycles < predict(spread).input_cycles
    assert predict(compact).cycles != predict(spread).cycles
    assert predict(compact,root_timing=RootTiming(add_cycles=5)).cycles > predict(compact).cycles


@pytest.mark.parametrize('policy',['noc','unindp-adapted','fixed-k','fixed-row'])
def test_budget_and_no_oracle_leak(policy):
    W,x = workload(32,32)
    called = []
    def evaluator(plan):
        called.append(plan.digest)
        return SimpleNamespace(cycles=100-len(called))
    result = search(32,32,W,x,policy=policy,exact_budget=2,evaluator=evaluator)
    assert len(called) == len(result.evaluated) == 2
    assert called == [row['plan_sha256'] for row in result.ranking[:2]]
    # The lower measured result must win even when predicted order disagrees.
    assert result.plan.digest == called[-1]
    assert result.cycles == 98


def test_selected_mapper_plan_runs_and_matches_golden():
    W,x = workload(5,19)
    plans = catalogue(5,19,grids=((1,4),(4,1),(2,2)),mesh_x=4,mesh_y=4)
    result = search(5,19,W,x,exact_budget=2,candidates=plans)
    exact = run_grid(result.plan,W,x)
    assert exact.cycles == result.cycles
    assert exact.y == [sum(a*b for a,b in zip(row,x)) for row in W]


def test_invalid_budget_and_infeasible_fixed_policy_do_not_fallback():
    W,x = workload(5,19)
    with pytest.raises(ValueError,match='positive'):
        search(5,19,W,x,exact_budget=0)
    plans = catalogue(5,19,grids=((2,2),))
    with pytest.raises(MappingError,match='no feasible'):
        search(5,19,W,x,policy='fixed-k',candidates=plans)
    with pytest.raises(ValueError,match='dimensions'):
        search(6,19,W,x,candidates=plans)
