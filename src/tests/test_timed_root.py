"""Finite ingress/PSUM/completion resources and independently hand-timed service."""
from dataclasses import replace
from itertools import permutations
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from simulator.communication import ResultPacket, StatusCode
from simulator.functional import Demo0Tile
from simulator.system.demo1 import FinalGather, GatherConfig, PlanError, TransactionError, run_demo1
from simulator.system.demo2 import PSumReduce, run_demo2
from simulator.system.root_config import RootCapacityError, RootTiming
from simulator.system.tile_config import TileComputeConfig
from simulator.system.timed_endpoint import TimedRoot

ROOT=Path(__file__).resolve().parents[2]


def endpoint(*,mode='REDUCE_PSUM',count=4,source_mask=6,**timing):
    tile=Demo0Tile(3)
    tile.result_sram[:]=b'\x5a'*1024
    return TimedRoot(GatherConfig(9,3,0,source_mask,100,count,mode),tile,RootTiming(**timing))


def packet(src,values=(1,-2,3,-4),*,base=100,last=True,kind='PSUM'):
    return ResultPacket(src,3,9,base,(1<<len(values))-1,
                        tuple(values)+(0,)*(4-len(values)),kind,last)


def drive(root,ready=lambda cycle: True):
    trace,outputs=[],[]
    while root.done is None or not root.idle():
        assert root.cycle<20000
        result=root.step(done_ready=ready(root.cycle))
        trace.append(root.last_event)
        if result.done_accepted is not None: outputs.append(result.done_accepted)
    return trace,outputs


@pytest.mark.parametrize('lanes,cycles,writes',[(4,13,[9]),(2,20,[12,16]),(1,34,[18,22,26,30])])
def test_two_source_four_output_hand_trace(lanes,cycles,writes):
    r=endpoint(lanes_per_cycle=lanes,reduction_entries=4)
    r.accept(packet(1))
    r.accept(packet(2,(4,5,-6,-7)))
    assert not r.values and bytes(r.root.result_sram)==b'\x5a'*1024
    trace,done=drive(r)
    assert len(trace)==cycles
    assert [e['cycle'] for e in trace if e.get('written_indices')]==writes
    assert r.values=={100:5,101:3,102:-3,103:-11}
    assert [r.root.read_result(i) for i in range(4)]==[5,3,-3,-11]
    assert bytes(r.root.result_sram[16:])==b'\x5a'*(1024-16)
    assert len(done)==1 and done[0].completed_source_mask==6
    stats=r.stats()
    assert stats['read_elements']==stats['add_elements']==stats['partial_update_elements']==8
    assert stats['result_write_elements']==4 and stats['max_reduction_entries']==4
    assert stats['reduction_full_cycles']>0 and not r.accumulators
    assert sum(stats['stage_cycles'].values())==cycles


def test_long_service_cycles_do_not_commit_sram_or_done_early():
    r=endpoint(read_cycles=2,add_cycles=2,update_cycles=3,write_cycles=3)
    r.accept(packet(1));r.accept(packet(2))
    for _ in range(19):
        r.step()
        assert bytes(r.root.result_sram)==b'\x5a'*1024 and r.done is None
    r.step() # final Result write at cycle 19
    assert r.values=={100:2,101:-4,102:6,103:-8} and r.done is None
    trace,outputs=drive(r)
    assert r.cycle==23 and len(outputs)==1


@pytest.mark.parametrize('lanes,baseline',[(1,34),(2,20),(4,13)])
@pytest.mark.parametrize('write_cycles',[1,3])
def test_write_port_backpressure_holds_vector_and_delays_completion(lanes,baseline,write_cycles):
    r=endpoint(lanes_per_cycle=lanes,write_cycles=write_cycles)
    r.accept(packet(1));r.accept(packet(2,(4,5,-6,-7)))
    while r.write_request() is None: r.step(write_ready=False)
    held=r.write_request()
    assert held.address==0x2000 and held.lane_mask==(1<<lanes)-1
    assert held.data[:lanes]==(5,3,-3,-11)[:lanes]
    before=bytes(r.root.result_sram)
    for _ in range(5):
        r.step(write_ready=False)
        assert r.write_request()==held and r.remaining==1
        assert bytes(r.root.result_sram)==before and not r.values and r.done is None
        assert r.last_event['write_valid'] and not r.last_event['write_accepted']
    assert r.stats()['result_write_stall_cycles']==5
    r.step(write_ready=True)
    assert r.last_event['write_accepted'] and r.values[100]==5
    drive(r)
    assert r.cycle==baseline+5+(write_cycles-1)*(4//lanes)
    assert r.values=={100:5,101:3,102:-3,103:-11}


def test_one_packet_fifo_reserves_head_and_accepts_tail_without_an_extra_slot():
    r=endpoint(rx_queue_packets=1)
    r.reserve_header()
    assert r.rx_slots==1 and not r.can_accept()
    with pytest.raises(RuntimeError): r.reserve_header()
    with pytest.raises(BufferError): r.accept(packet(1))
    r.accept(packet(1),reserved=True)
    assert r.rx_slots==1 and not r.header_reserved
    r.step() # dispatch moves one packet into the separately bounded active register
    assert r.active is not None and r.can_accept()
    r.reserve_header()
    r.accept(packet(2),reserved=True)
    assert len(r.rx)==1 and r.active is not None
    _,outputs=drive(r)
    assert len(outputs)==1 and r.stats()['max_rx_slots']==1


def test_full_queue_rejection_does_not_reserve_metadata_and_payload_is_latched():
    r=endpoint(rx_queue_packets=1)
    first=replace(packet(1),data=[1,2,3,4])
    r.accept(first)
    before=dict(r.accepted_source_masks)
    with pytest.raises(BufferError): r.accept(packet(2))
    assert r.accepted_source_masks==before
    first.data[0]=999
    r.step();r.accept(packet(2,(1,1,1,1)))
    drive(r)
    assert r.values[100]==2


def test_completion_is_held_until_ready_and_emitted_once():
    r=endpoint()
    r.accept(packet(1));r.accept(packet(2))
    while r.done is None: r.step(done_ready=False)
    held=r.done_fifo[0]
    assert r.cycle==12
    for _ in range(5):
        result=r.step(done_ready=False)
        assert result.done_accepted is None and r.done_fifo[0]==held
    assert r.stats()['done_stall_cycles']==5
    assert r.step(done_ready=True).done_accepted==held
    for _ in range(3): assert r.step().done_accepted is None
    assert r.stats()['done_created_count']==r.stats()['done_accepted_count']==1
    assert r.stats()['max_done_queue']==1


def test_insufficient_partial_capacity_rejected_and_full_capacity_can_drain():
    with pytest.raises(RootCapacityError): endpoint(reduction_entries=3)
    r=endpoint(reduction_entries=4)
    r.accept(packet(1))
    while r.active is not None or r.rx: r.step()
    assert len(r.accumulators)==4 and not r.values
    assert bytes(r.root.result_sram)==b'\x5a'*1024
    r.accept(packet(2))
    drive(r)
    assert not r.accumulators and len(r.values)==4


@pytest.mark.parametrize('order',list(permutations((0,1,2))))
def test_three_sources_arrival_order_and_int32_wrap(order):
    r=endpoint(count=1,source_mask=7,rx_queue_packets=3)
    values={0:0x7fffffff,1:0x7fffffff,2:5}
    for src in order: r.accept(packet(src,(values[src],)))
    drive(r)
    assert r.values=={100:3} and r.received_source_masks=={100:7}


def test_duplicate_in_later_lane_is_rejected_atomically_even_before_service():
    r=endpoint()
    r.accept(packet(1,(5,),base=101,last=False))
    before=(dict(r.accepted_source_masks),r.accepted_last_mask,list(r.rx),bytes(r.root.result_sram))
    with pytest.raises(TransactionError) as error: r.accept(packet(1,(99,99)))
    assert error.value.status==StatusCode.DUPLICATE_RESULT
    assert (r.accepted_source_masks,r.accepted_last_mask,list(r.rx),bytes(r.root.result_sram))==before
    with pytest.raises(TransactionError): r.step()
    assert r.done is None and r.duplicate_errors==1


@pytest.mark.parametrize('changes,status',[
    ({'tid':10},StatusCode.CONFIG_MISS),({'dst_tile':0},StatusCode.CONFIG_MISS),
    ({'src_tile':0},StatusCode.BAD_TILE),({'kind':'FINAL'},StatusCode.KIND_MISMATCH),
    ({'lane_mask':0},StatusCode.BAD_FIELD),({'base_index':101},StatusCode.BAD_FIELD),
    ({'data':(1,0x80000000,0,0)},StatusCode.BAD_FIELD)])
def test_invalid_packet_cannot_partly_reserve_or_update(changes,status):
    r=endpoint(count=2)
    with pytest.raises(TransactionError) as error: r.accept(replace(packet(1,(3,5)),**changes))
    assert error.value.status==status
    assert not r.rx and not r.accepted_source_masks and not r.accumulators and not r.values
    assert not r.last_mask and not r.accepted_last_mask and r.done is None
    assert bytes(r.root.result_sram)==b'\x5a'*1024


def test_source_last_reservation_prevents_queued_new_index_and_missing_last_prevents_done():
    r=endpoint()
    r.accept(packet(1,(1,)))
    with pytest.raises(TransactionError) as error: r.accept(packet(1,(2,),base=101))
    assert error.value.status==StatusCode.PROTOCOL_ERROR and r.last_mask==0
    r=endpoint(count=1)
    r.accept(packet(1,(1,),last=False));r.accept(packet(2,(2,)))
    for _ in range(20): r.step()
    assert r.values=={100:3} and r.last_mask==4 and r.done is None


def test_final_gather_skips_reduction_and_matches_functional_endpoint():
    r=endpoint(mode='GATHER_FINAL')
    reference=FinalGather(r.config,Demo0Tile(3))
    for p in (packet(1,(3,5),kind='FINAL'),packet(2,(4,-7),base=102,kind='FINAL')):
        r.accept(p);reference.accept(p)
    trace,_=drive(r)
    assert len(trace)==8 and r.values==reference.values
    assert r.root.result_sram[:16]==reference.root.result_sram[:16]
    assert r.stats()['read_elements']==r.stats()['add_elements']==0


@pytest.mark.parametrize('runner',[run_demo1,run_demo2])
@pytest.mark.parametrize('root',[0,3])
def test_finite_system_matches_values_and_traffic_and_bounds_all_buffers(runner,root):
    matrix=[[((i+j)%7)-3 for j in range(4)] for i in range(128)]
    vector=[1,-2,3,-4]
    base=runner(matrix,vector,root_tile=root,tile_timing=TileComputeConfig())
    timing=RootTiming(rx_queue_packets=1,lanes_per_cycle=1,read_cycles=2,add_cycles=2,update_cycles=2,write_cycles=3)
    timed=runner(matrix,vector,root_tile=root,tile_timing=TileComputeConfig(),root_timing=timing,capture_root_trace=True)
    assert timed.y==base.y and timed.ack_keys==base.ack_keys
    assert timed.mesh.flit_hops==base.mesh.flit_hops and len(timed.host_done)==1
    stats=timed.root_stats
    assert stats['max_rx_slots']==1 and stats['max_reduction_entries']<=timing.reduction_entries
    assert stats['done_created_count']==stats['done_accepted_count']==1
    assert stats['accepted_packets']==stats['processed_packets']==timed.summary()['result_packet_count']
    assert stats['rx_blocked_flit_cycles']>0 and timed.mesh.fifo_full_cycles>0
    assert sum(s['result_stall_cycles'] for s in timed.compute_stats.values())>0
    assert timed.mesh.cycle>base.mesh.cycle
    assert stats['elapsed_cycles']==timed.mesh.cycle==sum(stats['stage_cycles'].values())
    assert sum(timed.summary()['phase_cycles'].values())==timed.mesh.cycle
    events=[e for e in timed.trace if e['event']=='ROOT_CYCLE']
    assert all(e['local_cycle']==e['network_cycle'] for e in events)
    assert all(e['rx_slots']<=1 and e['rx_slots_after']<=1 for e in events)
    assert len([e for e in timed.trace if e['event']=='ROOT_HEADER_RESERVED'])==stats['accepted_packets']
    assert 'finite Root' in timed.summary()['timing_scope']


def test_system_done_queue_backpressure_and_unsupported_options():
    timed=run_demo2(tile_timing=TileComputeConfig(),root_timing=RootTiming(),root_done_ready=lambda c:c>=100)
    assert timed.root_stats['done_stall_cycles']>0 and len(timed.host_done)==1
    assert timed.mesh.cycle>=100 and timed.root_stats['max_done_queue']==1
    with pytest.raises(PlanError): run_demo1(capture_root_trace=True)
    with pytest.raises(PlanError): run_demo1(root_timing={})
    with pytest.raises(RootCapacityError): run_demo2(root_timing=RootTiming(reduction_entries=3))


def test_unbalanced_source_fills_scratchpad_then_matching_source_drains_without_deadlock():
    matrix=[[((i+j)%7)-3 for j in range(64)] for i in range(32)]
    vector=[(j%5)-2 for j in range(64)]
    result=run_demo2(matrix,vector,k_split=1,tile_timing=TileComputeConfig(),
                    root_timing=RootTiming(rx_queue_packets=1,reduction_entries=32))
    assert result.y==[sum(a*b for a,b in zip(row,vector)) for row in matrix]
    assert result.root_stats['max_reduction_entries']==32
    assert result.root_stats['reduction_full_cycles']>0
    assert not result.gather.accumulators and len(result.host_done)==1


@pytest.mark.parametrize('payload',[[],{'bad':1},{'rx_queue_packets':0},{'lanes_per_cycle':5},
    {'lanes_per_cycle':True},{'read_cycles':-1},{'add_cycles':1.5},{'reduction_entries':0}])
def test_invalid_root_config(payload):
    with pytest.raises(ValueError): RootTiming.from_dict(payload)


@pytest.mark.parametrize('module',['demo1','demo2'])
def test_cli_runs_and_saves_bounded_root_trace(tmp_path,module):
    path=tmp_path/'run.json'
    result=subprocess.run([sys.executable,'-B','-m',f'simulator.system.{module}',
        '--compute-model','tile','--root-model','timed','--root-trace','--trace',str(path)],
        cwd=ROOT,env=os.environ|{'PYTHONPATH':str(ROOT/'src')},capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    data=json.loads(path.read_text())
    assert data['summary']['check']=='PASS' and data['summary']['root_model']=='finite-service'
    assert any(e['event']=='ROOT_CYCLE' for e in data['trace'])
