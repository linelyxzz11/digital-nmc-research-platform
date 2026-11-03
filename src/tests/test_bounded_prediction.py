"""Protocol-derived timing, finite pressure, preserved baselines and misselection."""
import gzip
import json
from pathlib import Path

import pytest

from compiler.bounded_predictor import PacketNetwork,input_cost,warm_arbitration
from compiler.grid_mapper import map_grid
from compiler.mapping_search import catalogue,predict,predict_v03,search,xy_path
from simulator.system.root_config import RootTiming
from simulator.system.scalable import run_grid


def test_input_rr_history_matches_actual_mesh_after_unequal_last_chunks():
    from simulator.communication import MemAckPacket,MemWritePacket
    from simulator.communication.scalable import Mesh
    plan=map_grid(5,9,mesh_x=4,mesh_y=4,grid=(2,2),placement='spread')
    actual=Mesh(4,4);writes=[]
    for seq,offset in enumerate(range(0,5,4)):
        for shard in plan.slices:
            size=min(4,shard.k_end-shard.k_start-offset)
            if size>0: writes.append(MemWritePacket(0,shard.tile_id,1,0x1000+offset,(1<<size)-1,seq,0))
    position=acked=0;outstanding=False;pending=None;seen=[0]*16
    while acked<len(writes):
        if pending is not None: actual.inject_packet(pending);pending=None
        if not outstanding and position<len(writes):
            actual.inject_packet(writes[position]);position+=1;outstanding=True
        actual.step()
        for tile in range(16):
            for packet in actual.received_packets[tile][seen[tile]:]:
                if isinstance(packet,MemWritePacket): pending=MemAckPacket(tile,0,1,packet.sequence)
                else: assert isinstance(packet,MemAckPacket);acked+=1;outstanding=False
            seen[tile]=len(actual.received_packets[tile])
    assert actual.idle()
    predicted=PacketNetwork(4,0,{})
    warm_arbitration(predicted,plan)
    for tile in range(16):
        for port,name in enumerate(('NORTH','EAST','SOUTH','WEST','LOCAL')):
            assert predicted.rr[tile,port]==actual.routers[tile].rr_next[name]


@pytest.mark.parametrize('length',[1,4,5,7])
@pytest.mark.parametrize('placement',['compact','spread'])
def test_stop_and_wait_cost_includes_partial_word_and_ack(length,placement):
    plan=map_grid(5,length,grid=(2,1),placement=placement)
    actual=run_grid(plan,[[1]*length for _ in range(5)],[1]*length)
    cycles,packets,_=input_cost(plan,xy_path)
    assert cycles==actual.phases['input_load']
    assert packets==actual.ack_count
    old=predict_v03(plan)
    assert old.input_cycles-cycles==2*packets


def test_remote_root_and_host_and_single_compute_tile():
    plan=map_grid(3,9,mesh_x=2,mesh_y=2,grid=(1,1),host_tile=3,root_tile=2)
    actual=run_grid(plan,[[1]*9 for _ in range(3)],[2]*9)
    prediction=predict(plan)
    assert prediction.input_cycles==actual.phases['input_load']
    assert prediction.flit_hops==actual.network['flit_hops']
    assert prediction.max_router_flits<=4 and prediction.max_ni_flits<=4


def test_slow_endpoint_backpressures_compute_and_preserves_all_traffic():
    # Enough vectors to exceed distributed credits, so pressure reaches the NI.
    plan=map_grid(129,64,grid=(1,16),reduction='tree',placement='spread')
    timing=RootTiming(rx_queue_packets=1,add_cycles=4,write_cycles=3)
    prediction=predict(plan,root_timing=timing)
    actual=run_grid(plan,[[1]*64 for _ in range(129)],[1]*64,root_timing=timing)
    assert prediction.source_stall_cycles>0 and prediction.network_blocked_cycles>0
    assert prediction.max_rx_slots==1
    assert prediction.max_router_flits<=4 and prediction.max_ni_flits<=4
    assert prediction.flit_hops==actual.network['flit_hops']
    assert prediction.cycles>predict(plan).cycles


def test_scalar_reduction_chunks_tail_lanes_and_nonzero_global_ranges():
    plan=map_grid(5,7,grid=(2,2),reduction='tree',placement='spread')
    timing=RootTiming(lanes_per_cycle=1,rx_queue_packets=1)
    actual=run_grid(plan,[[1]*7 for _ in range(5)],[1]*7,root_timing=timing)
    prediction=predict(plan,root_timing=timing)
    assert prediction.flit_hops==actual.network['flit_hops']
    assert prediction.max_rx_slots==1


@pytest.mark.parametrize('shape,expected',[((129,65),990),((192,96),1465)])
def test_reported_misselections_are_fixed_with_one_exact_evaluation(shape,expected):
    m,k=shape
    W=[[(row+column)%7-3 for column in range(k)] for row in range(m)]
    x=[column%5-2 for column in range(k)]
    result=search(m,k,W,x,exact_budget=1)
    assert result.plan.grid==(4,4) and result.plan.placement=='compact' and result.plan.reduction=='tree'
    assert len(result.evaluated)==1 and result.cycles==expected


def test_predictor_never_calls_the_numerical_system_evaluator(monkeypatch):
    import simulator.system.scalable as system
    def forbidden(*args,**kwargs):
        raise AssertionError('prediction must not query the exact evaluator')
    monkeypatch.setattr(system,'run_grid',forbidden)
    estimate=predict(map_grid(5,7,grid=(1,2),reduction='tree'))
    assert estimate.cycles>0 and estimate.model=='bounded-packet-v0.4'


def test_resource_baseline_scores_remain_identical_to_frozen_campaign():
    root=Path(__file__).resolve().parents[2]
    records=json.loads(gzip.decompress((root/'data/results/mapping_search_20261028/records.json.gz').read_bytes()))['cases']
    for record in records:
        scores={row['plan_sha256']:row['estimate']['cycles']
                for row in record['policies']['unindp-adapted']['ranking']}
        for plan in catalogue(record['m'],record['k']).plans:
            assert predict(plan,communication=False).cycles==scores[plan.digest]
