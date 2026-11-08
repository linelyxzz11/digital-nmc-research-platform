"""ROW partition plans with one, two, or four final Roots."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from compiler.auto_mapper_v0_1 import MappingError
from compiler.grid_mapper import FinalRootRange, map_grid
from simulator.communication import StatusCode, TransactionDonePacket
from simulator.system.multi_root import RootDoneTracker
from simulator.system.root_config import RootTiming
from simulator.system.scalable import run_grid
from tools.experiments.multi_root_row import build_cases, run_experiment


ROOT_SETS = ((0,), (0, 63), (0, 7, 56, 63))
WORKERS = (1, 2, 3, 4)
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("roots", ROOT_SETS)
def test_row_plan_partitions_workers_across_final_roots(roots):
    plan = map_grid(13, 7, grid=(4, 1), workers=WORKERS,
                    root_tile=0, final_roots=roots)
    assert plan.final_roots == roots
    assert plan.final_root_ranges[0].row_start == 0
    assert plan.final_root_ranges[-1].row_end == 13
    assert all(a.row_end == b.row_start
               for a, b in zip(plan.final_root_ranges, plan.final_root_ranges[1:]))
    assert {task.result_dst_tile for task in plan.tasks} == set(roots)
    assert sum(r.row_end-r.row_start for r in plan.final_root_ranges) == 13
    assert sum(mask.bit_count() for _, _, mask in plan.root_ranges) == 4
    if len(roots) == 1:
        assert plan.to_dict()['plan_version'] == '0.2'
    else:
        assert plan.to_dict()['plan_version'] == '0.3'
        assert plan.to_dict()['final_roots'] == roots


def test_default_and_explicit_single_root_are_identical():
    implicit = map_grid(13, 7, grid=(4, 1), workers=WORKERS, root_tile=0)
    explicit = map_grid(13, 7, grid=(4, 1), workers=WORKERS,
                        root_tile=0, final_roots=(0,))
    assert implicit == explicit
    assert 'final_roots' not in implicit.to_dict()
    assert 'final_root_ranges' not in implicit.to_dict()
    assert implicit.final_root_ranges == (
        FinalRootRange(0, 0, 13, sum(1 << worker for worker in WORKERS)),
    )


@pytest.mark.parametrize("changes", [
    {"final_roots": (0, 0)},
    {"final_roots": (0, 64)},
    {"final_roots": (63, 0)},
    {"final_roots": (0, 7, 56)},
    {"final_roots": (0, 7, 56, 63), "grid": (2, 1)},
])
def test_illegal_multi_root_plans_fail_before_execution(changes):
    options = dict(m=13, k=7, grid=(4, 1), workers=WORKERS, root_tile=0)
    options.update(changes)
    with pytest.raises(MappingError):
        map_grid(**options)


def test_tampered_final_root_range_is_rejected():
    plan = map_grid(13, 7, grid=(4, 1), workers=WORKERS,
                    final_roots=(0, 63))
    broken = replace(plan, final_root_ranges=(
        replace(plan.final_root_ranges[0], row_end=8),
        plan.final_root_ranges[1],
    ))
    with pytest.raises(MappingError):
        broken.validate()


def test_single_root_tree_records_direct_reducers_not_leaf_workers():
    plan = map_grid(8, 16, grid=(1, 4), reduction='tree', fan_in=2)
    direct_mask = 0
    for _, _, mask in plan.root_ranges:
        direct_mask |= mask
    assert plan.final_root_ranges[0].source_mask == direct_mask
    assert direct_mask != sum(1 << worker for worker in plan.workers)


def done(src, count, mask, *, tid=9, dst=0, status=StatusCode.OK):
    return TransactionDonePacket(src, dst, tid, count, mask, status)


def test_host_declares_one_global_completion_after_all_roots_finish():
    tracker = RootDoneTracker(9, 0, {0: (7, 0b00110), 63: (6, 0b11000)})
    assert not tracker.complete and tracker.count == 0
    tracker.accept(done(63, 6, 0b11000))
    assert not tracker.complete and tracker.count == 1
    tracker.accept(done(0, 7, 0b00110))
    assert tracker.complete and tracker.count == 2
    assert set(tracker.done_by_root) == {0, 63}


@pytest.mark.parametrize("packet", [
    done(7, 7, 0b00110),
    done(0, 7, 0b00110, tid=10),
    done(0, 7, 0b00110, dst=1),
    done(0, 8, 0b00110),
    done(0, 7, 0b00111),
    done(0, 7, 0b00110, status=StatusCode.PROTOCOL_ERROR),
])
def test_host_rejects_invalid_root_done(packet):
    tracker = RootDoneTracker(9, 0, {0: (7, 0b00110), 63: (6, 0b11000)})
    with pytest.raises(RuntimeError):
        tracker.accept(packet)


def test_host_rejects_duplicate_root_done():
    tracker = RootDoneTracker(9, 0, {0: (7, 0b00110)})
    packet = done(0, 7, 0b00110)
    tracker.accept(packet)
    with pytest.raises(RuntimeError):
        tracker.accept(packet)


def workload(m=13, k=7):
    matrix = [[((row * 5 + col * 3) % 17) - 8 for col in range(k)]
              for row in range(m)]
    vector = [((col * 7) % 11) - 5 for col in range(k)]
    golden = [sum(a*b for a, b in zip(row, vector)) for row in matrix]
    return matrix, vector, golden


@pytest.mark.parametrize("roots", ROOT_SETS)
def test_row_multi_root_system_matches_golden_and_finishes_once(roots):
    matrix, vector, golden = workload()
    plan = map_grid(13, 7, grid=(4, 1), workers=WORKERS,
                    root_tile=0, final_roots=roots)
    run = run_grid(plan, matrix, vector)
    assert run.y == golden
    assert run.done_count == len(roots)
    assert run.global_done_count == 1
    assert sum(stats['useful_mac_ops'] for stats in run.compute.values()) == 13*7
    assert set(run.final_root_finish_cycles) == set(roots)
    assert set(run.summary()['final_roots']) == set(roots)


def test_each_root_writes_only_its_local_output_window():
    matrix, vector, golden = workload()
    plan = map_grid(13, 7, grid=(4, 1), workers=WORKERS,
                    final_roots=(0, 63))
    run = run_grid(plan, matrix, vector)
    assert run.y == golden
    assert run.endpoints[0]['result_write_elements'] == 7
    assert run.endpoints[63]['result_write_elements'] == 6
    assert run.root_metrics['output_rows'] == {'0': 7, '63': 6}


def test_explicit_single_root_preserves_default_cycles_and_traffic():
    matrix, vector, _ = workload()
    implicit = run_grid(map_grid(13, 7, grid=(4, 1), workers=WORKERS),
                        matrix, vector)
    explicit = run_grid(map_grid(13, 7, grid=(4, 1), workers=WORKERS,
                                 final_roots=(0,)), matrix, vector)
    assert implicit.cycles == explicit.cycles
    assert implicit.network == explicit.network
    assert implicit.packet_counts == explicit.packet_counts


def test_multi_root_backpressure_keeps_all_resources_bounded():
    matrix, vector, golden = workload()
    timing = RootTiming(rx_queue_packets=1, lanes_per_cycle=1, write_cycles=2)
    plan = map_grid(13, 7, grid=(4, 1), workers=WORKERS,
                    final_roots=(0, 63))
    run = run_grid(plan, matrix, vector, root_timing=timing,
                   sink_ready=lambda cycle, node: cycle % 7 < 2,
                   max_cycles=50000)
    assert run.y == golden
    assert all(stats['max_rx_slots'] <= 1 for stats in run.endpoints.values())
    assert any(stats['rx_blocked_flit_cycles'] for stats in run.endpoints.values())


def test_experiment_reuses_identical_workers_across_root_counts():
    config = json.loads((ROOT/'ini/scalable/multi_root_row.json').read_text())
    cases = build_cases(config)
    groups = {}
    for case in cases:
        key = (case['m'], case['k'], case['worker_count'],
               case['placement'], case['profile'])
        groups.setdefault(key, set()).add(tuple(case['workers']))
    assert groups and all(len(worker_sets) == 1 for worker_sets in groups.values())
    assert {len(case['final_roots']) for case in cases} == {1, 2, 4}


def test_smoke_experiment_writes_correct_json_and_csv(tmp_path):
    config = {
        'schema_version': 1, 'contract': 'multi-root-row-v0.1',
        'mesh_x': 8, 'mesh_y': 8, 'host_tile': 0,
        'root_sets': {'1': [0], '2': [0, 63]},
        'workloads': [{'m': 8, 'k': 8, 'workers': 4}],
        'placements': ['compact'],
        'root_profiles': {'baseline': {
            'rx_queue_packets': 2, 'reduction_entries': 256,
            'done_queue_packets': 1, 'lanes_per_cycle': 4,
            'read_cycles': 1, 'add_cycles': 1,
            'update_cycles': 1, 'write_cycles': 1}},
        'seed': 0, 'tid': 1,
    }
    config_path = tmp_path/'config.json'
    config_path.write_text(json.dumps(config))
    result = run_experiment(config_path, tmp_path/'out')
    assert result['cases'] == result['ok'] == 2
    assert (tmp_path/'out/results.json').exists()
    assert (tmp_path/'out/results.csv').exists()
    assert all(row['golden_ok'] for row in result['results'])
