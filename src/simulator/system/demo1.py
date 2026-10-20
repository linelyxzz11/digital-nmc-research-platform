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
from typing import Callable, Protocol

from simulator.communication import (
    MemAckPacket, MemWritePacket, Mesh2x2, ResultNI, ResultPacket,
    StatusCode, TransactionDonePacket, pack_packet,
)
from simulator.functional import Demo0Tile, ResultEmitter, TileTask
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


def _run_partitioned(
    matrix: list[list[int]] | None, vector: list[int] | None,
    *, tid: int, global_row_base: int, workers: tuple[int, int], root_tile: int,
    sink_ready: Callable[[int, int, str], bool] | None, max_cycles: int,
    partition: str, endpoint_factory: Callable[[GatherConfig, Demo0Tile], ResultEndpoint],
    k_split: int | None = None,
) -> NMCRun:
    """Shared Host/Compute/NI/Mesh loop for row and K partitioning."""
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
    tiles = [Demo0Tile(t) for t in range(4)]
    mesh = Mesh2x2()
    nis = [ResultNI(t) for t in range(4)]
    # Share the actual injection FIFO: no second queue bypasses NI capacity.
    for t in range(4):
        mesh.ni_injection[t] = nis[t].injection_fifo
    trace: list[dict] = []

    def record(event: str, **fields) -> None:
        trace.append({"network_cycle": mesh.cycle, "event": event, **fields})

    if partition == "ROW":
        split = (m + 1) // 2
    else:
        split = (k + 1) // 2 if k_split is None else k_split
    dimension = m if partition == "ROW" else k
    if not isinstance(split, int) or isinstance(split, bool) or not 1 <= split < dimension:
        raise ValueError("split must leave two nonempty partitions")
    inputs: dict[int, list[int]] = {}
    tasks: list[TileTask] = []
    for tile, begin, end in ((workers[0], 0, split), (workers[1], split, dimension)):
        local_w = matrix[begin:end] if partition == "ROW" else [row[begin:end] for row in matrix]
        inputs[tile] = vector if partition == "ROW" else vector[begin:end]
        tiles[tile].load_weight_matrix(local_w)
        local_m, local_k = len(local_w), len(inputs[tile])
        if local_k > 1024:
            raise ValueError("local K exceeds Input SRAM capacity")
        tasks.append(TileTask(tid, tile, local_m, local_k,
                              global_row_base=global_row_base + begin if partition == "ROW" else global_row_base,
                              result_dst_tile=root_tile, result_kind="FINAL" if partition == "ROW" else "PSUM"))
        record("WEIGHTS_RESIDENT", tile=tile, rows=[begin, end] if partition == "ROW" else [0, m],
               k=local_k, k_range=[0, k] if partition == "ROW" else [begin, end])

    gather = endpoint_factory(config, tiles[root_tile])
    writes: deque[MemWritePacket] = deque()
    for seq, offset in enumerate(range(0, max(len(v) for v in inputs.values()), 4)):
        for tile in workers:
            chunk = inputs[tile][offset:offset + 4]
            if not chunk:
                continue
            word = sum((value & 0xFF) << (8 * i) for i, value in enumerate(chunk))
            writes.append(MemWritePacket(0, tile, tid, X_START + offset,
                                         (1 << len(chunk)) - 1, seq, word))
    expected_acks = {(p.dst_tile, p.tid, p.sequence) for p in writes}
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, help="optional JSON trace output")
    args = parser.parse_args()
    run = run_demo1()
    for event in run.trace:
        if event["event"] != "PACKET_RECEIVE":
            print(json.dumps(event, ensure_ascii=False))
    summary = run.summary()
    # Golden belongs to the verifier/Host, never supplies Root result values.
    import numpy as np
    golden = (np.asarray(DEMO_W, dtype=np.int64) @ np.asarray(DEMO_X, dtype=np.int64)).tolist()
    if run.y != golden:
        raise AssertionError(f"Root result {run.y} != NumPy Golden {golden}")
    print(json.dumps({**summary, "golden": golden, "check": "PASS"}, indent=2, ensure_ascii=False))
    if args.trace:
        args.trace.parent.mkdir(parents=True, exist_ok=True)
        args.trace.write_text(json.dumps({"summary": summary, "trace": run.trace}, indent=2,
                                         ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
