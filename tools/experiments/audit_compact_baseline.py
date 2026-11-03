#!/usr/bin/env python3
"""Strengthen the adapted baseline with a fixed compact placement prior.

Replay decisions on the frozen, numerically validated candidate measurements.
No additional cycle simulations or compilation-wall-time claims are made.
"""
import argparse
from copy import deepcopy
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from compiler.mapping_search import catalogue,search,clear_prediction_cache


def audit_record(record):
    m,k=record['m'],record['k']
    first=next(iter(record['plans'].values()))
    plans=catalogue(m,k,grids=record['provenance']['grids'],placements=('compact',),
                    mesh_x=first['mesh_x'],mesh_y=first['mesh_y'])
    if any(plan.digest not in record['measured'] for plan in plans.plans):
        raise ValueError('the compact catalogue differs from the frozen measurements')
    rng=random.Random(record['seed']+m*65537+k)
    W=[[rng.randint(-128,127) for _ in range(k)] for _ in range(m)]
    x=[rng.randint(-128,127) for _ in range(k)]
    clear_prediction_cache()
    budgets=record['provenance']['budgets']
    result=search(m,k,W,x,policy='unindp-adapted',exact_budget=max(budgets),candidates=plans,
        evaluator=lambda plan:SimpleNamespace(cycles=record['measured'][plan.digest]['metrics']['cycles']))
    oracle=min(r['metrics']['cycles'] for r in record['measured'].values())
    rows=[]
    for budget in budgets:
        exact=result.evaluated[:budget]
        winner=min(exact,key=lambda row:(row['cycles'],row['plan_sha256']))
        rows.append(dict(split=record['split'],m=m,k=k,policy='unindp-compact',budget=budget,
            cycles=winner['cycles'],oracle_cycles=oracle,regret=winner['cycles']/oracle-1,
            oracle_hit=winner['cycles']==oracle,plan_sha256=winner['plan_sha256'],
            actual_evaluations=len(exact)))
    # Keep the actual predeclared NoC decisions, rather than retuning them.
    rows.extend(dict(split=record['split'],m=m,k=k,**deepcopy(row))
                for row in record['quality'] if row['policy']=='noc')
    return rows


def audit(records):
    rows=[row for record in records for row in audit_record(record)]
    groups=[]
    for split in sorted({r['split'] for r in rows}):
        for budget in sorted({r['budget'] for r in rows}):
            baseline={(r['m'],r['k']):r for r in rows if r['split']==split and
                      r['budget']==budget and r['policy']=='unindp-compact'}
            noc=[r for r in rows if r['split']==split and r['budget']==budget and r['policy']=='noc']
            ratios=[baseline[r['m'],r['k']]['cycles']/r['cycles'] for r in noc]
            groups.append(dict(split=split,budget=budget,cases=len(noc),
                geomean_speedup_vs_compact=math.exp(sum(math.log(r) for r in ratios)/len(ratios)),
                wins=sum(r>1 for r in ratios),ties=sum(r==1 for r in ratios),losses=sum(r<1 for r in ratios),
                worst_speedup=min(ratios),baseline_oracle_hits=sum(r['oracle_hit'] for r in baseline.values()),
                noc_oracle_hits=sum(r['oracle_hit'] for r in noc)))
    return dict(schema_version=1,scope='post-hoc baseline-strength audit; frozen candidate cycle replay; no NoC retuning',
        baseline='same UniNDP-adapted resource predictor/search, fixed compact placement; no free oracle placement choice',
        timing='no new simulator calls or compilation-time comparison',results=rows,groups=groups)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    raw=args.records.read_bytes()
    records=json.loads(gzip.decompress(raw))['cases']
    for name,expected in records[0]['provenance']['source_sha256'].items():
        if hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest()!=expected:
            raise ValueError(f'current model differs from measurements: {name}')
    report=audit(records)
    report.update(records_sha256=hashlib.sha256(raw).hexdigest(),
                  audit_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    for row in report['groups']:
        if row['split']=='holdout':
            print(row)


if __name__=='__main__':
    main()
