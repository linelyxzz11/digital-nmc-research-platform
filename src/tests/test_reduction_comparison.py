"""Fair central-reduction versus tree-reduction experiment."""

import json

import pytest

from tools.experiments.reduction_comparison import (
    main,
    run_experiment,
    validate_config,
)


def smoke_config():
    return {
        "schema_version": 1,
        "contract": "reduction-comparison-v0.1",
        "mesh_x": 8,
        "mesh_y": 8,
        "host_tile": 0,
        "root_tile": 0,
        "tid": 1,
        "placement": "compact",
        "fan_in": 4,
        "tile_config": "ini/tile_system/baseline.json",
        "root_config": "ini/root_service/baseline.json",
        "workloads": [{
            "m": 16,
            "k": 16,
            "workers": 4,
            "grids": {"row": [4, 1], "k": [1, 4], "2d": [2, 2]},
        }],
    }


def test_real_smoke_run_keeps_workers_equal_and_counts_tree_reducers():
    result = run_experiment(smoke_config())
    records = {
        (record["split"], record["reduction"]): record
        for record in result["records"]
    }

    assert len(records) == 6
    assert all(record["golden_ok"] for record in records.values())
    for split in ("row", "k", "2d"):
        central = records[(split, "central")]
        tree = records[(split, "tree")]
        assert central["workers_physical"] == tree["workers_physical"]
        assert central["total_mac_lanes"] == tree["total_mac_lanes"] == 16
        assert central["reducer_tiles"] == []
        assert central["speedup_tree_vs_central"] == 1.0
        assert tree["speedup_tree_vs_central"] == pytest.approx(
            central["total_cycles"] / tree["total_cycles"]
        )

    assert records[("row", "tree")]["reducer_tiles"] == []
    assert records[("row", "tree")]["total_cycles"] == records[("row", "central")]["total_cycles"]
    assert len(records[("k", "tree")]["reducer_tiles"]) == 1
    assert len(records[("2d", "tree")]["reducer_tiles"]) == 2


def test_config_rejects_a_non_single_root_contract():
    payload = smoke_config()
    payload["root_tile"] = 63

    with pytest.raises(ValueError, match="Host and Root"):
        validate_config(payload)


def test_cli_exports_auditable_tables_and_figures(tmp_path):
    config_path = tmp_path / "smoke.json"
    config_path.write_text(json.dumps(smoke_config()), encoding="utf-8")
    output = tmp_path / "out"

    assert main(["--config", str(config_path), "--output-dir", str(output)]) == 0
    assert {path.name for path in output.iterdir()} >= {
        "results.json",
        "results.csv",
        "summary.json",
        "figure",
    }
    assert (output / "figure" / "total_cycles.png").is_file()
    assert (output / "figure" / "tree_speedup.png").is_file()


@pytest.mark.parametrize("grid,reduction,roots", [
    ((4, 1), "central", (0,)),
    ((1, 4), "central", (0,)),
    ((1, 4), "tree", (0,)),
    ((2, 2), "tree", (0,)),
    ((1, 4), "central", (0, 63)),
    ((1, 4), "central", (0, 7, 56, 63)),
])
def test_write_metrics_observe_only_final_root_commits(monkeypatch, grid, reduction, roots):
    from compiler.grid_mapper import map_grid
    from simulator.system.scalable import ReductionEndpoint, run_grid
    from tools.experiments.mapper_sweep import generate_workload

    observed = []
    original_step = ReductionEndpoint.step

    def observe_step(endpoint, mesh):
        event = original_step(endpoint, mesh)
        indices = endpoint.root.last_event.get("written_indices", [])
        if indices:
            observed.append((endpoint.root.config.root_tile, mesh.cycle + 1, tuple(indices)))
        return event

    monkeypatch.setattr(ReductionEndpoint, "step", observe_step)
    plan = map_grid(16, 16, grid=grid, reduction=reduction, final_roots=roots)
    matrix, vector = generate_workload(16, 16)
    result = run_grid(plan, matrix, vector)
    final_writes = [item for item in observed if item[0] in roots]
    cycles = {cycle for _, cycle, _ in final_writes}
    indices = [index for _, _, values in final_writes for index in values]
    assert sorted(indices) == list(range(16))
    if plan.reducers:
        assert any(tile not in roots for tile, _, _ in observed)
    metrics = result.root_metrics
    assert metrics["first_result_write_cycle"] == min(cycles)
    assert metrics["last_result_write_cycle"] == max(cycles)
    assert metrics["output_span_cycles"] == max(cycles) - min(cycles) + 1
    # Concurrent writes on different final Roots count as one global clock cycle.
    assert metrics["result_write_cycles"] == len(cycles)
    assert max(cycles) <= max(result.final_root_finish_cycles.values()) <= result.cycles
    assert result.done_count == len(roots) and result.global_done_count == 1


def test_cli_refuses_to_overwrite_previous_results(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps(smoke_config()), encoding="utf-8")
    output = tmp_path / "previous_run"
    output.mkdir()
    marker = output / "results.json"
    marker.write_text("previous evidence", encoding="utf-8")
    with pytest.raises(ValueError, match="absent or empty"):
        main(["--config", str(config), "--output-dir", str(output)])
    assert marker.read_text(encoding="utf-8") == "previous evidence"
