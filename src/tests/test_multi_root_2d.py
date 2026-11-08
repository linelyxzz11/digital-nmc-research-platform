"""Two-dimensional GEMV plans with multiple final Roots."""
from dataclasses import replace
import json
from pathlib import Path
import random

import pytest

from compiler.auto_mapper_v0_1 import MappingError
from compiler.grid_mapper import map_grid
from compiler.mapping_search import predict, predict_v03
from simulator.system.root_config import RootTiming
from simulator.system.scalable import run_grid
from tools.experiments.multi_root_2d import build_cases, run_experiment


WORKERS = (1, 2, 3, 4, 5, 6, 8, 9)
ROOT_SETS = ((0,), (0, 63), (0, 7, 56, 63))
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('roots', ROOT_SETS[1:])
def test_2d_plan_assigns_complete_row_partitions_to_roots(roots):
    plan = map_grid(13, 16, grid=(4, 2), workers=WORKERS,
                    final_roots=roots, reduction='central')
    assert plan.to_dict()['plan_version'] == '0.5'
    assert 'task_queues' not in plan.to_dict()
    assert plan.final_root_ranges[0].row_start == 0
    assert plan.final_root_ranges[-1].row_end == 13
    assert all(a.row_end == b.row_start
               for a, b in zip(plan.final_root_ranges, plan.final_root_ranges[1:]))
    assert all(len(queue.tasks) == 1 for queue in plan.task_queues)
    for row in range(4):
        row_tasks = plan.tasks[2*row:2*row+2]
        assert len({task.result_dst_tile for task in row_tasks}) == 1
        owner = next(item.root_tile for item in plan.final_root_ranges
                     if item.row_start <= plan.slices[2*row].row_start < item.row_end)
        assert all(task.result_dst_tile == owner for task in row_tasks)


def test_2d_root_ranges_keep_exact_per_row_source_masks():
    plan = map_grid(13, 16, grid=(4, 2), workers=WORKERS,
                    final_roots=(0, 63), reduction='central')
    assert plan.root_ranges == (
        (0, 4, (1 << 1) | (1 << 2)),
        (4, 7, (1 << 3) | (1 << 4)),
        (7, 10, (1 << 5) | (1 << 6)),
        (10, 13, (1 << 8) | (1 << 9)),
    )
    assert plan.final_root_ranges[0].source_mask == sum(1 << tile for tile in (1, 2, 3, 4))
    assert plan.final_root_ranges[1].source_mask == sum(1 << tile for tile in (5, 6, 8, 9))


def test_plan_versions_for_existing_modes_remain_unchanged():
    single = map_grid(13, 16, grid=(4, 2), workers=WORKERS)
    row = map_grid(13, 16, grid=(4, 1), workers=(1, 2, 3, 4),
                   final_roots=(0, 63))
    k = map_grid(13, 16, grid=(1, 4), workers=(1, 2, 3, 4),
                 final_roots=(0, 63))
    assert single.to_dict()['plan_version'] == '0.2'
    assert row.to_dict()['plan_version'] == '0.3'
    assert k.to_dict()['plan_version'] == '0.4'


@pytest.mark.parametrize('options', [
    dict(grid=(4, 2), final_roots=(0, 63), reduction='tree'),
    dict(grid=(2, 2), final_roots=(0, 7, 56, 63), reduction='central'),
])
def test_illegal_2d_multi_root_scope_is_rejected(options):
    with pytest.raises(MappingError):
        map_grid(13, 16, workers=tuple(range(1, options['grid'][0]*options['grid'][1]+1)),
                 **options)


def test_tampered_2d_task_destination_is_rejected():
    plan = map_grid(13, 16, grid=(4, 2), workers=WORKERS,
                    final_roots=(0, 63), reduction='central')
    broken = replace(plan, tasks=(replace(plan.tasks[0], result_dst_tile=63), *plan.tasks[1:]))
    with pytest.raises(MappingError):
        broken.validate()


def workload(m=13, k=16):
    rng = random.Random(307)
    matrix = [[rng.randint(-8, 7) for _ in range(k)] for _ in range(m)]
    vector = [rng.randint(-5, 5) for _ in range(k)]
    golden = [sum(a*b for a, b in zip(row, vector)) for row in matrix]
    return matrix, vector, golden


@pytest.mark.parametrize('roots', ROOT_SETS)
def test_2d_multi_root_matches_golden_and_finishes_once(roots):
    matrix, vector, golden = workload()
    plan = map_grid(13, 16, grid=(4, 2), workers=WORKERS,
                    final_roots=roots, reduction='central')
    run = run_grid(plan, matrix, vector)
    assert run.y == golden
    assert run.done_count == len(roots)
    assert run.global_done_count == 1
    assert sum(stats['useful_mac_ops'] for stats in run.compute.values()) == 13*16
    assert all(stats['subtasks'] == 1 for stats in run.compute.values())
    assert run.queue_metrics['task_switch_cycles'] == 0


def test_2d_roots_write_only_their_local_windows():
    matrix, vector, _ = workload()
    plan = map_grid(13, 16, grid=(4, 2), workers=WORKERS,
                    final_roots=(0, 63), reduction='central')
    run = run_grid(plan, matrix, vector)
    assert run.root_metrics['output_rows'] == {'0': 7, '63': 6}
    assert run.endpoints[0]['result_write_elements'] == 7
    assert run.endpoints[63]['result_write_elements'] == 6


def test_2d_multi_root_backpressure_remains_bounded():
    matrix, vector, golden = workload()
    plan = map_grid(13, 16, grid=(4, 2), workers=WORKERS,
                    final_roots=(0, 63), reduction='central')
    run = run_grid(plan, matrix, vector,
        root_timing=RootTiming(rx_queue_packets=1, lanes_per_cycle=1, write_cycles=2),
        sink_ready=lambda cycle, node: cycle % 7 < 2, max_cycles=100000)
    assert run.y == golden
    assert all(stats['max_rx_slots'] <= 1 for stats in run.endpoints.values())
    assert any(stats['rx_blocked_flit_cycles'] for stats in run.endpoints.values())
    assert run.network['output_blocked_port_cycles'] > 0


def test_explicit_single_root_2d_remains_cycle_identical():
    matrix, vector, _ = workload()
    implicit = run_grid(map_grid(13, 16, grid=(4, 2), workers=WORKERS), matrix, vector)
    explicit = run_grid(map_grid(13, 16, grid=(4, 2), workers=WORKERS,
                                 final_roots=(0,)), matrix, vector)
    assert implicit.cycles == explicit.cycles
    assert implicit.network == explicit.network
    assert implicit.packet_counts == explicit.packet_counts


def test_predictors_reject_2d_multi_root_instead_of_mispredicting():
    plan = map_grid(13, 16, grid=(4, 2), workers=WORKERS,
                    final_roots=(0, 63), reduction='central')
    message = '2D multi-Root plans are not supported by this predictor'
    with pytest.raises(ValueError, match=message):
        predict_v03(plan)
    with pytest.raises(ValueError, match=message):
        predict(plan)


def test_2d_experiment_has_36_cases_and_common_workers():
    config = json.loads((ROOT/'ini/scalable/multi_root_2d.json').read_text())
    cases = build_cases(config)
    assert len(cases) == 36
    groups = {}
    for case in cases:
        key = (case['m'], case['k'], tuple(case['grid']),
               case['placement'], case['profile'])
        groups.setdefault(key, set()).add(tuple(case['workers']))
    assert groups and all(len(worker_sets) == 1 for worker_sets in groups.values())
    assert {len(case['final_roots']) for case in cases} == {1, 2, 4}
    assert {tuple(case['grid']) for case in cases} == {(4, 4), (8, 2), (8, 4)}


def test_2d_smoke_experiment_writes_json_and_csv(tmp_path):
    config = {
        'schema_version': 1, 'contract': 'multi-root-2d-v0.1',
        'mesh_x': 8, 'mesh_y': 8, 'host_tile': 0,
        'root_sets': {'1': [0], '2': [0, 63]},
        'workloads': [{'m': 8, 'k': 8, 'grid': [2, 2]}],
        'placements': ['compact'],
        'root_profiles': {'baseline': {
            'rx_queue_packets': 2, 'reduction_entries': 256,
            'done_queue_packets': 1, 'lanes_per_cycle': 4,
            'read_cycles': 1, 'add_cycles': 1,
            'update_cycles': 1, 'write_cycles': 1}},
        'seed': 0, 'tid': 1,
    }
    path = tmp_path/'config.json'
    path.write_text(json.dumps(config))
    payload = run_experiment(path, tmp_path/'out')
    assert payload['cases'] == payload['ok'] == 2
    assert (tmp_path/'out/results.json').exists()
    assert (tmp_path/'out/results.csv').exists()
    assert all(row['golden_ok'] and row['global_transaction_done'] == 1 and
               row['task_switch_cycles'] == 0 for row in payload['results'])
