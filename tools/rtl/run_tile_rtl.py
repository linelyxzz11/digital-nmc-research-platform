#!/usr/bin/env python3
"""Compile single-Tile RTL and compare edges, packets and SRAM to Python."""
from __future__ import annotations
import argparse
import csv
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from simulator.functional.demo0_model import Demo0Tile, TileTask, int32_wrap
from simulator.communication.v0_1 import StatusCode
from simulator.system.tile_timing import PortScheduledCompute

RTL_DIR = ROOT / 'src/rtl/nmc_tile'
SOURCES = ['nmc_mac4.sv', 'nmc_tile_sram.sv', 'nmc_tile_compute.sv']
STATES = {'INIT_ACC': 2, 'RUN': 3, 'WRITE_RESULT': 4, 'EMIT_RESULT': 5, 'LOCAL_DONE': 6}
HAND_W = [[1, 2, -1], [-2, 0, 4], [3, -1, 2], [0, 5, -2], [-1, 2, 1]]
HAND_X = [2, -1, 3]


def command(arguments, cwd, output, name):
    result = subprocess.run([str(a) for a in arguments], cwd=cwd, capture_output=True,
                            text=True, timeout=120)
    (output / f'{name}.log').write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f'{name} failed ({result.returncode}):\n{(result.stdout + result.stderr)[-7000:]}')
    return result.stdout + result.stderr


def cases(seeds):
    def case(name, matrix=HAND_W, vector=HAND_X, **options):
        fields = options.pop('fields', {})
        return dict(name=name, matrix=matrix, vector=vector,
                    task=TileTask(0x1234, 1, len(matrix), len(vector), global_row_base=8, **fields),
                    result_stalls=options.pop('result_stalls', 0), done_stalls=options.pop('done_stalls', 0),
                    reset_cycle=options.pop('reset_cycle', -1), **options)
    result = [case('hand_5x3'), case('hand_stalls', result_stalls=3, done_stalls=3),
              case('two_by_two', [[1, 2], [3, 4]], [5, 6]),
              case('one_signed', [[-128]], [127]),
              case('psum_not_last', fields=dict(result_kind='PSUM', task_last=False)),
              case('full_weight_input', [[(-1)**(i+k) * 127 for k in range(1024)] for i in range(4)], [-128]*1024),
              case('full_result', [[-128, 127, -1, 0] for _ in range(256)], [-128, 127, 1, -1])]
    for alignment in (4, 8, 12):
        result.append(case(f'offset_y_{alignment}', fields=dict(w_base=4, x_base=0x1001, y_base=0x2000+alignment)))
    result.append(case('global_index_edge'))
    result[-1]['task'] = replace(result[-1]['task'], global_row_base=65531)
    for cycle in (0, 2, 3, 5, 6, 14):
        result.append(case(f'reset_cycle_{cycle}', reset_cycle=cycle,
                           result_stalls=5 if cycle==6 else 0, done_stalls=5 if cycle==14 else 0))
    invalid = [dict(opcode='INVALID'), dict(version=0), dict(result_kind='INVALID'),
               dict(m_local=0), dict(k_local=0), dict(m_local=257), dict(k_local=1025),
               dict(tile_id=2), dict(result_dst_tile=4), dict(global_row_base=65535),
               dict(w_base=1), dict(y_base=0x2001), dict(w_base=0xffc),
               dict(x_base=0xfff), dict(x_base=0x13ff), dict(y_base=0x23fc),
               dict(opcode='INVALID', m_local=0, tile_id=4),
               dict(version=0, m_local=0, tile_id=4),
               dict(m_local=0, tile_id=4, y_base=0x2001),
               dict(tile_id=4, global_row_base=65535, w_base=1),
               dict(global_row_base=65535, w_base=1), dict(w_base=1, x_base=0xfff)]
    for i, fields in enumerate(invalid):
        item = case(f'invalid_{i:02d}', done_stalls=3)
        item['task'] = replace(item['task'], **fields)
        result.append(item)
    for seed in range(seeds):
        rng = random.Random(seed)
        m, k = rng.choice([(1, 17), (3, 7), (5, 19), (9, 7), (13, 37), (17, 53), (33, 5)])
        matrix = [[rng.randint(-128,127) for _ in range(k)] for _ in range(m)]
        vector = [rng.randint(-128,127) for _ in range(k)]
        result.append(case(f'random_{seed:03d}', matrix, vector, result_stalls=seed%4,
                           done_stalls=seed%3, fields=dict(w_base=4, x_base=0x1001,
                           y_base=0x2000+4*(seed%4), result_kind='PSUM' if seed%2 else 'FINAL', task_last=bool(seed%2))))
    return result


def prepare(item, directory):
    directory.mkdir(parents=True, exist_ok=True)
    task = item['task']
    tile = Demo0Tile(1)
    tile.weight_sram[:4] = tile.input_sram[:4] = bytes.fromhex('dd33bb11')
    tile.result_sram[:] = b'\x5a' * 1024
    valid = tile._validate(task) == 'OK'
    w_base, x_base = (task.w_base, task.x_base) if valid else (0, 0x1000)
    tile.load_weight_matrix(item['matrix'], w_base)
    tile.load_input_vector(item['vector'], x_base)
    writes = []
    weight_bytes = 4*((len(item['matrix'])+3)//4)*len(item['vector'])
    for offset in range(0, weight_bytes, 4):
        address = w_base+offset
        data = int.from_bytes(tile.weight_sram[address:address+4], 'little')
        writes.append((address,data,15))
    for address in range(x_base&~3, (x_base+len(item['vector'])+3)&~3, 4):
        mask = sum(1<<i for i in range(4) if x_base <= address+i < x_base+len(item['vector']))
        data = int.from_bytes(tile.input_sram[address-0x1000:address-0x1000+4], 'little')
        writes.append((address,data,mask))
    (directory/'writes.txt').write_text(''.join(f'{a:04x} {d:08x} {m:x}\n' for a,d,m in writes))
    config = [task.version, 1 if task.opcode=='GEMV_LOCAL' else 255, task.tid, task.tile_id,
              task.m_local,task.k_local,task.w_base,task.x_base,task.y_base,task.global_row_base,
              {'FINAL':0,'PSUM':1}.get(task.result_kind,2),task.result_dst_tile,int(task.task_last),
              item['result_stalls'],item['done_stalls'],item['reset_cycle'],0,1]
    (directory/'config.hex').write_text(''.join(f'{value & 0xffffffff:08x}\n' for value in config))
    return tile


def number(row, key, *, hex=False):
    return int(row[key], 16 if hex else 10)


def acc(value):
    packed=int(value,16)
    return tuple(int32_wrap(packed>>(32*lane)) for lane in range(4))


def compare(item, tile, directory):
    task=item['task']
    status=tile._validate(task)
    with (directory/'trace.csv').open() as source:
        actual=list(csv.DictReader(source))
    with (directory/'memory.csv').open() as source:
        memory=list(csv.DictReader(source))
    summaries=[]
    phases=(0,1) if item['reset_cycle']>=0 else (0,)
    for phase in phases:
        rows=[r for r in actual if number(r,'phase')==phase]
        aborted=phase==0 and item['reset_cycle']>=0
        model=PortScheduledCompute(tile,task) if status=='OK' else None
        result_holds=done_holds=0
        beats=[]
        for cycle,row in enumerate(rows):
            def check(condition, reason):
                if not condition:
                    raise AssertionError(f"{item['name']} phase {phase} cycle {cycle}: {reason}; RTL={row}")
            check(number(row,'cycle')==cycle,'cycle numbering')
            signals=model.signals() if model else None
            result_valid=bool(signals and signals.result_valid)
            done_valid=signals.local_done_valid if signals else True
            ready=result_holds>=item['result_stalls']
            done_ready=done_holds>=item['done_stalls']
            check(bool(number(row,'result_ready'))==ready,'result ready stimulus')
            check(bool(number(row,'done_ready'))==done_ready,'done ready stimulus')
            check(bool(number(row,'result_valid'))==result_valid,'result valid')
            check(bool(number(row,'done_valid'))==done_valid,'done valid')
            if result_valid:
                b=signals.result_payload
                check((number(row,'result_tid',hex=True),number(row,'result_dst',hex=True),
                       number(row,'result_base',hex=True),number(row,'result_mask',hex=True),
                       acc(row['result_data']),number(row,'result_kind',hex=True),bool(number(row,'result_last'))) ==
                      (b.tid,b.dst_tile,b.base_index,b.lane_mask,b.data,0 if b.kind=='FINAL' else 1,b.last), 'ResultBeat fields')
                if ready: beats.append(cycle)
            if done_valid:
                check(number(row,'done_tid',hex=True)==task.tid,'LocalDone tid')
                check(number(row,'done_status',hex=True)==int(StatusCode[status]),'LocalDone status')
            if result_valid:
                result_holds=0 if ready else result_holds+1
            if done_valid and not done_ready: done_holds+=1
            if model:
                model.step(result_ready=ready,local_done_ready=done_ready)
                e=model.last_event
                check(number(row,'state')==STATES[e['stage']], 'controller state')
                check(acc(row['acc_before'])==e['acc_before'] and acc(row['acc_after'])==e['acc_after'], 'accumulators before/after edge')
                check(bool(number(row,'mac'))==(e['mac_k'] is not None), 'MAC enable')
                check(bool(number(row,'rd_req'))==bool(e['requests'] and e['stage']=='RUN'), 'SRAM request')
                check(bool(number(row,'rd_valid'))==bool(e['responses']), 'synchronous read response')
                check(bool(number(row,'y_write'))==(e['stage']=='WRITE_RESULT'), 'SRAM write edge')
                if e['stage']!='LOCAL_DONE': check(number(row,'group')==e['group'],'row group')
                if e['mac_k'] is not None: check(number(row,'k')==e['mac_k'],'MAC k index')
                if number(row,'rd_req'):
                    addresses={r['region']:r['address'] for r in e['requests']}
                    check(number(row,'w_addr',hex=True)==addresses['weight'] and number(row,'x_addr',hex=True)==addresses['input'],'read byte addresses')
                if e['responses']:
                    values={r['request']['region']:int.from_bytes(r['data'],'little') for r in e['responses']}
                    check(number(row,'w_data',hex=True)==values['weight'] and number(row,'x_data',hex=True)==values['input'],'SRAM returned data')
                if e['stage']=='WRITE_RESULT':
                    check(number(row,'y_addr',hex=True)==task.y_base+16*e['group'],'write byte address')
                    check(number(row,'y_mask',hex=True)==sum(1<<r['tag'][1] for r in e['grants']),'write lane mask')
            else:
                check(number(row,'state')==6 and not any(number(row,key) for key in ('rd_req','rd_valid','mac','y_write')),'invalid task had side effects')
                check(acc(row['acc_before'])==acc(row['acc_after'])==(0,0,0,0),'invalid task changed accumulator')
        if aborted:
            assert len(rows)==item['reset_cycle'],(item['name'],'reset cycle not reached')
        elif model:
            assert model.finished,(item['name'],'RTL completed before reference')
            baseline=((task.m_local+3)//4)*(task.k_local+4)+1
            assert len(rows)==baseline+((task.m_local+3)//4)*item['result_stalls']+item['done_stalls']
        else: assert len(rows)==1+item['done_stalls']
        expected={0: int.from_bytes(tile.weight_sram[:4],'little'),
                  0x1000:int.from_bytes(tile.input_sram[:4],'little')}
        expected.update({0x2000+offset:int.from_bytes(tile.result_sram[offset:offset+4],'little')
                         for offset in range(0,1024,4)})
        readback={number(r,'address',hex=True):number(r,'data',hex=True) for r in memory if number(r,'phase')==phase}
        assert readback==expected,(item['name'],phase,'SRAM readback or reset retention mismatch')
        summaries.append(dict(phase=phase,cycles=len(rows),aborted=aborted,result_handshake_cycles=beats,
                              status=status,cycle_trace_match=True,sram_readback_match=True))
    if item['name']=='hand_5x3':
        assert summaries[0]['result_handshake_cycles']==[6,13] and summaries[0]['cycles']==15
    return summaries


def run_regression(output, seeds=8, *, synthesis=True, case_list=None):
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    for tool in ('iverilog','vvp','verilator') + (('yosys',) if synthesis else ()):
        if not shutil.which(tool): raise RuntimeError(f'{tool} is required for RTL verification')
    executable=output/'tile.vvp'
    command(['iverilog','-g2012','-s','tb_nmc_tile_compute','-o',executable,*SOURCES,'tb_nmc_tile_compute.sv'],RTL_DIR,output,'compile')
    command(['verilator','--lint-only','-Wall','-Wno-UNUSEDSIGNAL','--top-module','nmc_tile_compute',*SOURCES],RTL_DIR,output,'lint')
    synthesis_info=None
    if synthesis:
        netlist=output/'synthesis.json'
        text='read_verilog -sv '+' '.join(SOURCES)+'; hierarchy -check -top nmc_tile_compute; proc; opt; memory_dff; memory_share; memory_collect; opt_clean; check -assert; stat; write_json "'+str(netlist)+'"'
        command(['yosys','-p',text],RTL_DIR,output,'synthesis')
        modules=json.loads(netlist.read_text())['modules']
        memories=[dict(module=name,cell=cell, width=int(body['parameters']['WIDTH'],2),size=int(body['parameters']['SIZE'],2),
                       read_ports=int(body['parameters']['RD_PORTS'],2),write_ports=int(body['parameters']['WR_PORTS'],2),
                       synchronous_read=bool(int(body['parameters']['RD_CLK_ENABLE'],2)))
                  for name,module in modules.items() for cell,body in module['cells'].items() if body['type']=='$mem_v2']
        assert sorted((m['width'],m['size']) for m in memories)==[(8,1024)]+[(32,64)]*4+[(32,1024)],memories
        assert all(m['read_ports']==m['write_ports']==1 and m['synchronous_read'] for m in memories),memories
        synthesis_info=dict(check='pass',memory_bits=sum(m['width']*m['size'] for m in memories),memories=memories)
    rows=[]
    for item in cases(seeds) if case_list is None else case_list:
        directory=output/item['name']
        tile=prepare(item,directory)
        args=['vvp',executable,f'+CASE_DIR={directory}']
        if item['name'] in ('hand_5x3','hand_stalls','reset_cycle_3','reset_cycle_6','reset_cycle_14'):
            args.append(f'+VCD={directory}/wave.vcd')
        result=command(args,RTL_DIR,directory,'simulation')
        if 'TILE RTL PASS' not in result: raise AssertionError(f"{item['name']}: no simulation pass marker")
        try: phases=compare(item,tile,directory)
        except Exception as error: raise AssertionError(f"case {item['name']}: {error}") from error
        rows.append(dict(name=item['name'],m=len(item['matrix']),k=len(item['vector']),
                         task=asdict(item['task']),phases=phases))
    files=[*(f'src/rtl/nmc_tile/{s}' for s in SOURCES), 'src/rtl/nmc_tile/tb_nmc_tile_compute.sv',
           'tools/rtl/run_tile_rtl.py','src/simulator/system/tile_memory.py','src/simulator/system/tile_timing.py',
           'src/simulator/functional/demo0_model.py','src/hardware.py','src/simulator/communication/v0_1.py']
    payload=dict(schema_version=1,cases=len(rows),phases=sum(len(r['phases']) for r in rows),
                 passed=len(rows),random_seeds=seeds,tools={tool:subprocess.check_output([tool,'--version' if tool=='verilator' else '-V'],text=True,stderr=subprocess.STDOUT).splitlines()[0] for tool in ('iverilog','verilator','yosys') if shutil.which(tool)},
                 synthesis=synthesis_info,source_sha256={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files},results=rows)
    (output/'results.json').write_text(json.dumps(payload,indent=2)+'\n')
    return payload


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'dump/rtl_tile')
    parser.add_argument('--seeds',type=int,default=8)
    parser.add_argument('--no-synthesis',action='store_true')
    args=parser.parse_args()
    if args.seeds<0: parser.error('seeds must be nonnegative')
    result=run_regression(args.output_dir,args.seeds,synthesis=not args.no_synthesis)
    print(json.dumps({key:result[key] for key in ('cases','phases','passed')}))


if __name__=='__main__': main()
