"""Executable subset of Digital NMC Communication Architecture v0.1.

The model fixes packet bit layout, ResultBeat packetization, deterministic XY
routing, finite router input FIFOs, wormhole output locks, and valid/ready
backpressure.  It is intentionally small enough to inspect cycle by cycle.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable, TypeAlias

from simulator.functional.demo0_model import ResultBeat


FLIT_DATA_W = 128
FLIT_MASK = (1 << FLIT_DATA_W) - 1
MESH_X = 2
MESH_Y = 2
TILE_COUNT = 4
INPUT_FIFO_DEPTH = 4
NI_FIFO_DEPTH = 4
PORTS = ("NORTH", "EAST", "SOUTH", "WEST", "LOCAL")
INPUT_ORDER = PORTS


class PacketFormatError(ValueError):
    """A packet violates the v0.1 wire contract."""


class FlitType(IntEnum):
    HEAD = 0
    BODY = 1
    TAIL = 2
    HEADTAIL = 3


class PacketType(IntEnum):
    RESULT = 0x1
    MEM_WRITE = 0x2
    MEM_ACK = 0x3
    TRANSACTION_DONE = 0x4
    ERROR = 0xF


class StatusCode(IntEnum):
    OK = 0
    BAD_OPCODE = 1
    BAD_FIELD = 2
    BAD_SHAPE = 3
    BAD_TILE = 4
    ADDR_RANGE = 5
    ADDR_ALIGN = 6
    PROTOCOL_ERROR = 7
    BAD_PACKET = 8
    CONFIG_MISS = 9
    DUPLICATE_RESULT = 10
    KIND_MISMATCH = 11
    MEM_ERROR = 12


@dataclass(frozen=True)
class Flit:
    type: FlitType
    data: int

    def __post_init__(self) -> None:
        if not isinstance(self.type, FlitType):
            raise TypeError("type must be FlitType")
        if not isinstance(self.data, int) or not 0 <= self.data <= FLIT_MASK:
            raise ValueError("flit data must be unsigned 128-bit")


@dataclass(frozen=True)
class ResultPacket:
    src_tile: int
    dst_tile: int
    tid: int
    base_index: int
    lane_mask: int
    data: tuple[int, int, int, int]
    kind: str
    last: bool
    status: StatusCode = StatusCode.OK


@dataclass(frozen=True)
class MemWritePacket:
    src_tile: int
    dst_tile: int
    tid: int
    local_addr: int
    wstrb: int
    sequence: int
    wdata: int
    status: StatusCode = StatusCode.OK


@dataclass(frozen=True)
class MemAckPacket:
    src_tile: int
    dst_tile: int
    tid: int
    sequence: int
    status: StatusCode = StatusCode.OK


@dataclass(frozen=True)
class TransactionDonePacket:
    src_tile: int
    dst_tile: int
    tid: int
    completed_element_count: int
    completed_source_mask: int
    status: StatusCode = StatusCode.OK


@dataclass(frozen=True)
class ErrorPacket:
    src_tile: int
    dst_tile: int
    tid: int
    status: StatusCode
    error_detail: int = 0


Packet: TypeAlias = (
    ResultPacket
    | MemWritePacket
    | MemAckPacket
    | TransactionDonePacket
    | ErrorPacket
)


def _uint(name: str, value: int, bits: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < (1 << bits):
        raise ValueError(f"{name} must be unsigned {bits}-bit")
    return value


def _tile(name: str, value: int) -> int:
    _uint(name, value, 8)
    if value >= TILE_COUNT:
        raise ValueError(f"{name} is outside the 2x2 mesh")
    return value


def _status(value: StatusCode | int) -> StatusCode:
    try:
        return StatusCode(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("unknown status code") from exc


def _u32(value: int) -> int:
    if not isinstance(value, int) or not -(1 << 31) <= value < (1 << 32):
        raise ValueError("payload lane must fit signed or unsigned 32-bit")
    return value & 0xFFFF_FFFF


def _s32(value: int) -> int:
    value &= 0xFFFF_FFFF
    return value if value < 0x8000_0000 else value - 0x1_0000_0000


def _common_header(
    packet_type: PacketType,
    src_tile: int,
    dst_tile: int,
    tid: int,
    status: StatusCode,
) -> int:
    src_tile = _tile("src_tile", src_tile)
    dst_tile = _tile("dst_tile", dst_tile)
    tid = _uint("tid", tid, 16)
    status = _status(status)
    return (
        int(packet_type)
        | (src_tile << 4)
        | (dst_tile << 12)
        | (tid << 20)
        | (int(status) << 36)
    )


def pack_packet(packet: Packet) -> tuple[Flit, ...]:
    """Pack one typed packet into the exact v0.1 flit sequence."""
    if isinstance(packet, ResultPacket):
        if packet.status is not StatusCode.OK:
            raise ValueError("RESULT status must be OK")
        base = _uint("base_index", packet.base_index, 16)
        mask = _uint("lane_mask", packet.lane_mask, 4)
        if mask == 0:
            raise ValueError("RESULT lane_mask must be nonzero")
        if packet.kind not in ("FINAL", "PSUM"):
            raise ValueError("RESULT kind must be FINAL or PSUM")
        if not isinstance(packet.last, bool):
            raise ValueError("RESULT last must be boolean")
        if len(packet.data) != 4:
            raise ValueError("RESULT needs four lanes")
        for lane, value in enumerate(packet.data):
            if not (mask & (1 << lane)) and value != 0:
                raise ValueError("invalid RESULT lanes must contain zero")
        header = _common_header(
            PacketType.RESULT, packet.src_tile, packet.dst_tile, packet.tid, packet.status
        )
        header |= base << 44
        header |= mask << 60
        header |= (packet.kind == "PSUM") << 64
        header |= int(packet.last) << 65
        payload = sum(_u32(value) << (32 * lane) for lane, value in enumerate(packet.data))
        return (Flit(FlitType.HEAD, header), Flit(FlitType.TAIL, payload))

    if isinstance(packet, MemWritePacket):
        if packet.status is not StatusCode.OK:
            raise ValueError("MEM_WRITE status must be OK")
        header = _common_header(
            PacketType.MEM_WRITE, packet.src_tile, packet.dst_tile, packet.tid, packet.status
        )
        header |= _uint("local_addr", packet.local_addr, 16) << 44
        header |= _uint("wstrb", packet.wstrb, 4) << 60
        header |= _uint("sequence", packet.sequence, 16) << 64
        return (
            Flit(FlitType.HEAD, header),
            Flit(FlitType.TAIL, _uint("wdata", packet.wdata, 32)),
        )

    if isinstance(packet, MemAckPacket):
        header = _common_header(
            PacketType.MEM_ACK, packet.src_tile, packet.dst_tile, packet.tid, packet.status
        )
        header |= _uint("sequence", packet.sequence, 16) << 44
        return (Flit(FlitType.HEADTAIL, header),)

    if isinstance(packet, TransactionDonePacket):
        if packet.status is not StatusCode.OK:
            raise ValueError("TRANSACTION_DONE status must be OK")
        header = _common_header(
            PacketType.TRANSACTION_DONE,
            packet.src_tile,
            packet.dst_tile,
            packet.tid,
            packet.status,
        )
        header |= _uint("completed_element_count", packet.completed_element_count, 16) << 44
        header |= _uint("completed_source_mask", packet.completed_source_mask, 4) << 60
        return (Flit(FlitType.HEADTAIL, header),)

    if isinstance(packet, ErrorPacket):
        status = _status(packet.status)
        if status is StatusCode.OK:
            raise ValueError("ERROR status must be nonzero")
        header = _common_header(
            PacketType.ERROR, packet.src_tile, packet.dst_tile, packet.tid, status
        )
        header |= _uint("error_detail", packet.error_detail, 16) << 44
        return (Flit(FlitType.HEADTAIL, header),)

    raise TypeError(f"unsupported packet type: {type(packet).__name__}")


def unpack_packet(flits: Iterable[Flit]) -> Packet:
    """Validate and unpack one complete v0.1 packet."""
    seq = tuple(flits)
    if not seq:
        raise PacketFormatError("packet is empty")
    header = seq[0]
    try:
        packet_type = PacketType(header.data & 0xF)
        status = StatusCode((header.data >> 36) & 0xFF)
    except ValueError as exc:
        raise PacketFormatError("unknown packet or status code") from exc
    src = (header.data >> 4) & 0xFF
    dst = (header.data >> 12) & 0xFF
    tid = (header.data >> 20) & 0xFFFF
    if src >= TILE_COUNT or dst >= TILE_COUNT:
        raise PacketFormatError("source or destination is outside the 2x2 mesh")

    two_flit = packet_type in (PacketType.RESULT, PacketType.MEM_WRITE)
    expected_types = (
        (FlitType.HEAD, FlitType.TAIL)
        if two_flit
        else (FlitType.HEADTAIL,)
    )
    if len(seq) != len(expected_types) or tuple(f.type for f in seq) != expected_types:
        raise PacketFormatError("packet length or flit-type sequence is invalid")

    if packet_type is PacketType.RESULT:
        if status is not StatusCode.OK or header.data >> 66:
            raise PacketFormatError("RESULT status/reserved bits are invalid")
        base = (header.data >> 44) & 0xFFFF
        mask = (header.data >> 60) & 0xF
        if mask == 0:
            raise PacketFormatError("RESULT lane_mask is zero")
        kind = "PSUM" if (header.data >> 64) & 1 else "FINAL"
        last = bool((header.data >> 65) & 1)
        data = tuple(_s32((seq[1].data >> (32 * lane)) & 0xFFFF_FFFF) for lane in range(4))
        if any(not (mask & (1 << lane)) and data[lane] != 0 for lane in range(4)):
            raise PacketFormatError("invalid RESULT lane is nonzero")
        return ResultPacket(src, dst, tid, base, mask, data, kind, last, status)

    if packet_type is PacketType.MEM_WRITE:
        if status is not StatusCode.OK or header.data >> 80 or seq[1].data >> 32:
            raise PacketFormatError("MEM_WRITE status/reserved bits are invalid")
        return MemWritePacket(
            src,
            dst,
            tid,
            (header.data >> 44) & 0xFFFF,
            (header.data >> 60) & 0xF,
            (header.data >> 64) & 0xFFFF,
            seq[1].data & 0xFFFF_FFFF,
            status,
        )

    if packet_type is PacketType.MEM_ACK:
        if header.data >> 60:
            raise PacketFormatError("MEM_ACK reserved bits are nonzero")
        return MemAckPacket(src, dst, tid, (header.data >> 44) & 0xFFFF, status)

    if packet_type is PacketType.TRANSACTION_DONE:
        if status is not StatusCode.OK or header.data >> 64:
            raise PacketFormatError("TRANSACTION_DONE status/reserved bits are invalid")
        return TransactionDonePacket(
            src,
            dst,
            tid,
            (header.data >> 44) & 0xFFFF,
            (header.data >> 60) & 0xF,
            status,
        )

    if packet_type is PacketType.ERROR:
        if status is StatusCode.OK or header.data >> 60:
            raise PacketFormatError("ERROR status/reserved bits are invalid")
        return ErrorPacket(src, dst, tid, status, (header.data >> 44) & 0xFFFF)

    raise PacketFormatError("unsupported packet type")


class ResultNI:
    """Compute-side NI injection FIFO and ResultBeat packetizer."""

    def __init__(self, tile_id: int, depth: int = NI_FIFO_DEPTH) -> None:
        self.tile_id = _tile("tile_id", tile_id)
        if depth < 2:
            raise ValueError("NI FIFO must hold an atomic two-flit RESULT")
        self.depth = depth
        self.injection_fifo: deque[Flit] = deque()
        self.accepted_results = 0
        self.inject_stall_cycles = 0

    @property
    def free_slots(self) -> int:
        return self.depth - len(self.injection_fifo)

    @property
    def result_ready(self) -> bool:
        return self.free_slots >= 2

    def accept_result(self, beat: ResultBeat) -> tuple[Flit, Flit]:
        if not self.result_ready:
            raise BufferError("ResultBeat must not be accepted without two free slots")
        packet = ResultPacket(
            src_tile=self.tile_id,
            dst_tile=beat.dst_tile,
            tid=beat.tid,
            base_index=beat.base_index,
            lane_mask=beat.lane_mask,
            data=beat.data,
            kind=beat.kind,
            last=beat.last,
        )
        head, tail = pack_packet(packet)
        self.injection_fifo.extend((head, tail))
        self.accepted_results += 1
        return head, tail


def _coords(tile: int) -> tuple[int, int]:
    _tile("tile", tile)
    return tile % MESH_X, tile // MESH_X


def _opposite(port: str) -> str:
    return {"NORTH": "SOUTH", "SOUTH": "NORTH", "EAST": "WEST", "WEST": "EAST"}[port]


def _route(current_tile: int, destination: int) -> str:
    x, y = _coords(current_tile)
    dx, dy = _coords(destination)
    if dx > x:
        return "EAST"
    if dx < x:
        return "WEST"
    if dy > y:
        return "SOUTH"
    if dy < y:
        return "NORTH"
    return "LOCAL"


def _neighbor(tile: int, output: str) -> tuple[int, str]:
    x, y = _coords(tile)
    if output == "EAST":
        x += 1
    elif output == "WEST":
        x -= 1
    elif output == "SOUTH":
        y += 1
    elif output == "NORTH":
        y -= 1
    else:
        raise ValueError("LOCAL has no neighboring router")
    if not (0 <= x < MESH_X and 0 <= y < MESH_Y):
        raise PacketFormatError("route leaves the mesh")
    return y * MESH_X + x, _opposite(output)


class _Router:
    def __init__(self, tile_id: int) -> None:
        self.tile_id = tile_id
        self.inputs = {port: deque() for port in PORTS}
        self.output_locks: dict[str, str | None] = {port: None for port in PORTS}
        self.held_grants: dict[str, str | None] = {port: None for port in PORTS}
        self.rr_next = {port: 0 for port in PORTS}

    def desired_output(self, input_port: str) -> str | None:
        queue = self.inputs[input_port]
        if not queue:
            return None
        flit = queue[0]
        for output, locked_input in self.output_locks.items():
            if locked_input == input_port:
                return output
        if flit.type not in (FlitType.HEAD, FlitType.HEADTAIL):
            raise PacketFormatError("BODY/TAIL reached an unlocked router input")
        destination = (flit.data >> 12) & 0xFF
        return _route(self.tile_id, destination)

    def select(self, output: str) -> str | None:
        held = self.held_grants[output]
        if held is not None:
            return held
        locked = self.output_locks[output]
        if locked is not None:
            return locked if self.inputs[locked] else None
        requests = [p for p in INPUT_ORDER if self.desired_output(p) == output]
        if not requests:
            return None
        start = self.rr_next[output]
        for offset in range(len(INPUT_ORDER)):
            candidate = INPUT_ORDER[(start + offset) % len(INPUT_ORDER)]
            if candidate in requests:
                return candidate
        raise AssertionError("round-robin request disappeared")


class Mesh2x2:
    """Cycle-stepped 2x2 mesh used by Comm 0A-0C."""

    def __init__(self, fifo_depth: int = INPUT_FIFO_DEPTH, ni_depth: int = NI_FIFO_DEPTH) -> None:
        if fifo_depth <= 0 or ni_depth <= 0:
            raise ValueError("FIFO depths must be positive")
        self.fifo_depth = fifo_depth
        self.ni_depth = ni_depth
        self.routers = [_Router(tile) for tile in range(TILE_COUNT)]
        self.ni_injection = [deque() for _ in range(TILE_COUNT)]
        self.ejection_ready = [True] * TILE_COUNT
        self._assembly = [[] for _ in range(TILE_COUNT)]
        self.received_packets: list[list[Packet]] = [[] for _ in range(TILE_COUNT)]
        self.received_flits: list[list[Flit]] = [[] for _ in range(TILE_COUNT)]
        self.cycle = 0
        self.flit_hops = 0
        self.link_flit_count: dict[tuple[int, int], int] = {}
        self.fifo_full_cycles = 0
        self.max_occupancy = 0
        self.inject_stall_cycles = [0] * TILE_COUNT
        self.output_blocked_cycles = 0

    def can_inject(self, tile: int, count: int) -> bool:
        _tile("tile", tile)
        return len(self.ni_injection[tile]) + count <= self.ni_depth

    def inject_flits(self, tile: int, flits: Iterable[Flit]) -> None:
        seq = tuple(flits)
        if not self.can_inject(tile, len(seq)):
            raise BufferError("NI injection FIFO is full")
        self.ni_injection[tile].extend(seq)

    def inject_packet(self, packet: Packet) -> None:
        self.inject_flits(packet.src_tile, pack_packet(packet))

    def visible_local_output(self, tile: int) -> Flit | None:
        router = self.routers[tile]
        selected = router.select("LOCAL")
        return None if selected is None else router.inputs[selected][0]

    def _accept_ejected(self, tile: int, flit: Flit) -> None:
        self.received_flits[tile].append(flit)
        assembly = self._assembly[tile]
        if flit.type is FlitType.HEADTAIL:
            if assembly:
                raise PacketFormatError("HEADTAIL arrived inside a packet")
            self.received_packets[tile].append(unpack_packet((flit,)))
        elif flit.type is FlitType.HEAD:
            if assembly:
                raise PacketFormatError("nested packet head")
            assembly.append(flit)
        elif flit.type is FlitType.BODY:
            if not assembly:
                raise PacketFormatError("BODY without HEAD")
            assembly.append(flit)
        elif flit.type is FlitType.TAIL:
            if not assembly:
                raise PacketFormatError("TAIL without HEAD")
            assembly.append(flit)
            self.received_packets[tile].append(unpack_packet(tuple(assembly)))
            assembly.clear()

    def step(self) -> None:
        """Advance one clock using start-of-cycle ready/valid state."""
        self.cycle += 1
        for router in self.routers:
            for queue in router.inputs.values():
                self.max_occupancy = max(self.max_occupancy, len(queue))
                if len(queue) == self.fifo_depth:
                    self.fifo_full_cycles += 1

        transfers: list[tuple[int, str, str, Flit]] = []
        for tile, router in enumerate(self.routers):
            for output in PORTS:
                selected = router.select(output)
                if selected is None:
                    router.held_grants[output] = None
                    continue
                flit = router.inputs[selected][0]
                if output == "LOCAL":
                    ready = self.ejection_ready[tile]
                else:
                    neighbor, neighbor_input = _neighbor(tile, output)
                    ready = len(self.routers[neighbor].inputs[neighbor_input]) < self.fifo_depth
                if ready:
                    transfers.append((tile, output, selected, flit))
                    router.held_grants[output] = None
                else:
                    router.held_grants[output] = selected
                    self.output_blocked_cycles += 1

        # A given input can request only one output, so all pops are independent.
        for tile, output, input_port, flit in transfers:
            router = self.routers[tile]
            popped = router.inputs[input_port].popleft()
            assert popped == flit
            if flit.type is FlitType.HEAD:
                router.output_locks[output] = input_port
            elif flit.type is FlitType.TAIL:
                if router.output_locks[output] != input_port:
                    raise PacketFormatError("TAIL does not match output lock")
                router.output_locks[output] = None
                router.rr_next[output] = (INPUT_ORDER.index(input_port) + 1) % len(INPUT_ORDER)
            elif flit.type is FlitType.HEADTAIL:
                router.rr_next[output] = (INPUT_ORDER.index(input_port) + 1) % len(INPUT_ORDER)

            if output == "LOCAL":
                self._accept_ejected(tile, flit)
            else:
                neighbor, neighbor_input = _neighbor(tile, output)
                self.routers[neighbor].inputs[neighbor_input].append(flit)
                self.flit_hops += 1
                key = (tile, neighbor)
                self.link_flit_count[key] = self.link_flit_count.get(key, 0) + 1

        # NI-to-local-router transfer is not counted as a network hop.
        for tile, queue in enumerate(self.ni_injection):
            local_input = self.routers[tile].inputs["LOCAL"]
            if queue and len(local_input) < self.fifo_depth:
                local_input.append(queue.popleft())
            elif queue:
                self.inject_stall_cycles[tile] += 1

        for router in self.routers:
            for queue in router.inputs.values():
                if len(queue) > self.fifo_depth:
                    raise AssertionError("router input FIFO overflow")
                self.max_occupancy = max(self.max_occupancy, len(queue))

    def idle(self) -> bool:
        return (
            all(not queue for queue in self.ni_injection)
            and all(not q for r in self.routers for q in r.inputs.values())
            and all(not a for a in self._assembly)
        )

    def run_until_idle(self, max_cycles: int = 1000) -> None:
        for _ in range(max_cycles):
            if self.idle():
                return
            self.step()
        raise TimeoutError("mesh did not become idle")
