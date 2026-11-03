"""Execute Mapper JSON plans through the real Demo scheduling loop."""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from compiler.auto_mapper_v0_1 import DEMO_W, DEMO_X, map_gemv
from simulator.functional import int32_wrap
from simulator.system.demo1 import (
    FinalGather, PlanError, load_plan, plan_from_mapping, run_demo1, run_plan,
)
from simulator.system.demo2 import PSumReduce, run_demo2


def _workload(m, k):
    W = [[(row * 5 + column * 3) % 19 - 9 for column in range(k)] for row in range(m)]
    x = [column % 7 - 3 for column in range(k)]
    return W, x


def _mapping(strategy, split, m, k, base, root, workers):
    W, x = _workload(m, k)
    return W, x, map_gemv(W, x, strategy=strategy, k_split=split,
                          workers=workers, root_tile=root, tid=1, global_row_base=base)


@pytest.mark.parametrize("strategy,split,m,k,base,root,workers,partition", [
    ("row", None, 4, 4, 0, 3, (1, 2), "ROW"),
    ("k", None, 4, 4, 0, 3, (1, 2), "K"),
    ("k", 1, 4, 4, 0, 3, (1, 2), "K"),
    ("row", None, 9, 5, 37, 0, (2, 1), "ROW"),
    ("k", 4, 9, 5, 65527, 1, (2, 3), "K"),
    ("auto", None, 4, 4, 0, 3, (1, 2), "ROW"),
    ("auto", None, 9, 600, 5, 3, (1, 2), "K"),
])
def test_mapper_plan_executes_identically_to_hardcoded_demo(
        strategy, split, m, k, base, root, workers, partition):
    W, x, mapping = _mapping(strategy, split, m, k, base, root, workers)
    plan = plan_from_mapping(json.loads(json.dumps(mapping)))
    assert plan.partition == partition

    factory = FinalGather if partition == "ROW" else PSumReduce
    executed = run_plan(plan, endpoint_factory=factory)
    if partition == "ROW":
        reference = run_demo1(W, x, tid=1, global_row_base=base,
                              workers=workers, root_tile=root)
    else:
        reference = run_demo2(W, x, tid=1, global_row_base=base,
                              workers=workers, root_tile=root, k_split=split)

    assert executed.y == reference.y
    assert [asdict(task) for task in executed.tasks] == [asdict(task) for task in reference.tasks]
    assert executed.gather.config == reference.gather.config
    assert executed.ack_keys == reference.ack_keys
    assert executed.trace == reference.trace
    assert executed.summary() == reference.summary()
    assert executed.y == [int32_wrap(sum(a * b for a, b in zip(row, x))) for row in W]


def test_demo_runners_reject_the_other_partition():
    _, _, row_mapping = _mapping("row", None, 4, 4, 0, 3, (1, 2))
    _, _, k_mapping = _mapping("k", None, 4, 4, 0, 3, (1, 2))
    with pytest.raises(PlanError, match="requires K"):
        run_plan(plan_from_mapping(row_mapping), endpoint_factory=PSumReduce,
                 expected_partition="K")
    with pytest.raises(PlanError, match="requires ROW"):
        run_plan(plan_from_mapping(k_mapping), endpoint_factory=FinalGather,
                 expected_partition="ROW")


def test_cli_generates_and_executes_plan(tmp_path):
    src = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(src))
    workload = tmp_path / "workload.json"
    workload.write_text(json.dumps({"W": DEMO_W, "x": DEMO_X}), encoding="utf-8")

    def run(arguments):
        return subprocess.run([sys.executable, "-B", *arguments], env=env,
                              text=True, capture_output=True, check=False)

    for strategy, module in (("row", "simulator.system.demo1"),
                             ("k", "simulator.system.demo2")):
        plan_path = tmp_path / f"{strategy}.json"
        generated = run(["-m", "compiler.auto_mapper_v0_1", "--workload", str(workload),
                         "--strategy", strategy, "-o", str(plan_path)])
        assert generated.returncode == 0, generated.stderr

        executed = run(["-m", module, "--plan", str(plan_path),
                        "--workload", str(workload)])
        assert executed.returncode == 0, executed.stderr
        assert '"check": "PASS"' in executed.stdout

        unverified = run(["-m", module, "--plan", str(plan_path)])
        assert unverified.returncode == 0, unverified.stderr
        assert '"check": "EXECUTED (no --workload golden)"' in unverified.stdout


def test_cli_rejects_plan_for_the_other_demo(tmp_path):
    src = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(src))
    plan_path = tmp_path / "k.json"
    _, _, mapping = _mapping("k", None, 4, 4, 0, 3, (1, 2))
    plan_path.write_text(json.dumps(mapping), encoding="utf-8")
    run = subprocess.run([sys.executable, "-B", "-m", "simulator.system.demo1",
                          "--plan", str(plan_path)], env=env, text=True,
                         capture_output=True, check=False)
    assert run.returncode == 2
    assert "requires ROW" in run.stderr


def _row_mapping():
    return json.loads(json.dumps(map_gemv(DEMO_W, DEMO_X, strategy="row")))


MUTATIONS = {
    "plan-version": lambda plan: plan.update(plan_version="9"),
    "partition": lambda plan: plan.update(partition="2D"),
    "mesh": lambda plan: plan["hardware"].update(mesh_x=3),
    "lanes": lambda plan: plan["hardware"].update(lanes_per_tile=8),
    "banks": lambda plan: plan["hardware"].update(banks_per_tile=0),
    "weight-base": lambda plan: plan["hardware"].update(weight_base=4),
    "duplicate-workers": lambda plan: plan["hardware"].update(workers=[1, 1]),
    "root-overlap": lambda plan: plan["hardware"].update(root_tile=1),
    "operand-type": lambda plan: plan["workload"].update(operand_type="float32"),
    "bool-size": lambda plan: plan["workload"].update(m=True),
    "mode": lambda plan: plan["gather_config"].update(mode="REDUCE_PSUM"),
    "output-count": lambda plan: plan["gather_config"].update(output_count=5),
    "source-mask": lambda plan: plan["gather_config"].update(source_mask=10),
    "task-tile": lambda plan: plan["tasks"][0].update(tile_id=3),
    "task-opcode": lambda plan: plan["tasks"][0].update(opcode="VADD"),
    "task-version": lambda plan: plan["tasks"][0].update(version=2),
    "task-kind": lambda plan: plan["tasks"][0].update(result_kind="PSUM"),
    "task-base": lambda plan: plan["tasks"][0].update(w_base=4),
    "task-shape": lambda plan: plan["tasks"][0].update(m_local=3),
    "task-index": lambda plan: plan["tasks"][1].update(global_row_base=3),
    "slice-rows": lambda plan: plan["tensor_slices"][0].update(rows=[1, 2]),
    "slice-order": lambda plan: plan["tensor_slices"][0].update(tile_id=2),
    "image-base": lambda plan: plan["weight_images"][0].update(base_addr=4),
    "image-length": lambda plan: plan["weight_images"][0]["words"].append(0),
    "image-range": lambda plan: plan["weight_images"][0]["words"].__setitem__(0, -1),
    "write-dst": lambda plan: plan["input_writes"][0].update(dst_tile=3),
    "write-src": lambda plan: plan["input_writes"][0].update(src_tile=1),
    "write-address": lambda plan: plan["input_writes"][0].update(local_addr=0x1100),
    "write-strobe": lambda plan: plan["input_writes"][0].update(wstrb=0),
    "write-duplicate": lambda plan: plan["input_writes"].append(dict(plan["input_writes"][0])),
    "ack-keys": lambda plan: plan["expected_ack_keys"].pop(),
}


@pytest.mark.parametrize("mutate", MUTATIONS.values(), ids=MUTATIONS.keys())
def test_invalid_plans_are_rejected(mutate):
    mapping = _row_mapping()
    mutate(mapping)
    with pytest.raises(PlanError):
        plan_from_mapping(mapping)


def test_load_plan_rejects_missing_or_malformed_files(tmp_path):
    with pytest.raises(PlanError):
        load_plan(tmp_path / "missing.json")
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not json", encoding="utf-8")
    with pytest.raises(PlanError):
        load_plan(malformed)
    wrong_shape = tmp_path / "wrong.json"
    wrong_shape.write_text("[]", encoding="utf-8")
    with pytest.raises(PlanError):
        load_plan(wrong_shape)
