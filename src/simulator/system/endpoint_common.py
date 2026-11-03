"""Shared transaction contract for functional and clocked Root endpoints."""
from dataclasses import dataclass
from hardware import DEFAULT_HARDWARE
from simulator.communication import StatusCode


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

    def __post_init__(self):
        if not (0 <= self.tid <= 0xFFFF
                and 0 <= self.root_tile < DEFAULT_HARDWARE.tile_count
                and 0 <= self.host_tile < DEFAULT_HARDWARE.tile_count
                and 0 < self.source_mask < (1 << DEFAULT_HARDWARE.tile_count)
                and 0 <= self.output_base < DEFAULT_HARDWARE.global_index_limit
                and 1 <= self.output_count <= DEFAULT_HARDWARE.max_local_rows
                and self.output_base + self.output_count <= DEFAULT_HARDWARE.global_index_limit
                and self.mode in ("GATHER_FINAL", "REDUCE_PSUM")):
            raise ValueError("invalid TransactionConfig")
