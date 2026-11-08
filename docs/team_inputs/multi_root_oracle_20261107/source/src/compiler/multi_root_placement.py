"""Finite multi-Root placement catalogues and result-independent ranking."""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections import defaultdict
import hashlib
import json
from math import ceil

from compiler.auto_mapper_v0_1 import MappingError
from compiler.grid_mapper import GridPlan, map_grid


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
        return (
            self.estimated_busiest_link_load,
            self.estimated_max_root_load,
            self.estimated_total_flit_hops,
            self.estimated_completion_hops,
            self.task_switch_count,
        )


@dataclass(frozen=True)
class RankedCandidate:
    candidate: PlacementCandidate
    estimate: PlacementEstimate

    @property
    def score(self) -> tuple[int, int, int, int, int, str]:
        return (*self.estimate.score, self.candidate.candidate_id)


def _candidate_id(plan: GridPlan) -> str:
    identity = {
        "grid": list(plan.grid),
        "final_roots": list(plan.final_roots),
        "workers": list(plan.workers),
    }
    encoded = json.dumps(
        identity, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def generate_candidates(
    m: int,
    k: int,
    *,
    grid: tuple[int, int],
    root_sets: tuple[tuple[int, ...], ...],
    seeds: tuple[int, ...],
    host_tile: int = 0,
    tid: int = 1,
) -> CandidateCatalogue:
    """Generate compact, spread, and seeded-random physical placements."""

    candidates: list[PlacementCandidate] = []
    rejected: list[dict] = []
    by_physical_plan: dict[tuple, int] = {}

    for roots in root_sets:
        final_roots = tuple(roots)
        requests = [("compact", None), ("spread", None)]
        requests.extend(("random", seed) for seed in dict.fromkeys(seeds))
        for placement, seed in requests:
            map_seed = 0 if seed is None else seed
            try:
                plan = map_grid(
                    m,
                    k,
                    grid=grid,
                    root_tile=final_roots[0] if final_roots else host_tile,
                    final_roots=final_roots,
                    placement=placement,
                    host_tile=host_tile,
                    seed=map_seed,
                    tid=tid,
                    reduction="central",
                )
            except MappingError as error:
                rejected.append({
                    "final_roots": list(final_roots),
                    "placement": placement,
                    "seed": seed,
                    "reason": str(error),
                })
                continue

            key = (plan.grid, plan.final_roots, plan.workers)
            seed_sources = () if seed is None else (seed,)
            if key in by_physical_plan:
                index = by_physical_plan[key]
                existing = candidates[index]
                merged = tuple(sorted(set(existing.seed_sources + seed_sources)))
                candidates[index] = replace(existing, seed_sources=merged)
                continue

            candidate = PlacementCandidate(
                candidate_id=_candidate_id(plan),
                final_roots=plan.final_roots,
                workers=plan.workers,
                placement=placement,
                seed=seed,
                seed_sources=seed_sources,
                plan=plan,
            )
            by_physical_plan[key] = len(candidates)
            candidates.append(candidate)

    return CandidateCatalogue(tuple(candidates), tuple(rejected))


def _xy_links(source: int, destination: int, width: int) -> tuple[tuple[int, int], ...]:
    current = source
    links = []
    while current % width != destination % width:
        next_tile = current + (1 if current % width < destination % width else -1)
        links.append((current, next_tile))
        current = next_tile
    while current != destination:
        next_tile = current + (width if current < destination else -width)
        links.append((current, next_tile))
        current = next_tile
    return tuple(links)


def estimate_candidate(candidate: PlacementCandidate) -> PlacementEstimate:
    """Estimate deterministic packet traffic without executing the cycle model."""

    plan = candidate.plan
    link_flits: dict[tuple[int, int], int] = defaultdict(int)

    def add_transfer(source: int, destination: int, flits: int) -> int:
        links = _xy_links(source, destination, plan.mesh_x)
        for link in links:
            link_flits[link] += flits
        return len(links) * flits

    total_flit_hops = 0
    for shard in plan.slices:
        input_packets = ceil((shard.k_end - shard.k_start) / 4)
        total_flit_hops += add_transfer(plan.host_tile, shard.tile_id, 2 * input_packets)
        total_flit_hops += add_transfer(shard.tile_id, plan.host_tile, input_packets)

    root_packets = {root: 0 for root in plan.final_roots}
    for task in plan.tasks:
        result_packets = ceil(task.m_local / 4)
        root_packets[task.result_dst_tile] = (
            root_packets.get(task.result_dst_tile, 0) + result_packets
        )
        total_flit_hops += add_transfer(
            task.tile_id, task.result_dst_tile, 2 * result_packets,
        )

    completion_hops = 0
    for root in plan.final_roots:
        distance = len(_xy_links(root, plan.host_tile, plan.mesh_x))
        completion_hops += distance
        total_flit_hops += add_transfer(root, plan.host_tile, 1)

    task_switch_count = sum(
        max(0, len(queue.tasks) - 1) for queue in plan.task_queues
    )
    serialized_links = tuple(
        (f"{source}->{destination}", count)
        for (source, destination), count in sorted(link_flits.items())
    )
    return PlacementEstimate(
        estimated_busiest_link_load=max(link_flits.values(), default=0),
        estimated_max_root_load=max(root_packets.values(), default=0),
        estimated_total_flit_hops=total_flit_hops,
        estimated_completion_hops=completion_hops,
        task_switch_count=task_switch_count,
        link_flits=serialized_links,
    )


def rank_candidates(
    candidates: tuple[PlacementCandidate, ...],
) -> tuple[RankedCandidate, ...]:
    ranking = tuple(
        RankedCandidate(candidate, estimate_candidate(candidate))
        for candidate in candidates
    )
    return tuple(sorted(ranking, key=lambda entry: entry.score))


def select_candidate(
    candidates: tuple[PlacementCandidate, ...],
) -> PlacementCandidate:
    ranking = rank_candidates(candidates)
    if not ranking:
        raise ValueError("cannot select from an empty placement catalogue")
    return ranking[0].candidate
