#!/usr/bin/env python3
"""Fixed-hardware mapping audit: placement/split candidates and restricted oracle."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
for directory in (ROOT,ROOT/'src'): sys.path.insert(0,str(directory))
from compiler.auto_mapper_v0_1 import MappingError, map_gemv
from simulator.system.demo1 import FinalGather, plan_from_mapping, run_plan
from simulator.system.demo2 import PSumReduce
from simulator.system.root_config import RootTiming
from simulator.system.tile_config import TileComputeConfig
from tools.experiments.mapper_sweep import enumerate_cases, generate_workload, parse_shapes, _golden


def run(output,shapes='suite',splits='sample'):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    rows=[];summaries=[];started=time.perf_counter()
    tile_timing=TileComputeConfig();root_timing=RootTiming()
    for m,k in parse_shapes(shapes):
        matrix,vector=generate_workload(m,k);golden=_golden(matrix,vector)
        local=[]
        for candidate in enumerate_cases(m,k,placement='all',k_splits='sample' if splits=='sample' else 'default'):
            row=dict(m=m,k=k,partition=candidate.strategy.upper(),workers=list(candidate.workers),
                     root_tile=candidate.root_tile,split=candidate.k_split,status='infeasible')
            begin=time.perf_counter()
            try:
                mapping=map_gemv(matrix,vector,strategy=candidate.strategy,workers=candidate.workers,
                    root_tile=candidate.root_tile,k_split=candidate.k_split)
            except MappingError as error:
                row['reason']=str(error);rows.append(row);local.append(row);continue
            row['generation_seconds']=time.perf_counter()-begin
            row['split']=mapping['split']
            plan=plan_from_mapping(mapping)
            begin=time.perf_counter()
            result=run_plan(plan,endpoint_factory=FinalGather if candidate.strategy=='row' else PSumReduce,
                            tile_timing=tile_timing,root_timing=root_timing,max_cycles=20000)
            assert result.y==golden and len(result.host_done)==1
            assert len(result.ack_keys)==len(plan.input_writes)
            summary=result.summary()
            row.update(status='ok',golden_ok=True,cycles=result.mesh.cycle,
                evaluation_seconds=time.perf_counter()-begin,cost_score=mapping['diagnostics']['cost_score'],
                flit_hops=result.mesh.flit_hops,
                ni_inject_stall_tile_cycles=sum(result.mesh.inject_stall_cycles),
                fifo_full_input_cycles=result.mesh.fifo_full_cycles,
                root_rx_blocked_flit_cycles=result.root_stats['rx_blocked_flit_cycles'],
                root_service_cycles=result.root_stats['service_cycles'],
                root_max_rx_slots=result.root_stats['max_rx_slots'],
                mac_utilization_total_window=m*k/(8*result.mesh.cycle),
                input_load_cycles=summary['phase_cycles']['input_load'],
                compute_and_gather_cycles=summary['phase_cycles']['compute_and_gather'],
                completion_drain_cycles=summary['phase_cycles']['completion_drain'])
            assert 0<row['mac_utilization_total_window']<=1
            rows.append(row);local.append(row)
        valid=[r for r in local if r['status']=='ok'];assert valid
        fixed=[r for r in valid if r['workers']==[1,2] and r['root_tile']==3 and
               r['split']==((m+1)//2 if r['partition']=='ROW' else (k+1)//2)]
        costs={r['partition']:r['cycles'] for r in fixed}
        auto=map_gemv(matrix,vector,strategy='auto')['partition']
        assert auto in costs
        default_oracle=min(costs.values())
        best=min(valid,key=lambda r:(r['cycles'],r['flit_hops'],r['root_tile'],r['workers'],r['split']))
        summaries.append(dict(m=m,k=k,candidates=len(local),feasible=len(valid),
            fixed_row=costs.get('ROW'),fixed_k=costs.get('K'),current_auto=costs[auto],
            fixed_placement_oracle=default_oracle,expanded_oracle=best['cycles'],
            expanded_choice={key:best[key] for key in ('partition','workers','root_tile','split')},
            headroom_vs_current_auto=costs[auto]/best['cycles'],
            headroom_vs_fixed_oracle=default_oracle/best['cycles']))
        print(f'{m}x{k}: {len(valid)}/{len(local)} valid; AUTO={costs[auto]} oracle={best["cycles"]}',flush=True)
    sources=('src/compiler/auto_mapper_v0_1.py','src/simulator/system/demo1.py',
             'src/simulator/system/tile_timing.py','src/simulator/system/tile_memory.py',
             'src/simulator/system/timed_endpoint.py','src/simulator/system/root_config.py',
             'src/simulator/communication/v0_1.py','src/hardware.py',
             'tools/experiments/mapper_sweep.py','tools/experiments/mapping_candidate_sweep.py')
    payload=dict(schema_version=1,
        scope='Same 2x2 hardware: balanced ROW, sampled K cuts, all legal two-worker/Root placements; exhaustive oracle over this finite candidate list only',
        timing_scope='Weight-resident, functional input commit, timed Tile and Root; not full end-to-end RTL',
        tile_timing=asdict(tile_timing),root_timing=asdict(root_timing),
        shapes=shapes,k_splits=splits,wall_seconds=time.perf_counter()-started,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources},
        cases=len(rows),ok=sum(r['status']=='ok' for r in rows),summaries=summaries,results=rows)
    (output/'results.json').write_text(json.dumps(payload,indent=2)+'\n')
    fields=list(dict.fromkeys(key for row in rows for key in row))
    with (output/'results.csv').open('w',newline='') as target:
        writer=csv.DictWriter(target,fieldnames=fields);writer.writeheader()
        writer.writerows({**r,'workers':','.join(map(str,r['workers']))} for r in rows)
    return payload


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'dump/mapping_candidates')
    parser.add_argument('--shapes',default='suite')
    parser.add_argument('--k-splits',choices=('balanced','sample'),default='sample')
    args=parser.parse_args();run(args.output_dir,args.shapes,args.k_splits)


if __name__=='__main__': main()
