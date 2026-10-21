"""Demo 1: resident weights, network input loading, row GEMV and FINAL gather.

Compute is functional, while network transfers advance Mesh2x2 cycles. These
cycles are a communication schedule, not an end-to-end performance prediction.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
from typing import Any, Callable, Protocol

from simulator.communication import (
    MemAckPacket, MemWritePacket, Mesh2x2, ResultNI, ResultPacket,
    StatusCode, TransactionDonePacket, pack_packet,
)
from simulator.functional import Demo0Tile, ResultEmitter, TileTask, int32_wrap
from simulator.functional.demo0_model import X_START, X_END, Y_START, Y_END, W_START, W_END


DEMO_W = [[1, 2, 3, 4], [-1, 0, 2, -2], [3, -1, 0, 1], [2, 2, -1, 0]]
DEMO_X = [2, -1, 3, 1]


class TransactionError(ValueError):
    def __init__(self, status: StatusCode, detail: str):
        super().__init__(f"{status.name}: {detail}")
        self.status = status


@dataclass(frozen=True)
class GatherConfig:
    tid: int
    root_tile: int
    host_tile: int
    source_mask: int
    output_base: int
    output_count: int
    mode: str = "GATHER_FINAL"

    def __post_init__(self) -> None:
        if not (0 <= self.tid <= 0xFFFF and 0 <= self.root_tile < 4
                and 0 <= self.host_tile < 4 and 0 < self.source_mask < 16
                and 0 <= self.output_base <= 0xFFFF and 1 <= self.output_count <= 256
                and self.output_base + self.output_count <= 0x10000
                and self.mode in ("GATHER_FINAL", "REDUCE_PSUM")):
            raise ValueError("invalid TransactionConfig")


PLAN_VERSION = "0.1"


class PlanError(ValueError):
    """A MappingPlan cannot be represented by this v0.1 runner."""


@dataclass(frozen=True)
class TensorSlice:
    tile_id: int
    row_start: int
    row_end: int
    k_start: int
    k_end: int


@dataclass(frozen=True)
class WeightImage:
    tile_id: int
    base_addr: int
    words: tuple[int, ...]


@dataclass(frozen=True)
class ExecutionPlan:
    partition: str
    tid: int
    workers: tuple[int, int]
    root_tile: int
    output_base: int
    output_count: int
    k: int
    slices: tuple[TensorSlice, ...]
    tasks: tuple[TileTask, ...]
    weight_images: tuple[WeightImage, ...]
    input_writes: tuple[MemWritePacket, ...]
    config: GatherConfig


class FinalGather:
    """Root endpoint: validate complete packets before changing result storage."""

    def __init__(self, config: GatherConfig, root: Demo0Tile):
        if root.physical_tile_id != config.root_tile:
            raise ValueError("root Tile does not match config")
        self.config = config
        self.root = root
        self.values: dict[int, int] = {}
        self.last_mask = 0
        self.done: TransactionDonePacket | None = None
        self.error: StatusCode | None = None

    def _fail(self, status: StatusCode, detail: str) -> None:
        self.error = status
        raise TransactionError(status, detail)

    def accept(self, packet: ResultPacket) -> TransactionDonePacket | None:
        c = self.config
        if self.error is not None:
            raise TransactionError(self.error, "transaction already terminated")
        if self.done is not None:
            self._fail(StatusCode.PROTOCOL_ERROR, "RESULT after global completion")
        if packet.tid != c.tid or packet.dst_tile != c.root_tile:
            self._fail(StatusCode.CONFIG_MISS, "tid or destination mismatch")
        if packet.status != StatusCode.OK:
            self._fail(StatusCode.PROTOCOL_ERROR, "RESULT has error status")
        if packet.kind != "FINAL":
            self._fail(StatusCode.KIND_MISMATCH, "GATHER_FINAL requires FINAL")
        if not (0 <= packet.src_tile < 4) or not c.source_mask & (1 << packet.src_tile):
            self._fail(StatusCode.BAD_TILE, "unexpected source")
        if not 0 < packet.lane_mask < 16:
            self._fail(StatusCode.BAD_FIELD, "invalid lane mask")
        elements = [(packet.base_index + i, packet.data[i])
                    for i in range(4) if packet.lane_mask & (1 << i)]
        # Check every lane first: a later invalid lane cannot partly commit a packet.
        for index, value in elements:
            if not c.output_base <= index < c.output_base + c.output_count:
                self._fail(StatusCode.BAD_FIELD, "output index outside config")
            if index in self.values:
                self._fail(StatusCode.DUPLICATE_RESULT, "duplicate global output index")
            if not -(1 << 31) <= value < (1 << 31):
                self._fail(StatusCode.BAD_FIELD, "output outside signed INT32")
        if self.last_mask & (1 << packet.src_tile):
            self._fail(StatusCode.PROTOCOL_ERROR, "RESULT after source last")
        for index, value in elements:
            row = index - c.output_base
            self.root.result_sram[4 * row:4 * row + 4] = value.to_bytes(4, "little", signed=True)
            self.values[index] = value
        if packet.last:
            self.last_mask |= 1 << packet.src_tile
        if len(self.values) == c.output_count and self.last_mask == c.source_mask:
            self.done = TransactionDonePacket(c.root_tile, c.host_tile, c.tid,
                                              c.output_count, c.source_mask)
            return self.done
        return None


def commit_mem_write(tile: Demo0Tile, packet: MemWritePacket, *, idle: bool) -> MemAckPacket | None:
    """Functional equivalent of the local write-port commit; no write when busy."""
    if not idle:
        return None
    status = StatusCode.OK
    region: tuple[int, bytearray] | None = None
    if packet.dst_tile != tile.physical_tile_id:
        status = StatusCode.BAD_TILE
    elif packet.local_addr % 4:
        status = StatusCode.ADDR_ALIGN
    elif not 0 < packet.wstrb < 16:
        status = StatusCode.BAD_FIELD
    else:
        for start, end, memory in ((W_START, W_END, tile.weight_sram),
                                    (X_START, X_END, tile.input_sram),
                                    (Y_START, Y_END, tile.result_sram)):
            if start <= packet.local_addr and packet.local_addr + 4 <= end:
                region = start, memory
                break
        if region is None:
            status = StatusCode.ADDR_RANGE
    if status == StatusCode.OK:
        assert region is not None
        start, memory = region
        for lane in range(4):
            if packet.wstrb & (1 << lane):
                memory[packet.local_addr - start + lane] = (packet.wdata >> (8 * lane)) & 0xFF
    return MemAckPacket(tile.physical_tile_id, packet.src_tile, packet.tid, packet.sequence, status)


class ResultEndpoint(Protocol):
    config: GatherConfig
    values: dict[int, int]
    last_mask: int
    done: TransactionDonePacket | None

    def accept(self, packet: ResultPacket) -> TransactionDonePacket | None: ...


@dataclass
class NMCRun:
    tiles: list[Demo0Tile]
    mesh: Mesh2x2
    gather: ResultEndpoint
    tasks: list[TileTask]
    trace: list[dict]
    host_done: list[TransactionDonePacket]
    ack_keys: set[tuple[int, int, int]]
    y: list[int]

    def summary(self) -> dict:
        config = self.gather.config
        summary = {"demo": "NMC Demo 1 / GATHER_FINAL" if config.mode == "GATHER_FINAL"
                   else "NMC Demo 2 / REDUCE_PSUM", "y": self.y,
                "host_done_count": len(self.host_done), "input_acks": len(self.ack_keys),
                "network_schedule_cycles": self.mesh.cycle, "flit_hops": self.mesh.flit_hops,
                "max_router_fifo_occupancy": self.mesh.max_occupancy,
                "timing_scope": "functional Compute + cycle-stepped NoC; not total GEMV latency"}
        summary["result_packet_count"] = sum(e["event"] == "RESULTBEAT_ACCEPT" for e in self.trace)
        if config.mode == "REDUCE_PSUM":
            summary["psum_elements"] = sum(e["lane_mask"].bit_count() for e in self.trace
                                            if e["event"] == "RESULTBEAT_ACCEPT")
        return summary


# Preserve the public name used by Demo 1 callers.
Demo1Run = NMCRun


def run_demo1(
    matrix: list[list[int]] | None = None, vector: list[int] | None = None,
    *, tid: int = 1, global_row_base: int = 0,
    workers: tuple[int, int] = (1, 2), root_tile: int = 3,
    sink_ready: Callable[[int, int, str], bool] | None = None,
    max_cycles: int = 10000,
) -> Demo1Run:
    """Host-driven two-way contiguous row partition with actual mesh delivery."""
    return _run_partitioned(matrix, vector, tid=tid, global_row_base=global_row_base,
                            workers=workers, root_tile=root_tile, sink_ready=sink_ready,
                            max_cycles=max_cycles, partition="ROW", endpoint_factory=FinalGather)


def _weight_image(tile_id: int, matrix: list[list[int]]) -> WeightImage:
    """Pack a local weight matrix exactly as Demo0Tile.load_weight_matrix does."""
    tile = Demo0Tile(tile_id)
    tile.load_weight_matrix(matrix)
    size = 4 * ((len(matrix) + 3) // 4) * len(matrix[0])
    return WeightImage(tile_id, W_START, tuple(
        int.from_bytes(tile.weight_sram[offset:offset + 4], "little")
        for offset in range(0, size, 4)))


def _build_demo_plan(
    matrix: list[list[int]] | None, vector: list[int] | None,
    *, tid: int, global_row_base: int, workers: tuple[int, int], root_tile: int,
    partition: str, k_split: int | None = None,
) -> ExecutionPlan:
    """Turn the hard-coded Demo parameters into the same plan shape a Mapper emits."""
    matrix = DEMO_W if matrix is None else matrix
    vector = DEMO_X if vector is None else vector
    m, k = len(matrix), len(vector)
    if partition not in ("ROW", "K"):
        raise ValueError("partition must be ROW or K")
    min_m, min_k, max_k = (2, 1, 1024) if partition == "ROW" else (1, 2, 2048)
    if not min_m <= m <= 256 or not min_k <= k <= max_k or any(len(row) != k for row in matrix):
        raise ValueError("invalid MxK matrix/vector dimensions for the selected partition")
    if len(workers) != 2 or len(set(workers)) != 2 or any(t not in (1, 2, 3) for t in workers):
        raise ValueError("two distinct non-host worker Tiles are required")
    if root_tile not in range(4) or root_tile in workers:
        raise ValueError("these demos use a root distinct from computing workers")
    if any(not isinstance(v, int) or isinstance(v, bool) or not -128 <= v <= 127
           for v in vector + [v for row in matrix for v in row]):
        raise ValueError("weights and inputs must be signed INT8")
    mode = "GATHER_FINAL" if partition == "ROW" else "REDUCE_PSUM"
    config = GatherConfig(tid, root_tile, 0, sum(1 << t for t in workers), global_row_base, m, mode)

    if partition == "ROW":
        split = (m + 1) // 2
    else:
        split = (k + 1) // 2 if k_split is None else k_split
    dimension = m if partition == "ROW" else k
    if not isinstance(split, int) or isinstance(split, bool) or not 1 <= split < dimension:
        raise ValueError("split must leave two nonempty partitions")

    inputs: dict[int, list[int]] = {}
    slices: list[TensorSlice] = []
    tasks: list[TileTask] = []
    images: list[WeightImage] = []
    for tile, begin, end in ((workers[0], 0, split), (workers[1], split, dimension)):
        local_w = matrix[begin:end] if partition == "ROW" else [row[begin:end] for row in matrix]
        inputs[tile] = vector if partition == "ROW" else vector[begin:end]
        local_k = len(inputs[tile])
        if local_k > 1024:
            raise ValueError("local K exceeds Input SRAM capacity")
        slices.append(TensorSlice(
            tile,
            begin if partition == "ROW" else 0,
            end if partition == "ROW" else m,
            0 if partition == "ROW" else begin,
            k if partition == "ROW" else end))
        tasks.append(TileTask(tid, tile, len(local_w), local_k,
                              global_row_base=global_row_base + begin if partition == "ROW" else global_row_base,
                              result_dst_tile=root_tile, result_kind="FINAL" if partition == "ROW" else "PSUM"))
        images.append(_weight_image(tile, local_w))

    writes = []
    for seq, offset in enumerate(range(0, max(len(v) for v in inputs.values()), 4)):
        for tile in workers:
            chunk = inputs[tile][offset:offset + 4]
            if not chunk:
                continue
            word = sum((value & 0xFF) << (8 * i) for i, value in enumerate(chunk))
            writes.append(MemWritePacket(0, tile, tid, X_START + offset,
                                         (1 << len(chunk)) - 1, seq, word))
    return ExecutionPlan(
        partition=partition, tid=tid, workers=tuple(workers), root_tile=root_tile,
        output_base=global_row_base, output_count=m, k=k,
        slices=tuple(slices), tasks=tuple(tasks), weight_images=tuple(images),
        input_writes=tuple(writes), config=config)


def _plan_int(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise PlanError(f"{name} must be an integer in [{low}, {high}]")
    return value


def _plan_object(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise PlanError(f"{name} must be a JSON object")
    return value


def _plan_array(value: Any, name: str, length: int | None = None) -> list:
    if not isinstance(value, list) or (length is not None and len(value) != length):
        expected = f"a JSON array of length {length}" if length is not None else "a JSON array"
        raise PlanError(f"{name} must be {expected}")
    return value


def plan_from_mapping(mapping: Any) -> ExecutionPlan:
    """Validate a Mapper JSON object and convert it into executable objects."""
    plan = _plan_object(mapping, "plan")
    if plan.get("plan_version") != PLAN_VERSION:
        raise PlanError(f"plan_version must be {PLAN_VERSION!r}")

    hardware = _plan_object(plan.get("hardware"), "hardware")
    fixed = {"mesh_x": 2, "mesh_y": 2, "tile_count": 4, "host_tile": 0,
             "lanes_per_worker": 4, "weight_sram_bytes": W_END - W_START,
             "input_sram_bytes": X_END - X_START, "result_sram_bytes": Y_END - Y_START}
    for key, expected in fixed.items():
        if type(hardware.get(key)) is not int or hardware[key] != expected:
            raise PlanError(f"hardware.{key} must be {expected}")
    raw_workers = _plan_array(hardware.get("workers"), "hardware.workers", 2)
    workers = tuple(_plan_int(tile, f"hardware.workers[{index}]", 1, 3)
                    for index, tile in enumerate(raw_workers))
    if workers[0] == workers[1]:
        raise PlanError("hardware.workers must be distinct")
    root_tile = _plan_int(hardware.get("root_tile"), "hardware.root_tile", 0, 3)
    if root_tile in workers:
        raise PlanError("hardware.root_tile must differ from the workers")

    workload = _plan_object(plan.get("workload"), "workload")
    m = _plan_int(workload.get("m"), "workload.m", 1, Y_END // 4)
    k = _plan_int(workload.get("k"), "workload.k", 1, 2048)
    if workload.get("operand_type") != "signed_int8":
        raise PlanError("workload.operand_type must be 'signed_int8'")
    if workload.get("accumulator_type") != "signed_int32_wrap":
        raise PlanError("workload.accumulator_type must be 'signed_int32_wrap'")

    partition = plan.get("partition")
    if partition not in ("ROW", "K"):
        raise PlanError("partition must be 'ROW' or 'K'")
    try:
        config = GatherConfig(**_plan_object(plan.get("gather_config"), "gather_config"))
    except (TypeError, ValueError) as error:
        raise PlanError(f"gather_config is invalid: {error}") from None
    if config.host_tile != 0:
        raise PlanError("gather_config.host_tile must be 0")
    if config.root_tile != root_tile:
        raise PlanError("gather_config.root_tile must match hardware.root_tile")
    expected_mode = "GATHER_FINAL" if partition == "ROW" else "REDUCE_PSUM"
    if config.mode != expected_mode:
        raise PlanError(f"gather_config.mode must be {expected_mode} for {partition} partitioning")
    if config.output_count != m:
        raise PlanError("gather_config.output_count must equal workload.m")
    if config.source_mask != sum(1 << tile for tile in workers):
        raise PlanError("gather_config.source_mask must name exactly the two workers")
    tid = config.tid

    slices: list[TensorSlice] = []
    for index, item in enumerate(_plan_array(plan.get("tensor_slices"), "tensor_slices", 2)):
        name = f"tensor_slices[{index}]"
        data = _plan_object(item, name)
        tile = _plan_int(data.get("tile_id"), f"{name}.tile_id", 1, 3)
        if tile != workers[index]:
            raise PlanError(f"{name}.tile_id must follow hardware.workers order")
        rows = _plan_array(data.get("rows"), f"{name}.rows", 2)
        columns = _plan_array(data.get("k"), f"{name}.k", 2)
        row_start, row_end = (_plan_int(rows[0], f"{name}.rows[0]", 0, m),
                              _plan_int(rows[1], f"{name}.rows[1]", 0, m))
        k_start, k_end = (_plan_int(columns[0], f"{name}.k[0]", 0, k),
                          _plan_int(columns[1], f"{name}.k[1]", 0, k))
        if not row_start < row_end:
            raise PlanError(f"{name}.rows must be a nonempty half-open interval")
        if not k_start < k_end:
            raise PlanError(f"{name}.k must be a nonempty half-open interval")
        slices.append(TensorSlice(tile, row_start, row_end, k_start, k_end))
    if partition == "ROW":
        if any((item.k_start, item.k_end) != (0, k) for item in slices):
            raise PlanError("ROW tensor_slices must cover the full K dimension")
        if (slices[0].row_start, slices[0].row_end) != (0, slices[1].row_start) or slices[1].row_end != m:
            raise PlanError("ROW tensor_slices must contiguously cover [0, m)")
    else:
        if any((item.row_start, item.row_end) != (0, m) for item in slices):
            raise PlanError("K tensor_slices must cover the full M dimension")
        if (slices[0].k_start, slices[0].k_end) != (0, slices[1].k_start) or slices[1].k_end != k:
            raise PlanError("K tensor_slices must contiguously cover [0, k)")

    tasks: list[TileTask] = []
    for index, item in enumerate(_plan_array(plan.get("tasks"), "tasks", 2)):
        name = f"tasks[{index}]"
        data = _plan_object(item, name)
        try:
            task = TileTask(**data)
        except (TypeError, ValueError) as error:
            raise PlanError(f"{name} is not a valid TileTask: {error}") from None
        tensor_slice = slices[index]
        if task.tile_id != workers[index]:
            raise PlanError(f"{name}.tile_id must match hardware.workers[{index}]")
        if task.tid != tid:
            raise PlanError(f"{name}.tid must match gather_config.tid")
        if task.opcode != "GEMV_LOCAL" or task.version != 1:
            raise PlanError(f"{name} must encode a GEMV_LOCAL version 1 task")
        if task.result_dst_tile != root_tile:
            raise PlanError(f"{name}.result_dst_tile must be the Root Tile")
        if task.result_kind != ("FINAL" if partition == "ROW" else "PSUM"):
            raise PlanError(f"{name}.result_kind does not match {partition} partitioning")
        if task.task_last is not True:
            raise PlanError(f"{name}.task_last must be true")
        if (task.w_base, task.x_base, task.y_base) != (W_START, X_START, Y_START):
            raise PlanError(f"{name} must use the canonical W/x/y bases")
        if not 1 <= task.m_local <= Y_END // 4:
            raise PlanError(f"{name}.m_local is outside v0.1 capacity")
        if not 1 <= task.k_local <= X_END - X_START:
            raise PlanError(f"{name}.k_local is outside v0.1 capacity")
        if 4 * ((task.m_local + 3) // 4) * task.k_local > W_END - W_START:
            raise PlanError(f"{name} weight footprint exceeds Weight SRAM")
        if 4 * task.m_local > Y_END - Y_START:
            raise PlanError(f"{name} result footprint exceeds Result SRAM")
        if (task.m_local, task.k_local) != (tensor_slice.row_end - tensor_slice.row_start,
                                            tensor_slice.k_end - tensor_slice.k_start):
            raise PlanError(f"{name} shape does not match tensor_slices[{index}]")
        first_row = tensor_slice.row_start if partition == "ROW" else 0
        if task.global_row_base != config.output_base + first_row:
            raise PlanError(f"{name}.global_row_base must start at gather_config.output_base")
        if task.global_row_base + task.m_local > 0x10000:
            raise PlanError(f"{name} output indices overflow the 16-bit global field")
        tasks.append(task)

    images: list[WeightImage] = []
    for index, item in enumerate(_plan_array(plan.get("weight_images"), "weight_images", 2)):
        name = f"weight_images[{index}]"
        data = _plan_object(item, name)
        tile = _plan_int(data.get("tile_id"), f"{name}.tile_id", 1, 3)
        if tile != workers[index]:
            raise PlanError(f"{name}.tile_id must match hardware.workers[{index}]")
        base = _plan_int(data.get("base_addr"), f"{name}.base_addr", 0, 0xFFFF)
        if base != W_START:
            raise PlanError(f"{name}.base_addr must be the canonical Weight base")
        words = _plan_array(data.get("words"), f"{name}.words")
        expected_words = ((tasks[index].m_local + 3) // 4) * tasks[index].k_local
        if len(words) != expected_words:
            raise PlanError(f"{name}.words must contain {expected_words} packed 32-bit words")
        for position, word in enumerate(words):
            _plan_int(word, f"{name}.words[{position}]", 0, 0xFFFF_FFFF)
        if base + 4 * len(words) > W_END:
            raise PlanError(f"{name} exceeds Weight SRAM")
        images.append(WeightImage(tile, base, tuple(words)))

    writes: list[MemWritePacket] = []
    covered = {tile: set() for tile in workers}
    local_k = {task.tile_id: task.k_local for task in tasks}
    ack_ids: set[tuple[int, int, int]] = set()
    for index, item in enumerate(_plan_array(plan.get("input_writes"), "input_writes")):
        name = f"input_writes[{index}]"
        data = _plan_object(item, name)
        try:
            packet = MemWritePacket(**data)
        except TypeError as error:
            raise PlanError(f"{name} is not a valid MemWritePacket: {error}") from None
        _plan_int(data.get("src_tile"), f"{name}.src_tile", 0, 3)
        _plan_int(data.get("dst_tile"), f"{name}.dst_tile", 0, 3)
        _plan_int(data.get("local_addr"), f"{name}.local_addr", 0, 0xFFFF)
        _plan_int(data.get("wstrb"), f"{name}.wstrb", 0, 15)
        _plan_int(data.get("sequence"), f"{name}.sequence", 0, 0xFFFF)
        _plan_int(data.get("wdata"), f"{name}.wdata", 0, 0xFFFF_FFFF)
        if packet.src_tile != 0:
            raise PlanError(f"{name}.src_tile must be the Host Tile 0")
        if packet.dst_tile not in workers:
            raise PlanError(f"{name}.dst_tile must be a worker Tile")
        if packet.tid != tid:
            raise PlanError(f"{name}.tid must match gather_config.tid")
        if packet.status != StatusCode.OK:
            raise PlanError(f"{name}.status must be OK")
        if packet.local_addr != X_START + 4 * packet.sequence:
            raise PlanError(f"{name}.local_addr must follow its sequence")
        if packet.local_addr + 4 > X_END:
            raise PlanError(f"{name} exceeds Input SRAM")
        if not 0 < packet.wstrb < 16:
            raise PlanError(f"{name}.wstrb must select between 1 and 4 lanes")
        key = (packet.dst_tile, packet.tid, packet.sequence)
        if key in ack_ids:
            raise PlanError(f"{name} repeats an ACK identity")
        ack_ids.add(key)
        written = covered[packet.dst_tile]
        for lane in range(4):
            if packet.wstrb & (1 << lane):
                offset = 4 * packet.sequence + lane
                if offset >= local_k[packet.dst_tile]:
                    raise PlanError(f"{name} writes beyond the local K")
                if offset in written:
                    raise PlanError(f"{name} writes the same input byte twice")
                written.add(offset)
        writes.append(packet)
    for tile in workers:
        if covered[tile] != set(range(local_k[tile])):
            raise PlanError(f"Tile {tile} input_writes do not exactly cover k_local bytes")

    if "expected_ack_keys" in plan:
        raw_keys = _plan_array(plan["expected_ack_keys"], "expected_ack_keys")
        expected = sorted([packet.dst_tile, packet.tid, packet.sequence] for packet in writes)
        normalized = []
        for index, key in enumerate(raw_keys):
            name = f"expected_ack_keys[{index}]"
            values = _plan_array(key, name, 3)
            normalized.append([_plan_int(values[0], f"{name}[0]", 0, 3),
                               _plan_int(values[1], f"{name}[1]", 0, 0xFFFF),
                               _plan_int(values[2], f"{name}[2]", 0, 0xFFFF)])
        if normalized != expected:
            raise PlanError("expected_ack_keys does not match input_writes")

    return ExecutionPlan(
        partition=partition, tid=tid, workers=workers, root_tile=root_tile,
        output_base=config.output_base, output_count=m, k=k,
        slices=tuple(slices), tasks=tuple(tasks), weight_images=tuple(images),
        input_writes=tuple(writes), config=config)


def load_plan(path: str | Path) -> ExecutionPlan:
    """Read and validate a MappingPlan JSON file."""
    try:
        mapping = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise PlanError(f"cannot read plan {path}: {error}") from None
    except json.JSONDecodeError as error:
        raise PlanError(f"plan {path} is not valid JSON: {error}") from None
    return plan_from_mapping(mapping)


def _execute_plan(
    plan: ExecutionPlan,
    endpoint_factory: Callable[[GatherConfig, Demo0Tile], ResultEndpoint],
    *, sink_ready: Callable[[int, int, str], bool] | None, max_cycles: int,
) -> NMCRun:
    """Shared Host/Compute/NI/Mesh loop for every validated ExecutionPlan."""
    tiles = [Demo0Tile(t) for t in range(4)]
    mesh = Mesh2x2()
    nis = [ResultNI(t) for t in range(4)]
    # Share the actual injection FIFO: no second queue bypasses NI capacity.
    for t in range(4):
        mesh.ni_injection[t] = nis[t].injection_fifo
    trace: list[dict] = []

    def record(event: str, **fields) -> None:
        trace.append({"network_cycle": mesh.cycle, "event": event, **fields})

    images = {image.tile_id: image for image in plan.weight_images}
    for tensor_slice in plan.slices:
        image = images[tensor_slice.tile_id]
        packed = b"".join(word.to_bytes(4, "little") for word in image.words)
        start = image.base_addr - W_START
        tiles[image.tile_id].weight_sram[start:start + len(packed)] = packed
        record("WEIGHTS_RESIDENT", tile=tensor_slice.tile_id,
               rows=[tensor_slice.row_start, tensor_slice.row_end],
               k=tensor_slice.k_end - tensor_slice.k_start,
               k_range=[tensor_slice.k_start, tensor_slice.k_end])

    tasks = list(plan.tasks)
    writes: deque[MemWritePacket] = deque(plan.input_writes)
    expected_acks = {(p.dst_tile, p.tid, p.sequence) for p in plan.input_writes}
    gather = endpoint_factory(plan.config, tiles[plan.root_tile])
    partition = plan.partition
    root_tile = plan.root_tile
    workers = plan.workers
    m = plan.output_count
    mode = plan.config.mode
    ack_keys: set[tuple[int, int, int]] = set()
    outstanding: tuple[int, int, int] | None = None
    # Pending Host commands/events are software queues, not hardware FIFO claims.
    pending = [deque() for _ in range(4)]
    seen = [0] * 4
    emitters: dict[int, ResultEmitter] = {}
    local_done: set[int] = set()
    host_done: list[TransactionDonePacket] = []
    phase = "LOAD"


    for _ in range(max_cycles):
        if phase == "LOAD" and outstanding is None and writes and mesh.can_inject(0, 2):
            packet = writes.popleft()
            mesh.inject_packet(packet)
            outstanding = (packet.dst_tile, packet.tid, packet.sequence)
            record("MEM_WRITE_SEND", **asdict(packet))
        if phase == "LOAD" and not writes and ack_keys == expected_acks:
            phase = "COMPUTE_AND_GATHER"
            for task in tasks:
                record("TASK_START", **asdict(task))
                beats, done = tiles[task.tile_id].execute(task)
                if done.status != "OK":
                    raise TransactionError(StatusCode[done.status], "invalid Compute task")
                emitters[task.tile_id] = ResultEmitter(beats, done)
        for tile, emitter in emitters.items():
            signals = emitter.signals()
            ready = nis[tile].result_ready
            if signals.result_valid and ready:
                beat = signals.result_payload
                assert beat is not None
                nis[tile].accept_result(beat)
                record("RESULTBEAT_ACCEPT", tile=tile, **asdict(beat))
            elif signals.result_valid:
                record("RESULTBEAT_STALL", tile=tile)
            if signals.local_done_valid:
                local_done.add(tile)
                record("LOCAL_DONE", tile=tile, **asdict(signals.local_done_payload))
            emitter.step(result_ready=ready)
        for tile in range(4):
            if pending[tile] and mesh.can_inject(tile, len(pack_packet(pending[tile][0]))):
                packet = pending[tile].popleft()
                mesh.inject_packet(packet)
                record("CONTROL_SEND", packet_type=type(packet).__name__, **asdict(packet))
            mesh.ejection_ready[tile] = True if sink_ready is None else sink_ready(mesh.cycle, tile, phase)
        mesh.step()
        for tile in range(4):
            for packet in mesh.received_packets[tile][seen[tile]:]:
                record("PACKET_RECEIVE", tile=tile, packet_type=type(packet).__name__, **asdict(packet))
                if isinstance(packet, MemWritePacket):
                    ack = commit_mem_write(tiles[tile], packet, idle=phase == "LOAD")
                    if ack is None:
                        raise RuntimeError("Host violated load-before-compute phase")
                    record("MEM_WRITE_COMMIT", tile=tile, sequence=packet.sequence, status=ack.status)
                    pending[tile].append(ack)
                elif isinstance(packet, MemAckPacket):
                    key = (packet.src_tile, packet.tid, packet.sequence)
                    if tile != 0 or key != outstanding or key in ack_keys:
                        raise RuntimeError("unexpected or duplicate MEM_ACK")
                    if packet.status != StatusCode.OK:
                        raise TransactionError(packet.status, "input loading failed")
                    ack_keys.add(key)
                    outstanding = None
                    record("HOST_ACK", tile=packet.src_tile, sequence=packet.sequence)
                elif isinstance(packet, ResultPacket):
                    done = gather.accept(packet)
                    record("FINAL_GATHER" if partition == "ROW" else "PSUM_REDUCE",
                           source=packet.src_tile, base_index=packet.base_index,
                           data=packet.data, lane_mask=packet.lane_mask,
                           collected=len(gather.values), last_mask=gather.last_mask)
                    if partition == "K":
                        # Endpoint exposes a per-index snapshot for learning/debugging.
                        trace[-1]["reduction_state"] = gather.snapshot(packet)  # type: ignore[attr-defined]
                    if done is not None:
                        pending[root_tile].append(done)
                        record("TRANSACTION_DONE_CREATE", **asdict(done))
                elif isinstance(packet, TransactionDonePacket):
                    if tile != 0 or packet != gather.done or host_done:
                        raise RuntimeError("invalid or repeated TRANSACTION_DONE")
                    host_done.append(packet)
                    record("HOST_TRANSACTION_DONE", **asdict(packet))
                else:
                    raise RuntimeError("unexpected packet in NMC demo")
            seen[tile] = len(mesh.received_packets[tile])
        if host_done and local_done == set(workers) and mesh.idle() and not any(pending):
            y = [tiles[root_tile].read_result(row) for row in range(m)]
            return NMCRun(tiles, mesh, gather, tasks, trace, host_done, ack_keys, y)
    raise TimeoutError(f"NMC {mode} did not complete in {max_cycles} network schedule cycles")


def _run_partitioned(
    matrix: list[list[int]] | None, vector: list[int] | None,
    *, tid: int, global_row_base: int, workers: tuple[int, int], root_tile: int,
    sink_ready: Callable[[int, int, str], bool] | None, max_cycles: int,
    partition: str, endpoint_factory: Callable[[GatherConfig, Demo0Tile], ResultEndpoint],
    k_split: int | None = None,
) -> NMCRun:
    """Build the Demo plan, then execute it through the shared plan loop."""
    plan = _build_demo_plan(
        matrix, vector, tid=tid, global_row_base=global_row_base, workers=workers,
        root_tile=root_tile, partition=partition, k_split=k_split)
    return _execute_plan(plan, endpoint_factory, sink_ready=sink_ready, max_cycles=max_cycles)


def run_plan(
    plan: ExecutionPlan, *,
    endpoint_factory: Callable[[GatherConfig, Demo0Tile], ResultEndpoint],
    expected_partition: str | None = None,
    sink_ready: Callable[[int, int, str], bool] | None = None,
    max_cycles: int = 10000,
) -> NMCRun:
    """Execute a validated Mapper plan without access to the original matrix."""
    if not isinstance(plan, ExecutionPlan):
        raise PlanError("run_plan requires a validated ExecutionPlan")
    if expected_partition is not None and plan.partition != expected_partition:
        raise PlanError(f"plan uses {plan.partition} partitioning; "
                        f"this runner requires {expected_partition}")
    return _execute_plan(plan, endpoint_factory, sink_ready=sink_ready, max_cycles=max_cycles)


def load_workload(path: Path) -> tuple[list[list[int]], list[int]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise PlanError(f"cannot read workload {path}: {error}") from None
    except json.JSONDecodeError as error:
        raise PlanError(f"workload {path} is not valid JSON: {error}") from None
    if not isinstance(payload, dict) or "W" not in payload or "x" not in payload:
        raise PlanError('workload must be a JSON object containing "W" and "x"')
    matrix, vector = payload["W"], payload["x"]
    if (not isinstance(matrix, list) or not matrix
            or not isinstance(vector, list) or not vector
            or any(not isinstance(row, list) or len(row) != len(vector) for row in matrix)
            or any(type(value) is not int or not -128 <= value <= 127
                   for value in vector + [value for row in matrix for value in row])):
        raise PlanError("workload must contain a rectangular signed-INT8 W and x")
    return matrix, vector


def _golden(matrix: list[list[int]], vector: list[int]) -> list[int]:
    return [int32_wrap(sum(a * b for a, b in zip(row, vector))) for row in matrix]


def emit_run(run: NMCRun, *, workload: tuple[list[list[int]], list[int]] | None,
              trace: Path | None) -> None:
    for event in run.trace:
        if event["event"] != "PACKET_RECEIVE":
            print(json.dumps(event, ensure_ascii=False))
    summary = run.summary()
    if workload is None:
        # A plan does not carry W/x, so numeric verification needs --workload.
        summary["golden"] = None
        summary["check"] = "EXECUTED (no --workload golden)"
    else:
        matrix, vector = workload
        if len(matrix) != len(run.y):
            raise PlanError("workload M does not match the executed plan")
        golden = _golden(matrix, vector)
        if run.y != golden:
            raise AssertionError(f"Root result {run.y} != golden {golden}")
        summary["golden"] = golden
        summary["check"] = "PASS"
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if trace:
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text(json.dumps({"summary": summary, "trace": run.trace}, indent=2,
                                    ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, help="optional JSON trace output")
    parser.add_argument("--plan", type=Path, help="execute a Mapper JSON plan instead of the built-in GEMV")
    parser.add_argument("--workload", type=Path, help='optional {"W","x"} JSON for the golden check')
    args = parser.parse_args()
    try:
        if args.plan is not None:
            plan = load_plan(args.plan)
            run = run_plan(plan, endpoint_factory=FinalGather, expected_partition="ROW")
            workload = None
            if args.workload is not None:
                workload = load_workload(args.workload)
                if (len(workload[0]), len(workload[1])) != (plan.output_count, plan.k):
                    raise PlanError("workload dimensions do not match the plan")
        else:
            if args.workload is not None:
                raise PlanError("--workload requires --plan")
            run = run_demo1()
            workload = (DEMO_W, DEMO_X)
    except PlanError as error:
        print(f"demo1: {error}", file=sys.stderr)
        raise SystemExit(2)
    emit_run(run, workload=workload, trace=args.trace)



if __name__ == "__main__":
    main()
