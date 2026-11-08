#!/usr/bin/env python3
"""Search multi-Root placements and compare a static Mapper with an Oracle."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import csv
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT, ROOT / "src"):
    sys.path.insert(0, str(directory))

from compiler.multi_root_placement import generate_candidates, rank_candidates
from simulator.system.root_config import RootTiming
from simulator.system.scalable import run_grid
from tools.experiments.mapper_sweep import generate_workload


CONTRACT = "multi-root-placement-search-v0.1"
SPLIT_ORDER = ("row", "k", "2d")


def _is_int(value) -> bool:
    return type(value) is int


def validate_config(config: dict) -> None:
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise ValueError("placement search needs schema_version 1")
    if config.get("contract") != CONTRACT:
        raise ValueError(f"placement search contract must be {CONTRACT}")
    if config.get("mesh_x") != 8 or config.get("mesh_y") != 8:
        raise ValueError("placement search requires an 8x8 Mesh")
    if config.get("host_tile") != 0:
        raise ValueError("Host and the first Root must stay on Tile 0")
    if config.get("reduction") != "central":
        raise ValueError("this placement search supports central reduction only")
    if not _is_int(config.get("tid")) or not 0 <= config["tid"] <= 65535:
        raise ValueError("tid must be a 16-bit unsigned integer")

    seeds = config.get("seeds")
    if (not isinstance(seeds, list) or not seeds or
            any(not _is_int(seed) or seed < 0 for seed in seeds) or
            len(set(seeds)) != len(seeds)):
        raise ValueError("seeds must be distinct nonnegative integers")

    root_sets = config.get("root_sets")
    canonical = config.get("canonical_root_sets")
    if not isinstance(root_sets, dict) or not isinstance(canonical, dict):
        raise ValueError("root_sets and canonical_root_sets must be objects")
    if set(root_sets) != set(canonical) or not root_sets:
        raise ValueError("root_sets and canonical_root_sets must use the same keys")
    for label, sets in root_sets.items():
        try:
            count = int(label)
        except (TypeError, ValueError) as error:
            raise ValueError("Root-set keys must be integer strings") from error
        if count not in (1, 2, 4) or not isinstance(sets, list) or not sets:
            raise ValueError("Root-set keys must select 1/2/4 nonempty catalogues")
        normalized = []
        for roots in sets:
            if (not isinstance(roots, list) or len(roots) != count or
                    len(set(roots)) != count or roots[0] != 0 or
                    any(not _is_int(tile) or not 0 <= tile < 64 for tile in roots)):
                raise ValueError("each Root template must be distinct, valid, and start at Tile 0")
            normalized.append(tuple(roots))
        baseline = canonical[label]
        if (not isinstance(baseline, list) or tuple(baseline) not in normalized):
            raise ValueError("canonical Root set must be present in its candidate catalogue")

    workloads = config.get("workloads")
    if not isinstance(workloads, list) or not workloads:
        raise ValueError("workloads must be a nonempty list")
    for workload in workloads:
        if (not isinstance(workload, dict) or
                any(not _is_int(workload.get(key)) or workload[key] < 1
                    for key in ("m", "k", "workers"))):
            raise ValueError("each workload needs positive integer M/K/Worker fields")
        grids = workload.get("grids")
        if not isinstance(grids, dict) or set(grids) != set(SPLIT_ORDER):
            raise ValueError("each workload needs ROW/K/2D grids")
        normalized_grids = {}
        for name, grid in grids.items():
            if (not isinstance(grid, list) or len(grid) != 2 or
                    any(not _is_int(value) or value < 1 for value in grid)):
                raise ValueError("each split grid needs two positive integers")
            normalized_grids[name] = tuple(grid)
        if (normalized_grids["row"][1] != 1 or normalized_grids["k"][0] != 1 or
                min(normalized_grids["2d"]) <= 1):
            raise ValueError("ROW/K/2D grid axes disagree with their split names")
        if any(rows * columns != workload["workers"]
               for rows, columns in normalized_grids.values()):
            raise ValueError("all split grids must use the same Worker count")

    profiles = config.get("root_profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("root_profiles must be a nonempty object")
    for payload in profiles.values():
        RootTiming.from_dict(payload)


def _digest(payload) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_groups(config: dict) -> list[dict]:
    validate_config(config)
    groups = []
    for workload in config["workloads"]:
        for split_family in SPLIT_ORDER:
            grid = tuple(workload["grids"][split_family])
            for profile in config["root_profiles"]:
                for label in sorted(config["root_sets"], key=int):
                    root_count = int(label)
                    identity = {
                        "m": workload["m"],
                        "k": workload["k"],
                        "worker_count": workload["workers"],
                        "split_family": split_family,
                        "grid": list(grid),
                        "root_count": root_count,
                        "profile": profile,
                    }
                    groups.append({
                        "group_id": _digest(identity)[:20],
                        **identity,
                        "grid": grid,
                        "root_sets": tuple(
                            tuple(roots) for roots in config["root_sets"][label]
                        ),
                        "canonical_roots": tuple(config["canonical_root_sets"][label]),
                    })
    return groups


def summarize_group(candidate_rows: list[dict], *, mapper_id: str,
                    compact_id: str, spread_id: str) -> dict:
    if not candidate_rows:
        raise ValueError("cannot summarize an empty candidate group")
    by_id = {row["candidate_id"]: row for row in candidate_rows}
    if len(by_id) != len(candidate_rows):
        raise ValueError("candidate IDs must be unique inside a comparison group")
    missing = [identity for identity in (mapper_id, compact_id, spread_id)
               if identity not in by_id]
    if missing:
        raise ValueError(f"summary references absent candidate IDs: {missing}")
    if any(not _is_int(row.get("cycles")) or row["cycles"] < 1
           for row in candidate_rows):
        raise ValueError("every exact candidate needs a positive integer cycle count")

    oracle_cycles = min(row["cycles"] for row in candidate_rows)
    mapper_cycles = by_id[mapper_id]["cycles"]
    compact_cycles = by_id[compact_id]["cycles"]
    spread_cycles = by_id[spread_id]["cycles"]
    regret = mapper_cycles / oracle_cycles - 1
    if regret < 0:
        raise AssertionError("Mapper cycles cannot beat the finite-catalogue Oracle")
    return {
        "oracle_cycles": oracle_cycles,
        "oracle_candidate_ids": sorted(
            row["candidate_id"] for row in candidate_rows
            if row["cycles"] == oracle_cycles
        ),
        "mapper_cycles": mapper_cycles,
        "fixed_compact_cycles": compact_cycles,
        "fixed_spread_cycles": spread_cycles,
        "oracle_hit": mapper_cycles == oracle_cycles,
        "regret": regret,
        "speedup_vs_fixed_compact": compact_cycles / mapper_cycles,
        "speedup_vs_fixed_spread": spread_cycles / mapper_cycles,
    }


def _geomean(values: list[float]) -> float:
    if not values or any(value <= 0 for value in values):
        raise ValueError("geometric mean needs positive values")
    return math.exp(sum(math.log(value) for value in values) / len(values))


def _aggregate(groups: list[dict]) -> dict:
    regrets = [group["regret"] for group in groups]
    compact = [group["speedup_vs_fixed_compact"] for group in groups]
    spread = [group["speedup_vs_fixed_spread"] for group in groups]

    def outcomes(values):
        return {
            "wins": sum(value > 1 for value in values),
            "ties": sum(value == 1 for value in values),
            "losses": sum(value < 1 for value in values),
        }

    return {
        "groups": len(groups),
        "oracle_hits": sum(group["oracle_hit"] for group in groups),
        "oracle_hit_rate": (
            sum(group["oracle_hit"] for group in groups) / len(groups)
            if groups else 0
        ),
        "mean_regret": sum(regrets) / len(regrets) if regrets else 0,
        "worst_regret": max(regrets, default=0),
        "geomean_speedup_vs_fixed_compact": _geomean(compact) if compact else 0,
        "geomean_speedup_vs_fixed_spread": _geomean(spread) if spread else 0,
        "outcomes_vs_fixed_compact": outcomes(compact),
        "outcomes_vs_fixed_spread": outcomes(spread),
    }


def _git(*args):
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args], text=True, capture_output=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _provenance(config_path: Path) -> dict:
    sources = {
        ROOT / "src/compiler/grid_mapper.py",
        ROOT / "src/compiler/multi_root_placement.py",
        ROOT / "src/simulator/system/scalable.py",
        Path(__file__).resolve(),
        config_path.resolve(),
    }
    hashes = {}
    for path in sorted(sources):
        try:
            name = path.relative_to(ROOT).as_posix()
        except ValueError:
            name = f"config/{path.name}"
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    status = _git("status", "--porcelain")
    return {
        "git_head": _git("rev-parse", "HEAD"),
        "git_dirty": bool(status),
        "git_status": status,
        "python": sys.version,
        "platform": platform.platform(),
        "source_sha256": hashes,
    }


def _find_baseline(candidates, canonical_roots, placement):
    matches = [candidate for candidate in candidates
               if candidate.final_roots == canonical_roots
               and candidate.placement == placement]
    if len(matches) != 1:
        raise ValueError(
            f"expected one fixed {placement} candidate for Roots {canonical_roots}"
        )
    return matches[0]


def _jsonable(value):
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _atomic_text(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, sort_keys=True)
                if isinstance(value, (dict, list, tuple)) else value
                for key, value in row.items()
            })
    temporary.replace(path)


def _save(output_dir: Path, payload: dict, rejected: list[dict]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_text(
        output_dir / "results.json",
        json.dumps(_jsonable(payload), indent=2, sort_keys=True) + "\n",
    )
    group_rows = [{key: value for key, value in group.items()
                   if key not in ("oracle_candidate_ids",)}
                  for group in payload["groups"]]
    candidate_rows = [{key: value for key, value in row.items() if key != "summary"}
                      for row in payload["candidates"]]
    _write_csv(output_dir / "results.csv", group_rows)
    _write_csv(output_dir / "candidates.csv", candidate_rows)
    _atomic_text(
        output_dir / "rejected.json",
        json.dumps(_jsonable(rejected), indent=2, sort_keys=True) + "\n",
    )


def _evaluate_group(group: dict, config: dict, evaluator) -> tuple[dict, list, list]:
    catalogue = generate_candidates(
        group["m"],
        group["k"],
        grid=group["grid"],
        root_sets=group["root_sets"],
        seeds=tuple(config["seeds"]),
        host_tile=config["host_tile"],
        tid=config["tid"],
    )
    if not catalogue.candidates:
        raise ValueError(f"group {group['group_id']} has no legal candidates")

    ranking = rank_candidates(catalogue.candidates)
    mapper = ranking[0].candidate
    compact = _find_baseline(
        catalogue.candidates, group["canonical_roots"], "compact",
    )
    spread = _find_baseline(
        catalogue.candidates, group["canonical_roots"], "spread",
    )
    rank_by_id = {
        entry.candidate.candidate_id: (index + 1, entry.estimate)
        for index, entry in enumerate(ranking)
    }
    selection_digest = _digest({
        "group_id": group["group_id"],
        "mapper_candidate_id": mapper.candidate_id,
        "ranking": [entry.candidate.candidate_id for entry in ranking],
    })

    matrix, vector = generate_workload(group["m"], group["k"])
    timing = RootTiming.from_dict(config["root_profiles"][group["profile"]])
    candidate_rows = []
    for candidate in catalogue.candidates:
        rank, estimate = rank_by_id[candidate.candidate_id]
        result = evaluator(candidate.plan, matrix, vector, root_timing=timing)
        candidate_rows.append({
            "group_id": group["group_id"],
            "candidate_id": candidate.candidate_id,
            "mapper_rank": rank,
            "selected_by_mapper": candidate.candidate_id == mapper.candidate_id,
            "is_fixed_compact": candidate.candidate_id == compact.candidate_id,
            "is_fixed_spread": candidate.candidate_id == spread.candidate_id,
            "m": group["m"],
            "k": group["k"],
            "worker_count": group["worker_count"],
            "split_family": group["split_family"],
            "grid": list(group["grid"]),
            "root_count": group["root_count"],
            "profile": group["profile"],
            "placement": candidate.placement,
            "seed": candidate.seed,
            "seed_sources": list(candidate.seed_sources),
            "final_roots": list(candidate.final_roots),
            "workers": list(candidate.workers),
            "cycles": result.cycles,
            "golden_ok": True,
            "flit_hops": result.network["flit_hops"],
            "busiest_link_utilization": result.network["busiest_link_utilization"],
            "output_blocked_port_cycles": result.network["output_blocked_port_cycles"],
            "root_load_imbalance": result.root_metrics["load_imbalance"],
            "root_finish_cycle_span": result.root_metrics["finish_cycle_span"],
            "task_switch_cycles": result.queue_metrics["task_switch_cycles"],
            "root_done_packets": result.done_count,
            "global_transaction_done": result.global_done_count,
            "estimated_busiest_link_load": estimate.estimated_busiest_link_load,
            "estimated_max_root_load": estimate.estimated_max_root_load,
            "estimated_total_flit_hops": estimate.estimated_total_flit_hops,
            "estimated_completion_hops": estimate.estimated_completion_hops,
            "estimated_task_switch_count": estimate.task_switch_count,
            "estimate_score": list(estimate.score),
            "summary": result.summary(),
        })

    metrics = summarize_group(
        candidate_rows,
        mapper_id=mapper.candidate_id,
        compact_id=compact.candidate_id,
        spread_id=spread.candidate_id,
    )
    group_row = {
        "group_id": group["group_id"],
        "m": group["m"],
        "k": group["k"],
        "worker_count": group["worker_count"],
        "split_family": group["split_family"],
        "grid": list(group["grid"]),
        "root_count": group["root_count"],
        "profile": group["profile"],
        "candidate_count": len(candidate_rows),
        "mapper_candidate_id": mapper.candidate_id,
        "fixed_compact_candidate_id": compact.candidate_id,
        "fixed_spread_candidate_id": spread.candidate_id,
        "selection_sha256": selection_digest,
        "selection_frozen_before_evaluation": True,
        **metrics,
    }
    rejected = [
        {"group_id": group["group_id"], **record}
        for record in catalogue.rejected
    ]
    return group_row, candidate_rows, rejected


def _evaluate_group_process(arguments):
    group, config = arguments
    return _evaluate_group(group, config, run_grid)


def run_experiment(config_path, output_dir, max_groups=None, *, evaluator=run_grid,
                   jobs=1):
    config_path = Path(config_path)
    output_dir = Path(output_dir)
    if not _is_int(jobs) or jobs < 1:
        raise ValueError("jobs must be a positive integer")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    groups = build_groups(config)
    if max_groups is not None:
        if not _is_int(max_groups) or max_groups < 1:
            raise ValueError("max_groups must be a positive integer")
        groups = groups[:max_groups]

    if jobs > 1 and evaluator is not run_grid:
        raise ValueError("parallel jobs require the real run_grid evaluator")
    if jobs == 1:
        evaluated = [_evaluate_group(group, config, evaluator) for group in groups]
        execution = {"jobs": 1, "mode": "sequential"}
    else:
        with ProcessPoolExecutor(max_workers=jobs) as executor:
            evaluated = list(executor.map(
                _evaluate_group_process,
                ((group, config) for group in groups),
            ))
        execution = {"jobs": jobs, "mode": "process-pool"}

    group_rows = [result[0] for result in evaluated]
    all_candidates = [row for result in evaluated for row in result[1]]
    all_rejected = [row for result in evaluated for row in result[2]]

    payload = {
        "schema_version": 1,
        "contract": CONTRACT,
        "scope": (
            "finite 8x8 central multi-Root placement catalogue; static Mapper "
            "selection precedes exact Python-cycle evaluation; no tree/RTL/scaling claim"
        ),
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "group_count": len(group_rows),
        "candidate_count": len(all_candidates),
        "rejected_count": len(all_rejected),
        "execution": execution,
        "provenance": _provenance(config_path),
        "aggregate": _aggregate(group_rows),
        "groups": group_rows,
        "candidates": all_candidates,
    }
    _save(output_dir, payload, all_rejected)
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path,
        default=ROOT / "ini/scalable/multi_root_placement_search.json",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-groups", type=int)
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        payload = run_experiment(
            args.config, args.output_dir, args.max_groups, jobs=args.jobs,
        )
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps({
        "groups": payload["group_count"],
        "candidates": payload["candidate_count"],
        "oracle_hit_rate": payload["aggregate"]["oracle_hit_rate"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
