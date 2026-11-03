"""Detailed Tile/NI/Mesh shared-clock equivalence and resource sensitivity."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from compiler.auto_mapper_v0_1 import MappingError, map_gemv
from simulator.system.compute_timing import ComputeTiming
from simulator.system.demo1 import FinalGather, PlanError, plan_from_mapping, run_demo1, run_plan
from simulator.system.demo2 import PSumReduce, run_demo2
from simulator.system.tile_config import TileComputeConfig
from tools.experiments.mapper_sweep import generate_workload, parse_shapes
from tools.experiments.pressure_sweep import run_experiment


def run(m=33, k=5, partition='row', *, root=3, config=None, **options):
    matrix, vector = generate_workload(m, k)
    plan = plan_from_mapping(map_gemv(matrix, vector, strategy=partition, root_tile=root))
    factory = FinalGather if partition=='row' else PSumReduce
    return run_plan(plan, endpoint_factory=factory, tile_timing=config, **options)


def check_conservation(result):
    summary=result.summary()
    assert sum(summary['phase_cycles'].values())==result.mesh.cycle
    assert summary['compute_model']=='request-level' and 'network_schedule_cycles' not in summary
    assert len(result.host_done)==1
    for task in result.tasks:
        stats=result.compute_stats[task.tile_id]
        start=next(e['network_cycle'] for e in result.trace if e['event']=='TASK_START' and e['tile_id']==task.tile_id)
        done=[e['network_cycle'] for e in result.trace if e['event']=='LOCAL_DONE' and e['tile']==task.tile_id]
        assert done==[start+stats['local_elapsed_cycles']-1]
        assert sum(stats['stage_cycles'].values())==stats['local_elapsed_cycles']
        assert stats['result_stall_cycles']==sum(e['event']=='RESULTBEAT_STALL' and e['tile']==task.tile_id for e in result.trace)
        assert stats['useful_mac_ops']==task.m_local*task.k_local
        assert stats['read_requests']==2*((task.m_local+3)//4)*task.k_local
        assert stats['write_requests']==task.m_local


@pytest.mark.parametrize('partition', ['row','k'])
@pytest.mark.parametrize('root', [0,3])
def test_default_ports_preserve_entire_formula_trace_packets_and_shared_clock(partition,root):
    baseline=run(partition=partition,root=root,compute_timing=ComputeTiming())
    detailed=run(partition=partition,root=root,config=TileComputeConfig())
    assert detailed.trace==baseline.trace
    assert detailed.y==baseline.y and detailed.ack_keys==baseline.ack_keys
    assert detailed.mesh.flit_hops==baseline.mesh.flit_hops
    assert detailed.mesh.inject_stall_cycles==baseline.mesh.inject_stall_cycles
    check_conservation(detailed)


def test_all_24_shapes_keep_feasibility_and_match_formula_baseline():
    successful=infeasible=0
    for m,k in parse_shapes('suite'):
        for partition in ('row','k'):
            try:
                baseline=run(m,k,partition,compute_timing=ComputeTiming())
            except MappingError:
                infeasible+=1
                continue
            detailed=run(m,k,partition,config=TileComputeConfig())
            assert detailed.trace==baseline.trace,(m,k,partition)
            assert detailed.mesh.cycle==baseline.mesh.cycle and detailed.y==baseline.y
            check_conservation(detailed)
            successful+=1
    assert (successful,infeasible)==(43,5)


@pytest.mark.parametrize('partition',['row','k'])
@pytest.mark.parametrize('root',[0,3])
def test_network_pressure_freezes_compute_and_does_not_duplicate_done(partition,root):
    gate=lambda cycle,tile,phase: phase=='LOAD' or tile!=root or cycle>=260
    baseline=run(65,5,partition,root=root,compute_timing=ComputeTiming(),sink_ready=gate)
    detailed=run(65,5,partition,root=root,config=TileComputeConfig(),sink_ready=gate)
    assert detailed.trace==baseline.trace and detailed.y==baseline.y
    assert detailed.mesh.fifo_full_cycles>0
    assert sum(s['result_stall_cycles'] for s in detailed.compute_stats.values())>0
    check_conservation(detailed)


@pytest.mark.parametrize('partition',['row','k'])
@pytest.mark.parametrize('overrides,counter', [
    ({'memory':{'weight':{'issue_interval':2}}},'port_busy_request_cycles'),
    ({'memory':{'result':{'banks':1}}},'bank_conflict_request_cycles'),
    ({'memory':{'input':{'word_bytes':4},'share_weight_input':True}},'bank_conflict_request_cycles'),
])
def test_limited_ports_change_runtime_but_preserve_results_and_traffic(partition,overrides,counter):
    baseline=run(16,8,partition,config=TileComputeConfig())
    limited=run(16,8,partition,config=TileComputeConfig.from_dict(overrides))
    assert limited.y==baseline.y and limited.ack_keys==baseline.ack_keys
    assert limited.mesh.flit_hops==baseline.mesh.flit_hops
    assert limited.mesh.cycle>baseline.mesh.cycle
    assert sum(s[counter] for s in limited.compute_stats.values())>0
    check_conservation(limited)


def test_optional_tile_trace_has_exact_clock_mapping_and_serializable_bytes():
    config=TileComputeConfig.from_dict({'memory':{'input':{'word_bytes':4},'share_weight_input':True}})
    plain=run(5,3,config=config)
    traced=run(5,3,config=config,capture_tile_trace=True)
    assert [e for e in traced.trace if e['event']!='TILE_CYCLE']==plain.trace
    json.dumps(traced.trace)
    for task in traced.tasks:
        events=[e for e in traced.trace if e['event']=='TILE_CYCLE' and e['tile']==task.tile_id]
        stats=traced.compute_stats[task.tile_id]
        assert len(events)==stats['local_elapsed_cycles']
        start=next(e['network_cycle'] for e in traced.trace if e['event']=='TASK_START' and e['tile_id']==task.tile_id)
        assert [e['local_cycle'] for e in events]==list(range(len(events)))
        assert all(e['network_cycle']==start+e['local_cycle'] for e in events)
        assert sum(len(e['denied']) for e in events)==stats['bank_conflict_request_cycles']+stats['port_busy_request_cycles']
        responses=[r for e in events for r in e['responses']]
        assert all(isinstance(r['data'],str) and bytes.fromhex(r['data']) for r in responses)


@pytest.mark.parametrize('payload', [[], {'unknown':1}, {'operand_slots':0}, {'mac_interval':True},
    {'memory':None}, {'memory':{'bad':{}}}, {'memory':{'weight':None}},
    {'memory':{'weight':{'bad':1}}}, {'memory':{'weight':{'read_latency':0}}},
    {'memory':{'weight':{'word_bytes':2}}}, {'memory':{'share_weight_input':True}}])
def test_invalid_config_rejected_before_execution(payload):
    with pytest.raises(ValueError): TileComputeConfig.from_dict(payload)


def test_partial_config_defaults_and_conflicting_runner_modes():
    config=TileComputeConfig.load(ROOT/'ini/tile_system/weight_ii2.json')
    assert config.memory.weight.issue_interval==2 and config.memory.input.word_bytes==1
    assert config.memory.result.banks==4 and config.operand_slots==2
    with pytest.raises(PlanError,match='either'):
        run(config=config,compute_timing=ComputeTiming())
    with pytest.raises(PlanError,match='requires'):
        run(capture_tile_trace=True)
    with pytest.raises(PlanError,match='TileComputeConfig'):
        run(config={})


@pytest.mark.parametrize('module,runner',[('demo1',run_demo1),('demo2',run_demo2)])
def test_demo_cli_uses_detailed_ports_and_exports_trace(tmp_path,module,runner):
    path=tmp_path/f'{module}.json'
    result=subprocess.run([sys.executable,'-B','-m',f'simulator.system.{module}',
        '--compute-model','tile','--tile-config',str(ROOT/'ini/tile_system/result_1bank.json'),
        '--tile-trace','--trace',str(path)],cwd=ROOT,env=os.environ|{'PYTHONPATH':str(ROOT/'src')},
        text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    payload=json.loads(path.read_text())
    expected=runner(tile_timing=TileComputeConfig.load(ROOT/'ini/tile_system/result_1bank.json'))
    assert payload['summary']['y']==expected.y and payload['summary']['check']=='PASS'
    assert payload['summary']['tile_timing']==asdict(TileComputeConfig.load(ROOT/'ini/tile_system/result_1bank.json'))
    assert any(e['event']=='TILE_CYCLE' for e in payload['trace'])


def test_pressure_experiment_records_full_config_counters_and_same_phase_window():
    config=TileComputeConfig.load(ROOT/'ini/tile_system/result_1bank.json')
    result=run_experiment([(16,8)],('open','eighth'),(0,),placement='default',tile_timing=config)
    assert result['summary']['ok']==4
    assert result['options']['tile_timing']==asdict(config)
    assert all(r['metrics']['bank_conflict_request_cycles']>0 for r in result['results'])
    assert all(r['metrics']['tile_useful_mac_ops']==16*8 for r in result['results'])
