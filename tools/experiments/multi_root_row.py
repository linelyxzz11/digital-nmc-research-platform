#!/usr/bin/env python3
"""Run controlled ROW GEMV comparisons with one, two, or four final Roots."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT, ROOT/'src'):
    sys.path.insert(0, str(directory))

from compiler.grid_mapper import map_grid
from simulator.system.root_config import RootTiming
from simulator.system.scalable import run_grid
from tools.experiments.mapper_sweep import generate_workload


CONTRACT = 'multi-root-row-v0.1'


def _validate_config(config):
    if not isinstance(config, dict) or config.get('schema_version') != 1:
        raise ValueError('multi-Root config needs schema_version 1')
    if config.get('contract') != CONTRACT:
        raise ValueError(f'multi-Root config contract must be {CONTRACT}')
    if config.get('mesh_x') != 8 or config.get('mesh_y') != 8:
        raise ValueError('multi-Root v0.1 requires an 8x8 Mesh')
    if config.get('host_tile') != 0:
        raise ValueError('the controlled experiment keeps Host on Tile 0')
    root_sets = config.get('root_sets')
    if not isinstance(root_sets, dict) or not root_sets:
        raise ValueError('root_sets must be a nonempty object')
    for label, values in root_sets.items():
        try:
            count = int(label)
        except (TypeError, ValueError) as error:
            raise ValueError('Root-set keys must be integer strings') from error
        if (count not in (1, 2, 4) or not isinstance(values, list) or
            len(values) != count or len(set(values)) != count or values[0] != 0 or
            any(type(tile) is not int or not 0 <= tile < 64 for tile in values)):
            raise ValueError('each Root set must contain 1/2/4 distinct Tiles starting at Tile 0')
    workloads = config.get('workloads')
    if not isinstance(workloads, list) or not workloads:
        raise ValueError('workloads must be a nonempty list')
    for item in workloads:
        if (not isinstance(item, dict) or
            any(type(item.get(key)) is not int or item[key] < 1 for key in ('m', 'k', 'workers'))):
            raise ValueError('each workload needs positive integer m/k/workers')
    placements = config.get('placements')
    if (not isinstance(placements, list) or not placements or
        any(value not in ('compact', 'spread') for value in placements)):
        raise ValueError('placements must select compact and/or spread')
    profiles = config.get('root_profiles')
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError('root_profiles must be a nonempty object')
    for payload in profiles.values():
        RootTiming.from_dict(payload)
    if type(config.get('seed')) is not int or config['seed'] < 0:
        raise ValueError('seed must be a nonnegative integer')
    if type(config.get('tid')) is not int or not 0 <= config['tid'] <= 65535:
        raise ValueError('tid must be a 16-bit unsigned integer')


def build_cases(config):
    _validate_config(config)
    largest = max(config['root_sets'], key=int)
    all_roots = tuple(config['root_sets'][largest])
    cases = []
    for workload in config['workloads']:
        grid = (workload['workers'], 1)
        for placement in config['placements']:
            reference = map_grid(
                workload['m'], workload['k'], grid=grid,
                root_tile=all_roots[0], final_roots=all_roots,
                placement=placement, host_tile=config['host_tile'],
                seed=config['seed'], tid=config['tid'],
            )
            common_workers = reference.workers
            for profile in config['root_profiles']:
                for count in sorted(config['root_sets'], key=int):
                    roots = tuple(config['root_sets'][count])
                    plan = map_grid(
                        workload['m'], workload['k'], grid=grid,
                        workers=common_workers, root_tile=roots[0],
                        final_roots=roots, placement=placement,
                        host_tile=config['host_tile'], seed=config['seed'],
                        tid=config['tid'],
                    )
                    cases.append({
                        'm': workload['m'], 'k': workload['k'],
                        'worker_count': workload['workers'],
                        'workers': list(common_workers), 'placement': placement,
                        'profile': profile, 'final_roots': list(roots),
                        'plan': plan,
                    })
    return cases


def _git(*args):
    result = subprocess.run(['git', '-C', str(ROOT), *args], text=True,
                            capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else None


def _provenance(config_path):
    sources = {
        ROOT/'src/compiler/grid_mapper.py',
        ROOT/'src/simulator/system/multi_root.py',
        ROOT/'src/simulator/system/scalable.py',
        ROOT/'src/simulator/system/root_config.py',
        ROOT/'tools/experiments/mapper_sweep.py',
        Path(__file__).resolve(),
        Path(config_path).resolve(),
    }
    hashes = {}
    for path in sorted(sources):
        try:
            name = path.relative_to(ROOT).as_posix()
        except ValueError:
            name = f'config/{path.name}'
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    status = _git('status', '--porcelain')
    return {
        'git_head': _git('rev-parse', 'HEAD'),
        'git_dirty': bool(status),
        'git_status': status,
        'python': sys.version,
        'platform': platform.platform(),
        'source_sha256': hashes,
    }


def _save(directory, payload):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'results.json').write_text(
        json.dumps(payload, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    flat = []
    for row in payload['results']:
        flat.append({
            key: (json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value)
            for key, value in row.items() if key != 'summary'
        })
    fields = list(dict.fromkeys(key for row in flat for key in row))
    with (directory/'results.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(flat)


def run_experiment(config_path, output_dir, max_cases=None):
    config_path = Path(config_path)
    config = json.loads(config_path.read_text(encoding='utf-8'))
    cases = build_cases(config)
    if max_cases is not None:
        if type(max_cases) is not int or max_cases < 1:
            raise ValueError('max_cases must be a positive integer')
        cases = cases[:max_cases]
    rows = []
    for case in cases:
        matrix, vector = generate_workload(case['m'], case['k'])
        timing = RootTiming.from_dict(config['root_profiles'][case['profile']])
        run = run_grid(case['plan'], matrix, vector, root_timing=timing)
        rows.append({
            'm': case['m'], 'k': case['k'],
            'worker_count': case['worker_count'],
            'placement': case['placement'], 'profile': case['profile'],
            'root_count': len(case['final_roots']),
            'final_roots': case['final_roots'], 'workers': case['workers'],
            'cycles': run.cycles, 'golden_ok': True,
            'flit_hops': run.network['flit_hops'],
            'busiest_link_utilization': run.network['busiest_link_utilization'],
            'root_load_imbalance': run.root_metrics['load_imbalance'],
            'root_finish_cycle_span': run.root_metrics['finish_cycle_span'],
            'root_done_packets': run.done_count,
            'global_transaction_done': run.global_done_count,
            'speedup_vs_1root': None,
            'summary': run.summary(),
        })
    baselines = {
        (row['m'], row['k'], row['worker_count'], row['placement'], row['profile']): row['cycles']
        for row in rows if row['root_count'] == 1
    }
    for row in rows:
        key = (row['m'], row['k'], row['worker_count'], row['placement'], row['profile'])
        if key in baselines:
            row['speedup_vs_1root'] = baselines[key]/row['cycles']
    payload = {
        'schema_version': 1,
        'scope': ('ROW-only 1/2/4 final Root Python cycle model; resident weights; '
                  'functional input commit; no multi-Root RTL or 4096 claim'),
        'contract': CONTRACT,
        'config_sha256': hashlib.sha256(config_path.read_bytes()).hexdigest(),
        'cases': len(rows), 'ok': sum(row['golden_ok'] for row in rows),
        'provenance': _provenance(config_path), 'results': rows,
    }
    _save(output_dir, payload)
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path,
                        default=ROOT/'ini/scalable/multi_root_row.json')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--max-cases', type=int)
    args = parser.parse_args(argv)
    try:
        payload = run_experiment(args.config, args.output_dir, args.max_cases)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps({'cases': payload['cases'], 'ok': payload['ok']}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
