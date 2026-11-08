#!/usr/bin/env python3
"""Run independent central gateways and a shared-fabric bottleneck control."""
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
    p.add_argument('--config',type=Path,default=ROOT/'ini/central_comparison/multi_entry.json')
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();config=json.loads(a.config.read_text())
    a.output_dir.mkdir(parents=True,exist_ok=True)
    records=[];rows=[];checked=0;started=time.perf_counter()
    for workload in config['workloads']:
        m,k=workload['m'],workload['k']
        rng=random.Random(config['seed']+m*65536+k)
        w=[[rng.randrange(-128,128) for _ in range(k)] for _ in range(m)]
        x=[rng.randrange(-128,128) for _ in range(k)]
        wh=hashlib.sha256(bytes(v&255 for row in w for v in row)+bytes(v&255 for v in x)).hexdigest()
        for interval in workload['link_intervals']:
            nmc=None;one_entry=None
            for model in config['models']:
                arch=model['architecture'];entries=model.get('entries')
                burst=config['weight_packet_bytes'];budget=config['prefetch_bytes_per_cluster']
                assert budget%burst==0
                t=time.perf_counter()
                r=run_comparison(w,x,architecture=arch,
                    central_tile=config['central_tile'] if arch=='central' else 0,
                    central_entries=entries,fabric_lanes=model.get('fabric_lanes',4),
                    gateway_queue_packets=config['gateway_queue_packets'],
                    fabric_base_latency=config['fabric_base_latency'],
                    weight_packet_bytes=burst,prefetch_packets=budget//burst,link_interval=interval)
                r.update(model=model['name'],wall_seconds=time.perf_counter()-t,workload_sha256=wh)
                if arch=='nmc':nmc=r
                assert nmc is not None and r['y']==nmc['y'] and r['workers']==nmc['workers']
                if entries==[config['central_tile']]:one_entry=r
                if entries is None:
                    old_name=(f'{m}x{k}_central_node36_ii{interval}_burst128' if arch=='central'
                              else f'{m}x{k}_nmc_node0_ii{interval}_burst16')
                    old=ROOT/'data/results/weight_bursts_20261108'/(old_name+'.json')
                    if old.exists():
                        historical=json.loads(old.read_text())
                        for field in ('y','workers','total_cycles','input_ready_cycle','host_output_ready_cycles',
                                      'flit_hops','network_injected_flits','workload_sha256'):
                            assert r[field]==historical[field],(model['name'],field)
                        checked+=1
                else:
                    f=r['fabric'];wire=r['network_injected_flits']
                    assert sum(f['transferred_flits']['in'].values())==wire['input']+wire['weight']
                    assert sum(f['transferred_flits']['out'].values())==wire['credit']+wire['result']
                    assert r['max_gateway_rx_packets']<=config['gateway_queue_packets']
                    assert r['max_gateway_tx_packets']<=config['gateway_queue_packets']
                name=f'{m}x{k}_ii{interval}_{model["name"]}'
                (a.output_dir/(name+'.json')).write_text(json.dumps(r,indent=2)+'\n')
                records.append(r)
                row=dict(m=m,k=k,model=model['name'],raw_link_bytes_per_cycle=16/interval,
                    entry_count=len(entries) if entries else (1 if arch=='central' else 0),
                    fabric_bytes_per_cycle=16*model['fabric_lanes'] if entries else 0,
                    total_cycles=r['total_cycles'],host_output_ready_cycles=r['host_output_ready_cycles'],
                    input_ready_cycle=r['input_ready_cycle'],kernel_to_host_cycles=r['kernel_to_host_cycles'],
                    central_over_nmc=r['total_cycles']/nmc['total_cycles'],
                    central_improvement_vs_one_entry=(1-r['total_cycles']/one_entry['total_cycles']) if entries else '',
                    mac_utilization=r['mac_utilization_total'],flit_hops=r['flit_hops'],
                    weight_payload_bytes=r['network_payload_bytes'].get('weight',0),
                    internal_in_flits=sum(r['fabric']['transferred_flits']['in'].values()) if entries else 0,
                    internal_out_flits=sum(r['fabric']['transferred_flits']['out'].values()) if entries else 0,
                    max_gateway_rx_packets=r['max_gateway_rx_packets'],
                    max_gateway_tx_packets=r['max_gateway_tx_packets'],
                    wall_seconds=r['wall_seconds'])
                rows.append(row)
                print(name,r['total_cycles'],f"ratio={row['central_over_nmc']:.3f}",f"{r['wall_seconds']:.2f}s",flush=True)
    with (a.output_dir/'comparison.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    sources=['src/simulator/system/central_comparison.py','src/simulator/system/central_fabric.py',
             'src/simulator/communication/v0_1.py','src/simulator/system/tile_timing.py',
             'src/simulator/system/tile_memory.py','src/simulator/functional/demo0_model.py',
             'src/compiler/grid_mapper.py','tools/experiments/central_entry_sweep.py']
    metadata=dict(config=config,config_sha256=hashlib.sha256(a.config.read_bytes()).hexdigest(),
        source_sha256={s:hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in sources},
        base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        total_wall_seconds=time.perf_counter()-started,cases=len(records),
        all_golden_ok=all(r['golden_ok'] for r in records),legacy_exact_replays=checked,
        hardware=dict(physical_routers=64,storage_workers=32,total_active_mac=128,
            hub=36,reserved_gateways=[36,37,44,45],weight_bytes_per_bank=4096,
            prefetch_bytes_per_cluster=128,source_assembly_bytes=128,source_tx_bytes=128,
            queue_packets_per_gateway_direction=config['gateway_queue_packets'],
            fabric_port_bytes_per_cycle=16,max_fabric_bytes_per_cycle_per_direction=64,
            gateway_rx_and_fabric_in_payload_budget_bytes=4*config['gateway_queue_packets']*128,
            gateway_tx_payload_budget_bytes=4*config['gateway_queue_packets']*16,
            fabric_out_payload_budget_bytes=4*config['gateway_queue_packets']*16,
            note='Queue budgets reserve all four gateways in all cases, including unused ports/NMC. Headers, descriptors and pipeline control are additional metadata. No area/Fmax claim.'))
    (a.output_dir/'provenance.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps({k:metadata[k] for k in ('cases','all_golden_ok','legacy_exact_replays','total_wall_seconds')}),flush=True)


if __name__=='__main__':main()
