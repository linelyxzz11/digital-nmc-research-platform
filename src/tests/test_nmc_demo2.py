"""K-split NMC integration plus independent Reduction Buffer acceptance tests."""
from dataclasses import replace
from itertools import permutations

import numpy as np
import pytest

from simulator.communication import ResultPacket, StatusCode
from simulator.functional import Demo0Tile
from simulator.system.demo1 import DEMO_W, DEMO_X, GatherConfig, TransactionError, run_demo1
from simulator.system.demo2 import PSumReduce, run_demo2


def test_demo2_4x4_k_partition_end_to_end():
    run = run_demo2()
    assert run.y == [13, 2, 8, -1]
    assert run.y == (np.asarray(DEMO_W, dtype=np.int64) @ np.asarray(DEMO_X)).tolist()
    assert [(t.tile_id, t.m_local, t.k_local, t.global_row_base, t.result_kind) for t in run.tasks] == [
        (1, 4, 2, 0, 'PSUM'), (2, 4, 2, 0, 'PSUM')]
    assert run.tiles[1].input_sram[:4] == bytes([2, 255, 0, 0])
    assert run.tiles[2].input_sram[:4] == bytes([3, 1, 0, 0])
    # Columns, not rows, are resident on each worker.
    assert run.tiles[1].weight_sram[:4] == bytes([1, 255, 3, 2])
    assert run.tiles[2].weight_sram[:4] == bytes([3, 2, 0, 255])
    assert [run.tiles[1].read_result(i) for i in range(4)] == [0, -2, 7, 2]
    assert [run.tiles[2].read_result(i) for i in range(4)] == [13, 4, 1, -3]
    assert len(run.ack_keys) == 2
    assert run.gather.received_source_masks == {i: 0b0110 for i in range(4)}
    assert run.gather.psum_elements == 8
    assert run.gather.last_mask == 0b0110
    assert len(run.host_done) == 1
    assert run.mesh.flit_hops == 12
    events = run.trace
    assert max(i for i,e in enumerate(events) if e['event']=='HOST_ACK') < min(
        i for i,e in enumerate(events) if e['event']=='TASK_START')
    reductions = [e for e in events if e['event']=='PSUM_REDUCE']
    assert len(reductions) == 2
    assert reductions[0]['collected'] == 0
    assert all(not state['complete'] for state in reductions[0]['reduction_state'])
    assert reductions[1]['collected'] == 4
    assert all(state['complete'] for state in reductions[1]['reduction_state'])
    local_cycles = [e['network_cycle'] for e in events if e['event']=='LOCAL_DONE']
    host_cycle = next(e['network_cycle'] for e in events if e['event']=='HOST_TRANSACTION_DONE')
    assert max(local_cycles) < host_cycle


@pytest.mark.parametrize('split', [1, 2, 3, 4])
def test_demo2_uneven_k_signed_tail_lanes_and_global_base(split):
    w = np.random.default_rng(42).integers(-8, 9, size=(9, 5)).tolist()
    x = [-128, -1, 0, 1, 127]
    run = run_demo2(w, x, k_split=split, global_row_base=100, tid=0xFFFF)
    assert run.y == (np.asarray(w, dtype=np.int64) @ np.asarray(x)).tolist()
    assert [t.k_local for t in run.tasks] == [split, 5-split]
    for tile, segment in ((1,x[:split]), (2,x[split:])):
        assert run.tiles[tile].input_sram[:len(segment)] == bytes(v&255 for v in segment)
        beats = [e for e in run.trace if e['event']=='RESULTBEAT_ACCEPT' and e['tile']==tile]
        assert [(e['base_index'], e['lane_mask'], e['last']) for e in beats] == [
            (100,15,False), (104,15,False), (108,1,True)]
    assert run.gather.psum_elements == 18
    assert set(run.gather.values) == set(range(100,109))


def test_demo2_same_workload_as_row_partition_has_different_result_traffic():
    w = [[(i+j)%7-3 for j in range(5)] for i in range(9)]
    x = [1,-2,3,-4,5]
    row = run_demo1(w,x)
    k = run_demo2(w,x)
    assert row.y == k.y == (np.asarray(w,dtype=np.int64) @ np.asarray(x)).tolist()
    assert row.summary()['result_packet_count'] == 3
    assert k.summary()['result_packet_count'] == 6
    assert len(row.ack_keys) == 4
    assert len(k.ack_keys) == 2
    # K split sends fewer input writes here, but more tagged result packets.


def test_demo2_root_backpressure_stalls_ni_and_recovers_without_duplicate_psums():
    w = [[(i+j)%9-4 for j in range(5)] for i in range(33)]
    x = [1,-2,3,-4,5]
    run = run_demo2(w,x,sink_ready=lambda cycle,tile,phase: tile!=3 or cycle>=100)
    assert run.y == (np.asarray(w,dtype=np.int64) @ np.asarray(x)).tolist()
    assert any(e['event']=='RESULTBEAT_STALL' for e in run.trace)
    assert run.mesh.fifo_full_cycles > 0
    assert run.mesh.max_occupancy <= 4
    assert run.gather.psum_elements == 66
    assert run.gather.duplicate_errors == 0
    assert len(run.host_done) == 1
    for tile in (1,2):
        source = [e for e in run.trace if e.get('tile')==tile]
        last_beat = max(e['network_cycle'] for e in source if e['event']=='RESULTBEAT_ACCEPT')
        local_done = next(e['network_cycle'] for e in source if e['event']=='LOCAL_DONE')
        assert local_done > last_beat


def test_demo2_host_root_and_single_output():
    run = run_demo2([[1,-2,3]], [4,5,6], root_tile=0)
    assert run.y == [12]
    assert run.host_done[0].src_tile == run.host_done[0].dst_tile == 0
    assert run.gather.received_source_masks == {0:0b0110}


@pytest.mark.parametrize('split', [0,5,-1,True,1.5])
def test_demo2_rejects_invalid_k_partition(split):
    with pytest.raises(ValueError):
        run_demo2([[1]*5], [1]*5, k_split=split)


def reducer(count=2,mask=0b0110):
    root = Demo0Tile(3)
    root.result_sram[:] = b'\x5a' * len(root.result_sram)
    config = GatherConfig(9,3,0,mask,100,count,mode='REDUCE_PSUM')
    return PSumReduce(config,root)


def test_reduce_first_source_keeps_partial_only_then_writes_final_once():
    r = reducer()
    before = bytes(r.root.result_sram)
    assert r.accept(ResultPacket(2,3,9,100,3,(4,-7,0,0),'PSUM',True)) is None
    assert r.accumulators == {100:4,101:-7}
    assert r.received_source_masks == {100:4,101:4}
    assert not r.values and bytes(r.root.result_sram)==before
    done = r.accept(ResultPacket(1,3,9,100,3,(3,5,0,0),'PSUM',True))
    assert [r.root.read_result(i) for i in range(2)] == [7,-2]
    assert done.completed_element_count == 2
    assert done.completed_source_mask == 0b0110


def test_reduce_masks_are_per_index_not_packet_count():
    r = reducer()
    r.accept(ResultPacket(1,3,9,100,1,(3,0,0,0),'PSUM',False))
    r.accept(ResultPacket(2,3,9,101,1,(5,0,0,0),'PSUM',False))
    assert r.received_source_masks == {100:2,101:4}
    assert not r.values and r.done is None
    r.accept(ResultPacket(2,3,9,100,1,(7,0,0,0),'PSUM',True))
    assert r.values == {100:10}
    assert r.done is None
    done = r.accept(ResultPacket(1,3,9,101,1,(-1,0,0,0),'PSUM',True))
    assert r.values == {100:10,101:4}
    assert done is not None


def test_reduce_requires_source_lasts_even_after_all_element_masks_complete():
    r = reducer()
    r.accept(ResultPacket(1,3,9,100,3,(3,5,0,0),'PSUM',False))
    r.accept(ResultPacket(2,3,9,100,3,(4,-7,0,0),'PSUM',True))
    assert len(r.values)==2 and r.last_mask==4 and r.done is None


@pytest.mark.parametrize('order', list(permutations((0,1,2))))
def test_reduce_three_sources_int32_wrap_is_arrival_order_independent(order):
    # Synthetic legal INT32 PSUMs exercise a range not reached by small INT8 GEMVs.
    r = reducer(count=1,mask=0b0111)
    data = {0:0x7fffffff, 1:0x7fffffff, 2:5}
    for src in order:
        r.accept(ResultPacket(src,3,9,100,1,(data[src],0,0,0),'PSUM',True))
    assert r.values[100] == 3  # (2*INT32_MAX+5) mod 2^32
    assert r.root.read_result(0) == 3
    assert r.done is not None


def test_reduce_duplicate_source_is_atomic_and_error_prevents_future_done():
    r = reducer(count=4)
    r.accept(ResultPacket(1,3,9,101,1,(5,0,0,0),'PSUM',False))
    before = (dict(r.accumulators),dict(r.received_source_masks),bytes(r.root.result_sram),r.psum_elements)
    # Lane 0 is new; lane 1 is duplicate. Neither lane may change state.
    with pytest.raises(TransactionError) as exc:
        r.accept(ResultPacket(1,3,9,100,3,(99,99,0,0),'PSUM',True))
    assert exc.value.status == StatusCode.DUPLICATE_RESULT
    assert (r.accumulators,r.received_source_masks,bytes(r.root.result_sram),r.psum_elements)==before
    assert r.duplicate_errors == 1 and r.last_mask == 0
    with pytest.raises(TransactionError):
        r.accept(ResultPacket(2,3,9,100,15,(1,2,3,4),'PSUM',True))
    assert r.done is None


def test_reduce_negative_overflow_wraps_to_positive_int32():
    r = reducer(count=1)
    r.accept(ResultPacket(1,3,9,100,1,(-0x80000000,0,0,0),'PSUM',True))
    r.accept(ResultPacket(2,3,9,100,1,(-1,0,0,0),'PSUM',True))
    assert r.values[100] == 0x7fffffff
    assert r.root.read_result(0) == 0x7fffffff


def test_reduce_does_not_return_a_second_done():
    r = reducer(count=1)
    a = ResultPacket(1,3,9,100,1,(2,0,0,0),'PSUM',True)
    b = ResultPacket(2,3,9,100,1,(3,0,0,0),'PSUM',True)
    assert r.accept(a) is None
    assert r.accept(b) is not None
    with pytest.raises(TransactionError):
        r.accept(b)
    assert r.values == {100:5}


@pytest.mark.parametrize('changes,status', [
    ({'tid':10},StatusCode.CONFIG_MISS),
    ({'dst_tile':0},StatusCode.CONFIG_MISS),
    ({'src_tile':0},StatusCode.BAD_TILE),
    ({'kind':'FINAL'},StatusCode.KIND_MISMATCH),
    ({'base_index':101},StatusCode.BAD_FIELD),
    ({'lane_mask':0},StatusCode.BAD_FIELD),
    ({'data':(1,0x80000000,0,0)},StatusCode.BAD_FIELD),
])
def test_reduce_invalid_packet_cannot_partly_change_buffer(changes,status):
    r = reducer()
    before = bytes(r.root.result_sram)
    p = replace(ResultPacket(1,3,9,100,3,(3,5,0,0),'PSUM',True),**changes)
    with pytest.raises(TransactionError) as exc:
        r.accept(p)
    assert exc.value.status == status
    assert not r.accumulators and not r.received_source_masks and not r.values
    assert r.last_mask==0 and r.psum_elements==0 and r.done is None
    assert bytes(r.root.result_sram)==before


def test_reduce_source_cannot_send_new_index_after_last():
    r = reducer(count=4)
    r.accept(ResultPacket(1,3,9,100,1,(3,0,0,0),'PSUM',True))
    with pytest.raises(TransactionError) as exc:
        r.accept(ResultPacket(1,3,9,101,1,(5,0,0,0),'PSUM',False))
    assert exc.value.status==StatusCode.PROTOCOL_ERROR
    assert r.received_source_masks=={100:2}
    assert r.done is None


def test_demo2_times_out_when_root_never_recovers():
    with pytest.raises(TimeoutError):
        run_demo2(sink_ready=lambda cycle,tile,phase: tile!=3,max_cycles=60)
