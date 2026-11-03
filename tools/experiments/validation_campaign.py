"""Frozen validation extension. Reuses the existing catalogue, predictors and evaluator.

freeze must run before run. Rankings precede all numerical labels. Timings use
independent numerical calls; catalogue quality uses recorded measured prefixes.
"""
from __future__ import annotations
import argparse, hashlib, json, math, os, platform, random, statistics, subprocess, sys, time
from dataclasses import asdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from compiler.mapping_search import catalogue,clear_prediction_cache,predict,predict_v03
from simulator.system.scalable import run_grid
from simulator.system.tile_config import TileComputeConfig
from simulator.system.root_config import RootTiming
from tools.experiments.prediction_validation import rank
from tools.experiments.scalable_experiments import source_hashes,save,seal,verified,digest
from tools.experiments.mapping_evaluation import CALIBRATION,HOLDOUT,spearman
POLICIES=('unindp-compact','noc-v03','noc-v04','fixed-row','fixed-k')
SHAPES=((37,19),(19,31),(35,69),(67,35),(81,81),(97,161),(161,97),(201,29),(29,201),(251,127),(128,640),(256,511))
HARDWARE={'default':({},{}),'mac2':({'mac_interval':2},{}),
 'weight_issue2':({'memory':{'weight':{'issue_interval':2}}},{}),
 'root_scalar':({},{'lanes_per_cycle':1}),'root_rx1':({},{'rx_queue_packets':1})}
def sources():
    return {**source_hashes(),Path(__file__).relative_to(ROOT).as_posix():hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
      **{f'tools/experiments/{name}.py':hashlib.sha256((ROOT/f'tools/experiments/{name}.py').read_bytes()).hexdigest()
         for name in ('prediction_validation','mapping_evaluation')}}
def independent_golden(W,x):
    return [((sum(int(a)*int(b) for a,b in zip(row,x))+(1<<31))%(1<<32))-(1<<31) for row in W]
def workload(m,k,seed):
    rng=random.Random(seed+m*65537+k)
    W=[[rng.randint(-128,127) for _ in range(k)] for _ in range(m)]
    x=[rng.randint(-128,127) for _ in range(k)]
    return W,x
def rankings(plans,tile,root):
    result={}
    for policy in POLICIES:
        clear_prediction_cache();start=time.perf_counter()
        if policy=='unindp-compact':
            # Preserve author-method pruning; replace timing arguments explicitly.
            from compiler.unindp_adapter import allowed_grids
            first=plans.plans[0];allowed=allowed_grids(first.m,first.k,tuple(sorted({math.prod(p.grid) for p in plans.plans})))
            eligible=[p for p in plans.plans if p.placement=='compact' and p.grid in allowed]
        else:eligible=[p for p in plans.plans if policy not in ('fixed-row','fixed-k') or p.grid[1 if policy=='fixed-row' else 0]==1]
        rows=[]
        for plan in eligible:
            fn=predict_v03 if policy=='noc-v03' else predict
            estimate=fn(plan,communication=policy!='unindp-compact',tile_timing=tile,root_timing=root)
            rows.append(dict(plan_sha256=plan.digest,estimate=vars(estimate)))
        rows.sort(key=lambda r:(r['estimate']['cycles'],r['plan_sha256']))
        result[policy]=dict(ranking=rows,ranking_seconds=time.perf_counter()-start,predictor_calls=len(rows),
          packet_proxy_calls=len(rows) if policy in ('noc-v04','fixed-row','fixed-k') else 0,
          proxy_modeled_cycles=sum(r['estimate']['compute_gather_cycles'] for r in rows) if policy in ('noc-v04','fixed-row','fixed-k') else 0,
          pruned=len(plans.plans)-len(rows),cache='cleared before each policy')
    return result
def measure(plan,W,x,tile,root):
    start=time.perf_counter();actual=run_grid(plan,W,x,tile_timing=tile,root_timing=root)
    elapsed=time.perf_counter()-start;metrics=actual.summary()
    assert metrics['y']==independent_golden(W,x)
    assert metrics['transaction_done']==1 and sum(metrics['phase_cycles'].values())==metrics['cycles']
    return dict(metrics=metrics,wall_seconds=elapsed,evidence_level='timed_python_evaluator')
def case(m,k,point,out,protocol):
    run_id=f'{point}-{m}x{k}';path=out/(run_id+'.json')
    if path.exists():
        r=verified(path);assert r['protocol_sha256']==protocol['sha256'];return r
    tile,root=HARDWARE[point];tile=TileComputeConfig.from_dict(tile);root=RootTiming.from_dict(root)
    W,x=workload(m,k,protocol['seed']);start=time.perf_counter();plans=catalogue(m,k);generation=time.perf_counter()-start
    frozen=verified(out/(run_id+'.ranking.json'))
    assert frozen['protocol_sha256']==protocol['sha256']
    ranked=frozen['policies']
    measured={};errors=[]
    for plan in plans.plans:
        try:measured[plan.digest]=measure(plan,W,x,tile,root)
        except Exception as e:
            errors.append(dict(plan_sha256=plan.digest,status='timeout' if isinstance(e,TimeoutError) else 'error',exception=repr(e)))
            save(out/(run_id+'.failure.json'),dict(W=W,x=x,seed=protocol['seed'],errors=errors,plan=plan.to_dict()))
    if errors:raise RuntimeError(f'{run_id}: {errors}')
    optimum=min(v['metrics']['cycles'] for v in measured.values());quality=[];phase=[]
    for policy,r in ranked.items():
        for budget in protocol['budgets']:
            prefix=r['ranking'][:budget]
            if not prefix:
                quality.append(dict(policy=policy,budget=budget,status='infeasible',reason='no candidate in fixed axis'));continue
            best=min(prefix,key=lambda p:(measured[p['plan_sha256']]['metrics']['cycles'],p['plan_sha256']))
            a=measured[best['plan_sha256']]['metrics'];quality.append(dict(policy=policy,budget=budget,status='ok',cycles=a['cycles'],
              oracle_cycles=optimum,regret=a['cycles']/optimum-1,oracle_hit=a['cycles']==optimum,plan_sha256=best['plan_sha256'],
              exact_budget=len(prefix),quality_evidence='recorded prefix replay; not a new timed search',
              spearman=spearman([p['estimate']['cycles'] for p in r['ranking']],[measured[p['plan_sha256']]['metrics']['cycles'] for p in r['ranking']])))
        if policy not in ('noc-v03','noc-v04'):continue
        for p in r['ranking']:
            a=measured[p['plan_sha256']]['metrics'];e=p['estimate'];ph=a['phase_cycles'];kernel=ph['compute_and_gather']+ph['completion_drain']
            if policy=='noc-v04':
                assert e['flit_hops']==a['network']['flit_hops']
                assert e['max_router_flits']<=4 and e['max_ni_flits']<=4 and e['max_rx_slots']<=root.rx_queue_packets
            phase.append(dict(policy=policy,plan_sha256=p['plan_sha256'],total_relative=(e['cycles']-a['cycles'])/a['cycles'],
              input_relative=(e['input_cycles']-ph['input_load'])/ph['input_load'],kernel_relative=(e['compute_gather_cycles']-kernel)/kernel))
    r=seal(dict(run_id=run_id,m=m,k=k,hardware_point=point,protocol_sha256=protocol['sha256'],seed=protocol['seed'],
      workload_sha256=digest(dict(W=W,x=x)),tile_timing=asdict(tile),root_timing=asdict(root),attempted=plans.attempted,
      feasible=len(plans.plans),rejected=plans.rejected,errors=errors,generation_seconds=generation,policies=ranked,
      measured=measured,plans={p.digest:p.to_dict() for p in plans.plans},quality=quality,phase_errors=phase))
    save(path,r);print(run_id,len(measured),'passed',flush=True);return r
def walltime(out,protocol):
    m,k=81,81;W,x=workload(m,k,protocol['seed']);samples=[]
    for repeat in range(6):
        order=POLICIES[repeat%5:]+POLICIES[:repeat%5]
        for policy in order:
            start=time.perf_counter();plans=catalogue(m,k);generation=time.perf_counter()-start
            # Only the selected policy is timed; ranking helper iterates policies,
            # so use the existing rank for primary methods and a filtered catalogue for fixed axes.
            clear_prediction_cache()
            if policy in ('fixed-row','fixed-k'):
                from types import SimpleNamespace
                selected=SimpleNamespace(plans=tuple(p for p in plans.plans if p.grid[1 if policy=='fixed-row' else 0]==1))
                r=rank(selected,'noc-v04')
            else:r=rank(plans,policy)
            ids={p.digest:p for p in plans.plans};durations=[];cycles=[]
            for row in r['ranking'][:4]:
                v=measure(ids[row['plan_sha256']],W,x,TileComputeConfig(),RootTiming());durations.append(v['wall_seconds']);cycles.append(v['metrics']['cycles'])
            samples.append(dict(repeat=repeat,warmup=repeat==0,policy=policy,order=list(order),m=m,k=k,generation_seconds=generation,
              ranking=r,evaluation_seconds=durations,cycles=cycles,budgets={str(b):generation+r['ranking_seconds']+sum(durations[:b]) for b in protocol['budgets']}))
            save(out/'walltime.json',seal(dict(protocol_sha256=protocol['sha256'],samples=samples,concurrency=1,cache='cold per policy; one warmup + five timed rounds')))
        print('walltime round',repeat,flush=True)
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['freeze','run','time']);parser.add_argument('--protocol',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True);args=parser.parse_args()
    if args.command=='freeze':
        if args.protocol.exists():raise ValueError('refuse to replace frozen protocol')
        old=set(CALIBRATION+HOLDOUT+((23,79),(47,143),(79,47),(111,173),(159,91),(223,43),(95,287),(191,239)))
        assert not old.intersection(SHAPES)
        save(args.protocol,seal(dict(seed=20261031,shapes=SHAPES,budgets=[1,2,4],policies=POLICIES,hardware=HARDWARE,
          sensitivity_shapes=[[67,35],[161,97]],application_shapes=[[128,128],[8,128],[128,8]],
          unsupported_shapes=[[256,513],[640,128],[4096,4096]],source_sha256=sources(),commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
          coverage='odd and tail, square, tall/wide, max M=256, capacity-sensitive K=511/640; no tuning; no selection based on cycle labels; v1/v2 superseded before any numerical label after feasibility-only inspection found 256x513 unsupported',
          walltime='81x81; all policies; one warmup + five timed rounds; rotated order; cold cache; budget prefixes 1/2/4',
          evidence_level='timed_python_evaluator',scope='64 physical Tiles; resident weights; functional input commit; no RTL or model accuracy claim')));return
    protocol=verified(args.protocol);assert protocol['source_sha256']==sources(),'sources changed after freeze'
    args.output_dir.mkdir(parents=True,exist_ok=True)
    if args.command=='time':
        walltime(args.output_dir,protocol);return
    jobs=[(m,k,'default') for m,k in protocol['shapes']+protocol['application_shapes']]
    jobs += [(m,k,p) for p in HARDWARE if p!='default' for m,k in protocol['sensitivity_shapes']]
    # Freeze every shape / hardware point / policy before the first evaluator call.
    for m,k,point in jobs:
        target=args.output_dir/f'{point}-{m}x{k}.ranking.json'
        if target.exists():
            assert verified(target)['protocol_sha256']==protocol['sha256'];continue
        t,r=HARDWARE[point]
        ranked=rankings(catalogue(m,k),TileComputeConfig.from_dict(t),RootTiming.from_dict(r))
        save(target,seal(dict(protocol_sha256=protocol['sha256'],policies=ranked)))
        print('ranked',point,m,k,flush=True)
    for m,k in protocol['shapes']:case(m,k,'default',args.output_dir,protocol)
    for point in HARDWARE:
        if point=='default':continue
        for m,k in protocol['sensitivity_shapes']:case(m,k,point,args.output_dir,protocol)
    for m,k in protocol['application_shapes']:case(m,k,'default',args.output_dir,protocol)
    unsupported=[]
    for m,k in protocol['unsupported_shapes']:
        from compiler.grid_mapper import map_grid
        from compiler.mapping_search import MAIN_GRIDS
        from compiler.auto_mapper_v0_1 import MappingError
        rejected=[];feasible=0
        for grid in MAIN_GRIDS:
            for placement in ('compact','spread'):
                for reduction in ('central','tree'):
                    try:map_grid(m,k,grid=grid,placement=placement,reduction=reduction);feasible+=1
                    except MappingError as e:rejected.append(dict(grid=grid,placement=placement,reduction=reduction,reason=str(e)))
        unsupported.append(dict(m=m,k=k,attempted=28,feasible=feasible,rejected=rejected,status='unsupported' if not feasible else 'feasible'))
    save(args.output_dir/'unsupported.json',seal(dict(cases=unsupported)))
    # Run `time` separately with all other experiment processes stopped.
if __name__=='__main__':main()
