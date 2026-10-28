"""Flat-PU specialization of UniNDP's partition and top-K search decisions.

This is a method adaptation, not the author's DRAM backend or timing predictor.
See docs/spec/mapping_search_v0.3.md for the exact retained/replaced decisions.
The specialization is checked against the pinned author methods by
tools/experiments/verify_unindp_port.py, without importing its dependencies.
"""
from dataclasses import dataclass

AUTHOR_COMMIT = 'c8b21684358441d29fc81fa0866d07cec20582ed'
AUTHOR_URL = 'https://github.com/godfather991/UniNDP'


@dataclass(frozen=True)
class PartitionDecision:
    budget: int
    grid: tuple[int, int]
    # Author MM dimensions are (input rows, K, output columns, batch).
    author_pu_partition: tuple[int, int, int, int]
    author_pu_ids: tuple[int, ...]


def partitions(m, k, budgets=(16, 32)):
    """Power-of-two partitions, full-PU pruning and contiguous logical PU IDs.

    Author GEMV shape is (1, k, m, 1). A single flat device with P PUs
    corresponds to P available compute roles, not P physical Mesh nodes.
    Empty shards are excluded by our existing GridPlan contract.
    """
    if type(m) is not int or type(k) is not int or min(m, k) < 1:
        raise ValueError('positive integer GEMV dimensions required')
    decisions = []
    for budget in budgets:
        if type(budget) is not int or budget < 1 or budget & (budget - 1):
            raise ValueError('flat adapter budgets must be powers of two')
        powers = [1 << bit for bit in range(budget.bit_length())]
        for pk in powers:
            for pm in powers:
                if pk > k or pm > m or pk * pm > budget:
                    continue
                # choose_from_partition_space_mm: use all PUs, or a saturated
                # partition for which increasing ANY dimension exceeds P.
                pu = (1, pk, pm, 1)
                saturated = all((dim + 1) * (pk * pm // dim) > budget for dim in pu)
                if pk * pm == budget or saturated:
                    decisions.append(PartitionDecision(budget, (pm, pk), pu,
                                                       tuple(range(pk * pm))))
    return tuple(decisions)


def allowed_grids(m, k, budgets=(16, 32)):
    return frozenset(decision.grid for decision in partitions(m, k, budgets))
