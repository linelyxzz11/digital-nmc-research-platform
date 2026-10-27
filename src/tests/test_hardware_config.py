"""Verify the shared hardware configuration and its derived constants."""
from __future__ import annotations

from dataclasses import replace

import pytest

from hardware import DEFAULT_HARDWARE, HardwareConfig, MemoryRegion


def test_default_configuration_describes_v0_1():
    hardware = DEFAULT_HARDWARE
    assert (hardware.mesh_x, hardware.mesh_y, hardware.tile_count) == (2, 2, 4)
    assert hardware.host_tile == 0
    assert hardware.lanes_per_tile == 4
    assert (hardware.weight.base, hardware.weight.size) == (0x0000, 0x1000)
    assert (hardware.input.base, hardware.input.size) == (0x1000, 0x0400)
    assert (hardware.result.base, hardware.result.size) == (0x2000, 0x0400)
    assert hardware.max_local_rows == 256
    assert hardware.max_local_k == 1024
    assert hardware.global_index_limit == 0x10000
    assert hardware.banks_per_tile > 0 and hardware.bank_width_bytes > 0


def test_describe_reports_every_plan_field():
    described = DEFAULT_HARDWARE.describe()
    expected = {"mesh_x", "mesh_y", "tile_count", "host_tile", "lanes_per_tile",
                "weight_base", "weight_sram_bytes", "input_base", "input_sram_bytes",
                "result_base", "result_sram_bytes", "banks_per_tile", "bank_width_bytes",
                "flit_data_width", "router_fifo_depth", "ni_fifo_depth",
                "global_index_width"}
    assert set(described) == expected
    assert described["weight_base"] + described["weight_sram_bytes"] <= described["input_base"]


def test_modules_derive_constants_from_the_configuration():
    from compiler import auto_mapper_v0_1 as mapper
    from simulator.communication import v0_1 as communication
    from simulator.functional import demo0_model as model

    hardware = DEFAULT_HARDWARE
    assert (model.W_START, model.W_END) == (hardware.weight.base,
                                            hardware.weight.base + hardware.weight.size)
    assert (model.X_START, model.X_END) == (hardware.input.base,
                                            hardware.input.base + hardware.input.size)
    assert (model.Y_START, model.Y_END) == (hardware.result.base,
                                            hardware.result.base + hardware.result.size)
    assert model.LANES == hardware.lanes_per_tile
    assert model.TILE_COUNT == hardware.tile_count
    assert (communication.MESH_X, communication.MESH_Y) == (hardware.mesh_x, hardware.mesh_y)
    assert communication.TILE_COUNT == hardware.tile_count
    assert communication.INPUT_FIFO_DEPTH == hardware.router_fifo_depth
    assert communication.NI_FIFO_DEPTH == hardware.ni_fifo_depth
    assert communication.FLIT_DATA_W == hardware.flit_data_width
    assert (mapper.W_BASE, mapper.X_BASE, mapper.Y_BASE) == (
        hardware.weight.base, hardware.input.base, hardware.result.base)
    assert mapper.MAX_M == hardware.max_local_rows
    assert mapper.MAX_K == hardware.max_local_k


@pytest.mark.parametrize("factory", [
    lambda: replace(DEFAULT_HARDWARE, host_tile=4),
    lambda: replace(DEFAULT_HARDWARE, lanes_per_tile=0),
    lambda: replace(DEFAULT_HARDWARE, weight=MemoryRegion(0x0000, 0x1100)),
    lambda: replace(DEFAULT_HARDWARE, result=MemoryRegion(0x2002, 0x0400)),
    lambda: replace(DEFAULT_HARDWARE, banks_per_tile=0),
    lambda: replace(DEFAULT_HARDWARE, global_index_width=0),
])
def test_invalid_configurations_are_rejected(factory):
    with pytest.raises(ValueError):
        factory()


def test_memory_region_requires_positive_size():
    with pytest.raises(ValueError):
        MemoryRegion(0, 0)
    with pytest.raises(ValueError):
        MemoryRegion(-4, 16)
