#!/usr/bin/env python3
"""Validate a single Tile's SRAM/MAC contract; keep raw cases and hand traces."""
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from simulator.functional.demo0_model import Demo0Tile, TileTask, int32_wrap
from simulator.system.tile_memory import SramGeometry, TileMemoryConfig
from simulator.system.tile_timing import PortScheduledCompute


SHAPES = [(1, 1), (2, 2), (4, 3), (5, 3), (8, 16), (16, 64), (32, 32),
          (64, 16), (128, 8), (256, 4), (4, 1024), (17, 53)]
HAND_W = [[1, 2, -1], [-2, 0, 4], [3, -1, 2], [0, 5, -2], [-1, 2, 1]]
HAND_X = [2, -1, 3]


def configurations():
    base = TileMemoryConfig()
    cases = {
        'reference': dict(memory=base),
        'weight_ii2': dict(memory=TileMemoryConfig(weight=SramGeometry(issue_interval=2))),
        'input_ii2': dict(memory=TileMemoryConfig(input=SramGeometry(word_bytes=1, issue_interval=2))),
        'read_l3_slots4': dict(memory=TileMemoryConfig(weight=SramGeometry(read_latency=3),
                                  input=SramGeometry(word_bytes=1, read_latency=3)), operand_slots=4),
        'read_l3_slots2': dict(memory=TileMemoryConfig(weight=SramGeometry(read_latency=3),
                                  input=SramGeometry(word_bytes=1, read_latency=3)), operand_slots=2),
        'one_operand_slot': dict(memory=base, operand_slots=1),
        'result_1bank': dict(memory=TileMemoryConfig(result=SramGeometry())),
        'result_2banks': dict(memory=TileMemoryConfig(result=SramGeometry(banks=2))),
        'mac_ii2': dict(memory=base, mac_interval=2),
    }
    for banks in (1, 2, 4):
        geometry = SramGeometry(banks=banks)
        cases[f'shared_wx_{banks}banks'] = dict(
            memory=TileMemoryConfig(geometry, geometry, share_weight_input=True))
    return cases


def run_case(matrix, vector, options, *, stalls=False, keep_trace=False):
    tile = Demo0Tile(1)
    tile.load_weight_matrix(matrix)
    tile.load_input_vector(vector)
    task = TileTask(1, 1, len(matrix), len(vector), global_row_base=8)
    model = PortScheduledCompute(tile, task, **options)
    beats, trace, pending = [], [], {}
    beat_hold, done_hold = Counter(), 0
    while not model.finished:
        if model.memory.cycle >= 100000:
            raise TimeoutError('single-Tile timing validation exceeded cycle budget')
        signals = model.signals()
        ready = done_ready = True
        if stalls and signals.result_valid:
            index = signals.result_payload.base_index
            ready = beat_hold[index] >= 3
            beat_hold[index] += 1
        if stalls and signals.local_done_valid:
            done_ready = done_hold >= 3
            done_hold += 1
        before = model.step(result_ready=ready, local_done_ready=done_ready)
        assert before == signals
        if before.result_valid and ready:
            beats.append(before.result_payload)
        event = model.last_event
        current = {r['client']: r for r in event['requests']}
        assert all(current.get(client) == request for client, request in pending.items())
        pending = {r['client']: r for r in event['denied']}
        for response in event['responses']:
            geometry = getattr(model.memory.config, response['request']['region'])
            assert response['available_cycle'] == response['accepted_cycle'] + geometry.read_latency
        # Check actual per-bank grants without treating independent regions as one port.
        grants = Counter()
        for request in event['grants']:
            geometry = getattr(model.memory.config, request['region'])
            pool = 'operands' if model.memory.config.share_weight_input and request['region'] in ('weight', 'input') else request['region']
            key = pool, request['address'] // geometry.word_bytes % geometry.banks
            grants[key] += 1
            assert grants[key] <= geometry.ports_per_bank
        if keep_trace:
            trace.append(event)
    expected = [int32_wrap(sum(w * x for w, x in zip(row, vector))) for row in matrix]
    assert [tile.read_result(i) for i in range(task.m_local)] == expected
    reconstructed = [value for beat in beats for lane, value in enumerate(beat.data) if beat.lane_mask & (1 << lane)]
    assert reconstructed == expected and model.done.status == 'OK'
    stats = model.stats()
    groups = (task.m_local + 3) // 4
    assert stats['read_requests'] == 2 * groups * task.k_local
    assert stats['write_requests'] == task.m_local and stats['write_bytes'] == 4 * task.m_local
    assert stats['useful_mac_ops'] == task.m_local * task.k_local
    assert sum(stats['stage_cycles'].values()) == stats['local_elapsed_cycles']
    assert stats['stage_cycles']['RUN'] == (stats['mac_active_cycles'] + stats['operand_wait_cycles']
                                          + stats['mac_interval_wait_cycles'])
    assert stats['stage_cycles']['EMIT_RESULT'] == groups + stats['result_stall_cycles']
    assert stats['stage_cycles']['LOCAL_DONE'] == 1 + stats['done_stall_cycles']
    assert stats['max_operand_slots_used'] <= model.operand_slots
    if options == dict(memory=TileMemoryConfig()):
        assert stats['local_elapsed_cycles'] == groups * (task.k_local + 4) + 1 + stats['result_stall_cycles'] + stats['done_stall_cycles']
    return dict(m=task.m_local, k=task.k_local, status='ok', golden_ok=True, stats=stats), trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    configs, rows, traces = configurations(), [], {}
    for name, options in configs.items():
        hand, trace = run_case(HAND_W, HAND_X, options, keep_trace=True)
        rows.append(dict(config=name, workload='hand_5x3', **hand))
        traces[name] = trace
        for seed in (0, 1):
            for m, k in SHAPES:
                rng = random.Random((seed * 1000 + m) * 10000 + k)
                matrix = [[rng.randint(-128, 127) for _ in range(k)] for _ in range(m)]
                vector = [rng.randint(-128, 127) for _ in range(k)]
                row, _ = run_case(matrix, vector, options)
                rows.append(dict(config=name, workload='random', seed=seed, **row))
    row, trace = run_case(HAND_W, HAND_X, configs['reference'], stalls=True, keep_trace=True)
    rows.append(dict(config='reference', workload='hand_5x3_stalls', **row))
    traces['reference_stalls'] = trace
    files = ['src/hardware.py', 'src/simulator/functional/demo0_model.py',
             'src/simulator/system/compute_timing.py', 'src/simulator/system/tile_memory.py',
             'src/simulator/system/tile_timing.py', 'src/tests/test_tile_timing.py',
             'tools/experiments/tile_timing.py']
    provenance = dict(
        head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        git_status=subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, text=True),
        source_sha256={f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in files})
    payload = dict(schema_version=1, scope='single Tile; preloaded SRAM; valid TileTask; Python model validation; not RTL-calibrated',
                   cases=len(rows), ok=len(rows), configurations={name: dict(memory=asdict(options['memory']),
                       operand_slots=options.get('operand_slots', 2), mac_interval=options.get('mac_interval', 1))
                       for name, options in configs.items()}, provenance=provenance, results=rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'results.json').write_text(json.dumps(payload, indent=2) + '\n')
    (args.output_dir / 'hand_traces.json').write_text(json.dumps(traces, indent=2,
                                                              default=lambda value: value.hex()) + '\n')
    with tarfile.open(args.output_dir / 'source_snapshot.tar.gz', 'w:gz') as snapshot:
        for filename in files:
            snapshot.add(ROOT / filename, arcname=filename)
    print(json.dumps(dict(cases=len(rows), ok=len(rows), output_dir=str(args.output_dir))))


if __name__ == '__main__':
    main()
