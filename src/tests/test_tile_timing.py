"""Hand traces, real operand responses, bank arbitration and capacity limits."""
from dataclasses import replace
import random

import pytest

from simulator.functional.demo0_model import Demo0Tile, TileTask
from simulator.system.compute_timing import ComputeTiming, TimedCompute
from simulator.system.tile_memory import (
    SramGeometry, SramRequest, TileMemoryConfig, TileMemoryPorts,
)
from simulator.system.tile_timing import PortScheduledCompute


MATRIX = [[1, 2, -1], [-2, 0, 4], [3, -1, 2], [0, 5, -2], [-1, 2, 1]]
VECTOR = [2, -1, 3]


def make_tile(matrix=MATRIX, vector=VECTOR, **fields):
    task = TileTask(1, 1, len(matrix), len(vector), **fields)
    tile = Demo0Tile(1)
    tile.load_weight_matrix(matrix, task.w_base)
    tile.load_input_vector(vector, task.x_base)
    tile.result_sram[:] = b'\x5a' * len(tile.result_sram)
    return tile, task


def drive(model, ready=lambda c: True, done_ready=lambda c: True):
    trace, beats = [], []
    while not model.finished:
        cycle = model.memory.cycle
        assert cycle < 30000
        before = model.signals()
        assert model.step(result_ready=ready(cycle), local_done_ready=done_ready(cycle)) == before
        if before.result_valid and ready(cycle):
            beats.append(before.result_payload)
        trace.append(model.last_event)
    return trace, beats


def test_five_by_three_requests_returns_write_visibility_and_exact_frozen_trace():
    tile, task = make_tile(global_row_base=8)
    model = PortScheduledCompute(tile, task)
    for _ in range(5):
        model.step(result_ready=True)
        assert bytes(tile.result_sram[:20]) == b'\x5a' * 20
    model.step(result_ready=True)
    assert [tile.read_result(i) for i in range(4)] == [-3, 8, 13, -11]
    assert bytes(tile.result_sram[16:20]) == b'\x5a' * 4
    assert len(model.last_event['grants']) == 4
    tail, beats = drive(model)
    assert [e['cycle'] for e in tail if e['result_valid']] == [6, 13]
    assert [b.data for b in beats] == [(-3, 8, 13, -11), (-1, 0, 0, 0)]
    assert [b.lane_mask for b in beats] == [15, 1]
    assert model.memory.cycle == 15 and tail[-1]['local_done_valid']
    assert bytes(tile.result_sram[20:]) == b'\x5a' * (len(tile.result_sram) - 20)

    tile, task = make_tile()
    trace, _ = drive(PortScheduledCompute(tile, task))
    assert [(e['cycle'], e['mac_k']) for e in trace if e['mac_k'] is not None] == [
        (2, 0), (3, 1), (4, 2), (9, 0), (10, 1), (11, 2)]
    assert {(r['region'], r['address'], r['size']) for r in trace[1]['grants']} == {
        ('input', 0x1000, 1), ('weight', 0, 4)}
    assert {r['request']['region']: r['data'] for r in trace[2]['responses']} == {
        'input': b'\x02', 'weight': b'\x01\xfe\x03\x00'}
    assert trace[2]['acc_after'] == (2, -4, 6, 0)
    assert trace[3]['acc_after'] == (0, -4, 7, -5)
    for event in trace:
        for response in event['responses']:
            assert response['available_cycle'] == response['accepted_cycle'] + 1
        reads = [r for r in event['grants'] if r['write_data'] is None]
        assert sum(r['size'] for r in reads) <= 5
        assert len(event['grants']) <= (4 if event['stage'] == 'WRITE_RESULT' else 2)


@pytest.mark.parametrize('y_base', [0x2000, 0x2004, 0x2008, 0x200c])
def test_four_result_banks_support_every_legal_word_alignment_in_one_cycle(y_base):
    tile, task = make_tile([[1]] * 4, [7], y_base=y_base)
    model = PortScheduledCompute(tile, task)
    trace, _ = drive(model)
    writes = [e for e in trace if e['stage'] == 'WRITE_RESULT']
    assert len(writes) == 1 and len(writes[0]['grants']) == 4
    assert {r['bank'] for r in writes[0]['bank_locations']} == {0, 1, 2, 3}
    assert [tile.read_result(i, y_base) for i in range(4)] == [7] * 4


def test_reads_sample_on_grant_return_after_latency_and_can_pipeline():
    tile, _ = make_tile()
    config = TileMemoryConfig(weight=SramGeometry(read_latency=3))
    ports = TileMemoryPorts(tile, config)
    req = lambda k: SramRequest('weight', 'weight', 4 * k, 4, (0, k))
    for k in range(3):
        edge = ports.tick([req(k)])
        assert edge.grants == (req(k),) and not edge.responses
    tile.weight_sram[:12] = b'\x00' * 12  # returned bytes were captured on request, not at MAC
    for k in range(3):
        edge = ports.tick()
        assert edge.responses[0].request == req(k)
        assert edge.responses[0].accepted_cycle == k
        assert edge.responses[0].available_cycle == k + 3
        assert edge.responses[0].data != b'\x00' * 4


@pytest.mark.parametrize('banks,expected_grants', [(1, 1), (2, 2)])
def test_address_mapping_decides_conflict_and_round_robin_is_not_call_order(banks, expected_grants):
    tile, _ = make_tile()
    geometry = SramGeometry(banks=banks)
    ports = TileMemoryPorts(tile, TileMemoryConfig(geometry, geometry, share_weight_input=True))
    w = SramRequest('weight', 'weight', 4, 4, (0, 0))
    x = SramRequest('input', 'input', 0x1000, 1, (0, 0))
    assert ports.location(w)[1] == 1 % banks and ports.location(x)[1] == 0
    first = ports.tick([x, w])
    assert len(first.grants) == expected_grants
    if banks == 1:
        assert first.grants == (w,)
        second = ports.tick([w, x])
        assert second.grants == (x,)  # loser wins next despite list order
        assert ports.counters['bank_conflict_request_cycles'] == 2
    else:
        assert ports.counters['bank_conflict_request_cycles'] == 0


def test_single_bank_round_robin_gives_each_continuously_pending_client_a_turn():
    tile, _ = make_tile()
    ports = TileMemoryPorts(tile, TileMemoryConfig(result=SramGeometry()))
    requests = [SramRequest(f'result{i}', 'result', 0x2000 + 4 * i, 4, (0, i), bytes([i]) * 4)
                for i in range(4)]
    granted = [ports.tick(list(reversed(requests))).grants[0].client for _ in range(8)]
    assert granted == ['result0', 'result1', 'result2', 'result3'] * 2


def test_two_ports_allow_two_grants_and_read_during_write_returns_pre_edge_bytes():
    tile, _ = make_tile()
    ports = TileMemoryPorts(tile, TileMemoryConfig(result=SramGeometry(ports_per_bank=2)))
    read = SramRequest('result0', 'result', 0x2000, 4, (0, 0))
    write = replace(read, client='result1', write_data=b'1234')
    other = replace(read, client='result2', address=0x2004)
    first = ports.tick([other, write, read])
    assert first.grants == (read, write) and first.denied == (other,)
    assert not first.responses and bytes(tile.result_sram[:4]) == b'1234'
    second = ports.tick([other])
    assert second.grants == (other,) and second.responses[0].data == b'\x5a' * 4


def test_shared_single_bank_hand_trace_requires_two_reads_per_k_and_stable_retries():
    tile, task = make_tile()
    shared = TileMemoryConfig(SramGeometry(), SramGeometry(), share_weight_input=True)
    model = PortScheduledCompute(tile, task, shared)
    trace, _ = drive(model)
    assert [(e['cycle'], e['mac_k']) for e in trace if e['mac_k'] is not None] == [
        (3, 0), (5, 1), (7, 2), (13, 0), (15, 1), (17, 2)]
    assert [e['cycle'] for e in trace if e['result_valid']] == [9, 19]
    assert len(trace) == 21 and model.stats()['bank_conflict_request_cycles'] == 10
    for previous, current in zip(trace, trace[1:]):
        requests = {r['client']: r for r in current['requests']}
        assert all(requests[r['client']] == r for r in previous['denied'])


def test_port_cooldown_is_distinct_from_bank_contention():
    tile, _ = make_tile()
    ports = TileMemoryPorts(tile, TileMemoryConfig(weight=SramGeometry(issue_interval=2)))
    req = SramRequest('weight', 'weight', 0, 4, (0, 0))
    assert ports.tick([req]).grants == (req,)
    assert ports.tick([req]).denied == (req,)
    assert ports.tick([req]).grants == (req,)
    assert ports.counters['port_busy_request_cycles'] == 1
    assert ports.counters['bank_conflict_request_cycles'] == 0


def test_single_result_bank_serializes_writes_and_never_emits_partial_group():
    tile, task = make_tile()
    model = PortScheduledCompute(tile, task, TileMemoryConfig(result=SramGeometry()))
    trace, beats = drive(model)
    assert [e['cycle'] for e in trace if e['stage'] == 'WRITE_RESULT'] == [5, 6, 7, 8, 15]
    assert [e['cycle'] for e in trace if e['result_valid']] == [9, 16]
    assert model.memory.cycle == 18 and len(beats) == 2
    assert model.stats()['bank_conflict_request_cycles'] == 6  # 3+2+1 waiting requests
    assert [tile.read_result(i) for i in range(5)] == [-3, 8, 13, -11, -1]


def test_delayed_weight_response_waits_for_pair_and_finite_slots_limit_lookahead():
    config = TileMemoryConfig(weight=SramGeometry(read_latency=3))
    results = []
    for slots in (1, 4):
        tile, task = make_tile([[1, 2, 3, 4]], [1] * 4)
        model = PortScheduledCompute(tile, task, config, operand_slots=slots)
        trace, _ = drive(model)
        assert [e['cycle'] for e in trace if e['mac_k'] is not None][0] == 4
        assert tile.read_result(0) == 10
        assert model.stats()['max_operand_slots_used'] <= slots
        results.append(model.memory.cycle)
    assert results == [20, 11]  # one slot gives a four-cycle read/consume turnaround


def test_result_and_done_stalls_hold_all_fields_and_block_next_group_memory():
    tile, task = make_tile()
    model = PortScheduledCompute(tile, task)
    trace, beats = drive(model, ready=lambda c: c not in range(6, 10), done_ready=lambda c: c >= 20)
    assert len(trace) == 21 and len(beats) == 2
    assert all(trace[c]['result_payload'] == trace[6]['result_payload'] for c in range(6, 11))
    assert all(not trace[c]['requests'] for c in range(6, 11))
    assert trace[11]['stage'] == 'INIT_ACC'
    assert [e['cycle'] for e in trace if e['local_done_valid']] == [18, 19, 20]
    assert model.stats()['result_stall_cycles'] == 4 and model.stats()['done_stall_cycles'] == 2
    stats = model.stats()
    assert not model.step(result_ready=True).local_done_valid and model.stats() == stats


@pytest.mark.parametrize('config', [
    TileMemoryConfig(),
    TileMemoryConfig(weight=SramGeometry(issue_interval=2)),
    TileMemoryConfig(input=SramGeometry(read_latency=3)),
    TileMemoryConfig(result=SramGeometry(banks=2)),
    TileMemoryConfig(SramGeometry(), SramGeometry(), share_weight_input=True),
    TileMemoryConfig(SramGeometry(banks=2), SramGeometry(banks=2), share_weight_input=True),
])
def test_random_signed_arithmetic_offsets_tails_and_accounting(config):
    rng = random.Random(231)
    for m, k in ((1, 1), (4, 5), (5, 3), (9, 17)):
        matrix = [[rng.randint(-128, 127) for _ in range(k)] for _ in range(m)]
        vector = [rng.randint(-128, 127) for _ in range(k)]
        fields = dict(w_base=4, x_base=0x1001, y_base=0x2008, global_row_base=37,
                      result_kind='PSUM', task_last=False)
        tile, task = make_tile(matrix, vector, **fields)
        golden, _ = make_tile(matrix, vector, **fields)
        beats, done = golden.execute(task)
        model = PortScheduledCompute(tile, task, config)
        trace, actual = drive(model)
        assert actual == beats and model.done == done
        assert tile.result_sram == golden.result_sram
        stats = model.stats()
        assert sum(stats['stage_cycles'].values()) == stats['local_elapsed_cycles']
        assert stats['read_requests'] == 2 * ((m + 3) // 4) * k
        assert stats['write_requests'] == m and stats['useful_mac_ops'] == m * k
        assert sum(len(e['denied']) for e in trace) == (
            stats['bank_conflict_request_cycles'] + stats['port_busy_request_cycles'])
        if config == TileMemoryConfig():
            fast_tile, _ = make_tile(matrix, vector, **fields)
            fast = TimedCompute(fast_tile, task, ComputeTiming())
            replay_tile, _ = make_tile(matrix, vector, **fields)
            replay = PortScheduledCompute(replay_tile, task)
            for _ in trace:
                assert fast.signals() == replay.signals()
                fast.step(result_ready=True)
                replay.step(result_ready=True)
                assert fast_tile.result_sram == replay_tile.result_sram
            assert fast.finished and fast.stats()['local_elapsed_cycles'] == len(trace)


@pytest.mark.parametrize('kwargs', [{'banks': 0}, {'ports_per_bank': True},
                                   {'word_bytes': 1.5}, {'read_latency': -1}, {'issue_interval': 0}])
def test_bad_sram_geometry(kwargs):
    with pytest.raises(ValueError):
        SramGeometry(**kwargs)


def test_invalid_batch_has_no_partial_write_or_time_advance():
    tile, _ = make_tile()
    ports = TileMemoryPorts(tile)
    before = bytes(tile.result_sram)
    good = SramRequest('result0', 'result', 0x2000, 4, (0, 0), b'1234')
    bad = replace(good, client='result1', address=0x23ff)
    with pytest.raises(ValueError):
        ports.tick([good, bad])
    assert ports.cycle == 0 and bytes(tile.result_sram) == before
    with pytest.raises(ValueError):
        ports.tick([good, good])
    assert ports.cycle == 0


def test_mac_interval_is_a_real_compute_resource_and_invalid_tasks_never_touch_sram():
    tile, task = make_tile([[1, 2, 3]], [1, 1, 1])
    model = PortScheduledCompute(tile, task, mac_interval=3)
    trace, _ = drive(model)
    assert [e['cycle'] for e in trace if e['mac_k'] is not None] == [2, 5, 8]
    assert model.stats()['mac_interval_wait_cycles'] > 0 and tile.read_result(0) == 6
    before = bytes(tile.result_sram)
    with pytest.raises(ValueError):
        PortScheduledCompute(tile, replace(task, m_local=0))
    assert bytes(tile.result_sram) == before
