"""Reusable Tile/protocol contracts, instance isolation and bounded 64-Tile trees."""
from dataclasses import replace
import random

import pytest

from compiler.auto_mapper_v0_1 import MappingError
from compiler.grid_mapper import map_grid
from simulator.communication import MemAckPacket, ResultPacket, StatusCode, TransactionDonePacket, pack_packet
from simulator.communication.scalable import Mesh, PacketCodec
from simulator.functional import Demo0Tile
from simulator.system.demo1 import run_demo1
from simulator.system.demo2 import run_demo2
from simulator.system.endpoint_common import TransactionError
from simulator.system.root_config import RootTiming
from simulator.system.scalable import GridGatherConfig, ReductionEndpoint, run_grid
from simulator.system.tile_config import TileComputeConfig
from simulator.system.timed_endpoint import TimedRoot


@pytest.mark.parametrize('nodes',[4,16,64])
def test_completion_source_mask_roundtrip_and_legacy_wire_compatibility(nodes):
    codec=PacketCodec(nodes)
    packet=TransactionDonePacket(nodes-1,0,123,256,(1<<nodes)-2)
    encoded=codec.pack(packet)
    assert len(encoded)==1 and codec.unpack(encoded)==packet
    if nodes==4: assert encoded==pack_packet(packet)
    with pytest.raises(ValueError): codec.pack(replace(packet,src_tile=nodes))
    with pytest.raises(ValueError): codec.pack(replace(packet,completed_source_mask=1<<nodes))


def test_differently_sized_meshes_can_coexist_without_mutating_globals():
    small,large=Mesh(2,2),Mesh(8,8)
    a=MemAckPacket(3,0,9,0);b=MemAckPacket(63,0,9,1)
    small.inject_packet(a);large.inject_packet(b)
    for _ in range(30): small.step();large.step()
    assert small.received_packets[0]==[a] and large.received_packets[0]==[b]
    assert small.tile_count==4 and large.tile_count==64
    with pytest.raises(ValueError): small.inject_packet(b)


@pytest.mark.parametrize('partition,grid,legacy',[
    ('ROW',(2,1),run_demo1),('K',(1,2),run_demo2)])
def test_two_worker_system_cycles_and_outputs_match_legacy(partition,grid,legacy):
    W=[[1,2,3,4],[-1,0,2,-2],[3,-1,0,1],[2,2,-1,0]];x=[2,-1,3,1]
    reference=legacy(W,x,tid=1,tile_timing=TileComputeConfig(),root_timing=RootTiming())
    plan=map_grid(4,4,mesh_x=2,mesh_y=2,grid=grid,workers=(1,2),root_tile=3)
    actual=run_grid(plan,W,x)
    assert actual.cycles==reference.mesh.cycle and actual.y==reference.y
    assert actual.network['flit_hops']==reference.mesh.flit_hops
    assert actual.ack_count==len(reference.ack_keys) and actual.done_count==1
    assert tuple(t for t in actual.plan.tasks)==tuple(reference.tasks)


@pytest.mark.parametrize('grid',[(1,8),(8,1),(2,4),(4,2)])
@pytest.mark.parametrize('reduction',['central','tree'])
def test_64_tile_row_k_and_2d_results_with_finite_endpoints(grid,reduction):
    rng=random.Random(73)
    W=[[rng.randint(-128,127) for _ in range(37)] for _ in range(13)]
    x=[rng.randint(-128,127) for _ in range(37)]
    plan=map_grid(13,37,grid=grid,reduction=reduction,placement='spread',fan_in=3)
    run=run_grid(plan,W,x)
    assert run.y==[sum(a*b for a,b in zip(row,x)) for row in W]
    assert run.done_count==1 and sum(run.phases.values())==run.cycles
    assert sum(s['useful_mac_ops'] for s in run.compute.values())==13*37
    assert run.network['max_fifo_occupancy']<=4
    for stats in run.endpoints.values():
        assert stats['accepted_packets']==stats['processed_packets']
        assert stats['max_rx_slots']<=2 and stats['max_output_slots']<=1
        assert stats['done_created_count']==stats['done_accepted_count']==1


def test_source_masks_above_bit_31_and_63_tile_root():
    plan=map_grid(5,7,grid=(1,3),workers=(31,32,62),root_tile=63)
    run=run_grid(plan,[[1]*7 for _ in range(5)],[2]*7)
    assert run.y==[14]*5 and run.endpoints[63]['processed_packets']==6


def test_multilevel_tree_and_parent_backpressure_do_not_lose_or_duplicate_results():
    plan=map_grid(17,64,grid=(1,16),reduction='tree',fan_in=2)
    run=run_grid(plan,[[-128]*64 for _ in range(17)],[127]*64,
        root_timing=RootTiming(rx_queue_packets=1,lanes_per_cycle=1,write_cycles=2),
        sink_ready=lambda cycle,node:cycle%11<3,max_cycles=50000)
    assert run.y==[-128*127*64]*17 and len(plan.reducers)==15
    assert any(s['rx_blocked_flit_cycles'] for s in run.endpoints.values())
    assert any(s['result_write_stall_cycles'] for node,s in run.endpoints.items() if node!=plan.root_tile)
    assert all(s['max_rx_slots']<=1 and s['max_output_slots']<=1 for s in run.endpoints.values())


def test_wrong_source_for_second_output_is_rejected_atomically():
    config=GridGatherConfig(9,63,0,(1<<5)|(1<<60),0,2,'GATHER_FINAL',64,
                            ((0,1,1<<5),(1,2,1<<60)))
    root=TimedRoot(config,Demo0Tile(63,tile_count=64))
    before=bytes(root.root.result_sram)
    with pytest.raises(TransactionError) as error:
        root.accept(ResultPacket(5,63,9,0,3,(1,2,0,0),'FINAL',True))
    assert error.value.status==StatusCode.BAD_TILE
    assert not root.rx and not root.accepted_source_masks and bytes(root.root.result_sram)==before


def test_large_source_int32_wrap_and_final_packet_waits_for_local_completion():
    config=GridGatherConfig(9,40,0,(1<<5)|(1<<60),0,1,'REDUCE_PSUM',64)
    endpoint=ReductionEndpoint(config,Demo0Tile(40,tile_count=64),RootTiming(),parent=63)
    endpoint.root.accept(ResultPacket(5,40,9,0,1,(0x7fffffff,0,0,0),'PSUM',True))
    endpoint.root.accept(ResultPacket(60,40,9,0,1,(5,0,0,0),'PSUM',True))
    mesh=Mesh(8,8)
    while endpoint.output is None: endpoint.step(mesh);mesh.step()
    assert endpoint.final_output and endpoint.root.done is None
    assert not mesh.ni_injection[40]
    for _ in range(30): endpoint.step(mesh);mesh.step()
    mesh.run_until_idle(100)
    outputs=mesh.received_packets[63]
    assert len(outputs)==1 and outputs[0].last and outputs[0].data[0]==-2147483644


@pytest.mark.parametrize('options',[
    dict(m=257,k=16),dict(m=8,k=3,grid=(1,4)),dict(m=16,k=16,workers=(1,1),grid=(1,2)),
    dict(m=16,k=16,mesh_x=9,mesh_y=8),dict(m=256,k=1024,grid=(1,2)),
    dict(m=8,k=16,mesh_x=2,mesh_y=2,grid=(1,2),workers=(1,2),root_tile=3,reduction='tree',fan_in=2),
])
def test_infeasible_plan_fails_before_running(options):
    with pytest.raises(MappingError): map_grid(**options)


def test_plans_are_deterministic_and_same_worker_budget_for_both_reductions():
    a=map_grid(16,32,grid=(2,4),placement='random',seed=17)
    b=map_grid(16,32,grid=(2,4),placement='random',seed=17,reduction='tree')
    assert a.workers==b.workers and set(b.workers).isdisjoint(r.tile_id for r in b.reducers)
    assert a.digest==map_grid(16,32,grid=(2,4),placement='random',seed=17).digest
    assert a.digest!=b.digest


def test_tampered_task_route_is_rejected_before_execution():
    plan=map_grid(16,32,grid=(2,4))
    tasks=(replace(plan.tasks[0],result_dst_tile=63),*plan.tasks[1:])
    with pytest.raises(MappingError): run_grid(replace(plan,tasks=tasks),[[0]*32 for _ in range(16)],[0]*32)
