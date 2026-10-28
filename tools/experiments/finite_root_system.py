#!/usr/bin/env python3
"""Actual finite Root service sensitivity; no synthetic ejection gate."""
from dataclasses import asdict
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[2]
for directory in (ROOT,ROOT/'src'):
    sys.path.insert(0,str(directory))
from compiler.auto_mapper_v0_1 import MappingError, map_gemv
from simulator.functional import Demo0Tile
from simulator.communication import ResultPacket
from simulator.system.demo1 import FinalGather, GatherConfig, plan_from_mapping, run_plan
from simulator.system.demo2 import PSumReduce
from simulator.system.root_config import RootTiming
from simulator.system.timed_endpoint import TimedRoot
from simulator.system.tile_config import TileComputeConfig
from tools.experiments.mapper_sweep import generate_workload, parse_shapes, _golden
from tools.experiments.pressure_sweep import provenance


def hand_trace(lanes):
    root=Demo0Tile(3)
    endpoint=TimedRoot(GatherConfig(9,3,0,6,100,4,'REDUCE_PSUM'),root,
                       RootTiming(lanes_per_cycle=lanes,reduction_entries=4))
    endpoint.accept(ResultPacket(1,3,9,100,15,(1,-2,3,-4),'PSUM',True))
    endpoint.accept(ResultPacket(2,3,9,100,15,(4,5,-6,-7),'PSUM',True))
    trace=[]
    while endpoint.done is None or not endpoint.idle():
        endpoint.step()
        trace.append(dict(endpoint.last_event,values=dict(endpoint.values),
                          partial_sums=dict(endpoint.accumulators),last_mask=endpoint.last_mask))
    assert endpoint.values=={100:5,101:3,102:-3,103:-11}
    return dict(lanes=lanes,cycles=endpoint.cycle,trace=trace)


def run(directory):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    configs={'functional':None}
    files=[Path(__file__)]
    for name in ('baseline','scalar','queue1','slow_write'):
        path=ROOT/'ini/root_service'/f'{name}.json'
        configs[name]=RootTiming.load(path)
        files.append(path)
    origin=provenance()
    for path in files:
        origin['source_sha256'][str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    rows=[]
    for m,k in parse_shapes('suite'):
        matrix,vector=generate_workload(m,k)
        for partition in ('row','k'):
            identity=dict(m=m,k=k,partition=partition.upper())
            try:
                plan=plan_from_mapping(map_gemv(matrix,vector,strategy=partition))
            except MappingError as error:
                rows.extend(dict(model=name,**identity,status='infeasible',reason=str(error)) for name in configs)
                continue
            baseline=None
            for name,timing in configs.items():
                run=run_plan(plan,endpoint_factory=FinalGather if partition=='row' else PSumReduce,
                    tile_timing=TileComputeConfig(),root_timing=timing,max_cycles=20000)
                assert run.y==_golden(matrix,vector) and len(run.host_done)==1
                assert len(run.ack_keys)==len(plan.input_writes)
                summary=run.summary()
                metrics=dict(cycles=run.mesh.cycle,flit_hops=run.mesh.flit_hops,
                    result_packets=summary['result_packet_count'],input_acks=len(run.ack_keys),
                    input_load_cycles=summary['phase_cycles']['input_load'],
                    compute_and_gather_cycles=summary['phase_cycles']['compute_and_gather'],
                    completion_drain_cycles=summary['phase_cycles']['completion_drain'],
                    fifo_full_input_cycles=run.mesh.fifo_full_cycles,
                    ni_inject_stall_tile_cycles=sum(run.mesh.inject_stall_cycles),
                    tile_result_stall_cycles=sum(s['result_stall_cycles'] for s in run.compute_stats.values()))
                assert metrics['cycles']==sum(metrics[key] for key in ('input_load_cycles','compute_and_gather_cycles','completion_drain_cycles'))
                if baseline is None: baseline=metrics
                for key in ('flit_hops','result_packets','input_acks','input_load_cycles'):
                    assert metrics[key]==baseline[key]
                if timing is not None:
                    stats=run.root_stats
                    assert stats['accepted_packets']==stats['processed_packets']==metrics['result_packets']
                    assert stats['max_rx_slots']<=timing.rx_queue_packets
                    assert stats['max_reduction_entries']<=timing.reduction_entries
                    assert stats['result_write_elements']==m
                    assert stats['done_created_count']==stats['done_accepted_count']==1
                    assert stats['add_elements']==(2*m if partition=='k' else 0)
                    metrics.update({key:stats[key] for key in ('service_cycles','rx_blocked_flit_cycles',
                        'rx_full_cycles','max_rx_slots','max_reduction_entries','done_stall_cycles')})
                rows.append(dict(model=name,**identity,status='ok',golden_ok=True,metrics=metrics,
                                 root_stats=run.root_stats))
        print(f'{m}x{k} checked',flush=True)
    models={}
    for name,timing in configs.items():
        results=[r for r in rows if r['model']==name]
        paired=[]
        for m,k in parse_shapes('suite'):
            pair={r['partition']:r['metrics']['cycles'] for r in results if r['m']==m and r['k']==k and r['status']=='ok'}
            if len(pair)==2: paired.append(pair)
        models[name]=dict(config=asdict(timing) if timing is not None else None,
            cases=len(results),ok=sum(r['status']=='ok' for r in results),
            infeasible=sum(r['status']=='infeasible' for r in results),paired_shapes=len(paired),
            k_wins=sum(p['K']<p['ROW'] for p in paired),row_wins=sum(p['ROW']<p['K'] for p in paired),
            ties=sum(p['ROW']==p['K'] for p in paired))
    payload=dict(schema_version=1,scope='request-level Tile + actual finite Root service + cycle NoC; no synthetic gate; functional input commit; resident weights; command/VALIDATE excluded; Root not RTL-calibrated',
        placement=dict(workers=[1,2],root=3),tile_timing=asdict(TileComputeConfig()),models=models,
        cases=len(rows),ok=sum(r['status']=='ok' for r in rows),
        infeasible=sum(r['status']=='infeasible' for r in rows),provenance=origin,results=rows)
    (directory/'results.json').write_text(json.dumps(payload,indent=2)+'\n')
    hand={str(lanes):hand_trace(lanes) for lanes in (1,2,4)}
    (directory/'hand_traces.json').write_text(json.dumps(hand,indent=2)+'\n')
    flat=[{**{key:value for key,value in r.items() if key not in ('metrics','root_stats')},**r.get('metrics',{})} for r in rows]
    fields=list(dict.fromkeys(key for r in flat for key in r))
    with (directory/'results.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader();writer.writerows(flat)
    with tarfile.open(directory/'source_snapshot.tar.gz','w:gz') as snapshot:
        for name in origin['source_sha256']: snapshot.add(ROOT/name,arcname=name)
    print(json.dumps({key:payload[key] for key in ('cases','ok','infeasible','models')},indent=2))
    return payload


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    run(args.output_dir)


if __name__=='__main__': main()
