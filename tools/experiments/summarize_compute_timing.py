#!/usr/bin/env python3
"""Summarize paired shapes without counting service phases as extra workloads."""
import argparse
from collections import defaultdict
import hashlib
import gzip
import json
from pathlib import Path


def summarize(payload):
    if payload["options"]["placement"] != "default":
        raise ValueError("this summary expects one fixed placement per shape")
    if payload["summary"]["timeout"] or payload["summary"]["validation_failure"]:
        raise ValueError("resolve incomplete/failed runs before comparing winners")
    groups = defaultdict(lambda: defaultdict(dict))
    for row in payload["results"]:
        groups[row["profile"]][(row["m"], row["k"])].setdefault(row["phase_offset"], {})[
            row["partition"]] = row
    def cycles(row):
        metrics = row["metrics"]
        return metrics.get("modeled_schedule_cycles", metrics.get("network_schedule_cycles"))
    summaries = []
    for profile, shapes in groups.items():
        counts = {"paired_shapes": 0, "forced_choice_shapes": 0, "excluded_shapes": 0,
                  "row_wins_all_phases": 0, "k_wins_all_phases": 0,
                  "ties_all_phases": 0, "phase_sensitive_shapes": 0}
        max_row = max_k = 1.0
        for shape, phases in shapes.items():
            legal = [{name: r for name, r in pair.items() if r["status"] == "ok"}
                     for pair in phases.values()]
            if all(len(pair) == 1 for pair in legal):
                counts["forced_choice_shapes"] += 1
                continue
            if any(len(pair) != 2 for pair in legal):
                counts["excluded_shapes"] += 1
                continue
            counts["paired_shapes"] += 1
            winners = set()
            for pair in legal:
                row, k = cycles(pair["ROW"]), cycles(pair["K"])
                winners.add("ROW" if row < k else "K" if k < row else "tie")
                max_row = max(max_row, k / row)
                max_k = max(max_k, row / k)
            field = ({"ROW": "row_wins_all_phases", "K": "k_wins_all_phases",
                      "tie": "ties_all_phases"}[next(iter(winners))]
                     if len(winners) == 1 else "phase_sensitive_shapes")
            counts[field] += 1
        summaries.append({"profile": profile, **counts,
                          "largest_row_speedup": max_row, "largest_k_speedup": max_k})
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = {"denominator": "shapes where ROW and K are both legal; each shape counted once per profile; wins must hold for every enumerated phase",
              "models": {}, "input_sha256": {}}
    for model in ("functional", "reference", "weight-ii2"):
        path = args.directory / model / "results.json"
        raw = path.read_bytes() if path.exists() else gzip.decompress(path.with_suffix('.json.gz').read_bytes())
        payload = json.loads(raw)
        output["input_sha256"][model] = hashlib.sha256(raw).hexdigest()
        output["models"][model] = {"summary": payload["summary"], "profiles": summarize(payload)}
    print(json.dumps(output, indent=2))
    if args.output:
        args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
