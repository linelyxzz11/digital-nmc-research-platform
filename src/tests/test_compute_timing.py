"""Reference cycle trace, SRAM visibility, arithmetic and joint NoC scheduling."""
from dataclasses import replace
import random

import pytest

from compiler.auto_mapper_v0_1 import map_gemv
from simulator.functional.demo0_model import Demo0Tile, TileTask
from simulator.system.compute_timing import ComputeTiming, TimedCompute
from simulator.system.demo1 import FinalGather, plan_from_mapping, run_plan
from simulator.system.demo2 import PSumReduce


def make_tile(matrix, vector, **task_fields):
    tile = Demo0Tile(1)
    tile.load_weight_matrix(matrix)
    tile.load_input_vector(vector)
    return tile, TileTask(1, 1, len(matrix), len(vector), **task_fields)


def drive(model, ready=lambda cycle: True, done_ready=lambda cycle: True):
    trace, beats = [], []
    while not model.finished:
        cycle = len(trace)
        assert cycle < 20000
        signals = model.signals()
        trace.append((cycle, model.stage, signals))
        if signals.result_valid and ready(cycle):
            beats.append(signals.result_payload)
        assert model.step(result_ready=ready(cycle), local_done_ready=done_ready(cycle)) == signals
    return trace, beats


def test_exact_five_by_three_frozen_reference_trace_and_no_early_sram_write():
    matrix = [[1, 2, -1], [-2, 0, 4], [3, -1, 2], [0, 5, -2], [-1, 2, 1]]
    tile, task = make_tile(matrix, [2, -1, 3], global_row_base=8)
    model = TimedCompute(tile, task, ComputeTiming())
    for _ in range(5):
        model.step(result_ready=True)
        assert bytes(tile.result_sram[:20]) == bytes(20)
    assert model.stage == "WRITE_RESULT"
    model.step(result_ready=True)
    assert [tile.read_result(i) for i in range(4)] == [-3, 8, 13, -11]
    assert tile.read_result(4) == 0
    assert model.signals().result_payload.base_index == 8
    tail, beats = drive(model)
    assert model.stats()["local_elapsed_cycles"] == 15
    assert model.stats()["local_no_stall_cycles"] == 15
    assert [beat.lane_mask for beat in beats] == [15, 1]
    assert [tile.read_result(i) for i in range(5)] == [-3, 8, 13, -11, -1]


def test_backpressure_holds_payload_and_delays_next_group_and_done():
    tile, task = make_tile([[1, 2, 3]] * 5, [1, 1, 1])
    model = TimedCompute(tile, task, ComputeTiming())
    trace, beats = drive(model, ready=lambda c: c not in range(6, 10),
                         done_ready=lambda c: c >= 20)
    assert len(trace) == 21  # baseline 15 + 4 result stalls + 2 done stalls
    assert all(trace[c][2].result_payload == trace[6][2].result_payload for c in range(6, 11))
    assert trace[11][1] == "INIT_ACC"
    assert [c for c, stage, _ in trace if stage == "LOCAL_DONE"] == [18, 19, 20]
    assert model.stats()["result_stall_cycles"] == 4
    assert model.stats()["done_stall_cycles"] == 2
    assert len(beats) == 2


@pytest.mark.parametrize("timing", [ComputeTiming(), ComputeTiming(weight_read_interval=2),
                                    ComputeTiming(input_read_interval=3),
                                    ComputeTiming(read_latency=3, result_write_cycles=2),
                                    ComputeTiming(mac_interval=2)])
def test_service_intervals_and_random_arithmetic_match_functional_reference(timing):
    rng = random.Random(42)
    for m, k in ((1, 1), (2, 5), (4, 3), (5, 3), (9, 7)):
        matrix = [[rng.randint(-128, 127) for _ in range(k)] for _ in range(m)]
        vector = [rng.randint(-128, 127) for _ in range(k)]
        tile, task = make_tile(matrix, vector, global_row_base=37, y_base=0x2008)
        reference, _ = make_tile(matrix, vector)
        expected_beats, expected_done = reference.execute(task)
        model = TimedCompute(tile, task, timing)
        trace, beats = drive(model)
        assert beats == expected_beats
        assert model.done == expected_done
        assert tile.result_sram == reference.result_sram
        groups = (m + 3) // 4
        assert len(trace) == groups * timing.group_cycles(k) + 1
        assert sum(stage == "MAC" for _, stage, _ in trace) == groups * k
        assert model.stats()["useful_mac_ops"] == m * k
        assert sum(stage == "RESOURCE_WAIT" for _, stage, _ in trace) == model.stats()["resource_gap_cycles"]


@pytest.mark.parametrize("strategy", ["row", "k"])
@pytest.mark.parametrize("root", [0, 3])
def test_timed_plan_preserves_results_packets_ack_and_uses_shared_clock(strategy, root):
    W = [[(i + j) % 9 - 4 for j in range(5)] for i in range(33)]
    x = [1, -2, 3, -4, 5]
    plan = plan_from_mapping(map_gemv(W, x, strategy=strategy, root_tile=root))
    factory = FinalGather if strategy == "row" else PSumReduce
    free = run_plan(plan, endpoint_factory=factory)
    timed = run_plan(plan, endpoint_factory=factory, compute_timing=ComputeTiming())
    assert free.y == timed.y
    assert free.ack_keys == timed.ack_keys
    assert free.mesh.flit_hops == timed.mesh.flit_hops
    assert len(timed.host_done) == 1
    summary = timed.summary()
    assert "network_schedule_cycles" not in summary
    assert summary["modeled_schedule_cycles"] == timed.mesh.cycle
    assert "functional reduction" in summary["timing_scope"]
    assert sum(s["useful_mac_ops"] for s in timed.compute_stats.values()) == 33 * 5
    for task in plan.tasks:
        stats = timed.compute_stats[task.tile_id]
        assert stats["local_elapsed_cycles"] == stats["local_no_stall_cycles"] + stats["result_stall_cycles"]
        start = next(e['network_cycle'] for e in timed.trace if e['event']=='TASK_START' and e['tile_id']==task.tile_id)
        accepted = [e['network_cycle'] for e in timed.trace if e['event']=='RESULTBEAT_ACCEPT' and e['tile']==task.tile_id]
        assert min(accepted) >= start + task.k_local + 3


def test_partial_timing_under_pressure_still_completes_and_has_real_compute_stalls():
    W = [[1] * 5 for _ in range(65)]
    plan = plan_from_mapping(map_gemv(W, [1] * 5, strategy="k"))
    run = run_plan(plan, endpoint_factory=PSumReduce, compute_timing=ComputeTiming(),
                   sink_ready=lambda cycle, tile, phase: tile != 3 or cycle >= 200)
    assert run.y == [5] * 65 and len(run.host_done) == 1
    assert sum(s["result_stall_cycles"] for s in run.compute_stats.values()) > 0
    assert run.mesh.fifo_full_cycles > 0


@pytest.mark.parametrize("kwargs", [{"read_latency": 0}, {"weight_read_interval": -1},
                                    {"mac_interval": True}, {"result_write_cycles": 1.5}])
def test_reject_invalid_timing(kwargs):
    with pytest.raises(ValueError):
        ComputeTiming(**kwargs)


def test_finished_compute_does_not_advance_or_repeat_done():
    tile, task = make_tile([[2]], [3])
    model = TimedCompute(tile, task, ComputeTiming())
    drive(model)
    before = model.stats()
    assert not model.step(result_ready=True).local_done_valid
    assert before == model.stats()
