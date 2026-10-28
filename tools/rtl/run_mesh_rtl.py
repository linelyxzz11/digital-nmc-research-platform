#!/usr/bin/env python3
"""Run complete 64-physical-Tile RTL with real packets, SRAM and reductions."""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from compiler.grid_mapper import map_grid
from simulator.communication import MemWritePacket,MemAckPacket,ResultPacket,TransactionDonePacket
from simulator.communication.v0_1 import Flit,FlitType
from simulator.communication.scalable import PacketCodec
from simulator.functional import Demo0Tile,int32_wrap
from simulator.system.scalable import run_grid

RTL = ROOT/'src/rtl'
SOURCES = [RTL/'nmc_tile'/name for name in ('nmc_mac4.sv','nmc_tile_sram.sv','nmc_tile_compute.sv')]
SOURCES += [RTL/'nmc_mesh'/name for name in ('nmc_router.sv','nmc_mesh.sv','nmc_packet_tx.sv','nmc_reduce.sv','nmc_mesh_system.sv')]
TB = RTL/'nmc_mesh/tb_nmc_mesh_system.sv'


def command(args,directory,name,*,timeout=240):
    result = subprocess.run([str(arg) for arg in args],cwd=ROOT,text=True,
                            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
    (directory/f'{name}.log').write_text(result.stdout)
    if result.returncode:
        raise AssertionError(f'{name} failed ({result.returncode}): {result.stdout[-5000:]}')
    return result.stdout


def cases():
    return [
        dict(name='hand_5x3_central',m=5,k=3,grid=(1,2),reduction='central',placement='spread'),
        dict(name='tail_tree',m=13,k=37,grid=(3,4),reduction='tree',placement='spread'),
        dict(name='row32',m=128,k=32,grid=(32,1),reduction='central',placement='compact'),
        dict(name='grid16_central',m=32,k=128,grid=(4,4),reduction='central',placement='compact'),
        dict(name='grid32_tree',m=32,k=128,grid=(4,8),reduction='tree',placement='spread'),
        dict(name='k32_central',m=256,k=64,grid=(1,32),reduction='central',placement='spread'),
        dict(name='k32_tree',m=256,k=64,grid=(1,32),reduction='tree',placement='spread'),
        dict(name='backpressure',m=32,k=32,grid=(1,16),reduction='tree',placement='spread',stall_until=300),
        dict(name='reset_in_flight',m=32,k=32,grid=(1,16),reduction='tree',placement='spread',abort_cycle=20),
    ]


def inputs_for(plan,vector):
    shards = {shard.tile_id:shard for shard in plan.slices}
    result = []
    maximum = max(s.k_end-s.k_start for s in plan.slices)
    for seq,offset in enumerate(range(0,maximum,4)):
        for worker in plan.workers:
            shard = shards[worker]
            chunk = vector[shard.k_start+offset:min(shard.k_start+offset+4,shard.k_end)] if offset<shard.k_end-shard.k_start else []
            if chunk:
                result.append(MemWritePacket(0,worker,1,0x1000+offset,(1<<len(chunk))-1,
                                             seq,sum((v & 255)<<(8*i) for i,v in enumerate(chunk))))
    return result


def prepare(item,directory):
    directory.mkdir(parents=True,exist_ok=True)
    plan = map_grid(**{key:item[key] for key in ('m','k','grid','reduction','placement')})
    rng = random.Random(20261028+item['m']+item['k'])
    matrix = [[rng.randint(-128,127) for _ in range(item['k'])] for _ in range(item['m'])]
    vector = [rng.randint(-128,127) for _ in range(item['k'])]
    if item['name'].startswith('hand_'):
        matrix = [[1,2,3],[-1,2,0],[4,-2,1],[0,0,-3],[7,1,-1]]
        vector = [2,-1,3]
    weights = []
    nodes = {}
    masks = []
    for shard,task in zip(plan.slices,plan.tasks):
        tile = Demo0Tile(shard.tile_id,tile_count=64)
        tile.load_weight_matrix([row[shard.k_start:shard.k_end] for row in matrix[shard.row_start:shard.row_end]])
        size = 4*((task.m_local+3)//4)*task.k_local
        for offset in range(0,size,4):
            weights.append(f'{shard.tile_id} {offset:04x} {int.from_bytes(tile.weight_sram[offset:offset+4],"little"):08x}\n')
        nodes[shard.tile_id] = (shard.tile_id,task.m_local,task.k_local,shard.row_start,
                                task.result_dst_tile,int(task.result_kind=='PSUM'),0,0,0,0,0)
    root_sources = 0
    for begin,end,bits in plan.root_ranges:
        root_sources |= bits
        for index in range(begin,end):
            masks.append(f'0 {index} {bits:016x}\n')
    nodes[0] = (0,0,0,0,0,0,1,int(plan.root_mode=='REDUCE_PSUM'),0,plan.m,root_sources)
    for node in plan.reducers:
        bits = sum(1<<child for child in node.sources)
        nodes[node.tile_id] = (node.tile_id,0,0,node.row_start,node.parent,0,1,1,1,node.row_end-node.row_start,bits)
        for index in range(node.row_start,node.row_end):
            masks.append(f'{node.tile_id} {index} {bits:016x}\n')
    codec = PacketCodec(64)
    inputs = inputs_for(plan,vector)
    (directory/'weights.txt').write_text(''.join(weights))
    (directory/'nodes.txt').write_text(''.join(' '.join(map(str,row[:-1]))+f' {row[-1]:016x}\n' for row in nodes.values()))
    (directory/'masks.txt').write_text(''.join(masks))
    (directory/'inputs.txt').write_text(''.join(f'{codec.pack(packet)[0].data:032x} {codec.pack(packet)[1].data:032x}\n' for packet in inputs))
    config = (plan.m,item.get('stall_until',0),item.get('abort_cycle',-1),0)
    (directory/'config.hex').write_text(''.join(f'{value & 0xffffffff:08x}\n' for value in config))
    (directory/'plan.json').write_text(json.dumps(plan.to_dict(),indent=2)+'\n')
    return plan,matrix,vector,inputs


def expected_results(plan,matrix,vector):
    by_id = {shard.tile_id:shard for shard in plan.slices}
    reducers = {node.tile_id:node for node in plan.reducers}
    def leaves(tile):
        if tile in by_id:
            return [by_id[tile]]
        return [shard for child in reducers[tile].sources for shard in leaves(child)]
    packets = []
    parents = {task.tile_id:task.result_dst_tile for task in plan.tasks}
    parents.update({node.tile_id:node.parent for node in plan.reducers})
    for source,parent in parents.items():
        shards = leaves(source)
        begin,end = shards[0].row_start,shards[0].row_end
        ks = sorted({index for shard in shards for index in range(shard.k_start,shard.k_end)})
        kind = ('FINAL' if plan.grid[1]==1 else 'PSUM') if source in by_id else ('FINAL' if parent==0 else 'PSUM')
        for base in range(begin,end,4):
            size = min(4,end-base)
            data = tuple([int32_wrap(sum(matrix[r][index]*vector[index] for index in ks)) for r in range(base,base+size)]+[0]*(4-size))
            packets.append(ResultPacket(source,parent,1,base,(1<<size)-1,data,kind,base+size==end))
    return packets


def compare(item,plan,matrix,vector,inputs,directory):
    codec = PacketCodec(64)
    phase = 1 if item.get('abort_cycle',-1)>=0 else 0
    partial = defaultdict(list);received = []
    with (directory/'packets.csv').open() as stream:
        for row in csv.DictReader(stream):
            if int(row['phase'])!=phase:
                continue
            node = int(row['node'])
            flit = Flit(FlitType(int(row['type'])),int(row['data'],16))
            partial[node].append(flit)
            if flit.type in (FlitType.TAIL,FlitType.HEADTAIL):
                received.append(codec.unpack(partial[node]));partial[node]=[]
    if any(partial.values()):
        raise AssertionError('completion left an unfinished packet')
    expected = list(inputs)+[MemAckPacket(p.dst_tile,0,1,p.sequence) for p in inputs]
    expected += expected_results(plan,matrix,vector)
    mask = 0
    for _,_,bits in plan.root_ranges:
        mask |= bits
    expected.append(TransactionDonePacket(0,0,1,plan.m,mask))
    normalize = lambda packet:json.dumps(dict(type=type(packet).__name__,**asdict(packet)),sort_keys=True)
    if Counter(map(normalize,received)) != Counter(map(normalize,expected)):
        actual,wanted = Counter(map(normalize,received)),Counter(map(normalize,expected))
        raise AssertionError(f'wire packets differ: missing={wanted-actual}, extra={actual-wanted}')
    with (directory/'memory.csv').open() as stream:
        actual_y = [int32_wrap(int(row['data'],16)) for row in csv.DictReader(stream)]
    golden = [int32_wrap(sum(a*b for a,b in zip(row,vector))) for row in matrix]
    if actual_y!=golden:
        raise AssertionError(f'Root SRAM readback disagrees with GEMV: {actual_y[:16]} vs {golden[:16]}')
    with (directory/'cycles.csv').open() as stream:
        phases = [{key:int(value) for key,value in row.items()} for row in csv.DictReader(stream)]
    final = phases[-1]
    if final['acks']!=len(inputs) or final['dones']!=1 or final['aborted']:
        raise AssertionError('input/completion acknowledgement contract failed')
    if item.get('abort_cycle',-1)>=0 and (len(phases)!=2 or not phases[0]['aborted']):
        raise AssertionError('reset case did not abort and replay')
    model = run_grid(plan,matrix,vector)
    if item.get('stall_until',0):
        load_cycles=model.phases['input_load']
        blocked_nodes={0,*(node.tile_id for node in plan.reducers)}
        model=run_grid(plan,matrix,vector,sink_ready=lambda cycle,node:
            node not in blocked_nodes or not load_cycles<=cycle<load_cycles+item['stall_until'])
    # Input commit is physically serialized through the one-byte X port in RTL.
    # The old model commits it functionally. Compare kernel separately too.
    model_kernel = model.phases['compute_and_gather']+model.phases['completion_drain']
    return dict(name=item['name'],plan_sha256=plan.digest,m=plan.m,k=plan.k,grid=plan.grid,
        active_compute=len(plan.workers),reducers=len(plan.reducers),physical_tiles=64,
        numeric_match=True,wire_packets_match=True,packets=len(received),input_acks=final['acks'],
        transaction_done=final['dones'],reset_replayed=len(phases)==2,
        rtl_phase_cycles=phases,model_cycles=model.cycles,model_kernel_cycles=model_kernel,
        matched_endpoint_stall_cycles=item.get('stall_until',0),
        kernel_difference_cycles=final['kernel_cycles']-model_kernel,
        kernel_relative_difference=final['kernel_cycles']/model_kernel-1,
        scope='integrated default-hardware RTL; real SRAM input writes and registered NI; no whole-system cycle-equivalence claim')


def run_regression(output,*,case_names=None,wave=False):
    output = Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    for tool in ('iverilog','vvp','verilator'):
        if not shutil.which(tool):
            raise RuntimeError(f'{tool} is required')
    executable = output/'mesh.vvp'
    command(['iverilog','-g2012','-s','tb_nmc_mesh_system','-o',executable,*SOURCES,TB],output,'compile')
    command(['verilator','--lint-only','-Wall','-Wno-UNUSEDSIGNAL','-Wno-PINCONNECTEMPTY',
             '--top-module','nmc_mesh_system',*SOURCES],output,'lint')
    reports = []
    selected = [item for item in cases() if case_names is None or item['name'] in case_names]
    if not selected or case_names is not None and set(case_names)!={item['name'] for item in selected}:
        raise ValueError('unknown/empty RTL case selection')
    for item in selected:
        directory = output/item['name']
        plan,W,x,inputs = prepare(item,directory)
        args = ['vvp',executable,f'+CASE_DIR={directory}']
        if wave and item['name']=='backpressure':
            args.append(f'+VCD={directory}/wave.vcd')
        started = time.perf_counter()
        log = command(args,directory,'simulation')
        if 'MESH RTL PASS' not in log:
            raise AssertionError('no integrated pass marker')
        report = compare(item,plan,W,x,inputs,directory)
        report['wall_seconds'] = time.perf_counter()-started
        reports.append(report)
        print(f"{item['name']}: 64-Tile RTL numeric/packets/Done PASS",flush=True)
    files = [*SOURCES,TB,Path(__file__).resolve()]
    payload = dict(cases=len(reports),passed=len(reports),results=reports,
        source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
    (output/'results.json').write_text(json.dumps(payload,indent=2)+'\n')
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'dump/mesh_rtl')
    parser.add_argument('--cases',nargs='+')
    parser.add_argument('--wave',action='store_true')
    args = parser.parse_args()
    report = run_regression(args.output_dir,case_names=args.cases,wave=args.wave)
    print(f"{report['passed']}/{report['cases']} integrated RTL cases passed")


if __name__=='__main__':
    main()
