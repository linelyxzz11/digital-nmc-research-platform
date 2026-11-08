#!/usr/bin/env python3
"""Sweep weight payload size under a constant byte budget; preserve round 1."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from simulator.system.central_comparison import run_comparison


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=ROOT/'ini/central_comparison/weight_bursts.json')
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args(); config=json.loads(a.config.read_text())
    a.output_dir.mkdir(parents=True,exist_ok=True)
    rows=[]; paired=[]; checked=0; started=time.perf_counter()
    for m,k in config['shapes']:
        rng=random.Random(config['seed']+m*65536+k)
        w=[[rng.randrange(-128,128) for _ in range(k)] for _ in range(m)]
        x=[rng.randrange(-128,128) for _ in range(k)]
        wh=hashlib.sha256(bytes(v&255 for row in w for v in row)+bytes(v&255 for v in x)).hexdigest()
        for interval in config['link_intervals']:
            baseline=None; nmc=None
            cases=[('nmc',16)]+[('central',b) for b in config['weight_packet_bytes']]
            for architecture,burst in cases:
                budget=config['prefetch_bytes_per_cluster']
                assert budget%burst==0 and budget>=burst
                node=config['central_tile'] if architecture=='central' else 0
                t=time.perf_counter()
                r=run_comparison(w,x,architecture=architecture,central_tile=node,
                    link_interval=interval,weight_packet_bytes=burst,
                    prefetch_packets=budget//burst,sink_interval=config['sink_interval'])
                r.update(wall_seconds=time.perf_counter()-t,workload_sha256=wh)
                assert r['max_weight_reserved_bytes']<=budget
                rows.append(r)
                name=f'{m}x{k}_{architecture}_node{node}_ii{interval}_burst{burst}'
                (a.output_dir/(name+'.json')).write_text(json.dumps(r,indent=2)+'\n')
                print(name,r['total_cycles'],f"{r['wall_seconds']:.2f}s",flush=True)
                if burst==16:
                    old=ROOT/'data/results/central_comparison_20261108'/f'{m}x{k}_{architecture}_node{node}_ii{interval}.json'
                    if old.exists():
                        historical=json.loads(old.read_text())
                        for field in ('y','total_cycles','input_ready_cycle','host_output_ready_cycles',
                                      'flit_hops','network_injected_flits','workload_sha256'):
                            assert r[field]==historical[field],(name,field)
                        checked+=1
                if architecture=='nmc':
                    nmc=r
                    continue
                assert r['y']==nmc['y'] and r['workers']==nmc['workers']
                if burst==16: baseline=r
                assert baseline is not None, 'configuration must begin with the 16 B reference'
                paired.append(dict(m=m,k=k,raw_link_bytes_per_cycle=16/interval,
                    weight_packet_bytes=burst,prefetch_packets=budget//burst,
                    prefetch_bytes_per_cluster=budget,central_cycles=r['total_cycles'],
                    nmc_cycles=nmc['total_cycles'],speedup=r['total_cycles']/nmc['total_cycles'],
                    central_cycle_reduction_vs_16=1-r['total_cycles']/baseline['total_cycles'],
                    central_input_ready=r['input_ready_cycle'],
                    central_kernel_to_host=r['kernel_to_host_cycles'],
                    central_mac_utilization=r['mac_utilization_total'],
                    weight_bytes=r['network_payload_bytes']['weight'],
                    weight_flits=r['network_injected_flits']['weight'],
                    credit_flits=r['network_injected_flits']['credit'],
                    flit_hops=r['flit_hops'],
                    max_weight_reserved_bytes=r['max_weight_reserved_bytes']))
    with (a.output_dir/'comparison.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(paired[0]));writer.writeheader();writer.writerows(paired)
    sources=['src/simulator/system/central_comparison.py','src/simulator/communication/v0_1.py',
             'src/simulator/system/tile_timing.py','src/simulator/system/tile_memory.py',
             'src/simulator/functional/demo0_model.py','src/compiler/grid_mapper.py',
             'tools/experiments/weight_burst_sweep.py']
    metadata=dict(config=config,config_sha256=hashlib.sha256(a.config.read_bytes()).hexdigest(),
        source_sha256={s:hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in sources},
        base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        total_wall_seconds=time.perf_counter()-started,cases=len(rows),pairs=len(paired),
        all_golden_ok=all(r['golden_ok'] for r in rows),round1_exact_replays=checked,
        hardware=dict(physical_routers=64,storage_workers=32,total_active_mac=128,
            prefetch_bytes_per_cluster=config['prefetch_bytes_per_cluster'],
            source_assembly_bytes=128,source_tx_bytes=128,source_pending_read_bytes=4,
            router_fifo_flits=4,ni_fifo_flits=4,
            note='Source stores fixed at maximum size for every point, including 16 B and NMC; not an area/Fmax or RTL claim.'))
    (a.output_dir/'provenance.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps({k:metadata[k] for k in ('cases','pairs','all_golden_ok','round1_exact_replays','total_wall_seconds')}),flush=True)


if __name__=='__main__':main()
