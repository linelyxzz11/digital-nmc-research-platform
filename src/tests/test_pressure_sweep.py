"""Controlled service, conservation, phase alignment and comparison regressions."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.experiments.mapper_sweep import generate_workload, run_sweep, SweepCase
from tools.experiments.pressure_sweep import (
    PROFILES, RootServiceGate, ServiceProfile, compare, main, measure, run_experiment,
)


def test_service_is_relative_to_compute_and_bypasses_load_other_tiles_and_done():
    gate = RootServiceGate(0, PROFILES["quarter"], offset=1)
    assert gate(100, 0, "LOAD")
    assert gate.start is None
    assert gate(101, 1, "COMPUTE_AND_GATHER")
    assert [gate(t, 0, "COMPUTE_AND_GATHER") for t in range(102, 106)] == [False, False, False, True]
    gate.endpoint = SimpleNamespace(done=object())
    assert gate(106, 0, "COMPUTE_AND_GATHER")
    assert gate.closed_cycles == 3


def test_initial_burst_releases_after_exact_number_of_compute_cycles():
    gate = RootServiceGate(3, ServiceProfile("hold3", hold=3))
    assert [gate(t, 3, "COMPUTE_AND_GATHER") for t in range(50, 55)] == [False] * 3 + [True] * 2


@pytest.mark.parametrize("partition", ["row", "k"])
@pytest.mark.parametrize("root", [0, 3])
def test_open_service_matches_existing_runner(partition, root):
    W, x = generate_workload(33, 5)
    case = SweepCase(partition, (1, 2), root, None)
    row = measure(W, x, case, PROFILES["open"])
    baseline = run_sweep(W, x, partitions=(partition,), placement="all")["results"]
    expected = next(r for r in baseline if r["workers"] == [1, 2] and r["root_tile"] == root)
    assert row["status"] == "ok"
    for key in ("network_schedule_cycles", "flit_hops"):
        assert row["metrics"][key] == expected["metrics"][key]
    metrics = row["metrics"]
    assert metrics["network_schedule_cycles"] == sum(metrics[k] for k in (
        "input_load_cycles", "result_gather_cycles", "completion_drain_cycles"))


@pytest.mark.parametrize("root", [0, 3])
def test_backpressure_changes_waiting_not_data_or_load(root):
    W, x = generate_workload(65, 5)
    case = SweepCase("k", (1, 2), root, None)
    free = measure(W, x, case, PROFILES["open"])
    blocked = measure(W, x, case, PROFILES["burst64"])
    a, b = free["metrics"], blocked["metrics"]
    assert blocked["status"] == "ok" and blocked["golden_ok"]
    for key in ("input_load_cycles", "flit_hops", "result_packets", "input_acks"):
        assert a[key] == b[key]
    assert b["host_done_count"] == 1
    assert b["fifo_full_input_cycles"] > 0
    assert b["ni_inject_stall_tile_cycles"] > 0
    assert b["resultbeat_stall_tile_cycles"] > 0
    assert b["max_fifo_occupancy"] <= 4
    assert b["network_schedule_cycles"] > a["network_schedule_cycles"]


def test_offsets_are_deduplicated_and_runs_are_deterministic():
    args = ([(9, 5)], ("open", "half", "quarter"), (0, 1, 2, 3))
    result = run_experiment(*args, placement="default")
    assert result["summary"]["cases"] == 2 * (1 + 2 + 4)
    assert result["summary"]["ok"] == 14
    assert result == run_experiment(*args, placement="default")


def test_timeouts_and_infeasible_cases_are_reported():
    result = run_experiment([(1, 5)], ("open",), placement="default", max_cycles=1)
    assert result["summary"]["infeasible"] == 1
    assert result["summary"]["timeout"] == 1


def test_comparison_preserves_ties_and_detects_strict_reversals():
    rows = []
    for profile, costs in (("open", (10, 20)), ("half", (30, 25))):
        for partition, cost in zip(("ROW", "K"), costs):
            rows.append(dict(m=4, k=4, profile=profile, phase_offset=0, status="ok",
                             partition=partition, workers=[1, 2], root=3, k_split=None,
                             metrics={"network_schedule_cycles": cost}))
    result = compare(rows)
    assert len(result["strict_row_k_reversals"]) == 1
    assert result["placement_regret"][0]["best_old_winner_regret_cycles"] == 5
    rows[1]["metrics"]["network_schedule_cycles"] = 10
    result = compare(rows)
    assert not result["strict_row_k_reversals"]
    assert result["placement_regret"][0]["baseline_tied_winners"] == 2
    assert result["placement_regret"][0]["best_old_winner_regret_cycles"] == 0


def test_cli_exports_results_and_source_provenance(tmp_path):
    assert main(["--shapes", "4x4", "--profiles", "open,quarter", "--placements", "default",
                 "--output-dir", str(tmp_path)]) == 0
    result = json.loads((tmp_path / "results.json").read_text())
    assert result["summary"]["ok"] == 4
    assert "src/hardware.py" in result["provenance"]["source_sha256"]
    assert "fifo_full_input_cycles" in (tmp_path / "results.csv").read_text()


def test_invalid_service_and_missing_reference_are_rejected():
    with pytest.raises(ValueError):
        ServiceProfile("bad", period=0)
    with pytest.raises(ValueError):
        run_experiment([(4, 4)], ("quarter",))
