"""Candidate search and finite-catalogue Oracle for multi-Root placement."""

import json
from pathlib import Path

import pytest

from compiler.multi_root_placement import (
    estimate_candidate,
    generate_candidates,
    rank_candidates,
    select_candidate,
)
from tools.experiments.multi_root_placement_search import (
    build_groups,
    main,
    run_experiment,
    summarize_group,
    validate_config,
)


ROOT = Path(__file__).resolve().parents[2]


def smoke_config():
    timing = {
        "rx_queue_packets": 2,
        "reduction_entries": 256,
        "done_queue_packets": 1,
        "lanes_per_cycle": 4,
        "read_cycles": 1,
        "add_cycles": 1,
        "update_cycles": 1,
        "write_cycles": 1,
    }
    return {
        "schema_version": 1,
        "contract": "multi-root-placement-search-v0.1",
        "mesh_x": 8,
        "mesh_y": 8,
        "host_tile": 0,
        "tid": 1,
        "reduction": "central",
        "seeds": [0, 1],
        "root_sets": {
            "1": [[0]],
            "2": [[0, 63], [0, 7]],
        },
        "canonical_root_sets": {
            "1": [0],
            "2": [0, 63],
        },
        "workloads": [{
            "m": 16,
            "k": 16,
            "workers": 4,
            "grids": {
                "row": [4, 1],
                "k": [1, 4],
                "2d": [2, 2],
            },
        }],
        "root_profiles": {"baseline": timing},
    }


def test_catalogue_contains_fixed_baselines_and_each_random_seed():
    catalogue = generate_candidates(
        16,
        16,
        grid=(4, 1),
        root_sets=((0, 63), (0, 7)),
        seeds=(0, 1, 2),
    )

    labels = {(candidate.final_roots, candidate.placement)
              for candidate in catalogue.candidates}
    assert ((0, 63), "compact") in labels
    assert ((0, 63), "spread") in labels
    assert ((0, 7), "compact") in labels
    assert ((0, 7), "spread") in labels
    assert {candidate.seed for candidate in catalogue.candidates
            if candidate.placement == "random"} == {0, 1, 2}
    assert all(len(candidate.workers) == 4 for candidate in catalogue.candidates)


def test_catalogue_deduplicates_physical_plans_and_merges_seed_sources():
    catalogue = generate_candidates(
        8,
        8,
        grid=(2, 2),
        root_sets=((0,),),
        seeds=(0, 0),
    )

    keys = [(candidate.final_roots, candidate.workers)
            for candidate in catalogue.candidates]
    assert len(keys) == len(set(keys))
    random_candidates = [candidate for candidate in catalogue.candidates
                         if candidate.placement == "random"]
    assert len(random_candidates) == 1
    assert random_candidates[0].seed_sources == (0,)


def test_illegal_root_set_is_recorded_instead_of_silently_dropped():
    catalogue = generate_candidates(
        8,
        8,
        grid=(2, 2),
        root_sets=((0, 0),),
        seeds=(0,),
    )

    assert catalogue.candidates == ()
    assert len(catalogue.rejected) == 3
    assert {record["placement"] for record in catalogue.rejected} == {
        "compact", "spread", "random",
    }
    assert all(record["final_roots"] == [0, 0]
               for record in catalogue.rejected)
    assert all(record["reason"] for record in catalogue.rejected)


def test_candidate_ids_and_order_are_reproducible():
    options = dict(
        m=32,
        k=32,
        grid=(4, 2),
        root_sets=((0, 63), (0, 7)),
        seeds=(0, 1, 2),
    )

    first = generate_candidates(**options)
    second = generate_candidates(**options)

    assert [candidate.candidate_id for candidate in first.candidates] == [
        candidate.candidate_id for candidate in second.candidates
    ]
    assert [candidate.workers for candidate in first.candidates] == [
        candidate.workers for candidate in second.candidates
    ]


def test_static_estimate_counts_hand_checked_packet_paths():
    candidate = generate_candidates(
        4,
        4,
        grid=(1, 1),
        root_sets=((0,),),
        seeds=(),
    ).candidates[0]

    assert candidate.workers == (1,)
    estimate = estimate_candidate(candidate)

    assert estimate.estimated_busiest_link_load == 3
    assert estimate.estimated_max_root_load == 1
    assert estimate.estimated_total_flit_hops == 5
    assert estimate.estimated_completion_hops == 0
    assert estimate.task_switch_count == 0
    assert dict(estimate.link_flits) == {"0->1": 2, "1->0": 3}


def test_k_estimate_counts_each_worker_subtask_switch_and_root_packets():
    candidate = generate_candidates(
        8,
        8,
        grid=(1, 2),
        root_sets=((0, 63),),
        seeds=(),
    ).candidates[0]

    estimate = estimate_candidate(candidate)

    assert estimate.task_switch_count == 2
    assert estimate.estimated_max_root_load == 2


def test_ranking_is_sorted_by_static_score_and_digest_tie_break():
    candidates = generate_candidates(
        32,
        32,
        grid=(4, 2),
        root_sets=((0, 63), (0, 7)),
        seeds=(0, 1),
    ).candidates

    ranking = rank_candidates(candidates)

    assert [entry.score for entry in ranking] == sorted(
        entry.score for entry in ranking
    )
    assert select_candidate(candidates) == ranking[0].candidate


def test_selection_does_not_depend_on_input_candidate_order():
    candidates = generate_candidates(
        32,
        32,
        grid=(4, 2),
        root_sets=((0, 63), (0, 7)),
        seeds=(0, 1, 2),
    ).candidates

    assert select_candidate(candidates).candidate_id == select_candidate(
        tuple(reversed(candidates))
    ).candidate_id


def test_group_building_keeps_common_shape_and_worker_budget():
    groups = build_groups(smoke_config())

    assert len(groups) == 6
    assert {(group["m"], group["k"], group["worker_count"])
            for group in groups} == {(16, 16, 4)}
    assert {group["split_family"] for group in groups} == {"row", "k", "2d"}
    assert {group["root_count"] for group in groups} == {1, 2}
    assert all(group["grid"][0] * group["grid"][1] == 4
               for group in groups)


def test_summary_uses_tied_minimum_as_oracle_and_correct_ratio_direction():
    summary = summarize_group(
        candidate_rows=[
            {"candidate_id": "mapper", "cycles": 100},
            {"candidate_id": "tie", "cycles": 100},
            {"candidate_id": "compact", "cycles": 120},
            {"candidate_id": "spread", "cycles": 110},
        ],
        mapper_id="mapper",
        compact_id="compact",
        spread_id="spread",
    )

    assert summary["oracle_cycles"] == 100
    assert summary["mapper_cycles"] == 100
    assert summary["oracle_hit"] is True
    assert summary["regret"] == 0
    assert summary["speedup_vs_fixed_compact"] == 1.2
    assert summary["speedup_vs_fixed_spread"] == 1.1


def test_config_rejects_duplicate_seeds_and_unequal_split_worker_counts():
    duplicate_seed = smoke_config()
    duplicate_seed["seeds"] = [0, 0]
    with pytest.raises(ValueError, match="seeds"):
        validate_config(duplicate_seed)

    unequal_workers = smoke_config()
    unequal_workers["workloads"][0]["grids"]["2d"] = [2, 3]
    with pytest.raises(ValueError, match="Worker"):
        validate_config(unequal_workers)


def test_config_rejects_root_without_host_and_missing_fixed_baseline():
    misplaced_root = smoke_config()
    misplaced_root["root_sets"]["2"][0] = [1, 63]
    with pytest.raises(ValueError, match="Tile 0"):
        validate_config(misplaced_root)

    missing_baseline = smoke_config()
    missing_baseline["canonical_root_sets"]["2"] = [0, 56]
    with pytest.raises(ValueError, match="canonical"):
        validate_config(missing_baseline)


def test_summary_rejects_missing_fixed_candidate():
    with pytest.raises(ValueError, match="absent candidate"):
        summarize_group(
            [{"candidate_id": "mapper", "cycles": 10}],
            mapper_id="mapper",
            compact_id="missing",
            spread_id="missing-too",
        )


def test_declared_full_config_is_valid_and_builds_72_fair_groups():
    config_path = ROOT / "ini/scalable/multi_root_placement_search.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    validate_config(config)
    groups = build_groups(config)

    assert len(groups) == 72
    assert {group["root_count"] for group in groups} == {1, 2, 4}
    assert {group["split_family"] for group in groups} == {"row", "k", "2d"}
    assert all(group["grid"][0] * group["grid"][1] == group["worker_count"]
               for group in groups)


def test_real_smoke_experiment_freezes_selection_and_writes_all_outputs(tmp_path):
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "out"
    config_path.write_text(json.dumps(smoke_config()), encoding="utf-8")

    payload = run_experiment(config_path, output_dir, max_groups=1)

    assert payload["group_count"] == 1
    assert payload["candidate_count"] >= 4
    group = payload["groups"][0]
    assert group["selection_frozen_before_evaluation"] is True
    assert group["mapper_candidate_id"]
    assert group["oracle_cycles"] <= group["mapper_cycles"]
    assert group["regret"] >= 0
    assert all(row["golden_ok"] for row in payload["candidates"])
    assert {path.name for path in output_dir.iterdir()} == {
        "results.json", "results.csv", "candidates.csv", "rejected.json",
    }


def test_experiment_rejects_nonpositive_parallel_job_count(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(smoke_config()), encoding="utf-8")

    with pytest.raises(ValueError, match="jobs"):
        run_experiment(config_path, tmp_path / "out", max_groups=1, jobs=0)


def test_parallel_execution_preserves_group_metrics_and_order(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(smoke_config()), encoding="utf-8")

    sequential = run_experiment(
        config_path, tmp_path / "sequential", max_groups=2, jobs=1,
    )
    parallel = run_experiment(
        config_path, tmp_path / "parallel", max_groups=2, jobs=2,
    )

    keys = (
        "group_id", "mapper_candidate_id", "oracle_cycles", "mapper_cycles",
        "oracle_hit", "regret", "speedup_vs_fixed_compact",
        "speedup_vs_fixed_spread",
    )
    assert [{key: group[key] for key in keys} for group in sequential["groups"]] == [
        {key: group[key] for key in keys} for group in parallel["groups"]
    ]
    assert sequential["execution"] == {"jobs": 1, "mode": "sequential"}
    assert parallel["execution"] == {"jobs": 2, "mode": "process-pool"}


def test_cli_accepts_parallel_job_count(tmp_path):
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "out"
    config_path.write_text(json.dumps(smoke_config()), encoding="utf-8")

    assert main([
        "--config", str(config_path),
        "--output-dir", str(output_dir),
        "--max-groups", "1",
        "--jobs", "2",
    ]) == 0
    result = json.loads((output_dir / "results.json").read_text(encoding="utf-8"))
    assert result["execution"] == {"jobs": 2, "mode": "process-pool"}
