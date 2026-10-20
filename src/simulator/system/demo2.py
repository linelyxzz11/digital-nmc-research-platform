"""Demo 2: K partition, network-scattered inputs and endpoint INT32 PSUM reduction.

Uses the same Host/Compute/NI/Mesh loop as Demo 1. Compute and reduction are
functional; mesh cycles are not a prediction of total GEMV latency.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from simulator.communication import ResultPacket, StatusCode, TransactionDonePacket
from simulator.functional import Demo0Tile, int32_wrap
from simulator.system.demo1 import (
    DEMO_W, DEMO_X, FinalGather, GatherConfig, NMCRun, TransactionError, _run_partitioned,
)


class PSumReduce(FinalGather):
    """One transaction's endpoint Reduction Buffer, keyed by output and source."""

    def __init__(self, config: GatherConfig, root: Demo0Tile):
        if config.mode != "REDUCE_PSUM":
            raise ValueError("PSumReduce requires REDUCE_PSUM config")
        super().__init__(config, root)
        self.accumulators: dict[int, int] = {}
        self.received_source_masks: dict[int, int] = {}
        self.psum_elements = 0
        self.duplicate_errors = 0

    def accept(self, packet: ResultPacket) -> TransactionDonePacket | None:
        c = self.config
        if self.error is not None:
            raise TransactionError(self.error, "transaction already terminated")
        if self.done is not None:
            self._fail(StatusCode.PROTOCOL_ERROR, "PSUM after global completion")
        if packet.tid != c.tid or packet.dst_tile != c.root_tile:
            self._fail(StatusCode.CONFIG_MISS, "tid or destination mismatch")
        if packet.status != StatusCode.OK:
            self._fail(StatusCode.PROTOCOL_ERROR, "RESULT has error status")
        if packet.kind != "PSUM":
            self._fail(StatusCode.KIND_MISMATCH, "REDUCE_PSUM requires PSUM")
        if not 0 <= packet.src_tile < 4 or not c.source_mask & (1 << packet.src_tile):
            self._fail(StatusCode.BAD_TILE, "unexpected source")
        if not 0 < packet.lane_mask < 16 or len(packet.data) != 4:
            self._fail(StatusCode.BAD_FIELD, "invalid lanes")
        source_bit = 1 << packet.src_tile
        elements = [(packet.base_index + i, packet.data[i])
                    for i in range(4) if packet.lane_mask & (1 << i)]
        # Validate all lanes before any accumulator, mask, SRAM or last update.
        for index, value in elements:
            if not c.output_base <= index < c.output_base + c.output_count:
                self._fail(StatusCode.BAD_FIELD, "output index outside config")
            if self.received_source_masks.get(index, 0) & source_bit:
                self.duplicate_errors += 1
                self._fail(StatusCode.DUPLICATE_RESULT, "same source supplied this index twice")
            if not isinstance(value, int) or not -(1 << 31) <= value < (1 << 31):
                self._fail(StatusCode.BAD_FIELD, "PSUM outside signed INT32")
        if self.last_mask & source_bit:
            self._fail(StatusCode.PROTOCOL_ERROR, "PSUM after source last")

        for index, value in elements:
            acc = int32_wrap(self.accumulators.get(index, 0) + value)
            mask = self.received_source_masks.get(index, 0) | source_bit
            self.accumulators[index] = acc
            self.received_source_masks[index] = mask
            self.psum_elements += 1
            if mask == c.source_mask:
                self.values[index] = acc
                row = index - c.output_base
                self.root.result_sram[4 * row:4 * row + 4] = acc.to_bytes(4, "little", signed=True)
        if packet.last:
            self.last_mask |= source_bit
        if len(self.values) == c.output_count and self.last_mask == c.source_mask:
            self.done = TransactionDonePacket(c.root_tile, c.host_tile, c.tid,
                                              c.output_count, c.source_mask)
            return self.done
        return None

    def snapshot(self, packet: ResultPacket) -> list[dict]:
        return [{"index": packet.base_index + lane,
                 "acc32": self.accumulators[packet.base_index + lane],
                 "received_source_mask": self.received_source_masks[packet.base_index + lane],
                 "complete": packet.base_index + lane in self.values}
                for lane in range(4) if packet.lane_mask & (1 << lane)]


def run_demo2(
    matrix: list[list[int]] | None = None, vector: list[int] | None = None,
    *, tid: int = 2, global_row_base: int = 0,
    workers: tuple[int, int] = (1, 2), root_tile: int = 3,
    k_split: int | None = None,
    sink_ready: Callable[[int, int, str], bool] | None = None,
    max_cycles: int = 10000,
) -> NMCRun:
    """Split W columns and x into two nonempty contiguous K partitions."""
    return _run_partitioned(matrix, vector, tid=tid, global_row_base=global_row_base,
                            workers=workers, root_tile=root_tile, sink_ready=sink_ready,
                            max_cycles=max_cycles, partition="K", endpoint_factory=PSumReduce,
                            k_split=k_split)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, help="optional JSON trace output")
    parser.add_argument("--k-split", type=int, help="K split position; default ceil(K/2)")
    args = parser.parse_args()
    run = run_demo2(k_split=args.k_split)
    for event in run.trace:
        if event["event"] != "PACKET_RECEIVE":
            print(json.dumps(event, ensure_ascii=False))
    import numpy as np
    golden = (np.asarray(DEMO_W, dtype=np.int64) @ np.asarray(DEMO_X, dtype=np.int64)).tolist()
    if run.y != golden:
        raise AssertionError(f"Root result {run.y} != NumPy Golden {golden}")
    summary = {**run.summary(), "golden": golden, "check": "PASS"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.trace:
        args.trace.parent.mkdir(parents=True, exist_ok=True)
        args.trace.write_text(json.dumps({"summary": summary, "trace": run.trace}, indent=2,
                                         ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
