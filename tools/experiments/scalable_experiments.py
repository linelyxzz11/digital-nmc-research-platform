#!/usr/bin/env python3
"""Prepare, resume and merge independent 64-Tile experiment shards."""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
for directory in (ROOT,ROOT/'src'): sys.path.insert(0,str(directory))
from compiler.auto_mapper_v0_1 import MappingError
from compiler.grid_mapper import map_grid
from simulator.system.root_config import RootTiming
from simulator.system.scalable import run_grid
from simulator.system.tile_config import TileComputeConfig
from simulator.functional import int32_wrap
from tools.experiments.mapper_sweep import generate_workload

CONTRACT='scalable-gemv-v0.2'


def digest(value):
    # JSON converts integer dictionary keys to strings. Normalize before sorting
    # so a recorded {7:...,16:...} hashes identically after another PC loads it.
    normalized=json.loads(json.dumps(value,ensure_ascii=True,allow_nan=False))
    return hashlib.sha256(json.dumps(normalized,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()).hexdigest()


def seal(value,key='sha256'): return dict(value,**{key:digest(value)})


def verified(path,key='sha256'):
    payload=json.loads(Path(path).read_text(encoding='utf-8'))
    expected=payload.pop(key,None)
    if expected!=digest(payload): raise ValueError(f'integrity check failed: {path}')
    payload[key]=expected
    return payload


def save(path,payload):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    temporary.replace(path)


def source_hashes():
    files={ROOT/'src/hardware.py',Path(__file__).resolve(),ROOT/'requirements.txt',ROOT/'pyproject.toml',
           ROOT/'tools/experiments/mapper_sweep.py'}
    for folder in ('src/compiler','src/simulator'): files.update((ROOT/folder).rglob('*.py'))
    return {str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_text(encoding='utf-8').encode()).hexdigest() for p in sorted(files)}


def revision():
    try: return subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,stderr=subprocess.DEVNULL).strip()
    except (OSError,subprocess.CalledProcessError): raise ValueError('run from a Git checkout')


def prepare(path,*,profile='smoke',shards=3,shapes=None,grids=None,placements=('compact','spread')):
    if type(shards) is not int or shards<1: raise ValueError('shards must be positive')
    if profile not in ('smoke','main'): raise ValueError('profile must be smoke/main')
    shapes=shapes or ([(16,16)] if profile=='smoke' else [(32,128),(128,32),(64,256),(256,64)])
    grids=grids or ([(1,4),(4,1),(2,2)] if profile=='smoke' else [(1,16),(16,1),(4,4),(1,32),(32,1),(4,8),(8,4)])
    hardware=dict(mesh_x=8,mesh_y=8,host_tile=0,root_tile=0,
                  tile_timing=asdict(TileComputeConfig()),root_timing=asdict(RootTiming()))
    jobs=[]
    for m,k in shapes:
        for grid in grids:
            for placement in placements:
                for reduction in ('central','tree'):
                    spec=dict(m=m,k=k,mesh_x=8,mesh_y=8,grid=list(grid),root_tile=0,host_tile=0,
                              reduction=reduction,fan_in=4,placement=placement,seed=0,tid=1)
                    job=dict(job_id=digest(dict(spec=spec,hardware=hardware))[:20],index=len(jobs),
                             shard=len(jobs)%shards,spec=spec)
                    try:
                        plan=map_grid(**spec)
                        job.update(expected_status='ok',plan_sha256=plan.digest)
                    except MappingError as error:
                        job.update(expected_status='infeasible',plan_sha256=None,reason=str(error))
                    jobs.append(job)
    if len({j['job_id'] for j in jobs})!=len(jobs): raise ValueError('duplicate experiment specifications')
    payload=seal(dict(schema_version=1,contract=CONTRACT,profile=profile,shards=shards,
        hardware=hardware,workload='deterministic-int8-formula-v1',prepared_from_commit=revision(),
        source_sha256=source_hashes(),jobs=jobs))
    save(path,payload)
    return payload


def load_manifest(path):
    manifest=verified(path)
    if manifest['contract']!=CONTRACT or manifest['source_sha256']!=source_hashes():
        raise ValueError('manifest/model source mismatch; use the same published checkout and manifest')
    jobs=manifest['jobs']
    if type(manifest['shards']) is not int or manifest['shards']<1: raise ValueError('invalid shard count')
    hardware=manifest['hardware']
    TileComputeConfig.from_dict(hardware['tile_timing']);RootTiming.from_dict(hardware['root_timing'])
    if len({j['job_id'] for j in jobs})!=len(jobs): raise ValueError('duplicate manifest job IDs')
    for index,job in enumerate(jobs):
        if job['index']!=index or job['shard']!=index%manifest['shards']:
            raise ValueError('invalid shard assignment')
        spec=job['spec']
        if job['job_id']!=digest(dict(spec=spec,hardware=hardware))[:20]:
            raise ValueError('job identity disagrees with its specification')
        if any(spec[key]!=hardware[key] for key in ('mesh_x','mesh_y','root_tile','host_tile')):
            raise ValueError('job and hardware topology disagree')
        try:
            plan=map_grid(**spec)
        except MappingError:
            if job['expected_status']!='infeasible' or job['plan_sha256'] is not None:
                raise ValueError('manifest feasibility disagrees with Mapper')
        else:
            if job['expected_status']!='ok' or job['plan_sha256']!=plan.digest:
                raise ValueError('manifest plan disagrees with Mapper')
    return manifest


def check_result(result,manifest,job):
    if (result['manifest_sha256']!=manifest['sha256'] or result['job_id']!=job['job_id'] or
        result['shard']!=job['shard'] or result['plan_sha256']!=job['plan_sha256']):
        raise ValueError('result does not match manifest job')
    if result['status']!='error' and result['status']!=job['expected_status']:
        raise ValueError('result feasibility disagrees with the declared plan')
    if result['status']=='ok':
        metrics=result['metrics'];spec=job['spec']
        if (metrics['plan_sha256']!=job['plan_sha256'] or metrics['m']!=spec['m'] or metrics['k']!=spec['k'] or
            metrics['cycles']!=sum(metrics['phase_cycles'].values()) or metrics['transaction_done']!=1 or
            len(metrics['y'])!=spec['m'] or result.get('golden_ok') is not True or
            metrics['tile_timing']!=manifest['hardware']['tile_timing'] or
            metrics['root_timing']!=manifest['hardware']['root_timing'] or
            metrics['mesh']!=[spec['mesh_x'],spec['mesh_y']]):
            raise ValueError('result metric/completion contract is inconsistent')


def run_shard(manifest_path,shard,output,*,machine=None,max_jobs=None):
    manifest=load_manifest(manifest_path)
    if type(shard) is not int or not 0<=shard<manifest['shards']: raise ValueError('shard outside manifest')
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    commit=revision();completed=skipped=errors=0
    jobs=[j for j in manifest['jobs'] if j['shard']==shard]
    for job in jobs:
        path=output/f"{job['job_id']}.result.json"
        if path.exists():
            previous=verified(path,'result_sha256');check_result(previous,manifest,job)
            if previous['commit']!=commit: raise ValueError('resume would mix Git revisions; use a fresh output directory')
            if previous['status']!='error': skipped+=1;continue
        if max_jobs is not None and completed>=max_jobs: break
        base=dict(schema_version=1,manifest_sha256=manifest['sha256'],job_id=job['job_id'],
            shard=shard,plan_sha256=job['plan_sha256'],commit=commit,machine=machine or f'pc-{shard}',
            python=platform.python_version(),os=platform.system())
        started=time.perf_counter()
        try:
            if job['expected_status']=='infeasible':
                base.update(status='infeasible',reason=job['reason'])
            else:
                plan=map_grid(**job['spec'])
                if plan.digest!=job['plan_sha256']: raise ValueError('plan differs across environments')
                W,x=generate_workload(plan.m,plan.k)
                result=run_grid(plan,W,x,tile_timing=TileComputeConfig.from_dict(manifest['hardware']['tile_timing']),
                    root_timing=RootTiming.from_dict(manifest['hardware']['root_timing']))
                base.update(status='ok',golden_ok=True,metrics=result.summary())
        except Exception as error:
            base.update(status='error',error_type=type(error).__name__,error=str(error));errors+=1
        base['wall_seconds']=time.perf_counter()-started
        save(path,seal(base,'result_sha256'));completed+=1
        print(f"shard {shard}: {job['index']+1}/{len(manifest['jobs'])} {base['status']}",flush=True)
    receipt=seal(dict(manifest_sha256=manifest['sha256'],commit=commit,shard=shard,
        planned=len(jobs),executed=completed,resumed=skipped,errors=errors))
    save(output/f'shard-{shard}.receipt.json',receipt)
    return receipt


def merge(manifest_path,inputs,output):
    manifest=load_manifest(manifest_path);expected={j['job_id']:j for j in manifest['jobs']}
    found={};commits=set();outputs={};golden={}
    for directory in inputs:
        for path in sorted(Path(directory).rglob('*.result.json')):
            result=verified(path,'result_sha256');identity=result['job_id']
            if identity not in expected: raise ValueError(f'unknown job: {identity}')
            if identity in found: raise ValueError(f'duplicate job: {identity}')
            check_result(result,manifest,expected[identity])
            if result['status']=='error': raise ValueError(f'failed job: {identity}: {result["error"]}')
            found[identity]=result;commits.add(result['commit'])
            if result['status']=='ok':
                spec=expected[identity]['spec'];key=spec['m'],spec['k']
                data=tuple(result['metrics']['y'])
                if key not in golden:
                    W,x=generate_workload(*key)
                    golden[key]=tuple(int32_wrap(sum(a*b for a,b in zip(row,x))) for row in W)
                if data!=golden[key]: raise ValueError('result disagrees with independent merge golden')
                if key in outputs and outputs[key]!=data: raise ValueError('mapping policies disagree numerically')
                outputs[key]=data
    if len(commits)!=1: raise ValueError('mixed/missing Git revisions in result set')
    if set(found)!=set(expected): raise ValueError(f'incomplete results: {len(set(expected)-set(found))} jobs missing')
    rows=[]
    for job in manifest['jobs']:
        result=found[job['job_id']];spec=job['spec']
        row=dict(job_id=job['job_id'],m=spec['m'],k=spec['k'],grid_rows=spec['grid'][0],
            grid_cols=spec['grid'][1],compute_tiles=spec['grid'][0]*spec['grid'][1],
            placement=spec['placement'],reduction=spec['reduction'],status=result['status'])
        if result['status']=='ok':
            metrics=result['metrics'];network=metrics['network'];endpoints=metrics['endpoint_stats']
            packets=metrics['packet_counts']
            flits=2*packets.get('MemWritePacket',0)+packets.get('MemAckPacket',0)+2*packets.get('ResultPacket',0)+packets.get('TransactionDonePacket',0)
            row.update(cycles=metrics['cycles'],flit_hops=network['flit_hops'],flit_data_bytes=16*flits,
                input_load_cycles=metrics['phase_cycles']['input_load'],
                compute_and_gather_cycles=metrics['phase_cycles']['compute_and_gather'],
                completion_drain_cycles=metrics['phase_cycles']['completion_drain'],
                ni_inject_stall_tile_cycles=network['ni_inject_stall_tile_cycles'],
                root_rx_blocked_flit_cycles=sum(s['rx_blocked_flit_cycles'] for s in endpoints.values()),
                reducer_count=len(metrics['reducer_tiles']),wall_seconds=result['wall_seconds'])
        rows.append(row)
    best=[]
    for m,k in sorted(outputs):
        candidates=[r for r in rows if r['m']==m and r['k']==k and r['status']=='ok']
        winner=min(candidates,key=lambda r:(r['cycles'],r['job_id']))
        best.append(dict(m=m,k=k,cycles=winner['cycles'],job_id=winner['job_id'],
                         label='offline best within the manifest; not an implemented mapper'))
    payload=seal(dict(schema_version=1,manifest_sha256=manifest['sha256'],commit=commits.pop(),
        jobs=len(rows),ok=sum(r['status']=='ok' for r in rows),infeasible=sum(r['status']=='infeasible' for r in rows),
        complete=True,best=best,results=rows))
    output=Path(output);save(output/'summary.json',payload)
    fields=list(dict.fromkeys(key for row in rows for key in row))
    with (output/'results.csv').open('w',newline='') as target:
        writer=csv.DictWriter(target,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    return payload


def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    prepare_parser=commands.add_parser('prepare')
    prepare_parser.add_argument('--manifest',type=Path,required=True)
    prepare_parser.add_argument('--profile',choices=('smoke','main'),default='main')
    prepare_parser.add_argument('--shards',type=int,default=3)
    worker=commands.add_parser('run')
    worker.add_argument('--manifest',type=Path,required=True);worker.add_argument('--shard',type=int,required=True)
    worker.add_argument('--output-dir',type=Path,required=True);worker.add_argument('--machine')
    worker.add_argument('--max-jobs',type=int)
    merger=commands.add_parser('merge')
    merger.add_argument('--manifest',type=Path,required=True);merger.add_argument('--inputs',type=Path,nargs='+',required=True)
    merger.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='prepare':
        result=prepare(args.manifest,profile=args.profile,shards=args.shards)
        print(json.dumps(dict(jobs=len(result['jobs']),shards=result['shards'],manifest_sha256=result['sha256'])))
    elif args.command=='run':
        if args.max_jobs is not None and args.max_jobs<1: parser.error('--max-jobs must be positive')
        result=run_shard(args.manifest,args.shard,args.output_dir,machine=args.machine,max_jobs=args.max_jobs)
        if result['errors']: raise SystemExit(1)
    else:
        result=merge(args.manifest,args.inputs,args.output_dir)
        print(json.dumps({k:result[k] for k in ('jobs','ok','infeasible','complete')}))


if __name__=='__main__': main()
