#!/usr/bin/env python3
"""Batch-execute Mapper plans and rank them by observed NoC metrics.

Every case maps the same GEMV with an explicit ROW/K strategy, worker
placement, Root placement and K split, then executes the generated plan
through the real Demo scheduling loop. The reported cycles cover the
cycle-stepped NoC; Compute remains functional, so these numbers compare
communication schedules rather than total GEMV latency.
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
for _path in (ROOT / "src", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from compiler.auto_mapper_v0_1 import DEMO_W, DEMO_X, MappingError, map_gemv
from hardware import DEFAULT_HARDWARE
from simulator.functional import int32_wrap
from simulator.system.demo1 import (
    FinalGather, PlanError, TransactionError, load_workload, plan_from_mapping, run_plan,
)
from simulator.system.demo2 import PSumReduce


TILE_IDS = tuple(range(DEFAULT_HARDWARE.tile_count))
WORKER_TILES = tuple(tile for tile in TILE_IDS if tile != DEFAULT_HARDWARE.host_tile)
MAX_SHAPE_M = DEFAULT_HARDWARE.max_local_rows
MAX_SHAPE_K = 2 * DEFAULT_HARDWARE.max_local_k
ALL_K_SPLIT_LIMIT = 64
K_SPLIT_FRACTIONS = (1 / 8, 1 / 4, 1 / 2, 3 / 4, 7 / 8)

SHAPE_PRESETS = {
    "frontier": ((4, 2048), (8, 1024), (16, 512), (32, 256),
                 (64, 128), (128, 64), (256, 32)),
    "half": ((4, 1024), (8, 512), (16, 256), (32, 128),
             (64, 64), (128, 32), (256, 16)),
    "extremes": ((1, 2048), (2, 2048), (1, 1024), (256, 1), (256, 8)),
    "aspect": ((8, 256), (256, 8), (16, 128), (128, 16), (32, 64), (64, 32)),
}
SHAPE_PRESETS["suite"] = tuple(dict.fromkeys(
    shape for name in ("frontier", "half", "extremes", "aspect")
    for shape in SHAPE_PRESETS[name]))


def parse_shapes(text: str) -> list[tuple[int, int]]:
    tokens = [token.strip().lower() for token in text.split(",") if token.strip()]
    if not tokens:
        raise ValueError("shapes must name at least one shape")
    shapes: list[tuple[int, int]] = []
    for token in tokens:
        if token in SHAPE_PRESETS:
            shapes.extend(SHAPE_PRESETS[token])
            continue
        dimensions = token.split("x")
        if len(dimensions) != 2:
            raise ValueError(f"shape {token!r} must be MxK or one of {', '.join(SHAPE_PRESETS)}")
        try:
            m, k = int(dimensions[0]), int(dimensions[1])
        except ValueError:
            raise ValueError(f"shape {token!r} must use integer MxK") from None
        if not 1 <= m <= MAX_SHAPE_M or not 1 <= k <= MAX_SHAPE_K:
            raise ValueError(f"shape {token!r} is outside the v0.1 planner limits")
        shapes.append((m, k))
    return list(dict.fromkeys(shapes))


def generate_workload(m: int, k: int) -> tuple[list[list[int]], list[int]]:
    if not 1 <= m <= MAX_SHAPE_M or not 1 <= k <= MAX_SHAPE_K:
        raise ValueError("shape is outside the v0.1 planner limits")
    matrix = [[((row * 7 + column * 3) % 17) - 8 for column in range(k)]
              for row in range(m)]
    vector = [(column % 11) - 5 for column in range(k)]
    return matrix, vector


@dataclass(frozen=True)
class SweepCase:
    strategy: str
    workers: tuple[int, int]
    root_tile: int
    k_split: int | None


def placements(kind: str) -> list[tuple[tuple[int, int], int]]:
    if kind == "default":
        return [((1, 2), 3)]
    if kind != "all":
        raise ValueError("placements must be 'default' or 'all'")
    pairs = [(a, b) for a in WORKER_TILES for b in WORKER_TILES if a != b]
    return [((a, b), root) for a, b in pairs for root in TILE_IDS if root not in (a, b)]


def _sampled_splits(k: int) -> list[int]:
    values = {max(1, min(k - 1, round(k * fraction))) for fraction in K_SPLIT_FRACTIONS}
    return sorted(values)


def _k_split_values(spec: str | tuple[int, ...], k: int) -> list[int | None]:
    if spec == "default":
        return [None]
    if spec == "all":
        if k < 2:
            return [None]
        return list(range(1, k)) if k <= ALL_K_SPLIT_LIMIT else _sampled_splits(k)
    if spec == "sample":
        return _sampled_splits(k) if k >= 2 else [None]
    if k < 2:
        raise ValueError("K partitioning requires K >= 2")
    for value in spec:
        if not 1 <= value < k:
            raise ValueError(f"k_split {value} must be in [1, {k - 1}]")
    return list(spec)


def enumerate_cases(
    m: int, k: int, *, partitions: tuple[str, ...] = ("row", "k"),
    placement: str = "default", k_splits: str | tuple[int, ...] = "default",
) -> list[SweepCase]:
    cases: list[SweepCase] = []
    for strategy in partitions:
        if strategy not in ("row", "k"):
            raise ValueError("partitions must contain only 'row' and 'k'")
        for workers, root in placements(placement):
            if strategy == "row":
                cases.append(SweepCase("row", workers, root, None))
            else:
                for split in _k_split_values(k_splits, k):
                    cases.append(SweepCase("k", workers, root, split))
    return cases


def _golden(matrix: list[list[int]], vector: list[int]) -> list[int]:
    return [int32_wrap(sum(a * b for a, b in zip(row, vector))) for row in matrix]


def run_sweep(
    matrix: list[list[int]], vector: list[int], *,
    partitions: tuple[str, ...] = ("row", "k"),
    placement: str = "default",
    k_splits: str | tuple[int, ...] = "default",
    tid: int = 1, global_row_base: int = 0, max_cycles: int = 10000,
) -> dict:
    if not matrix or not vector or any(len(row) != len(vector) for row in matrix):
        raise ValueError("workload must be a nonempty rectangular MxK matrix and vector")
    m, k = len(matrix), len(vector)
    golden = _golden(matrix, vector)
    results = []
    for case in enumerate_cases(m, k, partitions=partitions, placement=placement,
                                k_splits=k_splits):
        record = {"m": m, "k": k, "strategy": case.strategy.upper(),
                  "workers": list(case.workers), "root_tile": case.root_tile,
                  "k_split": case.k_split}
        try:
            mapping = map_gemv(matrix, vector, strategy=case.strategy,
                               workers=case.workers, root_tile=case.root_tile,
                               tid=tid, global_row_base=global_row_base,
                               k_split=case.k_split)
        except MappingError as error:
            results.append({**record, "status": "infeasible", "reason": str(error)})
            continue
        try:
            plan = plan_from_mapping(mapping)
            endpoint = FinalGather if plan.partition == "ROW" else PSumReduce
            run = run_plan(plan, endpoint_factory=endpoint, max_cycles=max_cycles)
        except (PlanError, TransactionError, TimeoutError, RuntimeError) as error:
            results.append({**record, "status": "error",
                            "reason": f"{type(error).__name__}: {error}"})
            continue
        summary = run.summary()
        diagnostics = mapping["diagnostics"]
        results.append({
            **record,
            "status": "ok",
            "partition": plan.partition,
            "golden_ok": run.y == golden,
            "metrics": {
                "network_schedule_cycles": summary["network_schedule_cycles"],
                "flit_hops": summary["flit_hops"],
                "max_router_fifo_occupancy": summary["max_router_fifo_occupancy"],
                "input_acks": summary["input_acks"],
                "result_packet_count": summary["result_packet_count"],
                "psum_elements": summary.get("psum_elements", 0),
                "compute_cycles": diagnostics["estimated_compute_cycles"],
                "estimated_packets": diagnostics["estimated_packets"],
                "cost_score": diagnostics["cost_score"],
            },
            "plan": {
                "input_write_count": diagnostics["input_write_count"],
                "total_weight_bytes": diagnostics["total_weight_bytes"],
                "total_input_bytes": diagnostics["total_input_bytes"],
                "useful_mac_count": diagnostics["useful_mac_count"],
            },
        })
    return {
        "workload": {"m": m, "k": k},
        "options": {
            "partitions": list(partitions), "placements": placement,
            "k_splits": k_splits if isinstance(k_splits, str) else list(k_splits),
            "tid": tid, "global_row_base": global_row_base,
            "timing_scope": "cycle-stepped NoC schedule; functional Compute; "
                            "not total GEMV latency",
        },
        "results": results,
    }


def ranked(results: list[dict]) -> list[dict]:
    completed = [result for result in results if result["status"] == "ok"]
    return sorted(completed, key=lambda result: (
        not result["golden_ok"],
        result["metrics"]["network_schedule_cycles"],
        result["metrics"]["flit_hops"],
        result["metrics"]["max_router_fifo_occupancy"],
        result["strategy"], result["workers"],
        -1 if result["k_split"] is None else result["k_split"]))


def summarize(results: list[dict]) -> dict[str, int]:
    return {
        "cases": len(results),
        "ok": sum(result["status"] == "ok" for result in results),
        "infeasible": sum(result["status"] == "infeasible" for result in results),
        "error": sum(result["status"] == "error" for result in results),
        "golden_failures": sum(result["status"] == "ok" and not result["golden_ok"]
                               for result in results),
    }


def calibration(payloads: list[dict]) -> dict[str, int]:
    """Check whether the first-order cost_score picks a best-observed candidate."""
    checked = agreed = 0
    for payload in payloads:
        completed = [result for result in payload["results"] if result["status"] == "ok"]
        if not completed:
            continue
        checked += 1
        best_observed = ranked(completed)[0]
        best_cost = min(completed, key=lambda result: (
            not result["golden_ok"], result["metrics"]["cost_score"]))
        if (best_cost["metrics"]["network_schedule_cycles"]
                == best_observed["metrics"]["network_schedule_cycles"]):
            agreed += 1
    return {"shapes_checked": checked, "agreements": agreed,
            "mismatches": checked - agreed}


def format_table(results: list[dict], top: int = 20) -> str:
    lines = ["rank  partition  split  workers  root  cycles  hops  fifo  "
             "res_pkts  compute  cost  golden"]
    for index, result in enumerate(ranked(results)[:top], start=1):
        metrics = result["metrics"]
        split = "-" if result["k_split"] is None else str(result["k_split"])
        lines.append(
            f"{index:>4}  {result['partition']:<9}  {split:>5}  "
            f"{str(tuple(result['workers'])):<9}  {result['root_tile']:>4}  "
            f"{metrics['network_schedule_cycles']:>6}  {metrics['flit_hops']:>4}  "
            f"{metrics['max_router_fifo_occupancy']:>4}  "
            f"{metrics['result_packet_count']:>8}  {metrics['compute_cycles']:>7}  "
            f"{metrics['cost_score']:>4}  "
            f"{'PASS' if result['golden_ok'] else 'FAIL'}")
    return "\n".join(lines)


def write_json(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


CSV_FIELDS = ["m", "k", "strategy", "partition", "workers", "root_tile", "k_split",
              "status", "golden_ok", "network_schedule_cycles", "flit_hops",
              "max_router_fifo_occupancy", "input_acks", "result_packet_count",
              "psum_elements", "compute_cycles", "estimated_packets", "cost_score",
              "reason"]


def write_csv(path: Path, results: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for result in results:
            metrics = result.get("metrics", {})
            writer.writerow({
                "m": result["m"],
                "k": result["k"],
                "strategy": result["strategy"],
                "partition": result.get("partition", ""),
                "workers": "-".join(str(tile) for tile in result["workers"]),
                "root_tile": result["root_tile"],
                "k_split": "" if result["k_split"] is None else result["k_split"],
                "status": result["status"],
                "golden_ok": result.get("golden_ok", ""),
                "network_schedule_cycles": metrics.get("network_schedule_cycles", ""),
                "flit_hops": metrics.get("flit_hops", ""),
                "max_router_fifo_occupancy": metrics.get("max_router_fifo_occupancy", ""),
                "input_acks": metrics.get("input_acks", ""),
                "result_packet_count": metrics.get("result_packet_count", ""),
                "psum_elements": metrics.get("psum_elements", ""),
                "compute_cycles": metrics.get("compute_cycles", ""),
                "estimated_packets": metrics.get("estimated_packets", ""),
                "cost_score": metrics.get("cost_score", ""),
                "reason": result.get("reason", ""),
            })


def _parse_k_splits(text: str) -> str | tuple[int, ...]:
    if text in ("default", "all", "sample"):
        return text
    try:
        values = tuple(int(part) for part in text.split(",") if part.strip())
    except ValueError:
        raise ValueError("k-splits must be 'default', 'sample', 'all', "
                         "or comma-separated integers") from None
    if not values:
        raise ValueError("k-splits must name at least one position")
    return values


def format_shape_summary(payloads: list[dict]) -> str:
    lines = ["shape     cases  ok  infeasible  error  golden_failures  best"]
    for payload in payloads:
        counts = summarize(payload["results"])
        m, k = payload["workload"]["m"], payload["workload"]["k"]
        best = ranked(payload["results"])[:1]
        if best:
            row = best[0]
            metrics = row["metrics"]
            split = "-" if row["k_split"] is None else row["k_split"]
            detail = (f"{row['partition']} split={split} workers={tuple(row['workers'])} "
                      f"root={row['root_tile']} cycles={metrics['network_schedule_cycles']} "
                      f"hops={metrics['flit_hops']} cost={metrics['cost_score']} "
                      f"golden={'PASS' if row['golden_ok'] else 'FAIL'}")
        else:
            detail = "no completed candidate"
        lines.append(f"{f'{m}x{k}':<9} {counts['cases']:>5} {counts['ok']:>3} "
                     f"{counts['infeasible']:>10} {counts['error']:>6} "
                     f"{counts['golden_failures']:>15}  {detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true", help="use the built-in 4x4 Demo GEMV")
    source.add_argument("--workload", type=Path, help='JSON object containing "W" and "x"')
    source.add_argument("--shapes",
                        help="MxK list or preset: suite, frontier, half, extremes, aspect")
    parser.add_argument("--partitions", default="row,k", help="comma-separated row,k subset")
    parser.add_argument("--placements", choices=("default", "all"), default="default")
    parser.add_argument("--k-splits", default="default",
                        help="'default', 'sample', 'all' (exhaustive only up to K=64), "
                             "or comma-separated split positions")
    parser.add_argument("--tid", type=int, default=1)
    parser.add_argument("--global-row-base", type=int, default=0)
    parser.add_argument("--max-cycles", type=int, default=10000)
    parser.add_argument("--output", type=Path, help="write the full run set as JSON")
    parser.add_argument("--csv", type=Path, help="write the run set as CSV")
    parser.add_argument("--top", type=int, default=None,
                        help="rows to print (default: 20 for one workload, "
                             "best-row summary for --shapes)")
    args = parser.parse_args(argv)

    try:
        partitions = tuple(part.strip().lower() for part in args.partitions.split(",")
                           if part.strip())
        if not partitions:
            raise ValueError("partitions must name at least one strategy")
        options = dict(partitions=partitions, placement=args.placements,
                       k_splits=_parse_k_splits(args.k_splits), tid=args.tid,
                       global_row_base=args.global_row_base, max_cycles=args.max_cycles)
        if args.shapes:
            payloads = [run_sweep(*generate_workload(m, k), **options)
                        for m, k in parse_shapes(args.shapes)]
        else:
            matrix, vector = (DEMO_W, DEMO_X) if args.demo else load_workload(args.workload)
            payloads = [run_sweep(matrix, vector, **options)]
    except (ValueError, PlanError, OSError) as error:
        print(f"mapper_sweep: {error}", file=sys.stderr)
        return 2

    totals = {key: sum(summarize(payload["results"])[key] for payload in payloads)
              for key in ("cases", "ok", "infeasible", "error", "golden_failures")}
    if len(payloads) == 1:
        workload = payloads[0]["workload"]
        print(f"Workload M={workload['m']} K={workload['k']}; cases={totals['cases']} "
              f"ok={totals['ok']} infeasible={totals['infeasible']} "
              f"error={totals['error']} golden_failures={totals['golden_failures']}")
        print(format_table(payloads[0]["results"], args.top or 20))
    else:
        print(f"Shapes={len(payloads)}; cases={totals['cases']} ok={totals['ok']} "
              f"infeasible={totals['infeasible']} error={totals['error']} "
              f"golden_failures={totals['golden_failures']}")
        print(format_shape_summary(payloads))
        if args.top:
            for payload in payloads:
                print(f"\nShape {payload['workload']['m']}x{payload['workload']['k']}")
                print(format_table(payload["results"], args.top))
    calibration_result = calibration(payloads)
    print(f"cost_score calibration: {calibration_result['agreements']}/"
          f"{calibration_result['shapes_checked']} shapes match the best observed cycles")
    if args.output:
        if len(payloads) == 1:
            write_json(args.output, {**payloads[0], "calibration": calibration_result})
        else:
            write_json(args.output, {
                "shapes": [{"m": payload["workload"]["m"], "k": payload["workload"]["k"]}
                           for payload in payloads],
                "options": payloads[0]["options"],
                "calibration": calibration_result,
                "workloads": payloads,
            })
        print(f"wrote {args.output}")
    if args.csv:
        write_csv(args.csv, [result for payload in payloads
                             for result in payload["results"]])
        print(f"wrote {args.csv}")
    return 1 if totals["error"] or totals["golden_failures"] else 0



if __name__ == "__main__":
    raise SystemExit(main())
