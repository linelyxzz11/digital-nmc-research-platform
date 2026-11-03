#!/usr/bin/env python3
"""Audit mapping headroom on saved, fixed-hardware finite Tile/Root results."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from compiler.auto_mapper_v0_1 import map_gemv


def audit(source,output):
    source=Path(source);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    payload=json.loads(gzip.decompress(source.read_bytes()))
    cases={}
    for record in payload['results']:
        if record['model']!='baseline': continue
        key=record['m'],record['k']
        case=cases.setdefault(key,dict(m=key[0],k=key[1],ROW=None,K=None))
        if record['status']=='ok':
            assert record['golden_ok']
            case[record['partition']]=record['metrics']['cycles']
        else: assert record['status']=='infeasible'
    rows=[]
    for (m,k),case in cases.items():
        # The current mapper's decision depends on shape and fixed resources,
        # never on INT8 values. Saved workload arithmetic was checked upstream.
        mapping=map_gemv([[0]*k for _ in range(m)],[0]*k,strategy='auto')
        chosen=mapping['partition']
        assert case[chosen] is not None
        row=dict(case,AUTO=case[chosen],AUTO_partition=chosen,
                 ORACLE=min(v for v in (case['ROW'],case['K']) if v is not None))
        rows.append(row)
    common=[r for r in rows if r['ROW'] is not None and r['K'] is not None]
    assert common
    summaries={}
    for policy in ('ROW','K','AUTO','ORACLE'):
        speedups=[r['K']/r[policy] for r in common]
        regrets=[r[policy]/r['ORACLE']-1 for r in common]
        summaries[policy]=dict(feasible_shapes=sum(r[policy] is not None for r in rows),
            total_shapes=len(rows),common_shapes=len(common),
            geometric_mean_speedup_vs_K=math.exp(sum(map(math.log,speedups))/len(speedups)),
            mean_regret_fraction=sum(regrets)/len(regrets),worst_regret_fraction=max(regrets),
            optimal_hits=sum(r[policy]==r['ORACLE'] for r in common),
            wins_vs_K=sum(r[policy]<r['K'] for r in common),
            losses_vs_K=sum(r[policy]>r['K'] for r in common))
    files=('src/compiler/auto_mapper_v0_1.py','tools/experiments/mapping_headroom.py')
    result=dict(schema_version=1,
        scope='Reanalysis only: fixed placement, balanced ROW/K, identical finite Tile/Root hardware; restricted oracle, not UniNDP comparison',
        source=str(source.resolve().relative_to(ROOT)),input_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        result_provenance=payload['provenance'],placement=payload['placement'],
        tile_timing=payload['tile_timing'],root_timing=payload['models']['baseline']['config'],
        analysis_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files},
        policies=summaries,results=rows)
    (output/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    with (output/'results.csv').open('w',newline='') as target:
        writer=csv.DictWriter(target,fieldnames=rows[0].keys());writer.writeheader();writer.writerows(rows)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=ROOT/'data/results/finite_root_system_20261028/results.json.gz')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'dump/mapping_headroom')
    args=parser.parse_args()
    print(json.dumps(audit(args.input,args.output_dir)['policies'],indent=2))


if __name__=='__main__': main()
