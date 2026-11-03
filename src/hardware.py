"""Single source of truth for the Digital NMC v0.1 hardware resources.

The mapper, the functional Tile model, the NoC model and the plan validator
all read this configuration instead of repeating address and capacity
literals. Bank fields describe resources only; they are not yet scheduled.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryRegion:
    base: int
    size: int

    def __post_init__(self) -> None:
        if self.base < 0 or self.size <= 0:
            raise ValueError("memory region needs a nonnegative base and a positive size")


@dataclass(frozen=True)
class HardwareConfig:
    mesh_x: int = 2
    mesh_y: int = 2
    host_tile: int = 0
    lanes_per_tile: int = 4
    weight: MemoryRegion = MemoryRegion(0x0000, 0x1000)
    input: MemoryRegion = MemoryRegion(0x1000, 0x0400)
    result: MemoryRegion = MemoryRegion(0x2000, 0x0400)
    banks_per_tile: int = 1
    bank_width_bytes: int = 4
    flit_data_width: int = 128
    router_fifo_depth: int = 4
    ni_fifo_depth: int = 4
    global_index_width: int = 16

    def __post_init__(self) -> None:
        if self.mesh_x <= 0 or self.mesh_y <= 0:
            raise ValueError("mesh dimensions must be positive")
        if not 0 <= self.host_tile < self.tile_count:
            raise ValueError("host_tile is outside the mesh")
        if self.lanes_per_tile <= 0:
            raise ValueError("lanes_per_tile must be positive")
        if self.weight.base % 4 or self.result.base % 4:
            raise ValueError("weight and result regions must be 4-byte aligned")
        regions = (("weight", self.weight), ("input", self.input), ("result", self.result))
        for index, (name, region) in enumerate(regions):
            for other_name, other in regions[index + 1:]:
                if region.base < other.base + other.size and other.base < region.base + region.size:
                    raise ValueError(f"{name} and {other_name} memory regions overlap")
        if self.banks_per_tile <= 0 or self.bank_width_bytes <= 0:
            raise ValueError("bank resources must be positive")
        if self.flit_data_width <= 0 or self.router_fifo_depth <= 0 or self.ni_fifo_depth <= 0:
            raise ValueError("network resources must be positive")
        if not 1 <= self.global_index_width <= 32:
            raise ValueError("global_index_width must be in [1, 32]")

    @property
    def tile_count(self) -> int:
        return self.mesh_x * self.mesh_y

    @property
    def max_local_rows(self) -> int:
        return self.result.size // 4

    @property
    def max_local_k(self) -> int:
        return self.input.size

    @property
    def global_index_limit(self) -> int:
        return 1 << self.global_index_width

    def describe(self) -> dict[str, int]:
        return {
            "mesh_x": self.mesh_x,
            "mesh_y": self.mesh_y,
            "tile_count": self.tile_count,
            "host_tile": self.host_tile,
            "lanes_per_tile": self.lanes_per_tile,
            "weight_base": self.weight.base,
            "weight_sram_bytes": self.weight.size,
            "input_base": self.input.base,
            "input_sram_bytes": self.input.size,
            "result_base": self.result.base,
            "result_sram_bytes": self.result.size,
            "banks_per_tile": self.banks_per_tile,
            "bank_width_bytes": self.bank_width_bytes,
            "flit_data_width": self.flit_data_width,
            "router_fifo_depth": self.router_fifo_depth,
            "ni_fifo_depth": self.ni_fifo_depth,
            "global_index_width": self.global_index_width,
        }


DEFAULT_HARDWARE = HardwareConfig()
