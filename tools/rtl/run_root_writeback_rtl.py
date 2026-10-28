#!/usr/bin/env python3
"""Replay finite Root writes through the real Tile RTL port and read back SRAM."""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from simulator.communication import ResultPacket
from simulator.functional import Demo0Tile, int32_wrap
from simulator.system.endpoint_common import GatherConfig
from simulator.system.root_config import RootTiming
from simulator.system.timed_endpoint import TimedRoot

from run_tile_rtl import command, RTL_DIR, SOURCES

SENTINEL=0x5a5a5a5a
HAND=(-3,8,13,-11,-1)


def invalid(address,mask):
    # Check full byte addresses independently of the RTL Bank address slicing.
    selected=[address+4*lane for lane in range(4) if mask&(1<<lane)]
    return (address%4!=0 or not selected or not 0x2000<=address<0x2400 or
            any(a<0x2000 or a+4>0x2400 for a in selected))


def requests(seeds):
    result=[]
    def append(address,mask,data):
        result.append(dict(address=address,mask=mask,data=data,error=invalid(address,mask)))
    # The first 16 vectors are an uninterrupted, all-lanes bandwidth probe.
    for i in range(16):
        append(0x2000+16*i,15,sum((0x80000000+i*4+lane)<<(32*lane) for lane in range(4)))
    for rotation in range(4):
        for mask in range(1,16):
            append(0x2080+4*rotation,mask,sum((0xfedc0000+mask*16+lane)<<(32*lane) for lane in range(4)))
    for address,mask in [(0x1ffc,2),(0x2000,0),(0x2001,15),(0x2002,1),(0x2003,8),
                         (0x23fc,1),(0x23fc,3),(0x23f8,3),(0x23f8,4),(0x23f4,8),
                         (0x23f0,8),(0x2400,1),(0xfffc,15),(0,15)]:
        append(address,mask,(1<<128)-1)
    for seed in range(seeds):
        rng=random.Random(seed)
        for _ in range(16):
            append(0x2000+4*rng.randrange(256),rng.randrange(1,16),rng.getrandbits(128))
    return result


def calibration_cases():
    def packet(src,base,mask,values,last,kind):
        return ResultPacket(src,3,9,100+base,mask,tuple(values),kind,last)
    cases=[]
    for count in (1,3,4,5,7,13,256):
        for mode in ('GATHER_FINAL','REDUCE_PSUM'):
            kind='FINAL' if mode=='GATHER_FINAL' else 'PSUM'
            packets=[]
            for src in ((1,) if kind=='FINAL' else (1,2)):
                for base in range(0,count,4):
                    values=[]
                    for lane in range(4):
                        i=base+lane
                        if count==4:
                            value=((1,-2,3,-4) if src==1 else (4,5,-6,-7))[lane]
                        else:
                            value=(0x7fffffff if i%7==0 else -0x80000000 if i%7==1 else i-100) if src==1 else 3*i+5
                        values.append(value)
                    mask=(1<<min(4,count-base))-1
                    packets.append(packet(src,base,mask,values,base+4>=count,kind))
            for lanes in (1,2,4):
                cases.append((f'{kind.lower()}_{count}_lanes{lanes}',mode,count,lanes,packets))
    # A vector may have complete and incomplete indices together. Only the
    # complete lanes go to Result SRAM; the other partial sums remain live.
    mixed=[packet(1,0,5,(1,0,3,0),False,'PSUM'),
           packet(2,0,15,(4,5,-6,-7),True,'PSUM'),
           packet(1,0,10,(0,-2,0,-4),True,'PSUM')]
    for lanes in (1,2,4):
        cases.append((f'psum_mixed_lanes{lanes}','REDUCE_PSUM',4,lanes,mixed))
    cases=[(*case,False) for case in cases]+[(*case,True) for case in cases
            if case[2]==4 and not case[0].startswith('psum_mixed')]
    result=[]
    for name,mode,count,lanes,packets,host_stall in cases:
        if host_stall: name+='_host_stall'
        config=GatherConfig(9,3,0,2 if mode=='GATHER_FINAL' else 6,100,count,mode)
        timing=RootTiming(lanes_per_cycle=lanes,rx_queue_packets=max(2,len(packets)))
        tile=Demo0Tile(3); tile.result_sram[:]=b'\x5a'*1024
        root=TimedRoot(config,tile,timing)
        for p in packets: root.accept(p)
        frames=[]; writes=[]; host_start=None
        while root.done is None or not root.idle():
            assert root.cycle<20000
            request=root.write_request()
            if host_stall and request is not None and host_start is None: host_start=root.cycle
            distance=root.cycle-host_start if host_start is not None else -1
            # Actual Host port timing: accept at 0, service at 1, hold response
            # at 2..5 and accept that response at 5. Root resumes at edge 6.
            ready=not (host_start is not None and 0<=distance<6)
            frame=dict(cycle=root.cycle,valid=request is not None,
                       address=request.address if request else 0x2000,
                       mask=request.lane_mask if request else 0,
                       data=request.packed_data if request else 0,
                       host_request=distance==0,host_rsp_ready=distance==5,ready=ready)
            root.step(write_ready=ready)
            event=root.last_event
            indices=event.get('written_indices',[])
            assert bool(indices)==(request is not None and ready)
            if indices:
                writes.append(dict(cycle=event['cycle'],indices=indices,**{k:frame[k] for k in ('address','mask','data')}))
            frames.append(frame)
        # Independent numeric sum over input packets, separate from TimedRoot.
        golden={i:0 for i in range(100,100+count)}
        for p in packets:
            for lane in range(4):
                if p.lane_mask&(1<<lane): golden[p.base_index+lane]=int32_wrap(golden[p.base_index+lane]+p.data[lane])
        assert root.values==golden
        expected=[SENTINEL]*256
        for i,value in golden.items(): expected[i-100]=value&0xffffffff
        result.append(dict(name=name,config=asdict(config),timing=asdict(timing),frames=frames,
                           writes=writes,expected=expected,cycles=root.cycle,
                           write_stall_cycles=root.stats()['result_write_stall_cycles']))
    return result


def number(row,key,base=10):
    return int(row[key],base)


def compare(output,stream,calibrations):
    with (output/'trace.csv').open() as source: trace=list(csv.DictReader(source))
    with (output/'memory.csv').open() as source: memory=list(csv.DictReader(source))
    expected=[SENTINEL]*256
    snapshots={}
    accepted=[]; stalled=0; host_responses=0
    active_host=None; held_root=None
    for row in trace:
        phase=number(row,'phase')
        reset=not number(row,'rst')
        if reset:
            assert not any(number(row,k) for k in ('root_ready','root_error','root_commit'))
            active_host=None
            held_root=None
            continue
        valid,ready=number(row,'root_valid'),number(row,'root_ready')
        bundle=tuple(row[k] for k in ('root_addr','root_mask','root_data'))
        if held_root is not None: assert valid and bundle==held_root,'Root request changed under backpressure'
        held_root=bundle if valid and not ready else None
        if valid:
            address,mask,data=(number(row,k,16) for k in ('root_addr','root_mask','root_data'))
            error=invalid(address,mask)
            assert bool(number(row,'root_error'))==bool(ready and error),row
            assert bool(number(row,'root_commit'))==bool(ready and not error),row
            if not ready: stalled+=1
            if ready:
                assert number(row,'state')==0 and not number(row,'mem_valid') and not number(row,'rsp_valid'),row
                accepted.append(dict(cycle=number(row,'cycle'),phase=phase,address=address,mask=mask,data=data,error=error))
                if not error:
                    for lane in range(4):
                        if mask&(1<<lane): expected[(address-0x2000)//4+lane]=(data>>(32*lane))&0xffffffff
            assert not number(row,'task_ready'),row
        if number(row,'mem_valid') and number(row,'mem_ready'):
            assert active_host is None,row
            active_host=dict(address=number(row,'mem_addr',16),data=number(row,'mem_data',16),
                             strobe=number(row,'mem_strb',16),write=number(row,'mem_write'))
            if active_host['write'] and 0x2000<=active_host['address']<0x2400:
                index=(active_host['address']-0x2000)//4
                for byte in range(4):
                    if active_host['strobe']&(1<<byte):
                        byte_mask=255<<(8*byte)
                        expected[index]=(expected[index]&~byte_mask)|(active_host['data']&byte_mask)
            elif 0x2000<=active_host['address']<0x2400:
                active_host['expected']=expected[(active_host['address']-0x2000)//4]
        if number(row,'rsp_valid'):
            assert active_host is not None,row
            if 'expected' in active_host: assert number(row,'rsp_data',16)==active_host['expected'],row
            if number(row,'rsp_ready'): active_host=None; host_responses+=1
        if number(row,'compute_write'):
            # The known 5x3 example writes Y at 0x2004, leaving other words intact.
            group=0 if number(row,'compute_mask',16)==15 else 1
            for lane in range(min(4,len(HAND)-group*4)):
                expected[1+4*group+lane]=HAND[4*group+lane]&0xffffffff
        # Snapshot after every observation; phase readbacks must match all prior
        # accepted operations, including Root/Host overwrites in arbitration tests.
        snapshots[phase]=list(expected)
    actual={}
    for row in memory: actual.setdefault(number(row,'phase'),{})[number(row,'address',16)]=number(row,'data',16)
    for phase in (1,2,3,4):
        assert actual[phase]=={0x2000+4*i:v for i,v in enumerate(snapshots[phase])},f'phase {phase} SRAM readback'
    received=[{k:r[k] for k in ('address','mask','data','error')} for r in accepted if r['phase']==1]
    assert received==stream,'lost, duplicated or reordered Root requests'
    burst=[r for r in accepted if r['phase']==1][:16]
    assert [r['cycle']-burst[0]['cycle'] for r in burst]==list(range(16))
    # All 4-byte rotations and all nonempty lane masks are exercised.
    assert {(r['address']%16,r['mask']) for r in received if not r['error']} >= {(a,m) for a in (0,4,8,12) for m in range(1,16)}
    compute=[r for r in trace if number(r,'phase')==3]
    start=next(number(r,'cycle') for r in compute if number(r,'state')==2)
    result_edges=[number(r,'cycle')-start for r in compute if number(r,'result_valid') and number(r,'result_ready')]
    done_edges=[number(r,'cycle')-start for r in compute if number(r,'done_valid') and number(r,'done_ready')]
    assert result_edges==[9,19] and done_edges==[23],(result_edges,done_edges)
    assert len([r for r in accepted if r['phase']==2])==4
    assert len([r for r in accepted if r['phase']==3])==1
    assert len([r for r in accepted if r['phase']==4])==1
    records=[]
    for case_id,case in enumerate(calibrations):
        phase=10+case_id
        replay=[r for r in trace if number(r,'phase')==phase and number(r,'replay_cycle')>=0]
        assert len(replay)==case['cycles']
        edges=[number(r,'replay_cycle') for r in replay if number(r,'root_commit')]
        assert edges==[w['cycle'] for w in case['writes']],case['name']
        for row,frame in zip(replay,case['frames']):
            assert number(row,'replay_cycle')==frame['cycle']
            assert bool(number(row,'root_valid'))==frame['valid'] and bool(number(row,'root_ready'))==frame['ready']
            assert bool(number(row,'root_commit'))==(frame['valid'] and frame['ready'])
            if frame['valid']:
                assert (number(row,'root_addr',16),number(row,'root_mask',16),number(row,'root_data',16))==(frame['address'],frame['mask'],frame['data'])
        assert actual[phase]=={0x2000+4*i:v for i,v in enumerate(case['expected'])},case['name']
        records.append({k:case[k] for k in ('name','config','timing','cycles','writes','write_stall_cycles')})
    compact=[r for r in trace if (number(r,'phase') in (1,2,3,4) and
                                 (number(r,'root_valid') or number(r,'result_valid') or number(r,'done_valid'))) or
                                (number(r,'replay_cycle')>=0 and number(r,'phase')>=55)]
    with (output/'interface_trace.csv').open('w',newline='') as target:
        writer=csv.DictWriter(target,fieldnames=trace[0].keys());writer.writeheader();writer.writerows(compact)
    return dict(requests=len(stream),accepted_valid=sum(not r['error'] for r in stream),
                rejected=sum(r['error'] for r in stream),stalled_root_cycles=stalled,
                host_responses_checked=host_responses,readback_words=len(memory),
                burst=dict(vectors=16,cycles=16,int32_words=64,bytes_per_cycle=16),
                compute_result_cycles=result_edges,compute_done_cycle=done_edges[0],
                calibration_cases=len(records),calibration=records)


def run_regression(output,seeds=8,*,wave=True):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    stream=requests(seeds);calibrations=calibration_cases()
    (output/'requests.txt').write_text(''.join(f"{r['address']:04x} {r['mask']:x} {r['data']:032x} {int(r['error'])}\n" for r in stream))
    (output/'calibration_cases.txt').write_text(''.join(f"{i} {c['cycles']}\n" for i,c in enumerate(calibrations)))
    for i,case in enumerate(calibrations):
        (output/f'calibration_{i}.txt').write_text(''.join(f"{int(f['valid'])} {f['address']:04x} {f['mask']:x} {f['data']:032x} {int(f['host_request'])} {int(f['host_rsp_ready'])} {int(f['ready'])}\n" for f in case['frames']))
    executable=output/'root_writeback.vvp'
    command(['iverilog','-g2012','-s','tb_nmc_root_writeback','-o',executable,*SOURCES,'tb_nmc_root_writeback.sv'],RTL_DIR,output,'compile')
    arguments=['vvp',executable,f'+CASE_DIR={output}']
    if wave: arguments.append(f'+VCD={output}/wave.vcd')
    result=command(arguments,RTL_DIR,output,'simulation')
    assert 'ROOT WRITEBACK RTL PASS' in result
    payload=dict(schema_version=1,random_seeds=seeds,
                 scope='Final Result SRAM write path; reduction arithmetic/control remain Python',
                 **compare(output,stream,calibrations))
    files=[*(f'src/rtl/nmc_tile/{s}' for s in SOURCES),'src/rtl/nmc_tile/tb_nmc_root_writeback.sv',
           'tools/rtl/run_root_writeback_rtl.py','src/simulator/system/timed_endpoint.py',
           'tools/rtl/run_tile_rtl.py','src/tests/test_root_writeback_rtl.py',
           'src/tests/test_timed_root.py','src/hardware.py',
           'src/simulator/system/root_config.py','src/simulator/system/endpoint_common.py',
           'src/simulator/functional/demo0_model.py','src/simulator/communication/v0_1.py']
    payload['source_sha256']={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files}
    (output/'results.json').write_text(json.dumps(payload,indent=2)+'\n')
    return payload


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'dump/rtl_root_writeback')
    parser.add_argument('--seeds',type=int,default=8)
    parser.add_argument('--no-wave',action='store_true')
    args=parser.parse_args()
    if args.seeds<0: parser.error('seeds must be nonnegative')
    result=run_regression(args.output_dir,args.seeds,wave=not args.no_wave)
    print(json.dumps({k:result[k] for k in ('requests','accepted_valid','rejected','calibration_cases','burst')}))


if __name__=='__main__': main()
