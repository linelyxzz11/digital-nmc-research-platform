"""Pure-K partition plans with sequential per-Tile multi-Root tasks."""
from dataclasses import replace
import json
from pathlib import Path
import random

import pytest

from compiler.auto_mapper_v0_1 import MappingError
from compiler.grid_mapper import map_grid
from compiler.mapping_search import predict, predict_v03
from simulator.functional import Demo0Tile, TileTask
from simulator.system.task_queue import QueuedCompute
from simulator.system.root_config import RootTiming
from simulator.system.scalable import run_grid
from simulator.system.tile_config import TileComputeConfig
from simulator.system.tile_timing import PortScheduledCompute
from tools.experiments.multi_root_k import build_cases, run_experiment


K_WORKERS = (1, 2, 3, 4)
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('roots', [(0, 63), (0, 7, 56, 63)])
def test_k_multi_root_builds_aligned_rotating_task_queues(roots):
    plan = map_grid(13, 16, grid=(1, 4), workers=K_WORKERS,
                    final_roots=roots, reduction='central')
    assert plan.to_dict()['plan_version'] == '0.4'
    assert len(plan.task_queues) == len(K_WORKERS)
    assert all(len(queue.tasks) == len(roots) for queue in plan.task_queues)
    assert plan.final_root_ranges[0].row_start == 0
    assert plan.final_root_ranges[-1].row_end == 13
    assert all(item.row_start % 4 == 0 for item in plan.final_root_ranges)
    assert all(a.row_end == b.row_start
               for a, b in zip(plan.final_root_ranges, plan.final_root_ranges[1:]))
    for worker_index, queue in enumerate(plan.task_queues):
        offset = worker_index % len(roots)
        expected = roots[offset:] + roots[:offset]
        assert tuple(task.result_dst_tile for task in queue.tasks) == expected
        assert sum(task.m_local for task in queue.tasks) == 13
        assert all(task.result_kind == 'PSUM' and task.task_last
                   for task in queue.tasks)


def test_k_subtask_weight_windows_cover_original_worker_once():
    plan = map_grid(13, 16, grid=(1, 4), workers=K_WORKERS,
                    final_roots=(0, 63), reduction='central')
    k_local = 4
    expected = {(0, 8, 0), (8, 13, 8*k_local)}
    for queue in plan.task_queues:
        actual = {(task.global_row_base,
                   task.global_row_base + task.m_local,
                   task.w_base) for task in queue.tasks}
        assert actual == expected
        assert sum(task.m_local * task.k_local for task in queue.tasks) == 13*k_local


def test_legacy_and_row_multi_root_serialization_stay_frozen():
    legacy = map_grid(13, 16, grid=(1, 4), workers=K_WORKERS)
    row = map_grid(13, 16, grid=(4, 1), workers=K_WORKERS,
                   final_roots=(0, 63))
    assert legacy.to_dict()['plan_version'] == '0.2'
    assert 'task_queues' not in legacy.to_dict()
    assert row.to_dict()['plan_version'] == '0.3'
    assert 'task_queues' not in row.to_dict()


@pytest.mark.parametrize('changes', [
    {'reduction': 'tree'},
    {'m': 3, 'final_roots': (0, 63)},
])
def test_illegal_k_multi_root_scope_is_rejected(changes):
    options = dict(m=13, k=16, grid=(1, 4), workers=K_WORKERS,
                   final_roots=(0, 63), reduction='central')
    options.update(changes)
    with pytest.raises(MappingError):
        map_grid(**options)


def test_tampered_k_task_queue_is_rejected():
    plan = map_grid(13, 16, grid=(1, 4), workers=K_WORKERS,
                    final_roots=(0, 63), reduction='central')
    first = plan.task_queues[0]
    broken_task = replace(first.tasks[0], w_base=first.tasks[0].w_base + 4)
    broken_queue = replace(first, tasks=(broken_task, *first.tasks[1:]))
    broken = replace(plan, task_queues=(broken_queue, *plan.task_queues[1:]))
    with pytest.raises(MappingError):
        broken.validate()


def run_compute(engine, ready=lambda cycle: True, limit=10000):
    beats = []
    done_cycles = []
    for cycle in range(limit):
        signals = engine.signals()
        accepted = ready(cycle)
        if signals.result_valid and accepted:
            beats.append(signals.result_payload)
        if signals.local_done_valid:
            done_cycles.append(cycle)
        engine.step(result_ready=accepted, local_done_ready=True)
        if engine.finished:
            return beats, done_cycles, cycle + 1
    raise AssertionError('compute did not finish')


def test_single_task_queue_matches_existing_compute_cycle_for_cycle():
    tile_a, tile_b = Demo0Tile(1), Demo0Tile(1)
    matrix = [[row + col for col in range(4)] for row in range(8)]
    vector = [1, -2, 3, -4]
    for tile in (tile_a, tile_b):
        tile.load_weight_matrix(matrix)
        tile.load_input_vector(vector)
    task = TileTask(9, 1, 8, 4, result_dst_tile=0, result_kind='PSUM')
    queued = QueuedCompute(tile_a, (task,), TileComputeConfig())
    direct = PortScheduledCompute(tile_b, task)
    assert run_compute(queued) == run_compute(direct)


def test_queue_hides_intermediate_done_and_charges_switch_cycle():
    tile = Demo0Tile(1, tile_count=64)
    tile.load_weight_matrix([[1, 2] for _ in range(8)])
    tile.load_input_vector([3, 4])
    tasks = (
        TileTask(9, 1, 4, 2, w_base=0, global_row_base=0,
                 result_dst_tile=0, result_kind='PSUM'),
        TileTask(9, 1, 4, 2, w_base=8, global_row_base=4,
                 result_dst_tile=63, result_kind='PSUM'),
    )
    engine = QueuedCompute(tile, tasks, TileComputeConfig(), task_switch_cycles=1)
    beats, done_cycles, _ = run_compute(engine, ready=lambda cycle: cycle % 5 != 0)
    assert [beat.dst_tile for beat in beats] == [0, 63]
    assert [beat.base_index for beat in beats] == [0, 4]
    assert all(beat.last for beat in beats)
    assert len(done_cycles) == 1
    assert engine.stats()['task_switch_cycles'] == 1
    assert engine.stats()['completed_subtasks'] == 2


def k_workload(m=13, k=16):
    rng = random.Random(211)
    matrix = [[rng.randint(-8, 7) for _ in range(k)] for _ in range(m)]
    vector = [rng.randint(-5, 5) for _ in range(k)]
    golden = [sum(a*b for a, b in zip(row, vector)) for row in matrix]
    return matrix, vector, golden


@pytest.mark.parametrize('roots', [(0,), (0, 63), (0, 7, 56, 63)])
def test_k_multi_root_matches_golden_and_finishes_once(roots):
    matrix, vector, golden = k_workload()
    plan = map_grid(13, 16, grid=(1, 4), workers=K_WORKERS,
                    final_roots=roots, reduction='central')
    run = run_grid(plan, matrix, vector)
    assert run.y == golden
    assert run.done_count == len(roots)
    assert run.global_done_count == 1
    assert sum(stats['useful_mac_ops'] for stats in run.compute.values()) == 13*16
    assert set(run.final_root_finish_cycles) == set(roots)


def test_k_multi_root_writes_only_local_root_windows():
    matrix, vector, _ = k_workload()
    run = run_grid(map_grid(13, 16, grid=(1, 4), workers=K_WORKERS,
                            final_roots=(0, 63)), matrix, vector)
    assert run.root_metrics['output_rows'] == {'0': 8, '63': 5}
    assert run.endpoints[0]['result_write_elements'] == 8
    assert run.endpoints[63]['result_write_elements'] == 5
    assert all(stats['subtasks'] == 2 for stats in run.compute.values())
    assert sum(stats['task_switch_cycles'] for stats in run.compute.values()) == 4


def test_k_multi_root_backpressure_keeps_queue_and_root_bounded():
    matrix, vector, golden = k_workload()
    timing = RootTiming(rx_queue_packets=1, lanes_per_cycle=1, write_cycles=2)
    plan = map_grid(13, 16, grid=(1, 4), workers=K_WORKERS,
                    final_roots=(0, 63))
    run = run_grid(plan, matrix, vector, root_timing=timing,
                   sink_ready=lambda cycle, node: cycle % 7 < 2,
                   max_cycles=100000)
    assert run.y == golden
    assert all(stats['max_rx_slots'] <= 1 for stats in run.endpoints.values())
    assert any(stats['rx_blocked_flit_cycles'] for stats in run.endpoints.values())
    assert run.network['output_blocked_port_cycles'] > 0


def test_explicit_single_root_k_remains_cycle_identical():
    matrix, vector, _ = k_workload()
    implicit = run_grid(map_grid(13, 16, grid=(1, 4), workers=K_WORKERS),
                        matrix, vector)
    explicit = run_grid(map_grid(13, 16, grid=(1, 4), workers=K_WORKERS,
                                 final_roots=(0,)), matrix, vector)
    assert implicit.cycles == explicit.cycles
    assert implicit.network == explicit.network
    assert implicit.packet_counts == explicit.packet_counts


def test_predictors_reject_k_multi_root_instead_of_mispredicting():
    plan = map_grid(13, 16, grid=(1, 4), workers=K_WORKERS,
                    final_roots=(0, 63), reduction='central')
    message = 'K multi-Root task queues are not supported by this predictor'
    with pytest.raises(ValueError, match=message):
        predict_v03(plan)
    with pytest.raises(ValueError, match=message):
        predict(plan)


def test_k_experiment_has_36_cases_and_reuses_identical_workers():
    config = json.loads((ROOT/'ini/scalable/multi_root_k.json').read_text())
    cases = build_cases(config)
    assert len(cases) == 36
    groups = {}
    for case in cases:
        key = (case['m'], case['k'], case['worker_count'],
               case['placement'], case['profile'])
        groups.setdefault(key, set()).add(tuple(case['workers']))
    assert groups and all(len(worker_sets) == 1 for worker_sets in groups.values())
    assert {len(case['final_roots']) for case in cases} == {1, 2, 4}


def test_k_smoke_experiment_writes_correct_json_and_csv(tmp_path):
    config = {
        'schema_version': 1, 'contract': 'multi-root-k-v0.1',
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
    payload = run_experiment(config_path, tmp_path/'out')
    assert payload['cases'] == payload['ok'] == 2
    assert (tmp_path/'out/results.json').exists()
    assert (tmp_path/'out/results.csv').exists()
    assert all(row['golden_ok'] and row['global_transaction_done'] == 1
               for row in payload['results'])
