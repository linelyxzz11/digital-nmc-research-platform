#!/usr/bin/env python3
"""Controlled Root RESULT-service experiments; NOT full GEMV latency.

LOAD/ACK and global DONE are never throttled. Periodic service is measured
relative to TASK_START, and phase offsets expose alignment sensitivity.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT / "src", ROOT):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from compiler.auto_mapper_v0_1 import MappingError, map_gemv
from hardware import DEFAULT_HARDWARE
from simulator.system.demo1 import FinalGather, plan_from_mapping, run_plan
from simulator.system.demo2 import PSumReduce
from simulator.system.compute_timing import ComputeTiming
from tools.experiments.mapper_sweep import enumerate_cases, generate_workload, parse_shapes, _golden


@dataclass(frozen=True)
class ServiceProfile:
    name: str
    hold: int = 0
    period: int = 1
    ready: int = 1

    def __post_init__(self):
        if any(type(v) is not int for v in (self.hold, self.period, self.ready)):
            raise ValueError("service parameters must be integers")
        if self.hold < 0 or not 1 <= self.ready <= self.period:
            raise ValueError("require hold >= 0 and 1 <= ready <= period")


PROFILES = {
    "open": ServiceProfile("open"),
    "half": ServiceProfile("half", period=2),
    "quarter": ServiceProfile("quarter", period=4),
    "eighth": ServiceProfile("eighth", period=8),
    "burst64": ServiceProfile("burst64", hold=64),
}
DEFAULT_SHAPES = "16x128,32x64,64x32,128x16,33x5,128x64"


class RootServiceGate:
    def __init__(self, root, profile, offset=0):
        if type(offset) is not int or offset < 0:
            raise ValueError("phase offset must be a nonnegative integer")
        self.root = root
        self.profile = profile
        self.offset = offset % profile.period
        self.start = None
        self.closed_cycles = 0
        self.endpoint = None

    def __call__(self, cycle, tile, phase):
        if tile != self.root or phase == "LOAD":
            return True
        # A Host that also serves as Root must not have its DONE throttled.
        if self.endpoint is not None and self.endpoint.done is not None:
            return True
        if self.start is None:
            self.start = cycle
        age = cycle - self.start
        ready = (age >= self.profile.hold and
                 (age - self.profile.hold + self.offset) % self.profile.period < self.profile.ready)
        self.closed_cycles += int(not ready)
        return ready


def measure(matrix, vector, case, profile, offset=0, max_cycles=10000, compute_timing=None):
    record = {"m": len(matrix), "k": len(vector), "partition": case.strategy.upper(),
              "workers": list(case.workers), "root": case.root_tile, "k_split": case.k_split,
              "profile": profile.name, "phase_offset": offset % profile.period}
    try:
        mapping = map_gemv(matrix, vector, strategy=case.strategy, workers=case.workers,
                           root_tile=case.root_tile, k_split=case.k_split)
    except MappingError as error:
        return {**record, "status": "infeasible", "reason": str(error)}
    plan = plan_from_mapping(mapping)
    gate = RootServiceGate(plan.root_tile, profile, offset)
    endpoint_class = FinalGather if plan.partition == "ROW" else PSumReduce

    def endpoint_factory(config, tile):
        gate.endpoint = endpoint_class(config, tile)
        return gate.endpoint

    try:
        run = run_plan(plan, endpoint_factory=endpoint_factory, sink_ready=gate,
                       max_cycles=max_cycles, compute_timing=compute_timing)
    except TimeoutError as error:
        return {**record, "status": "timeout", "reason": str(error)}
    trace, mesh = run.trace, run.mesh
    start = min(e["network_cycle"] for e in trace if e["event"] == "TASK_START")
    gathered = next(e["network_cycle"] for e in trace if e["event"] == "TRANSACTION_DONE_CREATE")
    host_done = next(e["network_cycle"] for e in trace if e["event"] == "HOST_TRANSACTION_DONE")
    expected_acks = {(p.dst_tile, p.tid, p.sequence) for p in plan.input_writes}
    good = (run.y == _golden(matrix, vector) and len(run.host_done) == 1
            and run.ack_keys == expected_acks and mesh.max_occupancy <= mesh.fifo_depth)
    metrics = {
        "network_schedule_cycles": mesh.cycle,
        "input_load_cycles": start,
        "result_gather_cycles": gathered - start,
        "completion_drain_cycles": mesh.cycle - gathered,
        "host_done_cycle": host_done,
        "flit_hops": mesh.flit_hops,
        "fifo_full_input_cycles": mesh.fifo_full_cycles,
        "router_output_blocked_cycles": mesh.output_blocked_cycles,
        "ni_inject_stall_tile_cycles": sum(mesh.inject_stall_cycles),
        "resultbeat_stall_tile_cycles": sum(e["event"] == "RESULTBEAT_STALL" for e in trace),
        "max_fifo_occupancy": mesh.max_occupancy,
        "root_scheduled_closed_cycles": gate.closed_cycles,
        "result_packets": sum(e["event"] == "RESULTBEAT_ACCEPT" for e in trace),
        "host_done_count": len(run.host_done),
        "input_acks": len(run.ack_keys),
        "max_link_utilization": max(mesh.link_flit_count.values(), default=0) / mesh.cycle,
        # 2x2 mesh: eight directed inter-router links, including unused links.
        "mean_link_utilization": mesh.flit_hops / (8 * mesh.cycle),
    }
    if compute_timing is not None:
        metrics["modeled_schedule_cycles"] = metrics.pop("network_schedule_cycles")
        metrics["compute_and_gather_cycles"] = metrics.pop("result_gather_cycles")
    return {**record, "status": "ok" if good else "validation_failure",
            "golden_ok": run.y == _golden(matrix, vector), "metrics": metrics,
            "compute_stats": run.compute_stats,
            "links": [{"src": src, "dst": dst, "flits": count,
                       "utilization": count / mesh.cycle}
                      for (src, dst), count in sorted(mesh.link_flit_count.items())],
            "ni_inject_stall_by_tile": mesh.inject_stall_cycles}


def mapping_key(row):
    return row["partition"], tuple(row["workers"]), row["root"], row["k_split"]


def compare(rows):
    """Keep all baseline ties; report strict ROW/K reversals at fixed placement."""
    groups = {}
    for row in rows:
        if row["status"] == "ok":
            groups.setdefault((row["m"], row["k"], row["profile"], row["phase_offset"]), {})[
                mapping_key(row)] = row
    regrets, reversals = [], []
    for (m, k, profile, offset), candidates in sorted(groups.items()):
        if profile == "open":
            continue
        baseline = groups.get((m, k, "open", 0), {})
        common = baseline.keys() & candidates.keys()
        if not common:
            continue
        cost = lambda r: r["metrics"].get("modeled_schedule_cycles", r["metrics"].get("network_schedule_cycles"))
        best_before = min(cost(baseline[key]) for key in common)
        old_winners = [key for key in common if cost(baseline[key]) == best_before]
        best_after = min(cost(candidates[key]) for key in common)
        regrets.append({"m": m, "k": k, "profile": profile, "phase_offset": offset,
                        "baseline_tied_winners": len(old_winners),
                        "pressure_best_cycles": best_after,
                        "best_old_winner_regret_cycles": min(cost(candidates[key]) for key in old_winners) - best_after,
                        "worst_old_winner_regret_cycles": max(cost(candidates[key]) for key in old_winners) - best_after,
                        "common_candidates": len(common),
                        "unpaired_candidates": len(baseline.keys() ^ candidates.keys())})
        for key in sorted(common, key=str):
            if key[0] != "ROW":
                continue
            other = ("K", *key[1:])
            if other not in common:
                continue
            before = cost(baseline[key]) - cost(baseline[other])
            after = cost(candidates[key]) - cost(candidates[other])
            if before * after < 0:
                reversals.append({"m": m, "k": k, "workers": list(key[1]), "root": key[2],
                                  "profile": profile, "phase_offset": offset,
                                  "open_row_cycles": cost(baseline[key]),
                                  "open_k_cycles": cost(baseline[other]),
                                  "pressure_row_cycles": cost(candidates[key]),
                                  "pressure_k_cycles": cost(candidates[other])})
    return {"placement_regret": regrets, "strict_row_k_reversals": reversals}


def run_experiment(shapes, profiles=("open", "half", "quarter", "eighth", "burst64"),
                   offsets=(0,), placement="all", max_cycles=10000, compute_timing=None):
    if len(set(profiles)) != len(profiles) or "open" not in profiles:
        raise ValueError("profiles must be unique and include open for paired comparisons")
    if any(name not in PROFILES for name in profiles):
        raise ValueError("unknown service profile")
    if not offsets or any(type(v) is not int or v < 0 for v in offsets):
        raise ValueError("offsets must be nonnegative integers")
    rows = []
    for m, k in shapes:
        matrix, vector = generate_workload(m, k)
        for case in enumerate_cases(m, k, placement=placement):
            for name in profiles:
                profile = PROFILES[name]
                for offset in sorted({value % profile.period for value in offsets}):
                    rows.append(measure(matrix, vector, case, profile, offset, max_cycles, compute_timing))
    return {
        "schema_version": 2,
        "scope": (("reference Compute/SRAM timing + " if compute_timing else "functional Compute + ")
                  + "cycle-stepped NoC with synthetic Root RESULT ejection service; functional reduction; not RTL-calibrated full GEMV latency"),
        "hardware": DEFAULT_HARDWARE.describe(),
        "options": {"shapes": [list(s) for s in shapes], "placement": placement,
                    "profiles": [asdict(PROFILES[name]) for name in profiles],
                    "phase_offsets": list(offsets), "max_cycles": max_cycles,
                    "compute_timing": asdict(compute_timing) if compute_timing is not None else None},
        "summary": {"cases": len(rows), **{status: sum(r["status"] == status for r in rows)
                    for status in ("ok", "infeasible", "timeout", "validation_failure")}},
        "comparison": compare(rows), "results": rows,
    }


def provenance():
    sources = [ROOT / "src/hardware.py", Path(__file__),
               ROOT / "tools/experiments/mapper_sweep.py"]
    for folder in ("src/compiler", "src/simulator"):
        sources.extend((ROOT / folder).rglob("*.py"))
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(set(sources))}
    def git(*args):
        result = subprocess.run(["git", "-C", str(ROOT), *args], text=True, capture_output=True)
        return result.stdout.strip() if result.returncode == 0 else None
    return {"git_head": git("rev-parse", "HEAD"), "git_status": git("status", "--short"),
            "source_sha256": hashes}


def save_results(directory, payload):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    flat = [{**{k: v for k, v in row.items() if k not in ("metrics", "links", "ni_inject_stall_by_tile", "compute_stats")},
             **row.get("metrics", {}), "workers": ",".join(map(str, row["workers"]))}
            for row in payload["results"]]
    fields = list(dict.fromkeys(key for row in flat for key in row))
    with (directory / "results.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(flat)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shapes", default=DEFAULT_SHAPES)
    parser.add_argument("--profiles", default=",".join(PROFILES))
    parser.add_argument("--phase-offsets", default="0")
    parser.add_argument("--placements", choices=("default", "all"), default="all")
    parser.add_argument("--max-cycles", type=int, default=10000)
    parser.add_argument("--compute-model", choices=("functional", "reference", "weight-ii2"), default="functional")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.max_cycles <= 0:
            raise ValueError("max-cycles must be positive")
        timing = (None if args.compute_model == "functional" else
                  ComputeTiming(weight_read_interval=2 if args.compute_model == "weight-ii2" else 1))
        payload = run_experiment(parse_shapes(args.shapes), tuple(args.profiles.split(",")),
                                 tuple(int(v) for v in args.phase_offsets.split(",")),
                                 args.placements, args.max_cycles, timing)
    except ValueError as error:
        parser.error(str(error))
    payload["provenance"] = provenance()
    print(json.dumps(payload["summary"], indent=2))
    print("Strict ROW/K ranking reversals:", len(payload["comparison"]["strict_row_k_reversals"]))
    print("Conditions where every open-service winner loses:", sum(
        r["best_old_winner_regret_cycles"] > 0 for r in payload["comparison"]["placement_regret"]))
    if args.output_dir:
        save_results(args.output_dir, payload)
        print("Results:", args.output_dir)
    return int(bool(payload["summary"]["timeout"] or payload["summary"]["validation_failure"]))


if __name__ == "__main__":
    raise SystemExit(main())
