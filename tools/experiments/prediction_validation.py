#!/usr/bin/env python3
"""Validate v0.4 on frozen development measurements and predeclared new shapes.

All ranking precedes numerical evaluations. Fresh policies get independent
numerical calls; the catalogue oracle is collected afterwards. Packet-proxy
calls, modeled cycles and measured ranking overhead are disclosed separately.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
for directory in (ROOT,ROOT/'src'): sys.path.insert(0,str(directory))
from compiler.mapping_search import catalogue,clear_prediction_cache,predict,predict_v03
from compiler.unindp_adapter import allowed_grids
from simulator.functional import int32_wrap
from simulator.system.root_config import RootTiming
from simulator.system.scalable import run_grid
from simulator.system.tile_config import TileComputeConfig
from tools.experiments.mapping_evaluation import spearman
from tools.experiments.scalable_experiments import digest,save,seal,source_hashes,verified

POLICIES=('unindp-compact','noc-v03','noc-v04')
PROTOCOL=ROOT/'ini/prediction_v0_4.json'
REFERENCE=ROOT/'data/results/mapping_search_20261028/records.json.gz'


def fingerprint(protocol=PROTOCOL):
    files=source_hashes()
    for path in (Path(__file__).resolve(),ROOT/'tools/experiments/mapping_evaluation.py'):
        files[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()
    return dict(source_sha256=files,protocol=json.loads(protocol.read_text()),
                protocol_source_sha256=hashlib.sha256(protocol.read_text(encoding='utf-8').encode()).hexdigest(),
                tile_timing=asdict(TileComputeConfig()),root_timing=asdict(RootTiming()))


def get_catalogue(m,k,protocol):
    return catalogue(m,k,grids=protocol['grids'],placements=protocol['placements'],
                     reductions=protocol['reductions'],mesh_x=protocol['mesh'][0],mesh_y=protocol['mesh'][1])


def rank(plans,policy):
    clear_prediction_cache()
    started=time.perf_counter()
    eligible=list(plans.plans)
    if policy=='unindp-compact':
        first=eligible[0]
        allowed=allowed_grids(first.m,first.k,tuple(sorted({math.prod(p.grid) for p in eligible})))
        eligible=[p for p in eligible if p.placement=='compact' and p.grid in allowed]
    elif policy not in ('noc-v03','noc-v04'): raise ValueError('unknown predictor')
    rows=[]
    for plan in eligible:
        estimate=(predict(plan,communication=False) if policy=='unindp-compact' else
                  predict_v03(plan) if policy=='noc-v03' else predict(plan))
        rows.append(dict(plan_sha256=plan.digest,estimate=vars(estimate)))
    rows.sort(key=lambda row:(row['estimate']['cycles'],row['plan_sha256']))
    return dict(ranking=rows,ranking_seconds=time.perf_counter()-started,
                predictor_calls=len(rows),packet_proxy_calls=len(rows) if policy=='noc-v04' else 0,
                proxy_modeled_cycles=sum(r['estimate']['compute_gather_cycles'] for r in rows) if policy=='noc-v04' else 0)


def assess(m,k,split,policies,measured,budgets,generation_seconds):
    optimum=min(row['metrics']['cycles'] for row in measured.values())
    rows=[]
    for policy in POLICIES:
        result=policies[policy]
        ranking=result['ranking'];pred=[r['estimate']['cycles'] for r in ranking]
        actual=[measured[r['plan_sha256']]['metrics']['cycles'] for r in ranking]
        correlation=spearman(pred,actual)
        for budget in budgets:
            candidates=ranking[:budget]
            winner=min(candidates,key=lambda r:(measured[r['plan_sha256']]['metrics']['cycles'],r['plan_sha256']))
            identity=winner['plan_sha256'];cycles=measured[identity]['metrics']['cycles']
            evaluation=result.get('evaluation_seconds')
            rows.append(dict(m=m,k=k,split=split,policy=policy,budget=budget,plan_sha256=identity,
                cycles=cycles,oracle_cycles=optimum,regret=cycles/optimum-1,oracle_hit=cycles==optimum,
                spearman=correlation,predictor_calls=result['predictor_calls'],packet_proxy_calls=result['packet_proxy_calls'],
                proxy_modeled_cycles=result['proxy_modeled_cycles'],ranking_seconds=result['ranking_seconds'],
                exact_evaluations=len(candidates),
                search_seconds=(generation_seconds+result['ranking_seconds']+sum(evaluation[:budget])) if evaluation is not None else None))
    return rows


def phase_errors(policies,measured):
    result=[]
    for policy in ('noc-v03','noc-v04'):
        for row in policies[policy]['ranking']:
            actual=measured[row['plan_sha256']]['metrics'];estimate=row['estimate']
            kernel=actual['phase_cycles']['compute_and_gather']+actual['phase_cycles']['completion_drain']
            result.append(dict(policy=policy,plan_sha256=row['plan_sha256'],actual_cycles=actual['cycles'],
                predicted_cycles=estimate['cycles'],input_error=estimate['input_cycles']-actual['phase_cycles']['input_load'],
                kernel_error=estimate['compute_gather_cycles']-kernel,
                relative_error=(estimate['cycles']-actual['cycles'])/actual['cycles']))
            if policy=='noc-v04':
                if estimate['input_cycles']!=actual['phase_cycles']['input_load']:
                    raise AssertionError('protocol-derived input timing disagrees with evaluator')
                if estimate['flit_hops']!=actual['network']['flit_hops']:
                    raise AssertionError('packet surrogate lost/added traffic')
                if estimate['max_router_flits']>4 or estimate['max_ni_flits']>4 or estimate['max_rx_slots']>2:
                    raise AssertionError('predictor exceeded declared credits')
    return result


def replay(output,protocol_path=PROTOCOL,reference=REFERENCE):
    version=fingerprint(protocol_path);protocol=version['protocol']
    records=json.loads(gzip.decompress(reference.read_bytes()))['cases']
    # Only the predictor module is changed. Verify every evaluator, layout,
    # workload and configuration dependency before reusing frozen cycles.
    for name,expected in records[0]['provenance']['source_sha256'].items():
        if name=='src/compiler/mapping_search.py': continue
        if hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest()!=expected:
            raise ValueError(f'frozen evaluator changed: {name}')
    cases=[]
    for record in records:
        m,k=record['m'],record['k'];started=time.perf_counter()
        plans=get_catalogue(m,k,protocol);generation=time.perf_counter()-started
        if {p.digest for p in plans.plans}!=set(record['measured']):
            raise ValueError('frozen catalogue changed')
        policies={policy:rank(plans,policy) for policy in POLICIES}
        for name,old_policy in (('unindp-compact','unindp-adapted'),('noc-v03','noc')):
            scores={row['plan_sha256']:row['estimate']['cycles'] for row in record['policies'][old_policy]['ranking']}
            assert all(row['estimate']['cycles']==scores[row['plan_sha256']] for row in policies[name]['ranking'])
        cases.append(dict(m=m,k=k,split='development-replay',attempted=plans.attempted,feasible=len(plans.plans),
            generation_seconds=generation,policies=policies,
            quality=assess(m,k,'development-replay',policies,record['measured'],protocol['budgets'],generation),
            phase_errors=phase_errors(policies,record['measured'])))
        print(f'development replay {m}x{k}: {len(plans.plans)} timing predictions checked',flush=True)
    report=seal(dict(version=version,reference_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
        scope='development replay, not fresh validation; no numerical calls or compile-time claim',cases=cases))
    save(output/'replay.json',report)
    return report


def fresh_case(m,k,output,protocol_path=PROTOCOL):
    version=fingerprint(protocol_path);protocol=version['protocol']
    path=output/f'fresh-{m}x{k}.result.json'
    if path.exists():
        previous=verified(path,'result_sha256')
        if previous['version_sha256']!=digest(version): raise ValueError('resume would mix predictor sources')
        return previous
    rng=random.Random(protocol['seed']+m*65537+k)
    W=[[rng.randint(-128,127) for _ in range(k)] for _ in range(m)]
    x=[rng.randint(-128,127) for _ in range(k)]
    started=time.perf_counter();plans=get_catalogue(m,k,protocol)
    generation=time.perf_counter()-started
    # Freeze ALL rankings before the first numerical result is available.
    policies={policy:rank(plans,policy) for policy in POLICIES}
    identities={p.digest:p for p in plans.plans};measured={};numeric_calls=0
    for policy,result in policies.items():
        result['evaluation_seconds']=[]
        for row in result['ranking'][:max(protocol['budgets'])]:
            plan=identities[row['plan_sha256']];started=time.perf_counter()
            actual=run_grid(plan,W,x);elapsed=time.perf_counter()-started;numeric_calls+=1
            result['evaluation_seconds'].append(elapsed)
            measured[plan.digest]=dict(metrics=actual.summary(),wall_seconds=elapsed)
    # Complete the finite-catalogue reference AFTER all blind policy choices.
    for plan in plans.plans:
        if plan.digest in measured: continue
        started=time.perf_counter();actual=run_grid(plan,W,x);numeric_calls+=1
        measured[plan.digest]=dict(metrics=actual.summary(),wall_seconds=time.perf_counter()-started)
    golden=[int32_wrap(sum(a*b for a,b in zip(row,x))) for row in W]
    if any(row['metrics']['y']!=golden for row in measured.values()): raise AssertionError('fresh GEMV mismatch')
    report=seal(dict(m=m,k=k,split='fresh',version=version,version_sha256=digest(version),
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        python=platform.python_version(),platform=platform.system(),attempted=plans.attempted,feasible=len(plans.plans),
        rejected=plans.rejected,golden_ok=True,numerical_calls=numeric_calls,
        plans={p.digest:p.to_dict() for p in plans.plans},measured=measured,policies=policies,
        generation_seconds=generation,quality=assess(m,k,'fresh',policies,measured,protocol['budgets'],generation),
        phase_errors=phase_errors(policies,measured)),'result_sha256')
    save(path,report);return report


def check_case(record,version):
    if record['version_sha256']!=digest(version) or digest(record['version'])!=digest(version) or not record['golden_ok']:
        raise ValueError('fresh sources or correctness disagree')
    m,k=record['m'],record['k'];protocol=version['protocol']
    rng=random.Random(protocol['seed']+m*65537+k)
    W=[[rng.randint(-128,127) for _ in range(k)] for _ in range(m)]
    x=[rng.randint(-128,127) for _ in range(k)]
    golden=[int32_wrap(sum(a*b for a,b in zip(row,x))) for row in W]
    plans=get_catalogue(m,k,protocol)
    if (record['attempted']!=plans.attempted or record['feasible']!=len(plans.plans) or
        set(record['measured'])!={p.digest for p in plans.plans}):
        raise ValueError('fresh catalogue is incomplete or changed')
    for identity,value in record['measured'].items():
        actual=value['metrics']
        if (actual['plan_sha256']!=identity or actual['y']!=golden or actual['transaction_done']!=1 or
            actual['cycles']!=sum(actual['phase_cycles'].values()) or
            actual['tile_timing']!=version['tile_timing'] or actual['root_timing']!=version['root_timing']):
            raise ValueError('fresh numerical/completion/hardware contract disagrees')
    expected=assess(m,k,'fresh',record['policies'],record['measured'],protocol['budgets'],record['generation_seconds'])
    if digest(expected)!=digest(record['quality']): raise ValueError('fresh budget quality disagrees with measured cycles')
    if digest(phase_errors(record['policies'],record['measured']))!=digest(record['phase_errors']):
        raise ValueError('fresh phase diagnostics disagree')


def summarize(inputs,output,*,protocol_path=PROTOCOL,replay_path=None):
    version=fingerprint(protocol_path);protocol=version['protocol'];found={}
    for directory in inputs:
        for path in Path(directory).rglob('*.result.json'):
            record=verified(path,'result_sha256');key=record['m'],record['k']
            if key in found: raise ValueError('duplicate fresh case')
            check_case(record,version)
            found[key]=record
    if set(found)!={tuple(shape) for shape in protocol['fresh_shapes']}: raise ValueError('incomplete/unknown fresh cases')
    cases=list(found.values())
    if replay_path is not None:
        development=verified(replay_path)
        if digest(development['version'])!=digest(version): raise ValueError('development predictor sources differ')
        cases+=development['cases']
    rows=[row for case in cases for row in case['quality']];groups=[]
    for split in sorted({r['split'] for r in rows}):
        for budget in protocol['budgets']:
            baseline={(r['m'],r['k']):r for r in rows if r['split']==split and r['budget']==budget and r['policy']=='unindp-compact'}
            for policy in POLICIES:
                selected=[r for r in rows if r['split']==split and r['budget']==budget and r['policy']==policy]
                ratios=[baseline[r['m'],r['k']]['cycles']/r['cycles'] for r in selected]
                groups.append(dict(split=split,budget=budget,policy=policy,cases=len(selected),
                    geomean_speedup=math.exp(sum(math.log(s) for s in ratios)/len(ratios)),
                    wins=sum(s>1 for s in ratios),ties=sum(s==1 for s in ratios),losses=sum(s<1 for s in ratios),
                    oracle_hits=sum(r['oracle_hit'] for r in selected),max_regret=max(r['regret'] for r in selected),
                    worst_speedup=min(ratios),mean_ranking_seconds=sum(r['ranking_seconds'] for r in selected)/len(selected),
                    mean_search_seconds=(sum(r['search_seconds'] for r in selected)/len(selected)) if split=='fresh' else None))
    errors=[]
    for split in sorted({case['split'] for case in cases}):
        for policy in ('noc-v03','noc-v04'):
            selected=[row for case in cases if case['split']==split for row in case['phase_errors'] if row['policy']==policy]
            errors.append(dict(split=split,policy=policy,candidates=len(selected),
                model_period_mape=sum(abs(r['relative_error']) for r in selected)/len(selected),
                max_absolute_input_error=max(abs(r['input_error']) for r in selected),
                max_absolute_kernel_error=max(abs(r['kernel_error']) for r in selected)))
    report=seal(dict(version=version,fresh_cases=len(found),development_cases=len(cases)-len(found),
        attempted=sum(r['attempted'] for r in found.values()),feasible=sum(r['feasible'] for r in found.values()),
        scope='same evaluator/hardware; fresh choices before oracle; proxy overhead included; cycle errors relative to Python, not RTL',
        groups=groups,phase_errors=errors,results=rows))
    save(output/'summary.json',report)
    import csv
    with (output/'quality.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('replay','run','merge'))
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--protocol',type=Path,default=PROTOCOL)
    parser.add_argument('--inputs',type=Path,nargs='+')
    parser.add_argument('--replay',type=Path)
    parser.add_argument('--shards',type=int,default=1)
    parser.add_argument('--shard-index',type=int,default=0)
    args=parser.parse_args()
    if args.command=='replay': replay(args.output_dir,args.protocol)
    elif args.command=='merge':
        if not args.inputs: parser.error('--inputs required')
        report=summarize(args.inputs,args.output_dir,protocol_path=args.protocol,replay_path=args.replay)
        print(f"{report['fresh_cases']} fresh and {report['development_cases']} development cases summarized")
    else:
        if not 0<=args.shard_index<args.shards: parser.error('invalid shard')
        protocol=json.loads(args.protocol.read_text())
        for index,(m,k) in enumerate(protocol['fresh_shapes']):
            if index%args.shards==args.shard_index:
                record=fresh_case(m,k,args.output_dir,args.protocol)
                print(f"fresh {m}x{k}: {record['feasible']}/{record['attempted']} plans numerically correct",flush=True)


if __name__=='__main__': main()
