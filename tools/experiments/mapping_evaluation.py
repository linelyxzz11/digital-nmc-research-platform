#!/usr/bin/env python3
"""Evaluate blind mapping policies before collecting the finite-catalogue oracle.

Each shape is an independent, resumable unit. All policies share hardware,
catalogue and exact-evaluation budgets. Oracle cycles never enter the predictor.
"""
from __future__ import annotations
import argparse
import csv
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT,ROOT/'src'):
    sys.path.insert(0,str(directory))
from compiler.mapping_search import MAIN_GRIDS,catalogue,search,clear_prediction_cache
from simulator.system.root_config import RootTiming
from simulator.system.tile_config import TileComputeConfig
from simulator.system.scalable import run_grid
from tools.experiments.scalable_experiments import digest,save,seal,verified,source_hashes

CALIBRATION = ((32,128),(128,32),(64,256),(256,64))
HOLDOUT = ((31,127),(33,129),(63,255),(127,33),(129,65),(255,63),
           (17,257),(65,257),(96,192),(192,96),(32,1024),(128,512))
POLICIES = ('unindp-adapted','noc','fixed-k','fixed-row')
BUDGETS = (1,2,4,8)


def provenance():
    files = source_hashes()
    files['tools/experiments/mapping_evaluation.py'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return dict(source_sha256=files,grids=MAIN_GRIDS,placements=('compact','spread'),
                reductions=('central','tree'),budgets=BUDGETS,seed=20261028,
                tile_timing=asdict(TileComputeConfig()),root_timing=asdict(RootTiming()),
                measurement='weight-resident input+compute+gather+drain; model cycles',
                predictor='source-derived packet reservation; no measured-cycle training')


def average_ranks(values):
    ordered = sorted(range(len(values)),key=lambda i:values[i])
    ranks = [0.0]*len(values)
    start = 0
    while start < len(ordered):
        end = start+1
        while end < len(ordered) and values[ordered[start]] == values[ordered[end]]:
            end += 1
        for position in range(start,end):
            ranks[ordered[position]] = (start+end-1)/2
        start = end
    return ranks


def spearman(x,y):
    if len(x)!=len(y) or not x:
        raise ValueError('equal nonempty vectors required')
    a,b = average_ranks(x),average_ranks(y)
    ma,mb = sum(a)/len(a),sum(b)/len(b)
    va = sum((v-ma)**2 for v in a); vb = sum((v-mb)**2 for v in b)
    return sum((u-ma)*(v-mb) for u,v in zip(a,b))/math.sqrt(va*vb) if va*vb else None


def one_case(m,k,split,output):
    expected = provenance()
    path = output/f'{split}-{m}x{k}.result.json'
    if path.exists():
        previous = verified(path,'result_sha256')
        if previous['provenance_sha256']!=digest(expected):
            raise ValueError('resume would mix model/policy sources; use a fresh output directory')
        return previous
    rng = random.Random(expected['seed']+m*65537+k)
    W = [[rng.randint(-128,127) for _ in range(k)] for _ in range(m)]
    x = [rng.randint(-128,127) for _ in range(k)]
    started = time.perf_counter()
    candidates = catalogue(m,k)
    generation_seconds = time.perf_counter()-started
    policy_results, measured = {}, {}
    # Policies execute first. Each gets fresh exact simulator calls, including
    # overlapping candidates, so reported search costs are not cache lookups.
    for policy in POLICIES:
        clear_prediction_cache()
        timings = []
        def evaluator(plan):
            start = time.perf_counter()
            result = run_grid(plan,W,x)
            elapsed = time.perf_counter()-start
            timings.append(elapsed)
            if plan.digest in measured and measured[plan.digest]['metrics']['cycles']!=result.cycles:
                raise AssertionError('same candidate changed between policy executions')
            measured[plan.digest] = dict(metrics=result.summary(),wall_seconds=elapsed)
            return result
        result = search(m,k,W,x,policy=policy,exact_budget=max(BUDGETS),
                        candidates=candidates,evaluator=evaluator)
        policy_results[policy] = dict(result.summary(),per_evaluation_seconds=timings)
    # Only now fill in the catalogue reference, for regret/ranking evaluation.
    for plan in candidates.plans:
        if plan.digest in measured:
            continue
        started = time.perf_counter()
        run = run_grid(plan,W,x)
        measured[plan.digest] = dict(metrics=run.summary(),wall_seconds=time.perf_counter()-started)
    golden = [sum(a*b for a,b in zip(row,x)) for row in W]
    if any(record['metrics']['y']!=golden for record in measured.values()):
        raise AssertionError('mapped results disagree with independent integer GEMV')
    oracle = min(record['metrics']['cycles'] for record in measured.values())
    quality = []
    for policy,result in policy_results.items():
        ranking = result['ranking']
        ids = [row['plan_sha256'] for row in ranking]
        predictions = [row['estimate']['cycles'] for row in ranking]
        cycles = [measured[identity]['metrics']['cycles'] for identity in ids]
        rank_correlation = spearman(predictions,cycles)
        optimum_ids = {identity for identity,record in measured.items() if record['metrics']['cycles']==oracle}
        for budget in BUDGETS:
            exact = result['evaluated'][:budget]
            winner = min(exact,key=lambda row:(row['cycles'],row['plan_sha256']))
            quality.append(dict(policy=policy,budget=budget,actual_evaluations=len(exact),
                plan_sha256=winner['plan_sha256'],cycles=winner['cycles'],oracle_cycles=oracle,
                regret=winner['cycles']/oracle-1,oracle_hit=winner['cycles']==oracle,
                topk_optimum_recall=len(set(ids[:budget]) & optimum_ids)/len(optimum_ids),
                spearman=rank_correlation,
                search_seconds=generation_seconds+result['prediction_seconds']+
                               sum(result['per_evaluation_seconds'][:budget])))
    report = seal(dict(m=m,k=k,split=split,seed=expected['seed'],golden_ok=True,
        provenance=expected,provenance_sha256=digest(expected),python=platform.python_version(),
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        attempted=candidates.attempted,feasible=len(candidates.plans),rejected=candidates.rejected,
        plans={plan.digest:plan.to_dict() for plan in candidates.plans},
        measured=measured,policies=policy_results,quality=quality,generation_seconds=generation_seconds),
        'result_sha256')
    save(path,report)
    return report


def merge(inputs,output,profile='main'):
    expected = {('calibration',m,k) for m,k in CALIBRATION}
    if profile == 'main':
        expected |= {('holdout',m,k) for m,k in HOLDOUT}
    elif profile == 'smoke':
        expected = {('calibration',32,128)}
    else:
        raise ValueError('unknown profile')
    found = {}
    hashes = set()
    for directory in inputs:
        for path in Path(directory).rglob('*.result.json'):
            record = verified(path,'result_sha256')
            key = record['split'],record['m'],record['k']
            if key not in expected or key in found:
                raise ValueError(f'unknown/duplicate case: {key}')
            if not record['golden_ok'] or record['provenance_sha256']!=digest(record['provenance']):
                raise ValueError('invalid case provenance/golden')
            found[key] = record
            hashes.add(record['provenance_sha256'])
    if set(found)!=expected or len(hashes)!=1:
        raise ValueError(f'incomplete or mixed sources: {len(found)}/{len(expected)} cases')
    rows = []
    for (split,m,k),record in sorted(found.items()):
        for quality in record['quality']:
            rows.append(dict(split=split,m=m,k=k,**quality))
    groups = []
    for split in sorted({row['split'] for row in rows}):
        for budget in BUDGETS:
            base = {(row['m'],row['k']):row for row in rows
                    if row['split']==split and row['budget']==budget and row['policy']=='unindp-adapted'}
            for policy in POLICIES:
                selected = [row for row in rows if row['split']==split and row['budget']==budget and row['policy']==policy]
                speedups = [base[(row['m'],row['k'])]['cycles']/row['cycles'] for row in selected]
                groups.append(dict(split=split,policy=policy,budget=budget,cases=len(selected),
                    geomean_speedup_vs_unindp_adapted=math.exp(sum(math.log(x) for x in speedups)/len(speedups)),
                    worst_speedup=min(speedups),wins=sum(x>1 for x in speedups),ties=sum(x==1 for x in speedups),
                    losses=sum(x<1 for x in speedups),mean_regret=sum(row['regret'] for row in selected)/len(selected),
                    max_regret=max(row['regret'] for row in selected),oracle_hits=sum(row['oracle_hit'] for row in selected),
                    mean_search_seconds=sum(row['search_seconds'] for row in selected)/len(selected)))
    report = seal(dict(profile=profile,cases=len(found),candidate_runs=sum(r['feasible'] for r in found.values()),
        sources_sha256=next(iter(hashes)),scope=provenance()['measurement'],
        reference='minimum within declared catalogue; not a general optimum',groups=groups,results=rows))
    save(output/'summary.json',report)
    output.mkdir(parents=True,exist_ok=True)
    with (output/'quality.csv').open('w',newline='') as stream:
        writer = csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('run','merge'))
    parser.add_argument('--profile',choices=('smoke','main'),default='main')
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--inputs',type=Path,nargs='+')
    parser.add_argument('--shards',type=int,default=1)
    parser.add_argument('--shard-index',type=int,default=0)
    args = parser.parse_args()
    if args.command=='merge':
        if not args.inputs:
            parser.error('--inputs is required for merge')
        report = merge(args.inputs,args.output_dir,args.profile)
        print(f"{report['cases']} cases, {report['candidate_runs']} unique feasible candidates merged")
    else:
        if not 0<=args.shard_index<args.shards:
            parser.error('invalid shard')
        cases = [('calibration',m,k) for m,k in CALIBRATION]
        if args.profile=='main':
            cases += [('holdout',m,k) for m,k in HOLDOUT]
        else:
            cases = cases[:1]
        for index,(split,m,k) in enumerate(cases):
            if index%args.shards != args.shard_index:
                continue
            report = one_case(m,k,split,args.output_dir)
            print(f"{split} {m}x{k}: {report['feasible']}/{report['attempted']} candidates correct",flush=True)


if __name__=='__main__':
    main()
