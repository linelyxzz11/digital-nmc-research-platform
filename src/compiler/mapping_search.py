"""Result-independent ranking and budgeted exact search over common GridPlans.

The predictor schedules packet paths and endpoint service analytically. It does
not run the Mesh, inspect measured candidate cycles, or fit benchmark labels.
Finite FIFO/wormhole timing is left to the exact shared evaluator of the top K.
"""
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
import heapq
import math
import time

from compiler.auto_mapper_v0_1 import MappingError
from compiler.grid_mapper import GridPlan, map_grid
from compiler.unindp_adapter import allowed_grids
from simulator.functional import Demo0Tile, TileTask
from simulator.system.root_config import RootTiming
from simulator.system.tile_config import TileComputeConfig
from simulator.system.tile_timing import PortScheduledCompute

MAIN_GRIDS = ((1,16), (16,1), (4,4), (1,32), (32,1), (4,8), (8,4))


@dataclass(frozen=True)
class Catalogue:
    plans: tuple[GridPlan, ...]
    rejected: tuple[dict, ...]
    attempted: int


def catalogue(m, k, *, grids=MAIN_GRIDS, placements=('compact','spread'),
              reductions=('central','tree'), mesh_x=8, mesh_y=8, seed=0):
    plans, rejected = [], []
    for grid in grids:
        for placement in placements:
            for reduction in reductions:
                spec = dict(m=m, k=k, grid=tuple(grid), placement=placement,
                            reduction=reduction, mesh_x=mesh_x, mesh_y=mesh_y, seed=seed)
                try:
                    plans.append(map_grid(**spec))
                except MappingError as error:
                    rejected.append(dict(spec=spec, reason=str(error)))
    if not plans:
        raise MappingError('no feasible mapping in the declared catalogue')
    if len({plan.digest for plan in plans}) != len(plans):
        raise MappingError('duplicate plans in the catalogue')
    return Catalogue(tuple(plans), tuple(rejected), len(plans) + len(rejected))


@lru_cache(maxsize=2048)
def _isolated_beats(m, k, timing):
    tile = Demo0Tile(1)
    task = TileTask(1, 1, m, k, result_dst_tile=3)
    model = PortScheduledCompute(tile, task, timing.memory,
                                 operand_slots=timing.operand_slots,
                                 mac_interval=timing.mac_interval)
    beats = []
    # Upper bound also covers long SRAM/Bank sensitivity configurations.
    limit = 100 + math.ceil(m / 4) * (k + 10) * max(
        timing.memory.weight.read_latency, timing.memory.input.read_latency,
        timing.memory.weight.issue_interval, timing.memory.input.issue_interval,
        timing.mac_interval, 4) * 20
    for cycle in range(limit):
        signal = model.signals()
        if signal.result_valid:
            beat = signal.result_payload
            beats.append((cycle, beat.base_index, beat.lane_mask, beat.last))
        model.step(result_ready=True)
        if model.finished:
            return tuple(beats)
    raise RuntimeError('isolated Tile predictor did not terminate')


def clear_prediction_cache():
    """Give each separately timed policy the same cold isolated-Tile cache."""
    _isolated_beats.cache_clear()


def xy_path(source, destination, width):
    """Directed physical link keys, followed by the destination's local port."""
    current = source
    links = []
    while current % width != destination % width:
        next_tile = current + (1 if current % width < destination % width else -1)
        links.append((current, next_tile)); current = next_tile
    while current != destination:
        next_tile = current + (width if current < destination else -width)
        links.append((current, next_tile)); current = next_tile
    return tuple(links) + ((destination, 'LOCAL'),)


@dataclass(frozen=True)
class Estimate:
    cycles: float
    input_cycles: int
    compute_gather_cycles: int
    flit_hops: int
    peak_link_flits: int
    endpoint_service_cycles: int


def predict(plan, *, communication=True, tile_timing=TileComputeConfig(),
            root_timing=RootTiming()):
    """Pre-execution SRAM-command estimate, optionally with routed link sharing.

    Input uses the implemented stop-and-wait unicast protocol. Compute beats
    come from isolated, zero-valued Tiles. Endpoint READ/ADD/UPDATE/WRITE work
    is serialized per physical node. Path reservations approximate contention;
    they deliberately do not claim finite-FIFO cycle accuracy.
    """
    edge_free, edge_load, endpoint_free = defaultdict(int), defaultdict(int), defaultdict(int)
    arrivals, counter = [], 0
    parents = {task.tile_id:task.result_dst_tile for task in plan.tasks}
    parents.update({node.tile_id:node.parent for node in plan.reducers})
    source_masks = {node.tile_id:sum(1 << child for child in node.sources)
                    for node in plan.reducers}
    ranges = {plan.root_tile:plan.root_ranges}
    for node in plan.reducers:
        ranges[node.tile_id] = ((node.row_start,node.row_end,source_masks[node.tile_id]),)
    output_seen, output_counts = {}, defaultdict(int)
    flit_hops = service_total = 0

    def send(source, destination, release, base, mask, last):
        nonlocal counter
        counter += 1
        heapq.heappush(arrivals, (release,counter,0,source,destination,base,mask,last))

    # Actual sender orders chunks first, then worker IDs; ACKs serialize all
    # input transfers. The topology-blind baseline retains command count.
    input_cycles = 0
    shards = {s.tile_id:s for s in plan.slices}
    for offset in range(0, max(s.k_end-s.k_start for s in plan.slices), 4):
        for worker in plan.workers:
            shard = shards[worker]
            if offset < shard.k_end-shard.k_start:
                hops = len(xy_path(plan.host_tile, worker, plan.mesh_x)) - 1
                input_cycles += 2*hops + 7 if communication else 7
                flit_hops += 3*hops
    for task in plan.tasks:
        for release, base, mask, last in _isolated_beats(task.m_local,task.k_local,tile_timing):
            send(task.tile_id, task.result_dst_tile, release,
                 task.global_row_base+base, mask, last)
    complete = 0
    while arrivals:
        arrival, _, event, source, destination, base, mask, last = heapq.heappop(arrivals)
        if event == 0:
            path = xy_path(source,destination,plan.mesh_x)
            time_at = arrival + 1  # NI -> router LOCAL input
            for edge in path:
                if communication:
                    time_at = max(time_at,edge_free[edge])
                    edge_free[edge] = time_at + 2
                edge_load[edge] += 2
                time_at += 1
            flit_hops += 2*(len(path)-1)
            counter += 1
            received = time_at+1 if communication else arrival+2
            heapq.heappush(arrivals,(received,counter,1,source,destination,base,mask,last))
            continue
        expected_ranges = ranges[destination]
        # A source's four-lane packet can cross a row-range only if it is
        # malformed. GridPlan leaves and reducers never emit such a packet.
        expected = next(bits for begin,end,bits in expected_ranges if begin <= base < end)
        reduce = destination != plan.root_tile or plan.root_mode == 'REDUCE_PSUM'
        ready_mask = 0
        for lane in range(4):
            if mask & (1 << lane):
                index = base + lane
                key = (destination,index)
                output_seen[key] = output_seen.get(key,0) | (1 << source)
                if output_seen[key] == expected:
                    ready_mask |= 1 << lane
        chunks = math.ceil(mask.bit_count()/root_timing.lanes_per_cycle)
        service = 2 + chunks * ((root_timing.read_cycles+root_timing.add_cycles+
                                 root_timing.update_cycles) if reduce else 0)
        if ready_mask:
            service += math.ceil(ready_mask.bit_count()/root_timing.lanes_per_cycle)*root_timing.write_cycles
        start = max(arrival,endpoint_free[destination])
        finish = start + service
        endpoint_free[destination] = finish
        service_total += service
        complete = max(complete,finish)
        if destination in parents and ready_mask:
            output_counts[destination] += ready_mask.bit_count()
            total = sum(end-begin for begin,end,_ in expected_ranges)
            send(destination,parents[destination],finish,base,ready_mask,
                 output_counts[destination] == total)
    kernel = complete + 3  # completion creation, held Done, network drain
    return Estimate(input_cycles+kernel,input_cycles,kernel,flit_hops,
                    max(edge_load.values(),default=0),service_total)


@dataclass
class SearchResult:
    policy: str
    plan: GridPlan
    cycles: int
    ranking: list[dict]
    evaluated: list[dict]
    generation_seconds: float
    prediction_seconds: float
    evaluation_seconds: float
    attempted: int
    feasible: int
    pruned: int
    rejected: tuple[dict, ...]

    def summary(self):
        return dict(policy=self.policy,plan_sha256=self.plan.digest,cycles=self.cycles,
                    selected_plan=self.plan.to_dict(),ranking=self.ranking,evaluated=self.evaluated,
                    generation_seconds=self.generation_seconds,prediction_seconds=self.prediction_seconds,
                    evaluation_seconds=self.evaluation_seconds,attempted=self.attempted,
                    feasible=self.feasible,pruned=self.pruned,rejected=self.rejected)


def search(m, k, matrix, vector, *, policy='noc', exact_budget=4,
           tile_timing=TileComputeConfig(), root_timing=RootTiming(),
           candidates=None, evaluator=None, **catalogue_options):
    """Rank without result labels, then evaluate at most exact_budget plans.

    unindp-adapted retains full-PU pruning and predictor -> top-K -> exact
    selection; its SRAM access predictor replaces the inapplicable DRAM one.
    noc uses the same catalogue, same hardware and same exact-evaluation budget.
    """
    if policy not in ('noc','unindp-adapted','fixed-k','fixed-row'):
        raise ValueError('unknown mapping policy')
    if type(exact_budget) is not int or exact_budget < 1:
        raise ValueError('exact_budget must be a positive integer')
    if len(matrix)!=m or len(vector)!=k or any(len(row)!=k for row in matrix):
        raise ValueError('workload dimensions disagree with search')
    started = time.perf_counter()
    candidates = candidates or catalogue(m,k,**catalogue_options)
    if any((p.m,p.k)!=(m,k) for p in candidates.plans):
        raise ValueError('catalogue dimensions disagree with search')
    eligible = list(candidates.plans)
    if policy == 'unindp-adapted':
        budgets = tuple(sorted({math.prod(plan.grid) for plan in eligible}))
        allowed = allowed_grids(m,k,budgets)
        eligible = [plan for plan in eligible if plan.grid in allowed]
    elif policy.startswith('fixed-'):
        axis = 0 if policy == 'fixed-k' else 1
        eligible = [plan for plan in eligible if plan.grid[axis] == 1]
    if not eligible:
        raise MappingError('policy has no feasible candidate; do not silently fall back')
    generation = time.perf_counter()-started
    started = time.perf_counter()
    scored = [(predict(plan,communication=policy!='unindp-adapted',
                       tile_timing=tile_timing,root_timing=root_timing),plan) for plan in eligible]
    scored.sort(key=lambda item:(item[0].cycles,item[1].digest))
    ranking = [dict(plan_sha256=plan.digest,estimate=vars(estimate)) for estimate,plan in scored]
    prediction = time.perf_counter()-started
    if evaluator is None:
        from simulator.system.scalable import run_grid
        evaluator = lambda plan:run_grid(plan,matrix,vector,tile_timing=tile_timing,
                                       root_timing=root_timing)
    started = time.perf_counter()
    evaluated = []
    for _,plan in scored[:exact_budget]:
        result = evaluator(plan)
        cycles = result.cycles
        if type(cycles) is not int or cycles < 1:
            raise ValueError('evaluator must return positive integer cycles')
        evaluated.append(dict(plan_sha256=plan.digest,cycles=cycles))
    best = min(evaluated,key=lambda row:(row['cycles'],row['plan_sha256']))
    selected = next(plan for plan in eligible if plan.digest == best['plan_sha256'])
    return SearchResult(policy,selected,best['cycles'],ranking,evaluated,generation,prediction,
                        time.perf_counter()-started,candidates.attempted,len(candidates.plans),
                        len(candidates.plans)-len(eligible),candidates.rejected)
