#!/usr/bin/env python3
"""Run matched Central/NMC cases; preserve cycles separately from wall time."""
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
    p.add_argument('--config',type=Path,default=ROOT/'ini/central_comparison/round1.json')
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args(); config=json.loads(a.config.read_text())
    a.output_dir.mkdir(parents=True,exist_ok=True)
    rows=[]; started=time.perf_counter()
    for m,k in config['shapes']:
        rng=random.Random(config['seed']+m*65536+k)
        w=[[rng.randrange(-128,128) for _ in range(k)] for _ in range(m)]
        x=[rng.randrange(-128,128) for _ in range(k)]
        wh=hashlib.sha256(bytes(v&255 for row in w for v in row)+bytes(v&255 for v in x)).hexdigest()
        for interval in config['link_intervals']:
            cases=[('nmc',0)]+[('central',node) for node in config['central_tiles']]
            for architecture,node in cases:
                t=time.perf_counter()
                result=run_comparison(w,x,architecture=architecture,central_tile=node,
                    link_interval=interval,prefetch_packets=config['prefetch_packets'],
                    sink_interval=config['sink_interval'])
                result.update(wall_seconds=time.perf_counter()-t,workload_sha256=wh)
                rows.append(result)
                name=f'{m}x{k}_{architecture}_node{node}_ii{interval}'
                (a.output_dir/(name+'.json')).write_text(json.dumps(result,indent=2)+'\n')
                print(name,result['total_cycles'],f"{result['wall_seconds']:.2f}s",flush=True)
    paired=[]
    for r in rows:
        if r['architecture']!='central':continue
        n=next(n for n in rows if n['architecture']=='nmc' and
               (n['m'],n['k'],n['link_interval'])==(r['m'],r['k'],r['link_interval']))
        assert r['y']==n['y'] and r['workers']==n['workers'] and r['workload_sha256']==n['workload_sha256']
        paired.append(dict(m=r['m'],k=r['k'],central_tile=r['central_tile'],
            raw_link_bytes_per_cycle=16/r['link_interval'],
            central_cycles=r['total_cycles'],nmc_cycles=n['total_cycles'],
            speedup=r['total_cycles']/n['total_cycles'],
            central_input_ready=r['input_ready_cycle'],nmc_input_ready=n['input_ready_cycle'],
            central_kernel_to_host=r['kernel_to_host_cycles'],nmc_kernel_to_host=n['kernel_to_host_cycles'],
            central_mac_utilization=r['mac_utilization_total'],nmc_mac_utilization=n['mac_utilization_total'],
            central_weight_network_bytes=r['network_payload_bytes']['weight'],nmc_weight_network_bytes=0,
            central_flit_hops=r['flit_hops'],nmc_flit_hops=n['flit_hops'],
            ideal_compute_cycles=r['m']*r['k']/128))
    with (a.output_dir/'comparison.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(paired[0]));writer.writeheader();writer.writerows(paired)
    sources=['src/simulator/system/central_comparison.py','src/simulator/communication/v0_1.py',
             'src/simulator/system/tile_timing.py','src/simulator/system/tile_memory.py',
             'src/simulator/functional/demo0_model.py','src/compiler/grid_mapper.py',
             'tools/experiments/central_comparison.py']
    metadata=dict(config=config,config_sha256=hashlib.sha256(a.config.read_bytes()).hexdigest(),
        source_sha256={s:hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in sources},
        base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        total_wall_seconds=time.perf_counter()-started,cases=len(rows),pairs=len(paired),all_golden_ok=all(r['golden_ok'] for r in rows),
        hardware=dict(physical_tiles=64,storage_workers=32,total_active_mac=128,
            weight_sram_bytes_per_storage_tile=4096,input_sram_bytes_per_cluster=1024,
            result_sram_bytes_per_cluster=1024,weight_bank_read_bytes_per_cycle=4,
            weight_bank_read_latency=1,input_port_bytes_per_cycle=1,
            host_output_bytes_per_cycle=16,router_fifo_flits=4,ni_fifo_flits=4,
            provisioned_prefetch_bytes=32*config['prefetch_packets']*16,
            note='Same capacity budgets; central relocates 32 X/Y buffers and four-lane clusters to one endpoint. No area/Fmax equivalence claim.'))
    (a.output_dir/'provenance.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps(dict(cases=len(rows),pairs=len(paired),all_golden_ok=metadata['all_golden_ok'])),flush=True)

if __name__=='__main__':main()
