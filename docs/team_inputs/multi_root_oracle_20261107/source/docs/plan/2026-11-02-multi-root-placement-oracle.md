# Multi-Root Placement Oracle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible multi-Root placement search that ranks Root/Worker candidates without measured-cycle leakage, evaluates a finite-catalogue Oracle, and reports hit rate, regret, and speedups versus fixed compact/spread.

**Architecture:** A focused compiler module generates and ranks placement candidates from existing `GridPlan` objects. A separate experiment driver freezes Mapper selections before exact simulation, evaluates every legal candidate with the existing `run_grid`, and produces candidate-level and group-level JSON/CSV outputs. Existing ROW/K/2D experiment entry points and execution models remain unchanged.

**Tech Stack:** Python 3.12, dataclasses, JSON/CSV, existing `compiler.grid_mapper`, existing `simulator.system.scalable`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-02-multi-root-placement-oracle-design.md`

## Global Constraints

- Run only on the local checkout; do not stage, commit, push, merge, or upload.
- Keep Host and first Root on Tile 0; search only the remaining Root positions and Worker positions.
- Use central reduction only in this phase; do not claim tree, multi-Root RTL, 8/16 Root, or Host output-gather support.
- Mapper ranking must be completed before any exact `run_grid` call and must never inspect candidate cycles.
- Oracle means the minimum exact cycle count in the declared finite candidate catalogue only.
- Preserve compatibility of `multi_root_row.py`, `multi_root_k.py`, and `multi_root_2d.py`.
- All generated numerical candidates must preserve the existing `M*K` useful-MAC invariant and golden GEMV check.

---

### Task 1: Candidate catalogue and deterministic deduplication

**Files:**
- Create: `src/compiler/multi_root_placement.py`
- Test: `src/tests/test_multi_root_placement.py`

**Interfaces:**
- Consumes: `compiler.grid_mapper.GridPlan`, `map_grid`, and `MappingError`.
- Produces: `PlacementCandidate`, `CandidateCatalogue`, and `generate_candidates` for later ranking and experiment execution.

- [ ] **Step 1: Write failing candidate-generation tests**

Add these tests:

```python
from compiler.multi_root_placement import generate_candidates


def test_catalogue_contains_fixed_baselines_and_random_seeds():
    catalogue = generate_candidates(
        128, 64, grid=(4, 4),
        root_sets=((0, 63), (0, 7)), seeds=(0, 1, 2),
        host_tile=0, tid=1,
    )
    labels = {(c.final_roots, c.placement) for c in catalogue.candidates}
    assert ((0, 63), "compact") in labels
    assert ((0, 63), "spread") in labels
    assert {c.seed for c in catalogue.candidates if c.placement == "random"} <= {0, 1, 2}
    assert all(len(c.plan.workers) == 16 for c in catalogue.candidates)


def test_catalogue_deduplicates_by_physical_plan_and_keeps_seed_sources():
    catalogue = generate_candidates(
        8, 8, grid=(2, 2), root_sets=((0,),), seeds=(0, 0),
        host_tile=0, tid=1,
    )
    keys = [(c.final_roots, c.workers) for c in catalogue.candidates]
    assert len(keys) == len(set(keys))
    assert any(candidate.seed_sources == (0,) for candidate in catalogue.candidates
               if candidate.placement == "random")


def test_illegal_root_set_is_recorded_not_silently_dropped():
    catalogue = generate_candidates(
        8, 8, grid=(2, 2), root_sets=((0, 0),), seeds=(0,),
        host_tile=0, tid=1,
    )
    assert catalogue.candidates == ()
    assert catalogue.rejected[0]["final_roots"] == [0, 0]
    assert catalogue.rejected[0]["reason"]
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```powershell
$py = '.\.venv\Scripts\python.exe'
& $py -m pytest -q -p no:cacheprovider src/tests/test_multi_root_placement.py
```

Expected: collection fails because `compiler.multi_root_placement` does not exist.

- [ ] **Step 3: Implement immutable catalogue types and generation**

Create these public types and signature:

```python
@dataclass(frozen=True)
class PlacementCandidate:
    candidate_id: str
    final_roots: tuple[int, ...]
    workers: tuple[int, ...]
    placement: str
    seed: int | None
    seed_sources: tuple[int, ...]
    plan: GridPlan


@dataclass(frozen=True)
class CandidateCatalogue:
    candidates: tuple[PlacementCandidate, ...]
    rejected: tuple[dict, ...]


generate_candidates(m: int, k: int, *, grid: tuple[int, int],
                    root_sets: tuple[tuple[int, ...], ...],
                    seeds: tuple[int, ...], host_tile: int = 0,
                    tid: int = 1) -> CandidateCatalogue
```

For each Root set, call `map_grid` once for `compact`, once for `spread`, and once per distinct random seed. Catch only `MappingError`; append a rejection record containing `final_roots`, `placement`, `seed`, and `reason`. Deduplicate on `(plan.grid, plan.final_roots, plan.workers)` and merge `seed_sources` for duplicate random plans. Compute `candidate_id` as SHA-256 over canonical JSON containing `grid`, `final_roots`, and `workers`.

- [ ] **Step 4: Run candidate tests and existing Mapper tests**

Run:

```powershell
& $py -m pytest -q -p no:cacheprovider src/tests/test_multi_root_placement.py src/tests/test_mapper_v0_1.py src/tests/test_multi_root_row.py src/tests/test_multi_root_k.py src/tests/test_multi_root_2d.py
```

Expected: all selected tests pass.

- [ ] **Step 5: Review the local diff without staging**

Run `git diff -- src/compiler/multi_root_placement.py src/tests/test_multi_root_placement.py` and confirm only catalogue behavior is present.

---

### Task 2: Static communication estimate and leakage-free Mapper selection

**Files:**
- Modify: `src/compiler/multi_root_placement.py`
- Modify: `src/tests/test_multi_root_placement.py`

**Interfaces:**
- Consumes: `PlacementCandidate` and `CandidateCatalogue` from Task 1.
- Produces: `PlacementEstimate`, `estimate_candidate`, `rank_candidates`, and `select_candidate`.

- [ ] **Step 1: Write failing estimator and ranking tests**

Add these tests:

```python
from dataclasses import replace
from compiler.multi_root_placement import (
    estimate_candidate, rank_candidates, select_candidate,
)


def test_estimate_counts_static_flits_and_xy_link_loads():
    catalogue = generate_candidates(
        16, 16, grid=(4, 1), root_sets=((0, 63),), seeds=(0,),
    )
    estimate = estimate_candidate(catalogue.candidates[0])
    assert estimate.estimated_total_flit_hops > 0
    assert estimate.estimated_busiest_link_load > 0
    assert estimate.estimated_max_root_load > 0
    assert estimate.task_switch_count == 0


def test_k_estimate_counts_one_switch_per_extra_subtask_per_worker():
    catalogue = generate_candidates(
        16, 16, grid=(1, 4), root_sets=((0, 63),), seeds=(0,),
    )
    estimate = estimate_candidate(catalogue.candidates[0])
    assert estimate.task_switch_count == 4


def test_selection_is_independent_of_measured_cycles():
    catalogue = generate_candidates(
        32, 32, grid=(4, 2), root_sets=((0, 63), (0, 7)), seeds=(0, 1),
    )
    first = select_candidate(catalogue.candidates)
    fake_cycles = {candidate.candidate_id: index * 999
                   for index, candidate in enumerate(reversed(catalogue.candidates))}
    second = select_candidate(catalogue.candidates)
    assert fake_cycles
    assert first.candidate_id == second.candidate_id


def test_ranking_uses_digest_only_as_final_tie_break():
    ranking = rank_candidates(generate_candidates(
        8, 8, grid=(2, 2), root_sets=((0,),), seeds=(0, 1),
    ).candidates)
    assert [entry.score for entry in ranking] == sorted(entry.score for entry in ranking)
```

- [ ] **Step 2: Run focused tests and confirm missing interfaces fail**

Run the Task 1 pytest command. Expected: import errors for the new estimator functions.

- [ ] **Step 3: Implement exact static packet accounting over XY paths**

Add:

```python
@dataclass(frozen=True)
class PlacementEstimate:
    estimated_busiest_link_load: int
    estimated_max_root_load: int
    estimated_total_flit_hops: int
    estimated_completion_hops: int
    task_switch_count: int
    link_flits: tuple[tuple[str, int], ...]

    @property
    def score(self) -> tuple[int, int, int, int, int]:
        return (self.estimated_busiest_link_load,
                self.estimated_max_root_load,
                self.estimated_total_flit_hops,
                self.estimated_completion_hops,
                self.task_switch_count)


@dataclass(frozen=True)
class RankedCandidate:
    candidate: PlacementCandidate
    estimate: PlacementEstimate

    @property
    def score(self) -> tuple:
        return (*self.estimate.score, self.candidate.candidate_id)
```

Use deterministic XY routing matching the simulator. Account for:

- each 4-byte input `MEM_WRITE`: two flits Host→Worker;
- each corresponding `MEM_ACK`: one flit Worker→Host;
- each four-lane result group: two flits Worker→`result_dst_tile`;
- each final Root `TRANSACTION_DONE`: one flit Root→Host;
- `task_switch_count = sum(max(0, len(queue.tasks)-1) for queue in plan.task_queues)`.

Accumulate directed link loads, total flit-hops, result packets per final Root, and completion hops. Sort using the spec tuple and candidate digest only as the final tie-break.

- [ ] **Step 4: Run estimator tests and verify deterministic ranking**

Run the focused pytest command twice. Expected: both runs pass and candidate IDs/ranking are identical.

- [ ] **Step 5: Run existing multi-Root tests**

Run:

```powershell
& $py -m pytest -q -p no:cacheprovider src/tests/test_multi_root_row.py src/tests/test_multi_root_k.py src/tests/test_multi_root_2d.py
```

Expected: all existing tests pass unchanged.

---

### Task 3: Experiment configuration, preselection freeze, Oracle, and metrics

**Files:**
- Create: `tools/experiments/multi_root_placement_search.py`
- Create: `ini/scalable/multi_root_placement_search.json`
- Modify: `src/tests/test_multi_root_placement.py`

**Interfaces:**
- Consumes: `generate_candidates`, `rank_candidates`, existing `RootTiming`, `generate_workload`, and `run_grid`.
- Produces: `validate_config`, `build_groups`, `run_experiment`, `summarize_group`, and CLI outputs `results.json`, `results.csv`, `candidates.csv`, `rejected.json`.

- [ ] **Step 1: Write failing config and summary tests**

Add these tests:

```python
from types import SimpleNamespace
from tools.experiments.multi_root_placement_search import (
    build_groups, summarize_group, run_experiment,
)


def test_group_building_uses_common_shape_and_worker_budget():
    config = smoke_config()
    groups = build_groups(config)
    assert {(g["m"], g["k"], g["worker_count"]) for g in groups} == {(16, 16, 4)}
    assert {g["split_family"] for g in groups} == {"row", "k", "2d"}
    assert {g["root_count"] for g in groups} == {1, 2}


def test_summary_formulas_and_tied_oracle_hit():
    summary = summarize_group(
        candidate_rows=[
            {"candidate_id": "mapper", "cycles": 100},
            {"candidate_id": "tie", "cycles": 100},
            {"candidate_id": "compact", "cycles": 120},
            {"candidate_id": "spread", "cycles": 110},
        ],
        mapper_id="mapper", compact_id="compact", spread_id="spread",
    )
    assert summary["oracle_cycles"] == 100
    assert summary["oracle_hit"] is True
    assert summary["regret"] == 0
    assert summary["speedup_vs_fixed_compact"] == 1.2
    assert summary["speedup_vs_fixed_spread"] == 1.1


def test_mapper_choice_is_frozen_before_exact_evaluation(monkeypatch, tmp_path):
    seen = []
    def fake_run(plan, matrix, vector, **kwargs):
        seen.append(plan.digest)
        return SimpleNamespace(
            cycles=100 + len(seen),
            network={"flit_hops": 10, "busiest_link_utilization": 0.1,
                     "output_blocked_port_cycles": 0},
            root_metrics={"load_imbalance": 1.0, "finish_cycle_span": 0},
            queue_metrics={"task_switch_cycles": 0},
            done_count=len(plan.final_roots), global_done_count=1,
            summary=lambda: {"plan_sha256": plan.digest},
        )
    payload = run_experiment(write_smoke_config(tmp_path), tmp_path / "out",
                             evaluator=fake_run, max_groups=1)
    assert payload["groups"][0]["mapper_candidate_id"]
    assert payload["groups"][0]["selection_frozen_before_evaluation"] is True
    assert seen
```

- [ ] **Step 2: Run tests and confirm missing driver fails**

Run the focused pytest command. Expected: import failure for the new experiment driver.

- [ ] **Step 3: Implement strict schema validation and group construction**

Use contract `multi-root-placement-search-v0.1`, schema version 1. Require:

- `mesh_x=8`, `mesh_y=8`, `host_tile=0`;
- seeds `[0..7]` without duplicates;
- Root templates keyed by `1`, `2`, `4`, each starting at 0;
- common workloads with explicit ROW/K/2D grids and equal worker products;
- baseline/pressure `RootTiming` dictionaries;
- `reduction="central"`.

Reject booleans where integers are required. Build one group for every workload × split family × root count × Root profile.

- [ ] **Step 4: Implement preselection, exact evaluation, and summaries**

For each group:

1. generate and rank candidates;
2. store `mapper_candidate_id` and ranking before calling the evaluator;
3. identify fixed compact/spread IDs using the canonical Root set;
4. generate the deterministic GEMV once;
5. run every candidate through `run_grid` with identical `RootTiming`;
6. store cycles plus existing NoC/Root/queue metrics;
7. compute Oracle and the exact formulas from the spec;
8. assert `regret >= 0`, golden completion, one global done, and unchanged useful MAC count via the existing evaluator assertions.

Add optional keyword-only `evaluator=run_grid` for tests and `max_groups=None` for smoke runs. Never pass measured rows back into `rank_candidates`.

- [ ] **Step 5: Implement output files and provenance**

Write atomically through temporary files followed by `Path.replace`:

- `results.json`: full configuration, provenance, group summaries, aggregate summary;
- `results.csv`: one row per comparison group;
- `candidates.csv`: one row per exact candidate execution;
- `rejected.json`: generation failures with group identity.

Aggregate arithmetic mean regret, worst regret, Oracle hit rate, geometric-mean speedups, and win/tie/loss counts. Preserve raw ratios so the report can be independently recomputed.

- [ ] **Step 6: Add the full declared configuration**

Encode the four workloads, seeds 0–7, Root templates, canonical Root sets, and baseline/pressure profiles exactly as the design spec. Do not derive the protocol from previous output files.

- [ ] **Step 7: Run driver tests and existing experiment smoke tests**

Run:

```powershell
& $py -m pytest -q -p no:cacheprovider src/tests/test_multi_root_placement.py src/tests/test_multi_root_row.py src/tests/test_multi_root_k.py src/tests/test_multi_root_2d.py
```

Expected: all tests pass.

---

### Task 4: CLI smoke execution and integrity checks

**Files:**
- Modify: `src/tests/test_multi_root_placement.py`
- Modify: `tools/experiments/multi_root_placement_search.py`

**Interfaces:**
- Consumes: Task 3 CLI and file outputs.
- Produces: a tested CLI contract and a small local smoke result.

- [ ] **Step 1: Add a CLI smoke test**

Create a temporary configuration with one 16×16 workload, ROW/K/2D grids with four Workers, 1/2 Root, one Root profile, two Root templates, and seeds 0/1. Invoke `main([...])` and assert all four output files exist and contain matching group/candidate counts.

- [ ] **Step 2: Add tamper/invariant tests**

Add tests that reject:

- duplicate seeds;
- a Root template not beginning at Tile 0;
- unequal Worker counts across ROW/K/2D grids;
- missing canonical fixed Root set;
- an empty candidate group;
- a summary where fixed compact/spread IDs are absent.

- [ ] **Step 3: Run the complete placement test file**

Run:

```powershell
& $py -m pytest -q -p no:cacheprovider src/tests/test_multi_root_placement.py
```

Expected: all tests pass.

- [ ] **Step 4: Run one real group through the CLI**

Run:

```powershell
& $py tools/experiments/multi_root_placement_search.py `
  --config ini/scalable/multi_root_placement_search.json `
  --output-dir dump/multi_root_placement_smoke_20261102 `
  --max-groups 1
```

Expected: command exits zero; JSON reports one complete group; all candidate runs have `golden_ok=true`; `regret >= 0`.

- [ ] **Step 5: Independently recompute the smoke metrics**

Read `candidates.csv`, recompute minimum cycles and ratios for the first group, and compare them to `results.csv`. Fail if any value differs beyond `1e-12`.

---

### Task 5: Regression verification, full campaign, and experiment contract

**Files:**
- Create: `docs/spec/multi_root_placement_search_v0.1.md`
- Output: `dump/multi_root_placement_search_20261102/`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: verified full results and a human-readable contract/result summary.

- [ ] **Step 1: Run focused multi-Root regression**

Run:

```powershell
& $py -m pytest -q -p no:cacheprovider `
  src/tests/test_multi_root_placement.py `
  src/tests/test_multi_root_row.py `
  src/tests/test_multi_root_k.py `
  src/tests/test_multi_root_2d.py
```

Expected: all selected tests pass.

- [ ] **Step 2: Run the broader Python regression**

Run:

```powershell
& $py -m pytest -q -p no:cacheprovider -ra
```

Expected: no failures. Existing RTL skips remain skips if RTL tools are unavailable.

- [ ] **Step 3: Execute the full 2160-candidate upper-bound campaign**

Run:

```powershell
& $py tools/experiments/multi_root_placement_search.py `
  --config ini/scalable/multi_root_placement_search.json `
  --output-dir dump/multi_root_placement_search_20261102
```

Expected: every group completes; exact candidate calls equal the sum of legal candidates; no failed or numerically incorrect run is included in Oracle.

- [ ] **Step 4: Verify full outputs independently**

Recompute for every group:

```python
oracle = min(row["cycles"] for row in candidates)
regret = mapper["cycles"] / oracle - 1
compact_speedup = compact["cycles"] / mapper["cycles"]
spread_speedup = spread["cycles"] / mapper["cycles"]
```

Assert equality with `results.json`, `regret >= 0`, and `oracle_hit == (mapper_cycles == oracle_cycles)`.

- [ ] **Step 5: Write the final experiment contract and measured summary**

Document:

- the finite-catalogue limitation;
- candidate counts and rejection counts;
- exact formulas;
- overall and stratified hit rate/regret/speedups;
- worst-regret workload and selected/Oracle Root/Worker positions;
- ROW/K/2D absolute-cycle table under common workloads;
- the absence of tree, RTL, 8/16 Root, and Host-output-gather claims;
- exact reproduction commands and output paths.

- [ ] **Step 6: Review local changes without staging or committing**

Run `git status --short` and `git diff --check`. Confirm only the intended local source, config, test, spec, plan, and dump outputs were added or modified. Do not run `git add`, `git commit`, or `git push`.
