#!/usr/bin/env python3
"""Compare central and tree reduction with frozen workers and hardware timing."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sys


ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT, ROOT / "src"):
    sys.path.insert(0, str(directory))

from compiler.grid_mapper import map_grid
from simulator.functional import int32_wrap
from simulator.system.root_config import RootTiming
from simulator.system.scalable import run_grid
from simulator.system.tile_config import TileComputeConfig
from tools.experiments.mapper_sweep import generate_workload


CONTRACT = "reduction-comparison-v0.1"
SPLITS = ("row", "k", "2d")
REDUCTIONS = ("central", "tree")


def _positive_int(value):
    return type(value) is int and value > 0


def validate_config(payload):
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("reduction comparison needs schema_version 1")
    if payload.get("contract") != CONTRACT:
        raise ValueError(f"contract must be {CONTRACT}")
    if (payload.get("mesh_x"), payload.get("mesh_y")) != (8, 8):
        raise ValueError("comparison requires the frozen 8x8 Mesh")
    if payload.get("host_tile") != 0 or payload.get("root_tile") != 0:
        raise ValueError("Host and Root must share Tile 0")
    if payload.get("placement") != "compact":
        raise ValueError("comparison freezes compact placement")
    if not _positive_int(payload.get("fan_in")) or payload["fan_in"] != 4:
        raise ValueError("comparison freezes tree fan-in at four")
    if not _positive_int(payload.get("tid")) or payload["tid"] > 65535:
        raise ValueError("tid must be a positive 16-bit integer")
    for key in ("tile_config", "root_config"):
        value = payload.get(key)
        if not isinstance(value, str) or not (ROOT / value).is_file():
            raise ValueError(f"{key} must name an existing repository-relative file")
    workloads = payload.get("workloads")
    if not isinstance(workloads, list) or not workloads:
        raise ValueError("workloads must be a nonempty list")
    seen = set()
    for workload in workloads:
        if not isinstance(workload, dict) or set(workload) != {"m", "k", "workers", "grids"}:
            raise ValueError("each workload needs only M/K/workers/grids")
        m, k, workers = (workload[name] for name in ("m", "k", "workers"))
        if any(not _positive_int(value) for value in (m, k, workers)):
            raise ValueError("M, K, and workers must be positive integers")
        identity = (m, k, workers)
        if identity in seen:
            raise ValueError("duplicate workload")
        seen.add(identity)
        grids = workload["grids"]
        if not isinstance(grids, dict) or set(grids) != set(SPLITS):
            raise ValueError("each workload needs ROW/K/2D grids")
        normalized = {}
        for split, grid in grids.items():
            if (not isinstance(grid, list) or len(grid) != 2
                    or any(not _positive_int(axis) for axis in grid)):
                raise ValueError("each grid needs two positive integer axes")
            normalized[split] = tuple(grid)
            if grid[0] * grid[1] != workers:
                raise ValueError("every grid product must equal the worker count")
        if normalized["row"][1] != 1 or normalized["k"][0] != 1:
            raise ValueError("ROW and K grids disagree with their split names")
        if min(normalized["2d"]) < 2:
            raise ValueError("2D needs at least two partitions on both axes")


def _record(split, reduction, matrix, vector, workload, payload, tile_timing, root_timing):
    m, k, workers = (workload[name] for name in ("m", "k", "workers"))
    plan = map_grid(
        m,
        k,
        mesh_x=payload["mesh_x"],
        mesh_y=payload["mesh_y"],
        grid=tuple(workload["grids"][split]),
        root_tile=payload["root_tile"],
        final_roots=(payload["root_tile"],),
        host_tile=payload["host_tile"],
        reduction=reduction,
        fan_in=payload["fan_in"],
        placement=payload["placement"],
        tid=payload["tid"],
    )
    run = run_grid(plan, matrix, vector, tile_timing=tile_timing, root_timing=root_timing)
    golden = [
        int32_wrap(sum(weight * value for weight, value in zip(row, vector)))
        for row in matrix
    ]
    useful_mac_ops = sum(stats["useful_mac_ops"] for stats in run.compute.values())
    kernel_cycles = run.phases["compute_and_gather"]
    final_root_idle = run.endpoints[payload["root_tile"]]["stage_cycles"]["IDLE"]
    root_waiting_cycles = max(
        0,
        final_root_idle - run.phases["input_load"] - run.phases["completion_drain"],
    )
    reducer_tiles = [node.tile_id for node in plan.reducers]
    active_tiles = set(plan.workers) | set(reducer_tiles) | {
        plan.host_tile,
        plan.root_tile,
    }
    record = {
        "m": m,
        "k": k,
        "workers": workers,
        "split": split,
        "reduction": reduction,
        "fan_in": plan.fan_in,
        "golden_ok": run.y == golden,
        "total_cycles": run.cycles,
        "input_cycles": run.phases["input_load"],
        "kernel_cycles": kernel_cycles,
        "completion_cycles": run.phases["completion_drain"],
        "output_span_cycles": run.root_metrics["output_span_cycles"],
        "result_write_cycles": run.root_metrics["result_write_cycles"],
        "useful_mac_ops": useful_mac_ops,
        "total_mac_lanes": 4 * workers,
        "lane_utilization": useful_mac_ops / (4 * workers * kernel_cycles),
        "flit_hops": run.network["flit_hops"],
        "busiest_link_utilization": run.network["busiest_link_utilization"],
        "fifo_full_input_cycles": run.network["fifo_full_input_cycles"],
        "output_blocked_port_cycles": run.network["output_blocked_port_cycles"],
        "root_waiting_cycles": root_waiting_cycles,
        "reducer_tiles": reducer_tiles,
        "reducer_count": len(reducer_tiles),
        "active_tile_count": len(active_tiles),
        "physical_tile_budget": plan.tile_count,
        "workers_physical": list(plan.workers),
        "grid": list(plan.grid),
        "plan_sha256": plan.digest,
    }
    if useful_mac_ops != m * k:
        raise AssertionError("mapped GEMV performed the wrong number of useful MACs")
    if run.cycles != sum(run.phases.values()):
        raise AssertionError("phase cycles do not sum to total cycles")
    return record


def run_experiment(payload):
    validate_config(payload)
    tile_timing = TileComputeConfig.load(ROOT / payload["tile_config"])
    root_timing = RootTiming.load(ROOT / payload["root_config"])
    records = []
    for workload in payload["workloads"]:
        matrix, vector = generate_workload(workload["m"], workload["k"])
        for split in SPLITS:
            pair = [
                _record(
                    split,
                    reduction,
                    matrix,
                    vector,
                    workload,
                    payload,
                    tile_timing,
                    root_timing,
                )
                for reduction in REDUCTIONS
            ]
            central_cycles = pair[0]["total_cycles"]
            for record in pair:
                record["speedup_tree_vs_central"] = (
                    1.0 if record["reduction"] == "central"
                    else central_cycles / record["total_cycles"]
                )
            records.extend(pair)
    return {
        "schema_version": 1,
        "contract": CONTRACT,
        "config": payload,
        "records": records,
    }


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _summary(records):
    tree = [record for record in records if record["reduction"] == "tree"]
    by_split = {}
    for split in SPLITS:
        selected = [record for record in tree if record["split"] == split]
        speeds = [record["speedup_tree_vs_central"] for record in selected]
        by_split[split] = {
            "cases": len(selected),
            "geomean_tree_speedup_vs_central": math.exp(
                sum(math.log(value) for value in speeds) / len(speeds)
            ),
            "tree_wins": sum(value > 1 for value in speeds),
            "ties": sum(value == 1 for value in speeds),
            "tree_losses": sum(value < 1 for value in speeds),
        }
    return {
        "records": len(records),
        "correctness_failures": sum(not record["golden_ok"] for record in records),
        "splits": by_split,
    }


def _write_csv(path, records):
    preferred = [
        "m", "k", "workers", "split", "reduction", "fan_in",
        "golden_ok", "total_cycles", "input_cycles", "kernel_cycles",
        "completion_cycles", "speedup_tree_vs_central", "reducer_count",
        "reducer_tiles", "active_tile_count", "physical_tile_budget",
        "flit_hops", "busiest_link_utilization", "root_waiting_cycles",
        "output_span_cycles", "result_write_cycles", "lane_utilization",
        "fifo_full_input_cycles", "output_blocked_port_cycles",
        "useful_mac_ops", "total_mac_lanes", "grid", "workers_physical",
        "plan_sha256",
    ]
    fields = preferred + sorted(
        set().union(*(record.keys() for record in records)) - set(preferred)
    )
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def _write_figures(directory, records):
    os.environ.setdefault("MPLCONFIGDIR", str(Path(directory) / ".mplconfig"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    # Shared grey/blue palette sampled from the team's reference figure.
    palette = {
        "light_grey": "#D9D9D9",
        "muted_blue": "#7B9AAD",
        "blue": "#0072B2",
        "edge": "#444444",
    }

    figure_dir = Path(directory) / "figure"
    figure_dir.mkdir(parents=True, exist_ok=True)
    groups = list(dict.fromkeys(
        (record["m"], record["k"], record["workers"], record["split"])
        for record in records
    ))
    lookup = {
        (
            record["m"], record["k"], record["workers"],
            record["split"], record["reduction"],
        ): record
        for record in records
    }
    labels = [f"{m}×{k}\nW={workers} {split.upper()}" for m, k, workers, split in groups]
    x_values = list(range(len(groups)))

    fig, axis = plt.subplots(figsize=(12.0, 5.0))
    width = 0.36
    for index, reduction in enumerate(REDUCTIONS):
        values = [lookup[(*group, reduction)]["total_cycles"] for group in groups]
        offset = (index - 0.5) * width
        axis.bar(
            [value + offset for value in x_values], values, width, label=reduction,
            color=palette["muted_blue" if reduction == "central" else "blue"],
            edgecolor=palette["edge"], linewidth=0.7,
            hatch=None if reduction == "central" else "///",
        )
    axis.set_xticks(x_values, labels, rotation=35, ha="right")
    axis.set_ylabel("Total cycles")
    axis.set_title("Central reduction and tree reduction end-to-end cycles")
    axis.legend(frameon=False)
    axis.set_axisbelow(True)
    axis.grid(axis="y", color="#EEEEEE", linewidth=0.8)
    axis.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    for suffix in ("png", "svg"):
        fig.savefig(figure_dir / f"total_cycles.{suffix}", dpi=220)
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(12.0, 5.0))
    speedups = [lookup[(*group, "tree")]["speedup_tree_vs_central"] for group in groups]
    split_colors = {
        "row": palette["light_grey"],
        "k": palette["blue"],
        "2d": palette["muted_blue"],
    }
    colors = [split_colors[group[3]] for group in groups]
    axis.bar(x_values, speedups, color=colors, edgecolor=palette["edge"], linewidth=0.7)
    axis.axhline(1.0, color=palette["edge"], linewidth=1, linestyle="--")
    axis.set_xticks(x_values, labels, rotation=35, ha="right")
    axis.set_ylabel("Tree speedup vs central")
    axis.set_title("Tree reduction speedup (>1 is faster)")
    axis.legend(handles=[Patch(facecolor=split_colors[split], edgecolor=palette["edge"],
                              label=split.upper()) for split in SPLITS],
                frameon=False, ncol=3, loc="upper left")
    axis.set_axisbelow(True)
    axis.grid(axis="y", color="#EEEEEE", linewidth=0.8)
    axis.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    for suffix in ("png", "svg"):
        fig.savefig(figure_dir / f"tree_speedup.{suffix}", dpi=220)
    plt.close(fig)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    payload = json.loads(args.config.read_text(encoding="utf-8"))
    validate_config(payload)
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise ValueError("output directory must be absent or empty")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result = run_experiment(payload)
    sources = [
        payload["tile_config"],
        payload["root_config"],
        "src/compiler/grid_mapper.py",
        "src/simulator/system/scalable.py",
        "tools/experiments/reduction_comparison.py",
    ]
    result["provenance"] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "config_sha256": _sha256(args.config),
        "source_sha256": {path: _sha256(ROOT / path) for path in sources},
    }
    summary = _summary(result["records"])
    (args.output_dir / "results.json").write_text(
        json.dumps(result, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _write_csv(args.output_dir / "results.csv", result["records"])
    _write_figures(args.output_dir, result["records"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
