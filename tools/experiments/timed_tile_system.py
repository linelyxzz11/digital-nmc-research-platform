#!/usr/bin/env python3
"""Fixed-placement integration check and explicit SRAM hardware sensitivity."""
from dataclasses import asdict
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[2]
for directory in (ROOT,ROOT/'src'):
    sys.path.insert(0,str(directory))
from simulator.system.compute_timing import ComputeTiming
from simulator.system.tile_config import TileComputeConfig
from tools.experiments.mapper_sweep import parse_shapes
from tools.experiments.pressure_sweep import provenance, run_experiment, save_results
from tools.experiments.summarize_compute_timing import summarize


def run(directory):
    directory=Path(directory)
    shapes=parse_shapes('suite')
    configs={'formula_reference':dict(compute_timing=ComputeTiming())}
    config_files=[]
    for name in ('baseline','weight_ii2','result_1bank','shared_wx_1bank'):
        path=ROOT/'ini/tile_system'/f'{name}.json'
        config_files.append(path)
        configs[f'tile_{name}']=dict(tile_timing=TileComputeConfig.load(path))
    origin=provenance()
    for path in [Path(__file__),*config_files]:
        origin['source_sha256'][str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
    payloads={}
    for name,options in configs.items():
        payload=run_experiment(shapes,('open','eighth'),(0,),placement='default',**options)
        assert not payload['summary']['timeout'] and not payload['summary']['validation_failure']
        payload['provenance']=origin
        save_results(directory/name,payload)
        payloads[name]=payload
        print(name,json.dumps(payload['summary']),flush=True)
    reference=payloads['formula_reference']['results']
    baseline=payloads['tile_baseline']['results']
    matched=0
    for left,right in zip(reference,baseline,strict=True):
        assert left['status']==right['status']
        if left['status']=='ok':
            assert all(right['metrics'][key]==value for key,value in left['metrics'].items())
            assert right['links']==left['links']
            assert right['ni_inject_stall_by_tile']==left['ni_inject_stall_by_tile']
            matched+=1
    for payload in payloads.values():
        for left,right in zip(reference,payload['results'],strict=True):
            assert left['status']==right['status']
            if left['status']=='ok':
                for key in ('input_load_cycles','flit_hops','result_packets','input_acks','host_done_count'):
                    assert left['metrics'][key]==right['metrics'][key]
    examples=[]
    for name,payload in payloads.items():
        for row in payload['results']:
            if (row['m'],row['k']) in ((16,128),(32,64),(64,32),(128,16)) and row['status']=='ok':
                examples.append(dict(model=name,m=row['m'],k=row['k'],partition=row['partition'],
                                     profile=row['profile'],metrics=row['metrics']))
    summary=dict(schema_version=1,
        scope='request-level Tile/NI/2x2 shared clock; functional input commit/reduction; resident weights; no command/VALIDATE latency',
        profiles='open and eighth, phase 0 only; synthetic Root service',
        baseline_equivalence=dict(records=len(reference),feasible_metrics_matched=matched,
                                  infeasible_matched=len(reference)-matched),
        models={name:dict(summary=p['summary'],wins=summarize(p),
                          tile_timing=p['options'].get('tile_timing')) for name,p in payloads.items()},
        provenance=origin,examples=examples)
    (directory/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    with tarfile.open(directory/'source_snapshot.tar.gz','w:gz') as snapshot:
        for name in origin['source_sha256']:
            snapshot.add(ROOT/name,arcname=name)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.output_dir)
    print('Baseline equivalence:',json.dumps(result['baseline_equivalence']))


if __name__=='__main__': main()
