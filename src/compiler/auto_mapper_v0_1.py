#!/usr/bin/env python3
"""Standalone capacity-aware mapper for Digital NMC Demo 1 / Demo 2.

Python 3.10+, standard library only. This module creates JSON-compatible plans;
it does not execute a GEMV, simulate the NoC, or predict end-to-end performance.
The existing Demo runner needs an adapter to consume a MappingPlan directly.

Examples:
    python3 auto_mapper_v0_1.py --demo --strategy row
    python3 auto_mapper_v0_1.py --demo --strategy k --k-split 1
    python3 auto_mapper_v0_1.py --workload workload.json --strategy auto -o plan.json
    python3 auto_mapper_v0_1.py --self-test

workload.json: {"W": [[1, 2], [3, 4]], "x": [5, 6]}
Weight images are preloaded before execution, matching the current Demos.
Only input-vector writes are placed in input_writes and require NoC ACKs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


HOST_TILE = 0
TILE_COUNT = 4
LANES = 4
W_BASE, X_BASE, Y_BASE = 0x0000, 0x1000, 0x2000
W_BYTES, X_BYTES, Y_BYTES = 4096, 1024, 1024

DEMO_W = [[1, 2, 3, 4], [-1, 0, 2, -2], [3, -1, 0, 1], [2, 2, -1, 0]]
DEMO_X = [2, -1, 3, 1]


class MappingError(ValueError):
    """The request cannot be represented by this mapper's fixed v0.1 scope."""


def _integer(value: Any, low: int, high: int, name: str) -> None:
    if type(value) is not int or not low <= value <= high:
        raise MappingError(f"{name} must be an integer in [{low}, {high}]")


def _validate_request(W, x, workers, root_tile, tid, global_row_base) -> tuple[int, int]:
    if not isinstance(W, (list, tuple)) or not W:
        raise MappingError("W must be a nonempty rectangular matrix")
    if not isinstance(x, (list, tuple)) or not x:
        raise MappingError("x must be a nonempty vector")
    m, k = len(W), len(x)
    _integer(m, 1, Y_BYTES // 4, "M (including the Root output-capacity limit)")
    if any(not isinstance(row, (list, tuple)) or len(row) != k for row in W):
        raise MappingError("every row of W must have exactly len(x) elements")
    for row_index, row in enumerate(W):
        for column_index, value in enumerate(row):
            _integer(value, -128, 127, f"W[{row_index}][{column_index}]")
    for index, value in enumerate(x):
        _integer(value, -128, 127, f"x[{index}]")
    if not isinstance(workers, (list, tuple)) or len(workers) != 2:
        raise MappingError("v0.1 requires exactly two worker Tiles")
    for tile in workers:
        _integer(tile, 1, TILE_COUNT - 1, "worker Tile (Host 0 is reserved)")
    if workers[0] == workers[1]:
        raise MappingError("worker Tiles must be distinct")
    _integer(root_tile, 0, TILE_COUNT - 1, "root_tile")
    if root_tile in workers:
        raise MappingError("Root must be distinct from the computing workers")
    _integer(tid, 0, 0xFFFF, "tid")
    _integer(global_row_base, 0, 0xFFFF, "global_row_base")
    if global_row_base + m > 0x10000:
        raise MappingError("global output indices overflow the 16-bit index field")
    return m, k


def _resources(tile: int, m: int, k: int) -> dict[str, int]:
    _integer(m, 1, 256, f"Tile {tile} m_local")
    _integer(k, 1, 1024, f"Tile {tile} k_local")
    groups = (m + LANES - 1) // LANES
    used = {"tile_id": tile, "m_local": m, "k_local": k,
            "row_groups": groups, "weight_bytes": LANES * groups * k,
            "input_bytes": k, "result_bytes": 4 * m}
    for field, capacity in (("weight_bytes", W_BYTES), ("input_bytes", X_BYTES),
                            ("result_bytes", Y_BYTES)):
        if used[field] > capacity:
            raise MappingError(f"Tile {tile} {field}={used[field]} exceeds {capacity}")
    return used


def _pack_weights(local_w: list[list[int]]) -> list[int]:
    """Pack (group, k, lane), with zero padding and little-endian INT8 lanes."""
    m, k = len(local_w), len(local_w[0])
    words = []
    for group in range((m + LANES - 1) // LANES):
        for column in range(k):
            word = 0
            for lane in range(LANES):
                row = LANES * group + lane
                value = local_w[row][column] if row < m else 0
                word |= (value & 0xFF) << (8 * lane)
            words.append(word)
    return words


def _candidate(W, x, *, partition, workers, root_tile, tid, global_row_base,
               k_split=None) -> dict[str, Any]:
    m, k = len(W), len(x)
    dimension = m if partition == "ROW" else k
    if dimension < 2:
        raise MappingError(f"{partition} needs a partition dimension of at least 2")
    split = k_split if partition == "K" and k_split is not None else (dimension + 1) // 2
    _integer(split, 1, dimension - 1, "split")

    slices, usage = [], []
    for tile, begin, end in ((workers[0], 0, split), (workers[1], split, dimension)):
        rows = [begin, end] if partition == "ROW" else [0, m]
        columns = [0, k] if partition == "ROW" else [begin, end]
        usage.append(_resources(tile, rows[1] - rows[0], columns[1] - columns[0]))
        slices.append({"tile_id": tile, "rows": rows, "k": columns})

    tasks, images, inputs = [], [], {}
    for tensor_slice, resource in zip(slices, usage):
        tile = tensor_slice["tile_id"]
        row_start, row_end = tensor_slice["rows"]
        k_start, k_end = tensor_slice["k"]
        local_w = [list(row[k_start:k_end]) for row in W[row_start:row_end]]
        inputs[tile] = list(x[k_start:k_end])
        tasks.append({"tid": tid, "tile_id": tile,
                      "m_local": resource["m_local"], "k_local": resource["k_local"],
                      "w_base": W_BASE, "x_base": X_BASE, "y_base": Y_BASE,
                      "global_row_base": global_row_base + row_start,
                      "result_kind": "FINAL" if partition == "ROW" else "PSUM",
                      "result_dst_tile": root_tile, "task_last": True,
                      "opcode": "GEMV_LOCAL", "version": 1})
        images.append({"tile_id": tile, "base_addr": W_BASE,
                       "words": _pack_weights(local_w)})

    # Match the current Demo ordering: word offset first, then worker order.
    writes = []
    for sequence, offset in enumerate(range(0, max(map(len, inputs.values())), 4)):
        for tile in workers:
            chunk = inputs[tile][offset:offset + 4]
            if chunk:
                writes.append({"src_tile": HOST_TILE, "dst_tile": tile, "tid": tid,
                               "local_addr": X_BASE + offset,
                               "wstrb": (1 << len(chunk)) - 1, "sequence": sequence,
                               "wdata": sum((v & 0xFF) << (8 * i) for i, v in enumerate(chunk))})

    return {
        "partition": partition,
        "split": split,
        "tensor_slices": slices,
        "tasks": tasks,
        "weight_images": images,
        "input_writes": writes,
        "gather_config": {"tid": tid, "root_tile": root_tile, "host_tile": HOST_TILE,
                          "source_mask": sum(1 << tile for tile in workers),
                          "output_base": global_row_base, "output_count": m,
                          "mode": "GATHER_FINAL" if partition == "ROW" else "REDUCE_PSUM"},
        "expected_ack_keys": sorted([[p["dst_tile"], p["tid"], p["sequence"]] for p in writes]),
        "diagnostics": {"tiles": usage, "input_write_count": len(writes),
                        "total_weight_bytes": sum(u["weight_bytes"] for u in usage),
                        "total_input_bytes": sum(u["input_bytes"] for u in usage),
                        "useful_mac_count": m * k},
    }


def map_gemv(W, x, *, strategy="auto", workers=(1, 2), root_tile=3,
             tid=1, global_row_base=0, k_split=None) -> dict[str, Any]:
    """Create a plan, without modifying inputs or touching simulator state.

    auto uses only the default balanced ROW and K candidates. ROW wins whenever
    it fits. This is a reproducible capacity baseline, not a speed prediction.
    Explicit row/k requests fail rather than silently switching strategies.
    """
    m, k = _validate_request(W, x, workers, root_tile, tid, global_row_base)
    if not isinstance(strategy, str) or strategy.lower() not in ("row", "k", "auto"):
        raise MappingError("strategy must be row, k, or auto")
    strategy = strategy.lower()
    if k_split is not None and strategy != "k":
        raise MappingError("k_split is allowed only with strategy='k'")
    candidates, summaries, failures = {}, {}, []
    names = ("ROW", "K") if strategy == "auto" else (strategy.upper(),)
    for name in names:
        try:
            candidates[name] = _candidate(
                W, x, partition=name, workers=workers, root_tile=root_tile,
                tid=tid, global_row_base=global_row_base, k_split=k_split)
            summaries[name] = {"feasible": True}
        except MappingError as error:
            summaries[name] = {"feasible": False, "reason": str(error)}
            failures.append(f"{name}: {error}")
    chosen = next((name for name in names if name in candidates), None)
    if chosen is None:
        raise MappingError("no legal default mapping; " + "; ".join(failures))
    plan = candidates[chosen]
    plan.update({
        "plan_version": "0.1",
        "strategy_requested": strategy,
        "selection_policy": "row_first_capacity_baseline" if strategy == "auto" else "explicit",
        "choice_reason": ("ROW is feasible and has baseline priority" if chosen == "ROW"
                          else "ROW is infeasible; default K is feasible") if strategy == "auto"
                         else f"explicit {chosen} request",
        "hardware": {"mesh_x": 2, "mesh_y": 2, "tile_count": TILE_COUNT,
                     "host_tile": HOST_TILE, "workers": list(workers), "root_tile": root_tile,
                     "lanes_per_worker": LANES, "weight_sram_bytes": W_BYTES,
                     "input_sram_bytes": X_BYTES, "result_sram_bytes": Y_BYTES},
        "workload": {"m": m, "k": k, "operand_type": "signed_int8",
                     "accumulator_type": "signed_int32_wrap"},
        "execution_contract": {
            "weights": "preload weight_images before launch; not timed in current Demos",
            "inputs": "send input_writes in order, waiting for each ACK before the next write",
            "launch": "configure Root and preload weights; launch tasks only after all input ACKs",
            "completion": "all final output indices complete AND all expected source last flags received",
            "performance_scope": "plan only; no prediction of GEMV execution cycles",
        },
    })
    plan["diagnostics"]["candidates"] = summaries
    return plan


def _self_test() -> None:
    """Check arithmetic through packed plans using an independent tiny interpreter."""
    def signed_byte(word, lane):
        byte = (word >> (8 * lane)) & 255
        return byte if byte < 128 else byte - 256

    def wrap(value):
        return ((value + (1 << 31)) % (1 << 32)) - (1 << 31)

    def evaluate(plan):
        images = {image["tile_id"]: image["words"] for image in plan["weight_images"]}
        memories = {task["tile_id"]: {} for task in plan["tasks"]}
        for packet in plan["input_writes"]:
            for lane in range(4):
                if packet["wstrb"] & (1 << lane):
                    memories[packet["dst_tile"]][packet["local_addr"] + lane] = signed_byte(packet["wdata"], lane)
        config = plan["gather_config"]
        result = [0] * config["output_count"]
        for task in plan["tasks"]:
            tile, k = task["tile_id"], task["k_local"]
            for row in range(task["m_local"]):
                value = wrap(sum(signed_byte(images[tile][(row // 4) * k + col], row % 4)
                                 * memories[tile][X_BASE + col] for col in range(k)))
                index = task["global_row_base"] + row - config["output_base"]
                result[index] = wrap(result[index] + value) if plan["partition"] == "K" else value
        return result

    def check(condition, message):
        if not condition:
            raise AssertionError(message)

    def rejected(**changes):
        try:
            map_gemv(changes.pop("W", DEMO_W), changes.pop("x", DEMO_X), **changes)
        except MappingError:
            return
        raise AssertionError(f"invalid request was accepted: {changes}")

    count = 0
    for strategy, split in (("row", None), ("k", None), ("k", 1), ("auto", None)):
        plan = map_gemv(DEMO_W, DEMO_X, strategy=strategy, k_split=split)
        check(evaluate(plan) == [13, 2, 8, -1], "default packed-plan arithmetic")
        count += 1
    check(map_gemv(DEMO_W, DEMO_X)["partition"] == "ROW", "auto ROW priority")
    for m, k in ((9, 5), (1, 4), (4, 1500), (4, 2048), (256, 1)):
        W = [[((row * 3 + col * 7) % 17) - 8 for col in range(k)] for row in range(m)]
        x = [(col % 9) - 4 for col in range(k)]
        golden = [wrap(sum(a * b for a, b in zip(row, x))) for row in W]
        for strategy in ("row", "k", "auto"):
            try:
                plan = map_gemv(W, x, strategy=strategy, global_row_base=37)
            except MappingError:
                if strategy == "auto":
                    raise
                continue
            check(evaluate(plan) == golden, "signed/tail/large/nonzero-base arithmetic")
            check(plan == map_gemv(W, x, strategy=strategy, global_row_base=37), "deterministic generation")
            check(len(plan["expected_ack_keys"]) == len({tuple(key) for key in plan["expected_ack_keys"]}), "unique ACK identities")
            count += 1
    for changes in ({"workers": (1, 1)}, {"workers": (0, 1)}, {"root_tile": 1},
                    {"tid": True}, {"global_row_base": 65534}, {"strategy": "other"},
                    {"strategy": "row", "k_split": 1}, {"strategy": "k", "k_split": 0},
                    {"strategy": "k", "k_split": True}, {"W": [[128]], "x": [1]},
                    {"W": [[1, 2]], "x": [1]}, {"W": [[True]], "x": [1]},
                    {"W": [[1] * 3000 for _ in range(4)], "x": [1] * 3000}):
        rejected(**changes)
        count += 1
    print(f"PASS: {count} mapping scenarios; packed arithmetic, tails, determinism and rejection checks")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true", help="use the existing 4x4 Demo GEMV")
    source.add_argument("--workload", type=Path, help='JSON object containing "W" and "x"')
    source.add_argument("--self-test", action="store_true", help="run built-in plan checks")
    parser.add_argument("--strategy", choices=("row", "k", "auto"), default="auto")
    parser.add_argument("--workers", nargs=2, type=int, default=(1, 2), metavar=("TILE_A", "TILE_B"))
    parser.add_argument("--root-tile", type=int, default=3)
    parser.add_argument("--tid", type=int, default=1)
    parser.add_argument("--global-row-base", type=int, default=0)
    parser.add_argument("--k-split", type=int)
    parser.add_argument("-o", "--output", type=Path, help="write the plan as JSON instead of stdout")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            _self_test()
            return 0
        payload = {"W": DEMO_W, "x": DEMO_X} if args.demo else json.loads(args.workload.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or "W" not in payload or "x" not in payload:
            raise MappingError('workload must be a JSON object containing "W" and "x"')
        plan = map_gemv(payload["W"], payload["x"], strategy=args.strategy,
                        workers=args.workers, root_tile=args.root_tile, tid=args.tid,
                        global_row_base=args.global_row_base, k_split=args.k_split)
        output = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            print(output, end="")
        return 0
    except (MappingError, OSError, ValueError) as error:
        print(f"mapper: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
