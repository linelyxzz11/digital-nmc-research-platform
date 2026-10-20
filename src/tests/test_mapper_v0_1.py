"""Verify mapper plans against the existing Demos and functional endpoints."""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from compiler.auto_mapper_v0_1 import DEMO_W, DEMO_X, MappingError, map_gemv
from simulator.communication import MemWritePacket, ResultPacket, StatusCode
from simulator.functional import Demo0Tile, TileTask, int32_wrap
from simulator.system.demo1 import FinalGather, GatherConfig, commit_mem_write, run_demo1
from simulator.system.demo2 import PSumReduce, run_demo2


def _workload(m, k):
    W = [[(row * 5 + column * 3) % 19 - 9 for column in range(k)] for row in range(m)]
    x = [column % 7 - 3 for column in range(k)]
    return W, x


def _execute_functional_plan(plan):
    """Use the real Tile and Root models; this helper does not time the NoC."""
    tiles = [Demo0Tile(tile) for tile in range(4)]
    for image in plan["weight_images"]:
        packed = b"".join(word.to_bytes(4, "little") for word in image["words"])
        offset = image["base_addr"]
        tiles[image["tile_id"]].weight_sram[offset:offset + len(packed)] = packed

    ack_keys = set()
    for write in plan["input_writes"]:
        ack = commit_mem_write(tiles[write["dst_tile"]], MemWritePacket(**write), idle=True)
        assert ack.status == StatusCode.OK
        ack_keys.add((ack.src_tile, ack.tid, ack.sequence))
    assert ack_keys == {tuple(key) for key in plan["expected_ack_keys"]}

    config = GatherConfig(**plan["gather_config"])
    endpoint_type = FinalGather if plan["partition"] == "ROW" else PSumReduce
    endpoint = endpoint_type(config, tiles[config.root_tile])
    done_count = 0
    # Reverse source arrival to ensure correctness is not tied to worker order.
    for fields in reversed(plan["tasks"]):
        beats, done = tiles[fields["tile_id"]].execute(TileTask(**fields))
        assert done.status == "OK"
        for beat in beats:
            packet = ResultPacket(
                src_tile=fields["tile_id"], dst_tile=beat.dst_tile, tid=beat.tid,
                base_index=beat.base_index, lane_mask=beat.lane_mask,
                data=beat.data, kind=beat.kind, last=beat.last,
            )
            done_count += endpoint.accept(packet) is not None
    assert done_count == 1
    return [tiles[config.root_tile].read_result(row) for row in range(config.output_count)]


@pytest.mark.parametrize("strategy,split,m,k,base,root,workers", [
    ("row", None, 4, 4, 0, 3, (1, 2)),
    ("k", None, 4, 4, 0, 3, (1, 2)),
    ("k", 1, 4, 4, 0, 3, (1, 2)),
    ("row", None, 9, 5, 37, 0, (2, 1)),
    ("k", 1, 9, 5, 37, 0, (2, 1)),
    ("k", 4, 9, 5, 65527, 1, (2, 3)),
])
def test_mapper_matches_existing_demo_and_executes(strategy, split, m, k, base, root, workers):
    W, x = _workload(m, k)
    options = dict(workers=workers, root_tile=root, tid=1, global_row_base=base)
    plan = map_gemv(W, x, strategy=strategy, k_split=split, **options)
    runner = run_demo1 if strategy == "row" else run_demo2
    if strategy == "k":
        options["k_split"] = split
    reference = runner(W, x, **options)
    assert plan["tasks"] == [asdict(task) for task in reference.tasks]
    assert plan["gather_config"] == asdict(reference.gather.config)
    assert {tuple(key) for key in plan["expected_ack_keys"]} == reference.ack_keys
    for image in plan["weight_images"]:
        packed = b"".join(word.to_bytes(4, "little") for word in image["words"])
        assert packed == reference.tiles[image["tile_id"]].weight_sram[:len(packed)]
    assert _execute_functional_plan(plan) == reference.y


@pytest.mark.parametrize("m,k,partition", [
    (4, 4, "ROW"),
    (1, 4, "K"),
    (4, 1500, "K"),
    (4, 2048, "K"),
    (9, 600, "K"),  # ROW needs 4800 bytes after Lane padding, not 5*600.
])
def test_auto_capacity_choice_produces_correct_results(m, k, partition):
    W, x = _workload(m, k)
    plan = map_gemv(W, x, strategy="auto")
    assert plan["partition"] == partition
    golden = [int32_wrap(sum(a * b for a, b in zip(row, x))) for row in W]
    assert _execute_functional_plan(plan) == golden
    assert plan == map_gemv(W, x, strategy="auto")


@pytest.mark.parametrize("changes", [
    {"workers": (1, 1)},
    {"workers": (0, 1)},
    {"root_tile": 1},
    {"tid": True},
    {"global_row_base": 65534},
    {"strategy": "unknown"},
    {"strategy": "row", "k_split": 1},
    {"strategy": "k", "k_split": 0},
    {"strategy": "k", "k_split": True},
    {"W": [[128]], "x": [1]},
    {"W": [[1, 2]], "x": [1]},
    {"W": [[True]], "x": [1]},
    {"W": [[1] * 3000 for _ in range(4)], "x": [1] * 3000},
    {"W": [[1] for _ in range(257)], "x": [1]},
])
def test_mapper_rejects_invalid_or_unrepresentable_requests(changes):
    options = dict(changes)
    W, x = options.pop("W", DEMO_W), options.pop("x", DEMO_X)
    with pytest.raises(MappingError):
        map_gemv(W, x, **options)


def test_mapper_cli_json_and_failure_exit_code(tmp_path):
    src = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(src))
    command = [sys.executable, "-B", "-m", "compiler.auto_mapper_v0_1"]
    for strategy in ("row", "k", "auto"):
        run = subprocess.run(command + ["--demo", "--strategy", strategy],
                             env=env, text=True, capture_output=True, check=False)
        assert run.returncode == 0, run.stderr
        assert not run.stderr
        assert json.loads(run.stdout)["partition"] == ("K" if strategy == "k" else "ROW")

    workload = tmp_path / "workload.json"
    output = tmp_path / "plan.json"
    workload.write_text(json.dumps({"W": DEMO_W, "x": DEMO_X}), encoding="utf-8")
    run = subprocess.run(command + ["--workload", str(workload), "-o", str(output)],
                         env=env, text=True, capture_output=True, check=False)
    assert run.returncode == 0, run.stderr
    assert not run.stdout
    assert json.loads(output.read_text(encoding="utf-8"))["partition"] == "ROW"

    run = subprocess.run(command + ["--demo", "--strategy", "k", "--k-split", "0"],
                         env=env, text=True, capture_output=True, check=False)
    assert run.returncode == 2
    assert not run.stdout
    assert "mapper:" in run.stderr
