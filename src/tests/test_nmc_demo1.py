"""NMC integration acceptance: SRAM -> GEMV -> NI -> Mesh -> Root -> Host."""
from dataclasses import replace

import numpy as np
import pytest

from simulator.communication import MemWritePacket, ResultPacket, StatusCode
from simulator.functional import Demo0Tile
from simulator.system.demo1 import (
    DEMO_W, DEMO_X, FinalGather, GatherConfig, TransactionError,
    commit_mem_write, run_demo1,
)


def test_demo1_4x4_row_partition_end_to_end():
    run = run_demo1()
    assert run.y == [13, 2, 8, -1]
    assert run.y == (np.array(DEMO_W, dtype=np.int64) @ np.array(DEMO_X)).tolist()
    assert [(t.tile_id, t.m_local, t.k_local, t.global_row_base) for t in run.tasks] == [
        (1, 2, 4, 0), (2, 2, 4, 2)]
    assert len(run.ack_keys) == 2
    for tile in (1, 2):
        assert list(run.tiles[tile].input_sram[:4]) == [2, 255, 3, 1]
    assert run.tiles[1].weight_sram[:4] == bytes([1, 255, 0, 0])
    assert run.tiles[2].weight_sram[:4] == bytes([3, 2, 0, 0])
    assert [run.tiles[1].read_result(i) for i in range(2)] == [13, 2]
    assert [run.tiles[2].read_result(i) for i in range(2)] == [8, -1]
    assert run.gather.values == {0: 13, 1: 2, 2: 8, 3: -1}
    assert run.gather.last_mask == 0b0110
    assert len(run.host_done) == 1
    assert run.host_done[0].src_tile == 3
    assert run.host_done[0].dst_tile == 0
    assert run.host_done[0].completed_element_count == 4
    events = run.trace
    starts = [i for i, e in enumerate(events) if e['event'] == 'TASK_START']
    acks = [i for i, e in enumerate(events) if e['event'] == 'HOST_ACK']
    assert max(acks) < min(starts)
    local = [e['network_cycle'] for e in events if e['event'] == 'LOCAL_DONE']
    host = next(e['network_cycle'] for e in events if e['event'] == 'HOST_TRANSACTION_DONE')
    assert max(local) < host
    # Two input unicasts, two ACKs, two RESULTs (one hop each),
    # plus the two-hop DONE: 4 + 2 + 4 + 2 = 12 flit-hops.
    assert run.mesh.flit_hops == 12


def test_demo1_root_can_be_host_and_done_still_traverses_local_packet_interface():
    run = run_demo1(root_tile=0)
    assert run.y == [13, 2, 8, -1]
    assert len(run.host_done) == 1
    assert (run.host_done[0].src_tile, run.host_done[0].dst_tile) == (0, 0)
    assert run.mesh.flit_hops == 10


def test_demo1_tail_input_bytes_tail_lanes_and_nonzero_global_base():
    rng = np.random.default_rng(71)
    w = rng.integers(-8, 9, size=(9, 5)).tolist()
    x = [-128, -1, 0, 1, 127]
    run = run_demo1(w, x, tid=0xFFFF, global_row_base=100)
    assert run.y == (np.asarray(w, dtype=np.int64) @ np.asarray(x)).tolist()
    assert len(run.ack_keys) == 4
    for t in (1, 2):
        assert run.tiles[t].input_sram[:5] == bytes(v & 0xFF for v in x)
        assert run.tiles[t].input_sram[5:8] == bytes(3)
    beats = [e for e in run.trace if e['event'] == 'RESULTBEAT_ACCEPT']
    assert [(e['tile'], e['base_index'], e['lane_mask'], e['last']) for e in beats] == [
        (1, 100, 15, False), (2, 105, 15, True), (1, 104, 1, True)]
    assert set(run.gather.values) == set(range(100, 109))


def test_demo1_network_backpressure_stalls_compute_result_handoff():
    w = [[(i + j) % 9 - 4 for j in range(5)] for i in range(65)]
    x = [1, -2, 3, -4, 5]
    # Keep Root blocked long enough to fill the network and NI FIFOs.
    run = run_demo1(w, x, sink_ready=lambda cycle, tile, phase: tile != 3 or cycle >= 100)
    assert run.y == (np.asarray(w, dtype=np.int64) @ np.asarray(x)).tolist()
    assert any(e['event'] == 'RESULTBEAT_STALL' for e in run.trace)
    assert run.mesh.fifo_full_cycles > 0
    assert run.mesh.max_occupancy <= 4
    assert len(run.host_done) == 1
    assert len(run.gather.values) == 65
    # Every worker's LocalDone follows its final ResultBeat acceptance.
    for worker in (1, 2):
        source_events = [e for e in run.trace if e.get('tile') == worker]
        last_result = max(e['network_cycle'] for e in source_events
                          if e['event'] == 'RESULTBEAT_ACCEPT')
        done = next(e['network_cycle'] for e in source_events if e['event'] == 'LOCAL_DONE')
        assert done > last_result


def test_demo1_blocked_input_loading_does_not_start_tasks():
    run = run_demo1(sink_ready=lambda cycle, tile, phase: tile != 1 or cycle >= 12)
    assert run.y == [13, 2, 8, -1]
    events = run.trace
    assert next(e['network_cycle'] for e in events if e['event'] == 'TASK_START') >= 12
    assert max(i for i, e in enumerate(events) if e['event'] == 'HOST_ACK') < min(
        i for i, e in enumerate(events) if e['event'] == 'TASK_START')


def test_remote_write_byte_strobes_and_busy_do_not_corrupt_memory():
    tile = Demo0Tile(1)
    tile.input_sram[:4] = bytes([9, 9, 9, 9])
    p = MemWritePacket(0, 1, 1, 0x1000, 0b0101, 7, 0xFF_03_FE_02)
    assert commit_mem_write(tile, p, idle=False) is None
    assert tile.input_sram[:4] == bytes([9, 9, 9, 9])
    ack = commit_mem_write(tile, p, idle=True)
    assert ack.status == StatusCode.OK
    assert (ack.src_tile, ack.dst_tile, ack.tid, ack.sequence) == (1, 0, 1, 7)
    assert tile.input_sram[:4] == bytes([2, 9, 3, 9])


@pytest.mark.parametrize('changes,status', [
    ({'local_addr': 0x1001}, StatusCode.ADDR_ALIGN),
    ({'local_addr': 0x1400}, StatusCode.ADDR_RANGE),
    ({'dst_tile': 2}, StatusCode.BAD_TILE),
    ({'wstrb': 0}, StatusCode.BAD_FIELD),
])
def test_invalid_remote_write_returns_error_ack_without_mutation(changes, status):
    tile = Demo0Tile(1)
    before = bytes(tile.weight_sram + tile.input_sram + tile.result_sram)
    p = replace(MemWritePacket(0, 1, 1, 0x1000, 15, 7, 0x12345678), **changes)
    assert commit_mem_write(tile, p, idle=True).status == status
    assert bytes(tile.weight_sram + tile.input_sram + tile.result_sram) == before


def make_gather(count=4):
    root = Demo0Tile(3)
    return FinalGather(GatherConfig(9, 3, 0, 0b0110, 100, count), root)


def test_gather_accepts_reordered_sources_and_done_only_after_all_outputs_and_lasts():
    g = make_gather()
    assert g.accept(ResultPacket(2, 3, 9, 102, 3, (8, -1, 0, 0), 'FINAL', True)) is None
    assert g.done is None
    done = g.accept(ResultPacket(1, 3, 9, 100, 3, (13, 2, 0, 0), 'FINAL', True))
    assert done.completed_element_count == 4
    assert [g.root.read_result(i) for i in range(4)] == [13, 2, 8, -1]
    missing_last = make_gather()
    missing_last.accept(ResultPacket(1, 3, 9, 100, 3, (13, 2, 0, 0), 'FINAL', False))
    assert missing_last.accept(ResultPacket(2, 3, 9, 102, 3, (8, -1, 0, 0), 'FINAL', True)) is None
    assert len(missing_last.values) == 4
    assert missing_last.done is None


@pytest.mark.parametrize('changes,status', [
    ({'tid': 10}, StatusCode.CONFIG_MISS),
    ({'dst_tile': 0}, StatusCode.CONFIG_MISS),
    ({'kind': 'PSUM'}, StatusCode.KIND_MISMATCH),
    ({'src_tile': 0}, StatusCode.BAD_TILE),
    ({'base_index': 103}, StatusCode.BAD_FIELD),
])
def test_gather_rejects_invalid_packet_atomically(changes, status):
    g = make_gather()
    packet = replace(ResultPacket(1, 3, 9, 100, 3, (13, 2, 0, 0), 'FINAL', True), **changes)
    with pytest.raises(TransactionError) as exc:
        g.accept(packet)
    assert exc.value.status == status
    assert not g.values and not any(g.root.result_sram)
    assert g.done is None
    # A terminated transaction cannot later complete via otherwise valid results.
    with pytest.raises(TransactionError):
        g.accept(ResultPacket(2, 3, 9, 102, 3, (8, -1, 0, 0), 'FINAL', True))
    assert g.done is None


def test_gather_duplicate_index_does_not_overwrite_first_result():
    g = make_gather()
    p = ResultPacket(1, 3, 9, 100, 3, (13, 2, 0, 0), 'FINAL', True)
    g.accept(p)
    with pytest.raises(TransactionError) as exc:
        g.accept(replace(p, src_tile=2, data=(99, 99, 0, 0)))
    assert exc.value.status == StatusCode.DUPLICATE_RESULT
    assert [g.root.read_result(i) for i in range(2)] == [13, 2]
    assert g.done is None


def test_demo1_times_out_when_sink_never_recovers():
    with pytest.raises(TimeoutError):
        run_demo1(sink_ready=lambda cycle, tile, phase: tile != 3, max_cycles=60)
