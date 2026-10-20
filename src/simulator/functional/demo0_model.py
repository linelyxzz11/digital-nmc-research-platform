"""Minimal executable interpretation of Compute Architecture v0.1.

The model is deliberately small.  It validates the architectural contract for
Demo 0; it is not the later cycle/performance simulator.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Literal


W_START, W_END = 0x0000, 0x1000
X_START, X_END = 0x1000, 0x1400
Y_START, Y_END = 0x2000, 0x2400
LANES = 4
TILE_COUNT = 4
UINT16_MAX = 0xFFFF

Status = Literal[
    "OK",
    "BAD_OPCODE",
    "BAD_FIELD",
    "BAD_SHAPE",
    "BAD_TILE",
    "ADDR_RANGE",
    "ADDR_ALIGN",
    "PROTOCOL_ERROR",
]


def int32_wrap(value: int) -> int:
    """Return the signed two's-complement INT32 interpretation of value."""
    value &= 0xFFFF_FFFF
    return value if value < 0x8000_0000 else value - 0x1_0000_0000


def _int8(value: int) -> int:
    if not -128 <= value <= 127:
        raise ValueError(f"{value} is not representable as signed INT8")
    return value


def _decode_int8(byte: int) -> int:
    return byte if byte < 128 else byte - 256


def _is_uint(value: object, bits: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value < (1 << bits)
    )


@dataclass(frozen=True)
class TileTask:
    tid: int
    tile_id: int
    m_local: int
    k_local: int
    w_base: int = 0x0000
    x_base: int = 0x1000
    y_base: int = 0x2000
    global_row_base: int = 0
    result_kind: Literal["FINAL", "PSUM"] = "FINAL"
    result_dst_tile: int = 0
    task_last: bool = True
    opcode: str = "GEMV_LOCAL"
    version: int = 1

    def __post_init__(self) -> None:
        encoded_fields = (
            ("version", self.version, 8),
            ("tid", self.tid, 16),
            ("tile_id", self.tile_id, 8),
            ("m_local", self.m_local, 16),
            ("k_local", self.k_local, 16),
            ("w_base", self.w_base, 16),
            ("x_base", self.x_base, 16),
            ("y_base", self.y_base, 16),
            ("global_row_base", self.global_row_base, 16),
            ("result_dst_tile", self.result_dst_tile, 8),
        )
        for name, value, bits in encoded_fields:
            if not _is_uint(value, bits):
                raise ValueError(f"{name} is not representable as unsigned {bits}-bit")
        if not isinstance(self.task_last, bool):
            raise ValueError("task_last must be boolean")


@dataclass(frozen=True)
class ResultBeat:
    tid: int
    dst_tile: int
    base_index: int
    lane_mask: int
    data: tuple[int, int, int, int]
    kind: Literal["FINAL", "PSUM"]
    last: bool


@dataclass(frozen=True)
class LocalDone:
    tid: int
    status: Status


class Demo0Tile:
    """Functional Tile with the three local SRAM address regions."""

    def __init__(self, physical_tile_id: int = 0) -> None:
        if not 0 <= physical_tile_id < TILE_COUNT:
            raise ValueError("physical_tile_id is outside the 2x2 Tile array")
        self.physical_tile_id = physical_tile_id
        self.weight_sram = bytearray(W_END - W_START)
        self.input_sram = bytearray(X_END - X_START)
        self.result_sram = bytearray(Y_END - Y_START)

    def load_weight_matrix(self, matrix: list[list[int]], base: int = W_START) -> None:
        """Pack row-major logical W into group-major 4-lane Weight Words."""
        if not matrix or not matrix[0]:
            raise ValueError("weight matrix must be non-empty")
        m_local, k_local = len(matrix), len(matrix[0])
        if any(len(row) != k_local for row in matrix):
            raise ValueError("all weight rows must have equal length")

        size = 4 * ceil(m_local / LANES) * k_local
        if base % 4 or not (W_START <= base and base + size <= W_END):
            raise ValueError("packed weights are misaligned or out of range")

        for group in range(ceil(m_local / LANES)):
            for k in range(k_local):
                word_addr = base + 4 * (group * k_local + k)
                for lane in range(LANES):
                    row = 4 * group + lane
                    value = 0 if row >= m_local else _int8(matrix[row][k])
                    self.weight_sram[word_addr + lane - W_START] = value & 0xFF

    def load_input_vector(self, vector: list[int], base: int = X_START) -> None:
        if not (X_START <= base and base + len(vector) <= X_END):
            raise ValueError("input vector is out of range")
        for offset, value in enumerate(vector):
            self.input_sram[base + offset - X_START] = _int8(value) & 0xFF

    def read_result(self, row: int, base: int = Y_START) -> int:
        address = base + 4 * row
        raw = self.result_sram[address - Y_START : address - Y_START + 4]
        return int.from_bytes(raw, byteorder="little", signed=True)

    def _validate(self, task: TileTask) -> Status:
        if task.opcode != "GEMV_LOCAL":
            return "BAD_OPCODE"
        if (
            task.version != 1
            or task.result_kind not in ("FINAL", "PSUM")
        ):
            return "BAD_FIELD"
        if not (1 <= task.m_local <= 256 and 1 <= task.k_local <= 1024):
            return "BAD_SHAPE"
        if (
            task.tile_id >= TILE_COUNT
            or task.tile_id != self.physical_tile_id
            or task.result_dst_tile >= TILE_COUNT
        ):
            return "BAD_TILE"
        if task.global_row_base + task.m_local - 1 > UINT16_MAX:
            return "BAD_FIELD"
        if task.w_base % 4 or task.y_base % 4:
            return "ADDR_ALIGN"

        weight_bytes = 4 * ceil(task.m_local / LANES) * task.k_local
        ranges_ok = (
            W_START <= task.w_base
            and task.w_base + weight_bytes <= W_END
            and X_START <= task.x_base
            and task.x_base + task.k_local <= X_END
            and Y_START <= task.y_base
            and task.y_base + 4 * task.m_local <= Y_END
        )
        return "OK" if ranges_ok else "ADDR_RANGE"

    def execute(self, task: TileTask) -> tuple[list[ResultBeat], LocalDone]:
        """Execute one task and return its architecturally visible events."""
        status = self._validate(task)
        if status != "OK":
            return [], LocalDone(task.tid, status)

        beats: list[ResultBeat] = []
        group_count = ceil(task.m_local / LANES)
        for group in range(group_count):
            valid_lanes = min(LANES, task.m_local - LANES * group)
            lane_mask = (1 << valid_lanes) - 1
            accumulators = [0] * LANES

            for k in range(task.k_local):
                x_addr = task.x_base + k
                x = _decode_int8(self.input_sram[x_addr - X_START])
                word_addr = task.w_base + 4 * (group * task.k_local + k)
                for lane in range(valid_lanes):
                    w = _decode_int8(self.weight_sram[word_addr + lane - W_START])
                    accumulators[lane] = int32_wrap(accumulators[lane] + w * x)

            for lane in range(valid_lanes):
                row = LANES * group + lane
                address = task.y_base + 4 * row
                encoded = accumulators[lane].to_bytes(4, "little", signed=True)
                start = address - Y_START
                self.result_sram[start : start + 4] = encoded

            beats.append(
                ResultBeat(
                    tid=task.tid,
                    dst_tile=task.result_dst_tile,
                    base_index=task.global_row_base + LANES * group,
                    lane_mask=lane_mask,
                    data=tuple(accumulators),
                    kind=task.result_kind,
                    last=task.task_last and group == group_count - 1,
                )
            )

        return beats, LocalDone(task.tid, "OK")


@dataclass(frozen=True)
class CycleSignals:
    result_valid: bool
    result_payload: ResultBeat | None
    local_done_valid: bool
    local_done_payload: LocalDone | None


class ResultEmitter:
    """Tiny ready/valid model used only to validate result backpressure."""

    def __init__(self, beats: list[ResultBeat], done: LocalDone) -> None:
        self._beats = beats
        self._done = done
        self._index = 0
        self._done_accepted = False

    def signals(self) -> CycleSignals:
        result_valid = self._index < len(self._beats)
        done_valid = self._index == len(self._beats) and not self._done_accepted
        return CycleSignals(
            result_valid=result_valid,
            result_payload=self._beats[self._index] if result_valid else None,
            local_done_valid=done_valid,
            local_done_payload=self._done if done_valid else None,
        )

    def step(self, *, result_ready: bool, local_done_ready: bool = True) -> CycleSignals:
        """Return pre-edge signals, then apply handshakes at the cycle edge."""
        before_edge = self.signals()
        if before_edge.result_valid and result_ready:
            self._index += 1
        elif before_edge.local_done_valid and local_done_ready:
            self._done_accepted = True
        return before_edge
