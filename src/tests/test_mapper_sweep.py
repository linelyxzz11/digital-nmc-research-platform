"""Verify the batch plan sweep: enumeration, execution, ranking and outputs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from compiler.auto_mapper_v0_1 import DEMO_W, DEMO_X

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.experiments.mapper_sweep import (
    SHAPE_PRESETS, calibration, enumerate_cases, generate_workload, parse_shapes, ranked,
    run_sweep, summarize, write_csv,
)


def _workload(m, k):
    W = [[(row * 5 + column * 3) % 19 - 9 for column in range(k)] for row in range(m)]
    x = [column % 7 - 3 for column in range(k)]
    return W, x


def test_enumerate_default_cases():
    cases = enumerate_cases(4, 4)
    assert [(case.strategy, case.workers, case.root_tile, case.k_split)
            for case in cases] == [("row", (1, 2), 3, None), ("k", (1, 2), 3, None)]


def test_enumerate_all_placements():
    cases = enumerate_cases(4, 4, partitions=("row",), placement="all")
    assert len(cases) == 12
    assert len({(case.workers, case.root_tile) for case in cases}) == 12
    assert all(case.root_tile not in case.workers for case in cases)


def test_enumerate_k_split_sweep():
    cases = enumerate_cases(4, 5, partitions=("k",), k_splits="all")
    assert [case.k_split for case in cases] == [1, 2, 3, 4]
    cases = enumerate_cases(4, 5, partitions=("k",), k_splits=(1, 4))
    assert [case.k_split for case in cases] == [1, 4]


def test_all_k_splits_are_sampled_above_the_limit():
    assert [case.k_split for case in enumerate_cases(4, 32, partitions=("k",), k_splits="all")] \
        == list(range(1, 32))
    sampled = [case.k_split for case in enumerate_cases(4, 128, partitions=("k",), k_splits="all")]
    assert sampled == [16, 32, 64, 96, 112]
    assert [case.k_split for case in enumerate_cases(4, 128, partitions=("k",),
                                                     k_splits="sample")] == sampled


def test_shape_presets_and_parser():
    assert len(parse_shapes("frontier")) == 7
    assert len(parse_shapes("half")) == 7
    assert len(parse_shapes("extremes")) == 5
    assert len(parse_shapes("aspect")) == 6
    assert len(parse_shapes("suite")) == 24
    assert parse_shapes("16x8, 8x16") == [(16, 8), (8, 16)]
    assert parse_shapes("frontier,frontier") == list(SHAPE_PRESETS["frontier"])
    with pytest.raises(ValueError):
        parse_shapes("300x8")
    with pytest.raises(ValueError):
        parse_shapes("16by8")


def test_generated_workloads_are_deterministic_and_bounded():
    W, x = generate_workload(16, 32)
    assert (len(W), len(x), len(W[0])) == (16, 32, 32)
    assert all(-8 <= value <= 8 for row in W for value in row)
    assert all(-5 <= value <= 5 for value in x)
    assert generate_workload(16, 32) == (W, x)


def test_sweep_executes_and_ranks_every_default_case():
    W, x = _workload(9, 5)
    payload = run_sweep(W, x)
    assert payload["workload"] == {"m": 9, "k": 5}
    assert summarize(payload["results"]) == {
        "cases": 2, "ok": 2, "infeasible": 0, "error": 0, "golden_failures": 0}
    assert all(result["status"] == "ok" and result["golden_ok"]
               for result in payload["results"])
    assert payload == run_sweep(W, x)
    ordered = ranked(payload["results"])
    assert ordered == sorted(ordered, key=lambda result: (
        result["metrics"]["network_schedule_cycles"],
        result["metrics"]["flit_hops"]))


def test_sweep_records_infeasible_partitions():
    payload = run_sweep([[1, 2, 3, 4]], [1, 2, 3, 4])
    by_strategy = {result["strategy"]: result for result in payload["results"]}
    assert by_strategy["ROW"]["status"] == "infeasible"
    assert "at least 2" in by_strategy["ROW"]["reason"]
    assert by_strategy["K"]["status"] == "ok"
    assert by_strategy["K"]["golden_ok"] is True


def test_sweep_reports_cost_estimates_and_calibration():
    payload = run_sweep(DEMO_W, DEMO_X)
    for result in payload["results"]:
        if result["status"] == "ok":
            metrics = result["metrics"]
            assert metrics["cost_score"] == (metrics["compute_cycles"]
                                            + metrics["estimated_packets"])
    assert calibration([payload]) == {"shapes_checked": 1, "agreements": 1, "mismatches": 0}


def test_csv_writer_has_one_row_per_case(tmp_path):
    payload = run_sweep(DEMO_W, DEMO_X)
    path = tmp_path / "sweep.csv"
    write_csv(path, payload["results"])
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("m,k,strategy,partition,workers,root_tile,k_split,status")
    assert len(lines) == len(payload["results"]) + 1


def test_cli_writes_json_and_csv(tmp_path):
    output = tmp_path / "sweep.json"
    csv_path = tmp_path / "sweep.csv"
    run = subprocess.run(
        [sys.executable, "-B", str(ROOT / "tools/experiments/mapper_sweep.py"),
         "--demo", "--placements", "all", "--output", str(output),
         "--csv", str(csv_path), "--top", "5"],
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        text=True, capture_output=True, check=False)
    assert run.returncode == 0, run.stderr
    assert "cases=24 ok=24 infeasible=0 error=0 golden_failures=0" in run.stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert len(payload["results"]) == 24
    assert all(result["status"] == "ok" and result["golden_ok"]
               for result in payload["results"])
    assert csv_path.exists()


def test_cli_runs_a_shape_matrix(tmp_path):
    output = tmp_path / "shapes.json"
    csv_path = tmp_path / "shapes.csv"
    run = subprocess.run(
        [sys.executable, "-B", str(ROOT / "tools/experiments/mapper_sweep.py"),
         "--shapes", "8x16,16x8", "--output", str(output), "--csv", str(csv_path)],
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        text=True, capture_output=True, check=False)
    assert run.returncode == 0, run.stderr
    assert "Shapes=2; cases=4 ok=4" in run.stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["shapes"] == [{"m": 8, "k": 16}, {"m": 16, "k": 8}]
    assert [item["workload"] for item in payload["workloads"]] == payload["shapes"]
    assert len(csv_path.read_text(encoding="utf-8").splitlines()) == 1 + 4


def test_cli_rejects_invalid_k_split():
    run = subprocess.run(
        [sys.executable, "-B", str(ROOT / "tools/experiments/mapper_sweep.py"),
         "--demo", "--k-splits", "0"],
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        text=True, capture_output=True, check=False)
    assert run.returncode == 2
    assert "k_split 0" in run.stderr
